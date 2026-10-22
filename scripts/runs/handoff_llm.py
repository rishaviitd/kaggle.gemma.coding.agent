"""Route an agent run to a student model for the first N agent calls, then to a teacher.

Used to build On-policy Expert Corrections (OEC) data: the student starts a task, the
teacher takes over in the same ADK session and container.
"""
from __future__ import annotations

import hashlib
import random
import re
from importlib import metadata
from pathlib import Path
from typing import AsyncGenerator, Callable

from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from pydantic import ConfigDict, Field

SUMMARY_PROMPT = 'The following is a conversation history between a user and an AI agent.'


def is_summary_request(llm_request: LlmRequest) -> bool:
    """ADK context compaction: a tool-less request holding one summarizing prompt."""
    tools = getattr(llm_request.config, 'tools', None) if llm_request.config else None
    if tools:
        return False
    contents = llm_request.contents or []
    if len(contents) != 1:
        return False
    parts = contents[0].parts or []
    return bool(parts) and (parts[0].text or '').startswith(SUMMARY_PROMPT)


class HandoffStop(LlmCallsLimitExceededError):
    """Raised at the switch in run A so the teacher client is never built.

    Subclasses LlmCallsLimitExceededError: swegemma treats that as a clean episode end and still
    takes the fallback diff while the sandbox is alive. Any other exception would destroy the
    workspace first. The message must not match the retry plugin's phrases.
    """

    def __init__(self, switch_after: int):
        super().__init__(f'handoff after {switch_after} student calls: teacher not called')
        self.switch_after = switch_after


class HandoffState:
    """Mutable run state shared by every copy of a HandoffLlm.

    ADK and pydantic copy the registered model when an agent runs. Copies return this same
    object, so the registered instance sees the counters and responses of the running one.
    """

    def __init__(self) -> None:
        self.agent_calls = 0
        self.teacher_calls = 0
        self.summary_before_handoff = 0
        self.student_responses: list[dict] = []
        self.stopped = False

    def __copy__(self) -> 'HandoffState':
        return self

    def __deepcopy__(self, memo) -> 'HandoffState':
        return self


class HandoffLlm(BaseLlm):
    """Agent calls 1..switch_after go to the student, later ones to the teacher.

    Compaction summary calls are not counted. After the first teacher call they go to the
    teacher; before it they raise, so no teacher call is spent on a run that is rejected anyway.

    With stop_at_switch, the first call past switch_after calls on_switch, sets stopped and
    raises HandoffStop instead of routing to the teacher.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    student: BaseLlm
    teacher: BaseLlm
    switch_after: int
    stop_at_switch: bool = False
    on_switch: Callable[[], None] | None = None
    state: HandoffState = Field(default_factory=HandoffState)

    @property
    def stopped(self) -> bool:
        return self.state.stopped

    def _route(self, llm_request: LlmRequest) -> BaseLlm:
        state = self.state
        if is_summary_request(llm_request):
            if state.teacher_calls == 0:
                state.summary_before_handoff += 1
                raise RuntimeError('compaction before handoff: run rejected')
            return self.teacher
        state.agent_calls += 1
        if state.agent_calls <= self.switch_after:
            return self.student
        if self.stop_at_switch:
            if self.on_switch is not None:
                self.on_switch()
            state.stopped = True
            raise HandoffStop(self.switch_after)
        state.teacher_calls += 1
        return self.teacher

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        target = self._route(llm_request)
        # ADK stamps the agent's alias on the request; LiteLlm would send it upstream.
        llm_request.model = target.model
        async for response in target.generate_content_async(llm_request, stream=stream):
            if target is self.student:
                self.state.student_responses.append(response.model_dump(mode='json', exclude_none=True))
            yield response

    def student_responses(self) -> list[dict]:
        return list(self.state.student_responses)

    def record(self) -> dict:
        return {
            'switch_after': self.switch_after,
            'agent_calls': self.state.agent_calls,
            'student_calls': min(self.state.agent_calls, self.switch_after),
            'teacher_calls': self.state.teacher_calls,
            'summary_calls_before_handoff': self.state.summary_before_handoff,
            'student_model': self.student.model,
            'teacher_model': self.teacher.model,
        }


def draw_switch_points(attempts: int, low: int, high: int, seed: int | str) -> list[int]:
    """One switch point per equal-width bin of [low, high]: early, middle and late handoffs."""
    if attempts < 1 or low < 1 or high < low:
        raise ValueError('need attempts >= 1 and 1 <= low <= high')
    rng = random.Random(seed)
    span = high - low + 1
    points = []
    for index in range(attempts):
        start = low + (span * index) // attempts
        end = max(start, low + (span * (index + 1)) // attempts - 1)
        points.append(rng.randint(start, end))
    return points


def swegemma_version() -> str:
    try:
        return metadata.version('swegemma')
    except metadata.PackageNotFoundError:
        return 'unknown'


def config_fingerprint(agent_dir: Path, sandbox: str) -> dict:
    """Hash the agent config actually used, plus the sandbox backend and harness version."""
    digest = hashlib.sha256()
    for path in sorted(agent_dir.rglob('*')):
        relative = path.relative_to(agent_dir)
        if not path.is_file() or '__pycache__' in relative.parts:
            continue
        digest.update(str(relative).encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return {'agent_config_sha256': digest.hexdigest(), 'sandbox': sandbox,
            'swegemma_version': swegemma_version()}


def validate_handoff_args(provider: str, teacher_model: str | None, switch_after: int | None) -> None:
    if teacher_model is None and switch_after is None:
        return
    if teacher_model is None or switch_after is None:
        raise ValueError('--handoff-teacher-model and --handoff-after must be given together')
    if provider != 'vllm':
        raise ValueError('handoff needs --provider vllm (the student)')
    if switch_after < 1:
        raise ValueError('--handoff-after must be a positive integer')


def submission_aliases(model: str, agent_dir: Path) -> set[str]:
    """The serving name plus every `model:` alias declared by the submission YAML files."""
    aliases = {model}
    for config_path in agent_dir.rglob('*.yaml'):
        match = re.search(r'^model:\s*([^\s#]+)', config_path.read_text(), re.MULTILINE)
        if match:
            aliases.add(match.group(1).strip('"\''))
    return aliases


class StoppedTeacher(BaseLlm):
    """Teacher stand-in for run A: no Anthropic client exists, and any call is a bug."""

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        raise RuntimeError('teacher must not be called in stop mode')
        yield  # pragma: no cover - makes this an async generator


class EventRecorder:
    """Keeps the non-partial ADK events of one run as JSON dicts, once per event id."""

    def __init__(self):
        self.events: list[dict] = []
        self._seen: set = set()

    def record(self, event) -> None:
        if event is None or getattr(event, 'partial', False):
            return
        event_id = getattr(event, 'id', None)
        if event_id is not None:
            if event_id in self._seen:
                return
            self._seen.add(event_id)
        self.events.append(event.model_dump(mode='json'))


def build_handoff_registry(*, student_model, teacher_model, agent_dir: Path, switch_after: int,
                           student_base=None, student_key=None, teacher_base=None, teacher_key=None,
                           reasoning_effort=None, student: BaseLlm | None = None,
                           stop_at_switch: bool = False, on_switch: Callable[[], None] | None = None):
    """Register one shared HandoffLlm under every alias so call counters are global.

    student overrides the LiteLLM student (run B replays recorded responses). With
    stop_at_switch the teacher is a StoppedTeacher and no teacher credentials are read.
    """
    from adk_submission import ModelRegistry
    from google.adk.models.lite_llm import LiteLlm

    if student is None:
        extra = {'reasoning_effort': reasoning_effort} if reasoning_effort else {}
        student = LiteLlm(model=student_model, api_base=student_base, api_key=student_key,
                          num_retries=2, **extra)
    if stop_at_switch:
        teacher = StoppedTeacher(model=teacher_model)
    else:
        teacher = LiteLlm(model=teacher_model, api_base=teacher_base, api_key=teacher_key,
                          num_retries=2)
    handoff = HandoffLlm(model=student_model, student=student, teacher=teacher,
                         switch_after=switch_after, stop_at_switch=stop_at_switch,
                         on_switch=on_switch)
    models = ModelRegistry()
    for alias in submission_aliases(student_model, agent_dir):
        models.register(alias, handoff)
    return models, handoff


def teacher_rewriter(thinking: str) -> Callable[[dict], dict]:
    """Haiku request rewriter: drop fields Haiku rejects, then set or omit thinking."""
    if thinking not in ('adaptive', 'off'):
        raise ValueError("thinking must be 'adaptive' or 'off'")
    dropped = ('temperature', 'top_p', 'top_k', 'thinking_token_budget',
               'chat_template_kwargs', 'extra_body', 'thinking')

    def rewrite(body: dict) -> dict:
        body = {k: v for k, v in body.items() if k not in dropped}
        if thinking == 'adaptive':
            body['thinking'] = {'type': 'adaptive', 'display': 'summarized'}
        return body

    return rewrite

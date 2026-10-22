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

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from pydantic import PrivateAttr

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


class HandoffLlm(BaseLlm):
    """Agent calls 1..switch_after go to the student, later ones to the teacher.

    Compaction summary calls are not counted. They go to the student until the first
    teacher call has happened, and to the teacher afterwards.
    """

    student: BaseLlm
    teacher: BaseLlm
    switch_after: int

    _agent_calls: int = PrivateAttr(default=0)
    _teacher_calls: int = PrivateAttr(default=0)
    _summary_before_handoff: int = PrivateAttr(default=0)

    def _route(self, llm_request: LlmRequest) -> BaseLlm:
        if is_summary_request(llm_request):
            if self._teacher_calls == 0:
                self._summary_before_handoff += 1
                return self.student
            return self.teacher
        self._agent_calls += 1
        if self._agent_calls <= self.switch_after:
            return self.student
        self._teacher_calls += 1
        return self.teacher

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        target = self._route(llm_request)
        # ADK stamps the agent's alias on the request; LiteLlm would send it upstream.
        llm_request.model = target.model
        async for response in target.generate_content_async(llm_request, stream=stream):
            yield response

    def record(self) -> dict:
        return {
            'switch_after': self.switch_after,
            'agent_calls': self._agent_calls,
            'student_calls': min(self._agent_calls, self.switch_after),
            'teacher_calls': self._teacher_calls,
            'summary_calls_before_handoff': self._summary_before_handoff,
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


def build_handoff_registry(*, student_base, student_key, student_model, teacher_base, teacher_key,
                           teacher_model, agent_dir: Path, switch_after: int, reasoning_effort=None):
    """Register one shared HandoffLlm under every alias so call counters are global."""
    from adk_submission import ModelRegistry
    from google.adk.models.lite_llm import LiteLlm

    extra = {'reasoning_effort': reasoning_effort} if reasoning_effort else {}
    student = LiteLlm(model=student_model, api_base=student_base, api_key=student_key,
                      num_retries=2, **extra)
    teacher = LiteLlm(model=teacher_model, api_base=teacher_base, api_key=teacher_key, num_retries=2)
    handoff = HandoffLlm(model=student_model, student=student, teacher=teacher,
                         switch_after=switch_after)
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

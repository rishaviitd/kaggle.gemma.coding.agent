# E1 OEC two-run generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate OEC handoff trajectories with the E4B student on Kaggle L4 (internet off, competition attached) and the Haiku teacher in local Docker, joined by a snapshot.

**Architecture:** Run A stops at the switch after the student's first k calls and saves the student responses and ADK session events. Run B replays the student responses in the same episode, executes tools live in a Linux container, checks each live tool result against the saved events, then sends the remaining calls to Haiku. The log layout matches today's pipeline, so `export_oec`, `build_oec_dataset`, `merge_datasets` and `audit_parity` run unchanged.

**Tech Stack:** Python 3.13, Google ADK (`BaseLlm`, `LlmRequest`, `LlmResponse`), swegemma harness (subprocess sandbox), LiteLLM, Docker, Kaggle CLI 2.2.4 via `uvx --from kaggle==2.2.4 kaggle ...`.

**Spec:** [docs/superpowers/specs/2026-10-23-e1-oec-two-run-design.md](../specs/2026-10-23-e1-oec-two-run-design.md) (amended 2026-10-23: replay instead of workspace restore). The E1 spec [docs/superpowers/specs/2026-10-23-e1-oec-dataset-design.md](../specs/2026-10-23-e1-oec-dataset-design.md) still governs filtering, parity and export.

## Global Constraints

- One agent config for all sources: `src/rishav/inspiration-one`; `agent_config_sha256` in every manifest; mismatches refused (E1 spec §4).
- One sandbox backend per dataset: `subprocess` for both runs. Recorded in every manifest.
- No Anthropic key in any file, kernel, or container image. Read from the environment by name at run B time only.
- Kaggle kernels for data generation are private. Kernel pushes are outward-facing: each push needs the user's go-ahead.
- Haiku spend needs the user's cost go-ahead for the full run. The 2-task dry run is the only run allowed before that.
- Do not read `.env` or `~/.kaggle/credentials.json`.
- Tests use `unittest` and run with `python -m unittest` from the test file's package, matching existing `scripts/tests/`.
- Preexisting failures (`scripts/sft_export/tests/test_export_sft.py`, 2 tests, user WIP) are not part of this plan.

---

### Task 1: Spike: swegemma session events and stop mechanism

**Files:**
- Read only: `.venv/.../swegemma/harness/agent_runner.py`, `.venv/.../swegemma/harness/verification.py`, the ADK runner call site.
- Output: `docs/superpowers/plans/2026-10-23-e1-oec-two-run-spike.md` (short notes, not code).

**Interfaces:**
- Produces: the exact call that returns the ADK session events for a run, the exact call that starts a run from pre-recorded LLM responses, and whether a `BaseLlm` exception ends the episode cleanly with the workspace still readable.

- [ ] **Step 1: Locate the session service and runner call**

Run: `grep -n -E "session_service|get_session|Runner\(|run_async|events" .venv/lib/python3.13/site-packages/swegemma/harness/agent_runner.py | head -60`
Expected: the lines that create the session and drive the runner. If the path differs, locate it with `python -c "import swegemma, os; print(os.path.dirname(swegemma.__file__))"`.

- [ ] **Step 2: Find how an exception from the model is handled**

Run: `grep -n -E "except|raise" <agent_runner path> | head -40`
Expected: whether a raised exception inside `generate_content_async` propagates out of `Evaluator.evaluate_task`. Record it. (`task_pipeline.run_one` already catches exceptions in `run()`, so a propagated `HandoffStop` will be caught there; confirm this is the path.)

- [ ] **Step 3: Record findings**

Write the answers to the three points above into the spike notes file. If the session events are not reachable from outside the runner, Task 3 must dump them from inside `HandoffLlm` instead. Record which option was chosen.

- [ ] **Step 4: Commit the notes**

```bash
git add docs/superpowers/plans/2026-10-23-e1-oec-two-run-spike.md
git commit -m "docs: record swegemma session and stop findings for two-run split"
```

---

### Task 2: Snapshot writer and reader

**Files:**
- Create: `scripts/runs/handoff_snapshot.py`
- Test: `scripts/tests/test_handoff_snapshot.py`

**Interfaces:**
- Produces:
  - `REQUIRED_FILES = ('handoff.json', 'student_responses.json', 'session_events.json', 'vllm_exchanges.json', 'manifest.json')`
  - `write_snapshot(out_dir: Path, *, handoff: dict, student_responses: list[dict], session_events: list[dict], vllm_exchanges: list[dict], manifest: dict) -> None`
  - `read_snapshot(snapshot_dir: Path, *, agent_dir: Path, sandbox: str) -> dict` returns `{'handoff', 'student_responses', 'session_events', 'vllm_exchanges', 'manifest'}`; raises `ValueError` on a missing file, a sandbox other than `subprocess`, or an `agent_config_sha256` that differs from `config_fingerprint(agent_dir, sandbox)`.

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_handoff_snapshot.py
import json
import tempfile
import unittest
from pathlib import Path

from runs import handoff_snapshot as snap  # import path matches test_handoff_llm.py
from runs.handoff_llm import config_fingerprint


def write_agent(root: Path) -> Path:
    agent = root / 'agent'
    agent.mkdir()
    (agent / 'agent.yaml').write_text('model: gemma4\n')
    return agent


def sample(agent: Path, sandbox: str = 'subprocess') -> dict:
    return dict(
        handoff={'switch_after': 3, 'agent_calls': 4, 'student_calls': 3, 'teacher_calls': 0},
        student_responses=[{'content': {'role': 'model', 'parts': [{'text': 'hi'}]}}],
        session_events=[{'author': 'user'}],
        vllm_exchanges=[{'request': {}, 'response': {}}],
        manifest=config_fingerprint(agent, sandbox),
    )


class SnapshotTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = write_agent(Path(tmp))
            out = Path(tmp) / 'snapshot'
            data = sample(agent)
            snap.write_snapshot(out, **data)
            loaded = snap.read_snapshot(out, agent_dir=agent, sandbox='subprocess')
            self.assertEqual(loaded['handoff'], data['handoff'])
            self.assertEqual(loaded['student_responses'], data['student_responses'])

    def test_refuses_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = write_agent(Path(tmp))
            out = Path(tmp) / 'snapshot'
            snap.write_snapshot(out, **sample(agent))
            (out / 'session_events.json').unlink()
            with self.assertRaisesRegex(ValueError, 'session_events.json'):
                snap.read_snapshot(out, agent_dir=agent, sandbox='subprocess')

    def test_refuses_docker_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = write_agent(Path(tmp))
            out = Path(tmp) / 'snapshot'
            snap.write_snapshot(out, **sample(agent, sandbox='docker'))
            with self.assertRaisesRegex(ValueError, 'subprocess'):
                snap.read_snapshot(out, agent_dir=agent, sandbox='subprocess')

    def test_refuses_config_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = write_agent(Path(tmp))
            out = Path(tmp) / 'snapshot'
            snap.write_snapshot(out, **sample(agent))
            (agent / 'agent.yaml').write_text('model: gemma4\ntools: []\n')
            with self.assertRaisesRegex(ValueError, 'config'):
                snap.read_snapshot(out, agent_dir=agent, sandbox='subprocess')


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd scripts && python -m unittest tests.test_handoff_snapshot -v`
Expected: FAIL with `ModuleNotFoundError` for `runs.handoff_snapshot`. (Confirm the import style against `scripts/tests/test_handoff_llm.py` first and adjust the import line to match.)

- [ ] **Step 3: Write the implementation**

```python
# scripts/runs/handoff_snapshot.py
"""Snapshot written by the student run (Kaggle) and read by the teacher run (local Docker)."""
from __future__ import annotations

import json
from pathlib import Path

from handoff_llm import config_fingerprint

REQUIRED_FILES = ('handoff.json', 'student_responses.json', 'session_events.json',
                  'vllm_exchanges.json', 'manifest.json')


def write_snapshot(out_dir: Path, *, handoff: dict, student_responses: list[dict],
                   session_events: list[dict], vllm_exchanges: list[dict], manifest: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payloads = {'handoff.json': handoff, 'student_responses.json': student_responses,
                'session_events.json': session_events, 'vllm_exchanges.json': vllm_exchanges,
                'manifest.json': manifest}
    for name, payload in payloads.items():
        (out_dir / name).write_text(json.dumps(payload, indent=2) + '\n')


def read_snapshot(snapshot_dir: Path, *, agent_dir: Path, sandbox: str) -> dict:
    missing = [name for name in REQUIRED_FILES if not (snapshot_dir / name).is_file()]
    if missing:
        raise ValueError('snapshot is missing: ' + ', '.join(missing))
    loaded = {name.removesuffix('.json'): json.loads((snapshot_dir / name).read_text())
              for name in REQUIRED_FILES}
    manifest = loaded['manifest']
    if manifest.get('sandbox') != 'subprocess' or sandbox != 'subprocess':
        raise ValueError('snapshot and run must both use the subprocess sandbox')
    current = config_fingerprint(agent_dir, sandbox)
    if manifest.get('agent_config_sha256') != current['agent_config_sha256']:
        raise ValueError('agent config differs from the snapshot run')
    return loaded
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd scripts && python -m unittest tests.test_handoff_snapshot -v`
Expected: 4 tests OK.

- [ ] **Step 5: Commit**

```bash
git add scripts/runs/handoff_snapshot.py scripts/tests/test_handoff_snapshot.py
git commit -m "feat: add handoff snapshot writer and validating reader"
```

---

### Task 3: HandoffLlm records student responses and can stop at the switch

**Files:**
- Modify: `scripts/runs/handoff_llm.py` (class `HandoffLlm`, lines 35-80)
- Test: `scripts/tests/test_handoff_llm.py` (add cases)

**Interfaces:**
- Consumes: `HandoffLlm(student, teacher, switch_after)` as today.
- Produces:
  - `class HandoffStop(LlmCallsLimitExceededError)` (from `google.adk.agents.invocation_context`). It must subclass that error: swegemma's `run_agent_sandbox` swallows any other exception and destroys the sandbox, but it catches this one, takes the fallback diff while the sandbox is alive, and returns normally (spike notes `2026-10-23-e1-oec-two-run-spike.md`, Answer 2). The message must avoid `rate limit`, `overloaded`, `quota exceeded` and 429/5xx codes (retry plugin).
  - `HandoffLlm` new private attributes: `_student_responses: list[dict]` (serialized `LlmResponse`s), and new optional field `stop_at_switch: bool = False`, and `on_switch: Callable[[], None] | None = None`.
  - `HandoffLlm.student_responses() -> list[dict]`.
  - Behaviour: on the call that would be teacher call 1 (agent call `switch_after + 1`), if `stop_at_switch`, call `on_switch()` then raise `HandoffStop`. The teacher is never called.

Note: the Task 1 spike decides whether `on_switch` receives events from here or the snapshot is written by the caller. Implement the hook as stated; the caller passes the events function.

- [ ] **Step 1: Write the failing tests**

Add to `scripts/tests/test_handoff_llm.py`, using the fake model helpers already in that file:

```python
class StopAtSwitchTests(unittest.TestCase):
    def test_teacher_is_never_called_and_hook_fires_once(self):
        calls = []
        student, teacher = FakeModel('student'), FakeModel('teacher')
        llm = HandoffLlm(model='gemma4', student=student, teacher=teacher, switch_after=2,
                         stop_at_switch=True, on_switch=lambda: calls.append('switch'))
        run_turns(llm, 2)
        with self.assertRaises(HandoffStop):
            run_turns(llm, 1)
        self.assertEqual(calls, ['switch'])
        self.assertEqual(teacher.calls, 0)

    def test_student_responses_are_recorded_in_order(self):
        student, teacher = FakeModel('student'), FakeModel('teacher')
        llm = HandoffLlm(model='gemma4', student=student, teacher=teacher, switch_after=2)
        run_turns(llm, 2)
        self.assertEqual(len(llm.student_responses()), 2)
        self.assertEqual(llm.student_responses()[0]['content']['parts'][0]['text'], 'student-1')
```

If `FakeModel` and `run_turns` do not exist in the test file, add them as small helpers: `FakeModel` is a `BaseLlm` subclass whose `generate_content_async` yields one `LlmResponse(content=types.Content(role='model', parts=[types.Part(text=f'{name}-{n}')]))`, counting calls; `run_turns(llm, n)` drives `llm.generate_content_async(LlmRequest(), stream=False)` `n` times with a minimal request that is not a summary request.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd scripts && python -m unittest tests.test_handoff_llm -v`
Expected: the new tests FAIL (`HandoffStop` undefined, `student_responses` missing).

- [ ] **Step 3: Implement**

In `scripts/runs/handoff_llm.py`:
- Add `class HandoffStop(LlmCallsLimitExceededError)` with a docstring: raised at the switch in run A so the teacher is never called. Check the base class's constructor signature first and pass what it needs. Also set `self.stopped = True` on the `HandoffLlm` (public bool, default False) before raising, because swegemma will report the stop as `Agent exceeded turns budget`.
- Add the fields `stop_at_switch: bool = False` and `on_switch: Callable[[], None] | None = None`, and `_student_responses: list = PrivateAttr(default_factory=list)`.
- In `_route`, before returning the teacher for agent call `switch_after + 1` when `stop_at_switch` is set: call `self.on_switch()` if set, then `raise HandoffStop()`.
- In `generate_content_async`, when the target is the student, iterate responses and append `response.model_dump(mode='json', exclude_none=True)` to `_student_responses` before yielding.
- Add `def student_responses(self) -> list[dict]: return list(self._student_responses)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd scripts && python -m unittest tests.test_handoff_llm -v`
Expected: all tests OK, including the pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add scripts/runs/handoff_llm.py scripts/tests/test_handoff_llm.py
git commit -m "feat: record student responses and stop at the handoff"
```

---

### Task 4: ReplayLlm and live-versus-recorded tool check

**Files:**
- Create: `scripts/runs/replay_llm.py`
- Test: `scripts/tests/test_replay_llm.py`

**Interfaces:**
- Produces:
  - `class ReplayLlm(BaseLlm)` with `responses: list[dict]` and `_index: int`. `generate_content_async` yields the next recorded response (deserialized with `LlmResponse.model_validate(...)`) and raises `RuntimeError('replay exhausted')` when called past the end.
  - `def tool_results(events: list[dict]) -> list[dict]` returns, for each function-response part in the event list, `{'name', 'response'}` in order.
  - `def verify_replay(recorded_events: list[dict], live_events: list[dict]) -> None` raises `ValueError` at the first mismatch, comparing `tool_results` after dropping timing keys (`duration`, `elapsed`, `time`, `timestamp`).

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_replay_llm.py
import asyncio
import unittest

from google.adk.models.llm_request import LlmRequest
from google.genai import types

from runs.replay_llm import ReplayLlm, tool_results, verify_replay


def recorded(text: str) -> dict:
    return {'content': {'role': 'model', 'parts': [{'text': text}]}}


def fn_event(name: str, response: dict, timestamp: float = 0.0) -> dict:
    return {'author': 'agent', 'timestamp': timestamp,
            'content': {'role': 'user', 'parts': [{'function_response': {
                'name': name, 'response': response}}]}}


class ReplayTests(unittest.TestCase):
    def drain(self, llm):
        async def go():
            return [r async for r in llm.generate_content_async(LlmRequest(), stream=False)]
        return asyncio.run(go())

    def test_returns_recorded_responses_in_order(self):
        llm = ReplayLlm(model='gemma4', responses=[recorded('a'), recorded('b')])
        first = self.drain(llm)[0]
        self.assertEqual(first.content.parts[0].text, 'a')
        self.assertEqual(self.drain(llm)[0].content.parts[0].text, 'b')

    def test_fails_when_overrun(self):
        llm = ReplayLlm(model='gemma4', responses=[recorded('a')])
        self.drain(llm)
        with self.assertRaisesRegex(RuntimeError, 'replay exhausted'):
            self.drain(llm)

    def test_verify_ignores_timing(self):
        recorded_events = [fn_event('bash', {'stdout': 'x', 'duration': 1.0}, 1.0)]
        live_events = [fn_event('bash', {'stdout': 'x', 'duration': 9.0}, 9.0)]
        verify_replay(recorded_events, live_events)

    def test_verify_flags_planted_difference(self):
        recorded_events = [fn_event('bash', {'stdout': 'x'})]
        live_events = [fn_event('bash', {'stdout': 'y'})]
        with self.assertRaisesRegex(ValueError, 'tool result 0'):
            verify_replay(recorded_events, live_events)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd scripts && python -m unittest tests.test_replay_llm -v`
Expected: FAIL with `ModuleNotFoundError` for `runs.replay_llm`.

- [ ] **Step 3: Implement**

```python
# scripts/runs/replay_llm.py
"""Replay the student's recorded responses inside a teacher run, and check live tool results."""
from __future__ import annotations

from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

TIMING_KEYS = {'duration', 'elapsed', 'time', 'timestamp'}


class ReplayLlm(BaseLlm):
    """Serves recorded student responses in order. Any call past the recording is a bug."""

    responses: list[dict]
    _index: int = 0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if self._index >= len(self.responses):
            raise RuntimeError('replay exhausted')
        response = LlmResponse.model_validate(self.responses[self._index])
        self._index += 1
        yield response


def tool_results(events: list[dict]) -> list[dict]:
    results = []
    for event in events:
        for part in (event.get('content') or {}).get('parts') or []:
            call = part.get('function_response')
            if call:
                results.append({'name': call['name'], 'response': _strip_timing(call['response'])})
    return results


def _strip_timing(value):
    if isinstance(value, dict):
        return {k: _strip_timing(v) for k, v in value.items() if k not in TIMING_KEYS}
    if isinstance(value, list):
        return [_strip_timing(v) for v in value]
    return value


def verify_replay(recorded_events: list[dict], live_events: list[dict]) -> None:
    expected, actual = tool_results(recorded_events), tool_results(live_events)
    for index, (want, got) in enumerate(zip(expected, actual)):
        if want != got:
            raise ValueError(f'tool result {index} differs from the snapshot: {want!r} != {got!r}')
    if len(expected) != len(actual):
        raise ValueError(f'tool result count differs: snapshot {len(expected)}, live {len(actual)}')
```

Check the `_index` handling: a pydantic `BaseLlm` field with a default works as a plain attribute; if the pydantic model rejects assignment, use `PrivateAttr` as `handoff_llm.py` does.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd scripts && python -m unittest tests.test_replay_llm -v`
Expected: 4 tests OK.

- [ ] **Step 5: Commit**

```bash
git add scripts/runs/replay_llm.py scripts/tests/test_replay_llm.py
git commit -m "feat: add replay of recorded student responses with tool-result check"
```

---

### Task 5: task_pipeline modes for run A and run B

**Files:**
- Modify: `scripts/runs/task_pipeline.py` (`arguments()` at line 123; `run_one()` around lines 405-470; `run()` around line 480)
- Test: `scripts/tests/test_run_oec.py` is for the driver; add argument checks in a new `scripts/tests/test_pipeline_modes.py`.

**Interfaces:**
- Consumes: `HandoffLlm`, `HandoffStop` (Task 3); `write_snapshot`, `read_snapshot` (Task 2); `ReplayLlm`, `verify_replay` (Task 4).
- Produces CLI flags:
  - `--handoff-stop-after-switch`: run A. Requires `--handoff-teacher-model`, `--handoff-after`. Builds no Anthropic client. On `HandoffStop`, writes the snapshot to `args.logs / task_id / 'snapshot'` and exits the task as stopped (no `result` file).
  - `--handoff-resume <snapshot_dir>`: run B. Requires `--handoff-teacher-model`. Reads the snapshot with `read_snapshot`, sets `switch_after` from `handoff.json`, uses `ReplayLlm` as the student, verifies tool results after the run with `verify_replay(snapshot['session_events'], live_events)`, and copies `vllm_exchanges.json` into the task log directory.

- [ ] **Step 1: Write the failing test for argument validation**

```python
# scripts/tests/test_pipeline_modes.py
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runs'))
import task_pipeline  # noqa: E402


class PipelineModeArgs(unittest.TestCase):
    def parse(self, *extra):
        argv = ['task_pipeline.py', '--split', 'val', '--task-id', 't1', '--submission', 'x',
                '--run-name', 'r', *extra]
        with mock.patch.object(sys, 'argv', argv):
            return task_pipeline.arguments()

    def test_stop_mode_needs_teacher_and_switch(self):
        with self.assertRaises(SystemExit):
            self.parse('--handoff-stop-after-switch')

    def test_resume_and_stop_are_exclusive(self):
        with self.assertRaises(SystemExit):
            self.parse('--handoff-stop-after-switch', '--handoff-resume', 'snap',
                       '--handoff-teacher-model', 'claude-haiku-5-5', '--handoff-after', '3',
                       '--provider', 'vllm')


if __name__ == '__main__':
    unittest.main()
```

Adjust the required arguments to what `arguments()` actually requires; read `arguments()` at `task_pipeline.py:123` first and mirror its required flags in `parse`.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd scripts && python -m unittest tests.test_pipeline_modes -v`
Expected: FAIL (flags not defined, so argparse errors do not match the intended cases).

- [ ] **Step 3: Implement the flags**

In `arguments()` add:
- `--handoff-stop-after-switch` (store_true)
- `--handoff-resume` (type Path, default None)
- A check: stop and resume are exclusive; resume needs `--handoff-teacher-model`; stop needs `--handoff-teacher-model` and `--handoff-after`; both call `p.error(...)`.

- [ ] **Step 4: Implement run A (stop) in `run_one`**

- When `args.handoff_stop_after_switch`, build the handoff registry as today, but pass `stop_at_switch=True` and `on_switch=lambda: switch_state.update(events=session_events())` where `session_events` is the Task 1 finding. Do not construct `teacher_proxy` or `teacher_key`.
- Do NOT catch `HandoffStop` around `evaluate_task` (it never reaches there; swegemma turns it into a normal `TaskResult`). Instead the `on_switch` closure writes the snapshot itself, inside the model call while the sandbox is alive. After `evaluate_task` returns, check `handoff.stopped`. Write the snapshot with `write_snapshot(args.logs / task_id / 'snapshot', handoff=handoff.record() | config_fingerprint(agent_dir, args.sandbox), student_responses=handoff.student_responses(), session_events=..., vllm_exchanges=proxy.exchanges, manifest=...)`. The manifest is `config_fingerprint(...)` plus `kaggle_kernel_version` and `accelerator` read from environment variables `KAGGLE_KERNEL_RUN_TYPE` and `NVIDIA_DEVICE_TYPE` if present, else `unknown`. Record the accelerator the kernel reports in `gen_student.py` (Task 9) rather than guessing here.
- When `handoff.stopped` is true after `evaluate_task`, the result is a stopped task, not a failure or a resolution: skip the `result_path` write and the `resolved` summary. `verify_task` will already have run on the captured diff; that is accepted cost.
- Events: build a `RecordingContext(SwegemmaContext)` whose `handle_adk_event` appends each non-partial event (as a dict via `model_dump(mode='json')`) to a list before calling `super()`. Construct it as `evaluate.py:244-256` does (use `Evaluator(config).docker` as `docker_manager`), pass it as `context=` to `evaluate_task`, and read the list in `on_switch` (run A) and after the run (run B). Read `.venv/lib/python3.12/site-packages/swegemma/evaluate.py:221-256` and `harness/agent_runner.py:378-382,521-528` first.

- [ ] **Step 5: Implement run B (resume) in `run_one`**

- When `args.handoff_resume` is set: `snapshot = read_snapshot(args.handoff_resume, agent_dir=agent_dir, sandbox=args.sandbox)`; check `snapshot['handoff']['switch_after']` is a positive int and `args.handoff_after` equals it if given.
- Build the registry with `student=ReplayLlm(model=args.litellm_model, responses=snapshot['student_responses'])` and the Haiku teacher as in today's teacher branch. Use `HandoffLlm(student=ReplayLlm(...), teacher=..., switch_after=snapshot['handoff']['switch_after'])`. Build the registry with the same `build_handoff_registry` path by passing a `student_factory` argument, or inline the construction; keep `submission_aliases` registration unchanged.
- After `evaluate_task`, call `verify_replay(snapshot['session_events'], live_events)`. Live events come from the `RecordingContext` list described above. If the list is empty, fail the run with a clear message rather than skipping the check.
- Copy `snapshot['vllm_exchanges']` to `task_logs / 'vllm_exchanges.json'` so `export_oec` finds the student side. Write `anthropic_exchanges.json` and `handoff.json` as today.
- Remove the vLLM student proxy from the resume path: `proxy` is not started.

- [ ] **Step 6: Run the tests and the existing handoff tests**

Run: `cd scripts && python -m unittest tests.test_pipeline_modes tests.test_handoff_llm tests.test_handoff_snapshot tests.test_replay_llm -v`
Expected: all OK.

- [ ] **Step 7: Commit**

```bash
git add scripts/runs/task_pipeline.py scripts/tests/test_pipeline_modes.py
git commit -m "feat: add run A stop and run B resume modes to the task pipeline"
```

---

### Task 6: run_oec driver stages

**Files:**
- Modify: `scripts/runs/run_oec.py` (`command_for` line 44, `job_done` line 55, `main` line 65)
- Test: `scripts/tests/test_run_oec.py`

**Interfaces:**
- Consumes: flags from Task 5.
- Produces: `--handoff-snapshot <dir>` on `run_oec.py` (the snapshot directory for the teacher stage; Task 7's wrapper passes it, and `command_for` forwards it as `--handoff-resume`), and `--stage {student,teacher}` on `run_oec.py`. `student` runs with `--handoff-stop-after-switch`. `teacher` runs with `--handoff-resume <logs>/<task>/snapshot`. `job_done` for `student` is "snapshot exists"; for `teacher` is "anthropic_exchanges.json and handoff.json exist with `teacher_calls >= 1`".

- [ ] **Step 1: Write the failing tests** in `scripts/tests/test_run_oec.py` (match the file's existing style): `command_for` for stage `student` includes `--handoff-stop-after-switch` and not `--handoff-resume`; stage `teacher` includes `--handoff-resume` with the snapshot path and not `--handoff-stop-after-switch`; `job_done` returns False for a missing snapshot and True for a snapshot with the five files.

- [ ] **Step 2: Run to verify failure**: `cd scripts && python -m unittest tests.test_run_oec -v`. Expected: new tests FAIL.

- [ ] **Step 3: Implement** the stage branch in `command_for` and `job_done`, and add `--stage` to `main`. Keep the dry-run plan output unchanged for the default stage.

- [ ] **Step 4: Run tests**: `cd scripts && python -m unittest tests.test_run_oec -v`. Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add scripts/runs/run_oec.py scripts/tests/test_run_oec.py
git commit -m "feat: add student and teacher stages to the OEC driver"
```

---

### Task 7: Docker teacher image and wrapper

**Files:**
- Create: `docker/teacher/Dockerfile`
- Create: `scripts/runs/run_teacher_docker.sh` (executable)

**Interfaces:**
- Produces: image `oec-teacher:<digest>` with the Kaggle image's Python version and the packages the subprocess sandbox needs; wrapper mounts `snapshot/` read-only and a run output directory read-write, passes `ANTHROPIC_API_KEY` by name (`-e ANTHROPIC_API_KEY`), and runs `run_oec.py --stage teacher`.

- [ ] **Step 1: Match the Kaggle image.** Read `data/transient/kaggle-oec-gen/gen_dry.py` and `notebooks/starter.ipynb` for the Python and package set the kernel uses. Record the base image and Python version in the Dockerfile header comment. If the Kaggle version cannot be read from the kernel output, record "unverified" and make the parity check (Task 8) the gate.

- [ ] **Step 2: Write the Dockerfile.** Base on a Python 3.13 Linux image, install the task wheel cache dependencies with `pip`, and `WORKDIR /repo`. The repo is mounted at runtime, not copied, so the agent config is the local checkout. Verify the Dockerfile builds: `docker build -t oec-teacher:dev docker/teacher`. Expected: build succeeds.

- [ ] **Step 3: Write the wrapper**

```bash
#!/usr/bin/env bash
# Run the Haiku continuation for one snapshot inside the teacher image.
# Usage: run_teacher_docker.sh <snapshot_dir> <run_name> <task_id> [extra run_oec args...]
set -euo pipefail
snapshot="$(cd "$1" && pwd)"; run_name="$2"; task_id="$3"; shift 3
repo="$(cd "$(dirname "$0")/../.." && pwd)"
docker run --rm \
  -e ANTHROPIC_API_KEY \
  -v "$repo:/repo" \
  -v "$snapshot:/snapshot:ro" \
  -w /repo \
  oec-teacher:dev \
  python scripts/runs/run_oec.py --stage teacher --run-prefix "$run_name" \
    --task-ids "$task_id" --sandbox subprocess --workers 1 --attempts 1 \
    --handoff-snapshot /snapshot "$@"
```

Note: `--handoff-snapshot` is not defined yet. Add it to `run_oec.py` in Task 6 as the snapshot root, passed to `command_for` as `--handoff-resume`. Add it to Task 6 before running this wrapper.

- [ ] **Step 4: Smoke test**: `bash -n scripts/runs/run_teacher_docker.sh` (syntax) and `docker run --rm oec-teacher:dev python -c "import sys; print(sys.version)"`. Expected: the Python version recorded in Step 1.

- [ ] **Step 5: Commit**

```bash
git add docker/teacher/Dockerfile scripts/runs/run_teacher_docker.sh
git commit -m "build: add teacher image and wrapper for the local Haiku continuation"
```

---

### Task 8: Container parity check

**Files:**
- Create: `scripts/runs/check_container_parity.py`
- Test: `scripts/tests/test_check_container_parity.py`

**Interfaces:**
- Produces: `def compare_tool_results(snapshot_events: list[dict], container_events: list[dict]) -> list[str]` returns a list of mismatch descriptions (empty means parity). Reuses `tool_results` from `runs/replay_llm.py`.
- CLI: `check_container_parity.py <snapshot_dir>...` reads each `session_events.json`, runs the replay in the container through the wrapper, and prints PASS or the mismatch list.

- [ ] **Step 1: Write the failing test** for `compare_tool_results` with equal and planted-different events, expecting `[]` and one mismatch string.

- [ ] **Step 2: Run to verify failure**: `cd scripts && python -m unittest tests.test_check_container_parity -v`.

- [ ] **Step 3: Implement** `compare_tool_results` as a thin wrapper over `tool_results`, reporting index, tool name, and both values. The CLI is a loop over snapshot directories that calls the wrapper from Task 7 with `--dry-run-parity` flag if needed; keep the CLI minimal and document it in the docstring.

- [ ] **Step 4: Run tests**: expected OK.

- [ ] **Step 5: Commit**

```bash
git add scripts/runs/check_container_parity.py scripts/tests/test_check_container_parity.py
git commit -m "feat: add container parity check over recorded tool results"
```

---

### Task 9: Kaggle student kernel (run A)

**Files:**
- Create: `data/transient/kaggle-oec-student/gen_student.py` (derived from `data/transient/kaggle-oec-gen/gen_dry.py`)
- Create: `data/transient/kaggle-oec-student/kernel-metadata.json`

**Interfaces:**
- Produces: kernel output `snapshot/` directory with the five snapshot files, and an `accelerator.txt` written from the kernel environment.

- [ ] **Step 1: Copy and reduce `gen_dry.py`.** Keep the secret check removed (run A has no key); keep the vLLM start on 127.0.0.1:8002 and the wait for it; call `run_oec.py --stage student --run-prefix oec-student --attempts 1 --workers 1 --switch-low 3 --switch-high 6 --task-ids <ids> --sandbox subprocess`. Write `accelerator.txt` by reading the GPU name with `nvidia-smi --query-gpu=name --format=csv,noheader` and copy the snapshot directory to `/kaggle/working/snapshot`.

- [ ] **Step 2: Metadata.** Copy `kernel-metadata.json` from `kaggle-oec-gen`, change `id` to `akshayggupta1/e1-oec-student`, `title`, `code_file` to `gen_student.py`, keep `enable_internet: "false"`, `competition_sources`, `machine_shape: "NvidiaL4"`, `is_private: "true"`.

- [ ] **Step 3: Gate before push.** Stop and ask the user for the push go-ahead. A kernel push is outward-facing. Do not push without the go-ahead.

- [ ] **Step 4: After go-ahead, push and verify accelerator.** `uvx --from kaggle==2.2.4 kaggle kernels push -p data/transient/kaggle-oec-student --accelerator NvidiaL4`, then `kaggle kernels status akshayggupta1/e1-oec-student`, and check the accelerator line on the kernel page. Expected: L4. If T4, record it in the ledger and stop; the data is labelled dry-run only.

- [ ] **Step 5: Commit the kernel script** (not the output). `git add data/transient/kaggle-oec-student`. Check first that `data/transient/` is not gitignored for this path; if it is, leave it uncommitted and say so.

---

### Task 10: Two-task dry run, end to end

**Files:**
- No new files. Output under `logs/` as the existing pipeline does.

**Interfaces:**
- Consumes: Tasks 2-9 outputs.

- [ ] **Step 1: Run A on Kaggle.** Push per Task 9 (after go-ahead), download the output to `/tmp/oec-student-<run>/`, and check it has the five snapshot files and an L4 accelerator record.

- [ ] **Step 2: Run B locally.** Copy `snapshot/` into `logs/oec-student-run/<task_id>/snapshot/`, then `bash scripts/runs/run_teacher_docker.sh <snapshot> oec-teacher-run <task_id>` with `ANTHROPIC_API_KEY` set in the shell environment only. Cost gate: Haiku calls for 2 tasks. Confirm with the user before running.

- [ ] **Step 3: Exporters.** Run `export_oec`, `build_oec_dataset`, `merge_datasets`, `audit_parity` on the output, as the existing README describes. Use the README's `--out` form so the clean v6 dataset is not overwritten.

- [ ] **Step 4: Parity check.** Run `check_container_parity.py` on the snapshot. Expected: PASS for both tasks.

- [ ] **Step 5: Report.** Record in the ledger: accelerator on the kernel page, replay verification result, Haiku call count and cost, audit result. Do not start the full run.

---

### Task 11: Full generation (gated)

Not executed in this plan. Starts only after the user approves the Haiku cost from Task 10's counts. The plan for the full run (attempts, switch range, task list) is written after the dry run, using the cost and yield numbers from it. Task 10 of `2026-10-23-e1-oec-dataset.md` (dataset upload, training, evaluation) follows the same gate.

# E1 OEC two-run: swegemma session and stop spike (Task 1)

Read-only spike. No source files were modified.

Package root (the brief's `python3.13` path does not exist here; the venv is 3.12):
`/Users/akshay/kaggle/kaggle.gemma.coding.agent/.venv/lib/python3.12/site-packages/swegemma`
ADK: `google-adk 1.36.1`. Harness helper: `adk_eval_core 0.1.0`.

Paths below are relative to the package root unless marked otherwise.

## Corrections to the brief and the two-run plan

1. **`HandoffStop` does not reach `task_pipeline.run_one`.** The brief (Step 2) and plan line 486 assume it propagates out of `Evaluator.evaluate_task`. It does not. `harness/agent_runner.py:799-801` catches every `Exception` in `run_agent_sandbox` and turns it into `agent_error = 'Sandbox execution error: ...'`. `evaluate.py:324-351` then returns a normal `TaskResult` with `error` set. The `except Exception` at `task_pipeline.py:551` is never reached for this case.
2. **The workspace is gone when a `HandoffStop` is swallowed.** `HandoffStop` skips the fallback diff block at `agent_runner.py:758-795` (it sits after the `except` at 741 and only runs for `TimeoutError`/`LlmCallsLimitExceededError`). It then reaches the `finally` at `agent_runner.py:802-810`, which calls `sandbox_stop`. For the subprocess backend that is `shutil.rmtree(entry['root'])` (`sandbox/subprocess.py:509-516`). For docker it is `stop()` and remove (`sandbox/docker.py:771`). Writing `snapshot/` after `evaluate_task` returns is therefore too late.
3. **There is no `on_switch` hook in `scripts/runs/handoff_llm.py`.** `HandoffLlm` (lines 35-80) has only `student`, `teacher`, `switch_after` and `_route`. The hook has to be added in Task 3. The plan's `stop_at_switch`/`on_switch` fields are a proposal, not existing code.

## Answer 1: how to obtain the ADK session events

**What exists.** `run_agent_sandbox` creates the session service and session locally, and nothing returns them:
- `harness/agent_runner.py:391` `session_service = InMemorySessionService()`
- `harness/agent_runner.py:398-402` `create_session(app_name='swegemma_eval', user_id='eval_user', state=...)`
- `harness/agent_runner.py:413-414` `App(...)` and `Runner(app=app, session_service=session_service)`
- `harness/agent_runner.py:521-526` `runner.run_async(user_id='eval_user', session_id=session.id, new_message=..., run_config=...)`
- `harness/agent_runner.py:596-600` `session_service.get_session(...)`, used only to count LLM calls.

The `Session` and `session_service` are locals of `run_agent_sandbox`. They are not stored on `Evaluator`, `EvalConfig`, `TaskResult`, or `SwegemmaContext`. After the function returns, the session is unreachable except through a copy you record yourself.

**Options checked:**
- **(a) Inside the model via `llm_request`: no.** `BaseLlm.generate_content_async(self, llm_request, stream)` (`google/adk/models/base_llm.py:50-52`) receives only the request. `LlmRequest` has `contents` (a derived, already-preprocessed view of the events) and no session or invocation context. `grep session` on `base_llm.py` and `llm_request.py` returns nothing. You can dump `llm_request.contents`, but that is not the raw session event list.
- **(b) Runner session service: not reachable from outside.** See above. Reaching it would mean monkeypatching `InMemorySessionService`. Not recommended.
- **(c) Recorded from the event stream: this works.** Every raw ADK event passes through `context.handle_adk_event(event, agent_name=...)`:
  - `harness/agent_runner.py:528` on each event yielded by `runner.run_async`. These events are already persisted, because `google/adk/runners.py:850-884` appends them before yielding.
  - `harness/agent_runner.py:378-382` `_on_event`, called from `EventDisplayPlugin` via `adk_eval_core/plugins/display_plugin.py:84-98`. This fires earlier, before persistence, for the same events.
  - `SwegemmaContext.handle_adk_event` (`context.py:210`) deduplicates by `(event.id, partial)` (`context.py:215-221`). It stores only counts and previews, not raw events.
  - `SwegemmaContext` is caller-supplied. `Evaluator.evaluate_task(..., context=...)` (`evaluate.py:221-229`) uses it as given (`evaluate.py:241-242`) and passes it to `run_agent_sandbox` (`evaluate.py:194-219`, `305-322`).

**Answer 1 result.** The caller can read the events live through a `SwegemmaContext` it constructs and passes in. The runner session service is not reachable.

## Answer 2: stopping from inside `generate_content_async`

**Call path (ADK):**
- `google/adk/flows/llm_flows/base_llm_flow.py:1290-1292` `increment_llm_call_count()` then `llm.generate_content_async(llm_request, ...)`.
- `base_llm_flow.py:319-418` `_run_and_handle_error` wraps the generator. An exception goes to `except Exception as model_error` (line 398), then the `on_model_error` plugin callbacks (lines 404-410). If none returns a response, it is re-raised (line 418).
- `google/adk/runners.py:482` `run_async` and `:521-600` `_run_with_trace` have no `try`/`except` on the normal path, so exceptions propagate to the caller of `runner.run_async`.

**Retry plugin:** `ModelRetryPlugin` (`adk_eval_core/plugins/retry_plugin.py`) is in `App.plugins` (`agent_runner.py:406`). `on_model_error_callback` (line 119) returns `None` for non-retryable errors, so the original error is re-raised. `_is_retryable_error` (line 54) returns `False` for any `EvalCoreError` (`adk_eval_core/errors.py:6`). Otherwise it matches status codes `429|500|502|503|504` (line 23) and phrases such as `rate limit`, `overloaded`, `quota exceeded`. A stop exception must not match these. Subclassing `EvalCoreError` or avoiding those words is enough.

**Where it lands in `run_agent_sandbox`:**
- `agent_runner.py:741-753` `except (TimeoutError, LlmCallsLimitExceededError)` sets `agent_error` and lets the function continue.
- `agent_runner.py:758-795` fallback: `git add -N .` and `git diff` inside the still-running sandbox. This captures the working-tree diff as `agent_patch`.
- `agent_runner.py:799-801` any other exception becomes `Sandbox execution error`. The fallback diff is skipped.
- `agent_runner.py:802-810` `finally` stops the sandbox.

**Answer 2 result.** A plain exception ends the episode, but the workspace is destroyed and the `snapshot/` cannot be taken afterwards. Raising `LlmCallsLimitExceededError`, or a subclass of it, ends the episode cleanly. The fallback diff runs while the sandbox is alive, and `evaluate_task` returns normally. `LlmCallsLimitExceededError` is defined at `google/adk/agents/invocation_context.py:48`.

Trade-off: `agent_error` is then labelled `Agent exceeded turns budget` (`agent_runner.py:748-752`). Record the stop reason in the caller's flag, not from the label. Because `agent_patch` is non-empty, `evaluate.py:372` will run `verify_task` (the test suite). Skip that in run A if it costs too much.

## Answer 3: reading live workspace and events after a replayed run

- **Workspace while running:** `agent_runner.py:256` `context.container_id = sandbox_id`, and `context.docker` is the sandbox manager (`context.py:66`, `context.py:574-588` `exec` on the container). A `on_switch` closure holding the `SwegemmaContext` can run `ctx.docker.exec(ctx.container_id, 'cd /workspace && ...')` before the stop. This is the only way to read the workspace from outside the runner.
- **Workspace after the run:** gone. `sandbox_stop` removes it in both backends (see correction 2).
- **Events after a replayed run:** the `InMemorySessionService` is local and discarded. `TaskResult.trace` is a `SessionTrace` of derived `TraceEntry` objects (`adk_eval_core/tracing/trace.py:81-122`), not raw `Event`s. It is saved by `harness/verification.py:40-52` `save_trace_artifact`, which the error path calls at `evaluate.py:324-327`. The recorded `SwegemmaContext` events from Answer 1 are the only raw-event source.

## Recommendation for Task 3

**Obtain `session_events` from a recording `SwegemmaContext`, not from the model's `llm_request` and not from the session service.**

1. In `scripts/runs/task_pipeline.py` (`run_one`), construct a subclass of `SwegemmaContext` (for example `RecordingContext`). Override `handle_adk_event` to append each event to a list, skipping `partial` events, and then call `super()`. Record before `super()`, because the base class deduplicates and early-returns.
2. In `run_one`, build `evaluator = Evaluator(config)` first, then construct the context with `docker_manager=evaluator.docker` (the same manager `evaluate_task` uses, `evaluate.py:79`), plus `problem_statement`, `task`, `repo`, `graph_dir`, `embeddings_dir`, `budget`, `harness`, mirroring `evaluate.py:244-256`. Pass the same object to `HandoffLlm` (as a plain attribute, not a pydantic field) and to `evaluator.evaluate_task(..., context=ctx)`. The current call at `task_pipeline.py:448` passes no `context`.
3. Implement `on_switch` in `HandoffLlm` (Task 3) as a callable supplied by the caller. Inside it, read `list(ctx.recorded_events)` for the session events, and optionally `ctx.docker.exec(ctx.container_id, ...)` for the workspace. The sandbox is still alive at that point because `on_switch` runs before the stop.
4. Raise a stop exception that is a subclass of `LlmCallsLimitExceededError`, not a plain `Exception`, so `agent_runner.py:741` catches it and the fallback diff runs before `sandbox_stop`. Set a flag in the caller's closure to distinguish it from a real turn-budget stop. Make its message free of the retry-plugin phrases (`rate limit`, `overloaded`, status codes).
5. Drop the `except HandoffStop` around `evaluate_task` in the plan (line 486). The stop arrives as a normal `TaskResult` with `agent_error` set. The `run()` handling at plan line 487 should check the flag instead.

Do not dump events inside `HandoffLlm` through `llm_request` alone. It carries derived contents, not the session.

## Concerns

- The plan's `HandoffStop` design (plain `Exception`, caught around `evaluate_task`) is incompatible with `run_agent_sandbox`. Revise Task 2 and Task 3 before implementing.
- `on_switch` is not implemented in the repo yet. The brief's "on_switch hook" refers to a proposal.
- Stopping through `LlmCallsLimitExceededError` runs `verify_task` on the captured diff, which costs test time.
- The per-task `run()` `except` at `task_pipeline.py:551` is only a last resort, because the swegemma catch-all turns every exception into `TaskResult.error`.
- The installed swegemma is 3.12 (`lib/python3.12`), not 3.13 as the brief's Step 1 says.

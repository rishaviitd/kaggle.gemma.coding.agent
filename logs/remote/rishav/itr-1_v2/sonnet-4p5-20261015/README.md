# Claude Sonnet 4.5 — 20 validation tasks

Latest selected outcome per task: **10 passed, 10 failed**.

Each task is under `val/<task_id>/`, containing:

- `agent_trace.json`: conversation and tool activity.
- `bedrock_exchanges.json`: native requests, responses and token usage.
- `model_trace.json`: structured model trace.
- `traces/trace_<task_id>.json`: ATIF trace.
- `results/<task_id>.json`: evaluation result and test output.
- `results/<task_id>.patch`: submitted patch.
- `logs/<task_id>.log`: task log.

`results-summary.json` lists the 20 selected task outcomes and their individual usage estimates. Original trace contents are preserved byte for byte. This folder contains the latest outcome per task; its per-task costs do not represent total experiment spending across all attempts.

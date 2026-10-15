# Claude Sonnet 5.5 high — 20 validation tasks

**14 passed,5 failed grading,1 blocked during evaluation (14/20 resolved,70%).** All20 tasks were attempted; no batch failure cutoff. Model:global.anthropic.claude-sonnet-5-5. Original Rishav itr-1_v2 prompts/tools and offline Docker environment;330-second active session,40 counted tool calls,80 model turns,240-second command/grading timeout,8192 output tokens per model request. Adaptive thinking with summarized display and explicit high effort. Setup/preflight/finalgrading outside the active agent timer. Idle-sleep prevention was enabled.

Estimated inference cost for these20 completed attempts: **$3.792456**, including compaction, at base $2/M input and $10/M output. This estimate does not adjust for cache discounts; it excludes preflight and the interrupted initial start and is not an AWS invoice. The initial start was preserved separately before correcting LiteLLM parameter dropping; the completed run forwards high effort with drop_params=False.

Opus5.5 resolved16/20 in its separate run, using defaultmedium effort; Sonnet5.5 here used high effort. The per-task limits match, but reasoning effort differs.

[Verified Langfuse export](https://us.cloud.langfuse.com/project/cmumoafg802zyad0chva4vpwb/traces/9ca7f81885192005ccd527659bca5e57). Receipt, effective config, task IDs and full task traces are included.

| Task | Outcome | Failure evidence |
| --- | --- | --- |
| fastapi_12942 | Passed | — |
| fastapi_14372 | Passed | — |
| fastapi_14794 | Passed | — |
| fastapi_5624 | Passed | — |
| fastapi_14077 | Passed | — |
| fastapi_14266 | Passed | — |
| fastapi_14246 | Failed | OpenAPI schema regression:1 failed,14 passed, plus snapshot teardown error. |
| fastapi_14482 | Passed | — |
| fastapi_14419 | Passed | — |
| fastapi_14306 | Failed | Validation-error endpoint/path context regressions:4 failed,3 passed. |
| rich_3043 | Failed | HTML export/save regressions:3 failed,93 passed. |
| rich_3905 | Passed | — |
| rich_4076 | Failed | ANSI decoding test_decode:1 failed,23 passed. |
| rich_3506 | Passed | — |
| rich_3935 | Failed | Column rendering/table padding:2 failed,20 passed. |
| rich_3052 | Passed | — |
| rich_3180 | Passed | — |
| requests_7205 | Passed | — |
| requests_7427 | Passed | — |
| httpx_3672 | Evaluation blocked | Evaluation blocked: test patch could not apply to tests/test_parsers.py; tests did not run. |

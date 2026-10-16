# GPT-6 Luna — 20 validation tasks

**12 passed, 8 failed grading (12/20 resolved).** Original Rishav itr-1_v2 prompts/tools and offline Docker environment, 330-second session, 40 counted tool calls, 80 model turns, 240-second command/grading timeout, 8192 output tokens per request. Reasoning effort high; detailed reasoning summaries requested.

Total tokens including compaction: 3,411,409 input (2,970,608 cached), 155,348 output (of which 120,459 reasoning). No cost estimate is given because GPT-6 pricing was not supplied.

Run locally with locally rebuilt wheel caches, so results are not strictly like-for-like with other runs. No Langfuse export.

| Task | Outcome | Failure evidence |
| --- | --- | --- |
| fastapi_12942 | Passed | — |
| fastapi_14372 | Passed | — |
| fastapi_14794 | Passed | — |
| fastapi_5624 | Passed | — |
| fastapi_14077 | Passed | — |
| fastapi_14266 | Passed | — |
| fastapi_14246 | Failed | 1 failed, 14 passed, 1 error in 1.30s |
| fastapi_14482 | Failed | 1 failed, 2 passed in 0.77s |
| fastapi_14419 | Passed | — |
| fastapi_14306 | Failed | 7 failed in 0.49s |
| rich_3043 | Passed | — |
| rich_3905 | Passed | — |
| rich_4076 | Failed | 4 failed, 20 passed in 0.11s |
| rich_3506 | Passed | — |
| rich_3935 | Failed | 3 failed, 19 passed in 0.19s |
| rich_3052 | Failed | 1 failed, 6 passed in 0.12s |
| rich_3180 | Failed | 3 failed, 111 passed in 0.26s |
| requests_7205 | Passed | — |
| requests_7427 | Passed | — |
| httpx_3672 | Failed | 5 failed, 19 passed in 0.11s |

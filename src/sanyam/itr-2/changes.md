# Sanyam / iteration 2

**Parent:** `src/rishav/itr-1/`

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| S1 | `prompts/system.md` | Added `edit_file` argument rules and a recovery rule: re-read after one failure, switch to `write_file` or `sed` after two. | Rishav's itr-1 traces still show edit_file loops from malformed arguments (fastapi_14605: 225 consecutive failures; fastapi_13713: 12; rich_3454: 12; rich_3180: 8). |

Tools, sampling and `eval_config.yaml` match the parent so the prompt rule is the only difference.

Test set: loop tasks fastapi_14605, fastapi_13713, fastapi_15763, fastapi_14186, rich_3454, rich_3180. Controls: fastapi_9555, fastapi_9753, rich_3006, requests_6644.

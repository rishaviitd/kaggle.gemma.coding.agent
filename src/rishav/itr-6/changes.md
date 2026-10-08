# Rishav / iteration 6

**Parent:** `src/rishav/itr-3`

`agent.yaml` and `configs/sampling.yaml` are unchanged copies of itr-3. Tool budget is 40 calls. Time budget is 5 minutes.

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| R1 | `prompts/system.md` | Section 1 only. The first tool call is fixed: `write_file` `_check.py` when a caller can trigger the bug, even if a function is also named. One `git grep` only when nothing can be run. No `grep -r`. | The itr-4 run of `fastapi_11355` opened with seven searches. `fastapi_13537` grepped a named function instead of reproducing. |

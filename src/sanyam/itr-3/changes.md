# Sanyam / itr-3

**Parent:** `src/sanyam/itr-2/` (continues the S-numbering from `src/sanyam/itr-2/changes.md`, which ends at S7)

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| S8 | `eval_config.yaml` | `max_tool_calls` raised from 28 to 36. `max_time_minutes` (8), `timeout_seconds` (240) and `max_turns` (80) unchanged. | Gives the check, targeted test and `edit_file` fallback room without cutting exploration. |
| S9 | `prompts/system.md` | Budget section updated to match: "36 tool calls, 8 minutes", "at most 36 tool calls", "Do not start new exploration after your 31st tool call". Last-5-calls reserve and edit-by-call-12 rule unchanged. | The prompt budget must equal `eval_config.yaml`. |

Everything else (S2-S7 from itr-2: root-cause and reuse rules, `edit_file` error fallback, `_check.py` verification) is identical. `diff -r` of itr-2 and itr-3 shows only S8 and S9, plus the `changes.md` files.

## Why this change

- itr-2 adds work per task (writing and running `_check.py`, a targeted test, possible `edit_file` fallback), so 28 calls may be tight.
- Comparing itr-2 and itr-3 isolates the effect of the extra 8 calls from the prompt change.
- The 8-minute cap stays. Observed mean runs are about 3 minutes, so the extra calls should rarely hit the time limit.

## Experiment plan (not yet run)

- Run itr-2 and itr-3 on the same tasks. Include the 11 edit-error tasks from the itr-2 audit and the 27 dev tasks.
- Compare resolve rate, share of runs that ran a check or pytest before submit, calls used, and runs ending on the budget.
- Check wall-clock time: 129 tasks must finish within the 12-hour competition limit.

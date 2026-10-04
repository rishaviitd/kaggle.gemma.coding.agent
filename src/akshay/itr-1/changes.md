# itr-1 changes over the baseline

Baseline: the itr-1 files as first committed on `main` (commit `c187b64`): the organizers' prompt with a 10-call / 1-minute budget.

## Problems (P)

| ID | Problem | Severity |
|---|---|---|
| P1 | Hits clock or call cap without submitting | High |
| P2 | Malformed edit_file loop (rejected calls are free, so no cap stops it) | High |
| P4 | Context-window overflow (16384 output cap on a 32768 context) | High |
| P6 | Forgets findings or repeats work after context compaction | Medium |
| P11 | edit_file match failures (old_string not found, wrong arguments) | Medium |
| P13 | Repeated identical tool calls | Medium |
| P20 | Late first edit: reads the right file, commits too late (commit failure, not find failure) | High |

## Changes (C)

| ID | Change | Type | Targets |
|---|---|---|---|
| C2 | edit_file fallback: python3 replace via run_command on missing-parameter errors; retry once with shorter old_string | prompt | P2, P11 |
| C5 | Output bounding: tail -25 on tests, git diff --stat, head -30 on grep | prompt | P4, P6 |
| C6 | Notes file /tmp/notes.md (CAUSE/TRIED/NEXT) appended inside commands already being run | prompt | P6, P2 |
| C8 | Deadlines tightened for 40 calls: first edit by call 15 / minute 3; checkpoints at 25 and 32 / minutes 6 and 8 | prompt | P1, P20 |
| C9 | Loop rule: same edit or command failed twice -> read notes and change approach | prompt | P13, P2 |

## What was changed in itr-1

| Change | File | Edit |
|---|---|---|
| C8 | `prompts/system.md` | New "Budget and deadlines" section: 40 calls / 10 min; first edit by call 15 or minute 3; a source edit by call 25 or minute 6; stop changing code and submit by call 32 or minute 8. |
| C6 | `prompts/system.md` | Notes bullet under "Working discipline": one line `CAUSE: file:line what \| TRIED: what you changed \| NEXT: what you will do`, appended to `/tmp/notes.md` inside a command already being run (no extra call). |
| C9 | `prompts/system.md` | Loop rule under "Working discipline": after two failures of the same edit or command, run `tail -5 /tmp/notes.md` and change approach. Replaces "Do not repeat the same query or failed command unchanged" in step 1, which now points to the rule. |
| C2 | `prompts/system.md` | `edit_file` errors bullet: on "mandatory input parameters are not present", use a `python3` replace through `run_command`; on "old_string not found", re-read once and retry once with a shorter `old_string`. |
| C5 | `prompts/system.md` | "Keep output small" bullet: `pytest -q -x tests/test_<module>.py 2>&1 \| tail -25`; `git diff --stat` (full diff once, before submitting); `\| head -30` on every `git grep`. |

## Supporting edits (not changes in their own right)

- `eval_config.yaml`: `max_tool_calls` 10 -> 40, `max_time_minutes` 1 -> 10, `timeout_seconds` 60 -> 120, `max_turns` 50 -> 100. C8 assumes a 40-call / 10-minute budget.
- `prompts/system.md`: "do not assume a fixed tool-call limit" replaced by "`get_status` shows the live remaining budget", because the old wording contradicted C8.

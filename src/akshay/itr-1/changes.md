# itr-1 changes over the baseline

Baseline: the itr-1 files as first committed on `main` (commit `c187b64`), i.e. the organizers' prompt with a 10-call / 1-minute budget.
Change IDs (C2, C5, C6, C8, C9) refer to `experiments/PROBLEMS_AND_SOLUTIONS.md`. All changes are prompt or config only. None has been run yet.

| ID | Problem targeted | File | Change |
|---|---|---|---|
| C8 | P1 hits clock or call cap without submitting; P20 late first edit | `prompts/system.md` | New "Budget and deadlines" section: 40 calls / 10 min. First edit by call 15 or minute 3. A source edit by call 25 or minute 6. Stop changing code and submit by call 32 or minute 8. |
| C6 | P6 forgets findings after context compaction; P2 edit loop | `prompts/system.md` | Notes file `/tmp/notes.md` (`CAUSE: file:line | TRIED: ... | NEXT: ...`), appended inside commands already being run so it costs no extra call. |
| C9 | P13 repeated identical tool calls; P2 edit loop | `prompts/system.md` | Loop rule: if the same edit or command failed twice, run `tail -5 /tmp/notes.md` and change approach. It replaces "Do not repeat the same query or failed command unchanged" in step 1, which now points to the rule. |
| C2 | P2 malformed `edit_file` loop; P11 `edit_file` match failures | `prompts/system.md` | On "mandatory input parameters are not present", apply the change with a `python3` replace through `run_command` instead. On "old_string not found", re-read once and retry once with a shorter `old_string`. |
| C5 | P4 context overflow; P6 | `prompts/system.md` | Keep output small: `pytest -q -x tests/test_<module>.py 2>&1 \| tail -25`, `git diff --stat` (full diff once, before submitting), `\| head -30` on every `git grep`. |
| (support) | Needed for C8 to apply | `eval_config.yaml` | `max_tool_calls` 10 -> 40, `max_time_minutes` 1 -> 10, `timeout_seconds` 60 -> 120, `max_turns` 50 -> 100. |
| (support) | Removes a contradiction with C8 | `prompts/system.md` | "do not assume a fixed tool-call limit" replaced by "`get_status` shows the live remaining budget". |

## Not included

C1 (diff-only reviewer), C3 (`max_output_tokens` 16384 -> 8192), C4 (localize skill), C33 (stop searching once the cause is named), and the `edit_file` worked example (C24). The sampling config and the `code_analyzer` sub-agent are unchanged from the baseline.

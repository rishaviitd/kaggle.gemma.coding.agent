# Sanyam / itr-4

**Parent:** `src/sanyam/itr-2/` (continues the S-numbering from `src/sanyam/itr-3/changes.md`, which ends at S9; itr-4 uses itr-3's 36 tool calls but not its prompt text)

No skills and no sub-agents. Every new instruction is in `prompts/system.md`. `agent.yaml` and `configs/sampling.yaml` are identical to itr-2. `eval_config.yaml` has `max_tool_calls` 36 (itr-2: 28) and `max_time_minutes` 5 (itr-2: 8), the same budget as itr-5 and itr-6. The prompt budget text matches: 36 calls, 5 minutes, no new exploration after call 31; the edit-by-call-12 and last-5-calls rules are unchanged from itr-2. About 12% of itr-2 runs took longer than 5 minutes, so compare itr-4 with itr-2 knowing both budgets changed.

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| S10 | `prompts/system.md` (section 2) | "Copy, never invent": quoted strings, exception types and parameter names appear verbatim; if the issue gives no wording, `git grep` the nearest existing message and match its style. | 9 of 24 reviewed itr-2 failures misread or invented strings or behavior. |
| S11 | `prompts/system.md` (section 2) | "Cover every path": after the first edit, list sibling code paths (sync and async twins, other classes, every `docs_src/` variant) and fix each one with the same bug. | 9 of 24 reviewed itr-2 failures were partial or wrong-location fixes. |
| S12 | `prompts/system.md` (section 2) | Short per-repo convention list (fastapi, rich, requests): where code, tests and examples live, exact-output tests in rich, network tests in requests cannot pass. | Replaces the repo-skill idea of itr-5 with inline text. Written from general knowledge of the repos, not checked against every task. |
| S13 | `prompts/system.md` (section 3) | The check now runs as a `python3 - <<'PY'` heredoc through `run_command` instead of `/workspace/_check.py`. It must use the issue's own example and strings, not the agent's reading. If a previously passing test now fails, fix the change. | itr-2's `_check.py` landed in the submitted patch (`submit_patch` runs `git add -N . && git diff HEAD`). Also saves a `write_file` call. 1 of 24 failures had a weak self-check, 2 broke existing behavior. |
| S14 | `prompts/system.md` (section 4) | Before `submit_patch`: `git status --short`, `rm` stray scratch files, never touch `pytest.ini` or `conftest.py`. | Keeps the patch clean. |

## Why this change

- Of 51 unresolved itr-2 tasks, 45 had a non-empty patch, so the failures are wrong or partial fixes, not tooling. The prompt rules target those patterns.
- Comparing itr-2 with itr-4 isolates "more prompt rules" from the sub-agent and skill design in itr-5.

## Experiment plan (not yet run)

- Run on the same 76 train tasks as itr-2. Compare resolve rate (itr-2: 25 of 76), empty patches, calls used, and runs ending on the budget.
- Check for patches that contain stray files, and the share of runs that ran a check before submit.

## Open questions

- Prompt is about 2,000 tokens (estimate), against about 1,550 for itr-2. A longer prompt may dilute the rules the model follows.
- Gemma may skip the heredoc check, or write one that cannot fail.

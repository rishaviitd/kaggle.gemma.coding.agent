# Sanyam / itr-2

**Parent:** `src/rishav/itr-3/` (continues the S-numbering from `src/sanyam/itr-1/changes.md`, which ends at S1)

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| S2 | `prompts/system.md` | Opening line stresses "as few tool calls as possible" and a direct path from problem statement to fix. | Cut exploration; most failed itr-1 runs burned budget reading. |
| S3 | `prompts/system.md` (section 2) | Fix the root cause: `git grep` callers before editing a function and fix the shared function once; reuse existing repo helpers. | itr-1 had wrong or incomplete fixes that patched only the path named in the issue. |
| S4 | `prompts/system.md` (section 2) | "Minimal means touching the fewest places, not skipping behavior the issue specifies. Feature requests get a complete implementation." | Stops over-minimal patches. |
| S5 | `prompts/system.md` (section 2) | `edit_file` error rule. On "mandatory input parameters are not present", stop using `edit_file` and apply the change with one `run_command` `python3` heredoc (`Path.read_text`, assert old text, `replace(old, new, 1)`), then `git diff`. On "old_string not found", re-read once and retry once with a 1-2 line `old_string`. Never send the same failing call twice. Replaces the S1 wording (backslashes via `chr(92)`, `sed -i` fallback) with a simpler rule. | 20 of 53 `edit_file` errors in itr-1 unresolved tasks were the "mandatory" type and often repeated (fastapi_14605, fastapi_14583, fastapi_14964). |
| S6 | `prompts/system.md` (section 3) | Section retitled "Verify With a Check and Targeted Tests". The agent writes `/workspace/_check.py`, which fails before the fix and passes after, asserts the issue's exact exception types and strings, and is run after each source edit. The existing module test file is run as a regression check. Notes on reading results (`status: error`, output in `details.stdout`, ignore the flasgger warning). Old targeted-test rules kept. | Only 11 of 76 itr-1 tasks ran pytest before submit. Blind submits resolved 3 of 23, verified runs 17 of 40. |
| S7 | `prompts/system.md` (section 4, budget) | Section 4 says "Once your check and targeted test pass". Budget text: reserve the last 5 calls for the check, targeted test, a correction and `submit_patch`; no new exploration after call 23. The edit-by-call-12 rule is kept. | Keeps the verify step inside the 28-call budget. |

`agent.yaml`, `configs/sampling.yaml` and `eval_config.yaml` (28 tool calls, 8 minutes, 240 s command timeout) match the parent, so only the prompt differs.

## Why these changes

- itr-1 outcomes (train): rich 6 of 28 resolved, fastapi 13 of 40.
- Verified runs still failed 23 of 40 times, so the check must assert the issue's expected behaviour, not just run.
- Deliberately not added: a `submit_patch` loop guard, temp-file cleanup, and a pre-submit test gate. Submissions are YAML-only, so a harness-side gate isn't possible.
- Scratch files go under `/workspace`, not `/tmp`. The sandbox rejects `write_file` to `/tmp`, and stray files in `/workspace` can end up in the patch.

## edit_file audit of itr-1 unresolved tasks

- 55 unresolved tasks. 28 had at least one `edit_file` error, 53 errors in total: 20 "mandatory input parameters", 4 "old_string not found", and 29 other, a mix of budget-exceeded and unclassified errors.
- No unresolved task had an empty patch caused only by edit errors. Eleven tasks had a patch but lost many calls to edit errors: fastapi_13786, 14482, 14487, 14583, 14605, 15763, rich_3468, 3469, 3486, 3934, 4077.
- Expectation: S5 mainly saves wasted calls on those 11, so it should give better patches, not flip many tasks on its own. Medium confidence.
- Resolved tasks also had 18 such errors, so edit errors do not always mean failure.

## Open questions

- Not yet run. The model may ignore the heredoc rule or write a bad one.
- "Free" `edit_file` failures not counting against the budget comes from the task description and is unverified.
- Prompt is about 1,550 tokens (estimate), against about 1,100 for the parent.

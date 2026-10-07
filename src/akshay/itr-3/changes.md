# itr-3 changes over itr-2

Baseline: `src/akshay/itr-2` as pushed (commit `c7cfbef`). The prompt was rewritten from scratch (8.2 KB -> 2.3 KB, about 370 words), not patched.
Evidence: `src/base/analysis/itr2_rule_compliance.md`, `src/akshay/findings/README.md`, and the failure analysis of `logs/remote/akshay/itr-2/train` below.

## Run settings

| Setting | itr-2 run | itr-3 |
|---|---|---|
| Tool calls | 25 (pipeline default; prompt said 40) | 36 |
| Session time | 10 min | 8 min |
| Command timeout (`timeout_seconds`) | 120 | 240 |
| Max turns | 100 | 80 |
| `max_output_tokens` | 16384 | 8192 |
| Thinking budget | 4096 | 4096 |

Files: `eval_config.yaml`, `configs/sampling.yaml`. Run with `--max-tool-calls 36 --max-minutes 8` (and `--max-output-tokens 8192`); `task_pipeline.py` takes the cap from its own flags, not from `eval_config.yaml`, so the prompt, the config and the flags must say the same number.

## Why: what the itr-2 failures were (76 train tasks, 22 resolved)

Agent patch compared with the gold patch and the test output:

| Group | Tasks |
|---|---|
| Empty patch (no source edit landed) | 15 |
| Gold file(s) edited, tests still fail | 21 |
| Multi-file task, only part of the gold files | 12 |
| Edited a different file | 5 |
| Scripts or tests only | 1 |

Of the 33 that edited the gold file(s), 31 changed the same place as the gold patch, so the location is mostly right and the content is wrong:

| Why the tests failed | Tasks |
|---|---|
| Environment: SSL certificate error in the test run (`requests_6589`, `6629`, `7328`, `7502`, `7505`; fail the same way in base and itr-1) | 5 |
| Patch broke imports or used an undefined name (`fastapi_14186`, `14419`, `14953`, `rich_3930`, `4070`) | 5 |
| Exact message wording tested but not in the issue (`fastapi_14479`: wrote "Query param ... must be of one of", test wants "Query parameter ... must be one of") | 3 |
| Wrong behaviour (logic differs from gold) | 18 |
| No usable test output | 2 |

- Agent patches are smaller than gold: median 5 changed lines vs 10 (11 smaller, 5 larger, 5 equal).
- Before its first edit, 1 of 28 wrong-fix traces ran tests (7 of 22 resolved ones), and 1 read an existing test (2 of 22). Resolved traces made their first source edit at call 10, wrong-fix ones at call 14.
- Empty patches: in 8 sampled traces, 88 reads and 74 greps against 10 test runs and 2 edit attempts in 24-25 calls. Nine of the 15 had opened the gold file.
- `rich_2943`: same one-line change as gold (`style._hash = None`) in the wrong method (`__add__` instead of `clear_meta_and_links`).

## Problems (P) and changes (C)

| ID | Problem | Change | What the prompt now says |
|---|---|---|---|
| P25 | Wrong content, location right (21+12 tasks); exact wording and values live in the existing test | C15 | Before editing, `git grep` the tests for the function or error text and read the closest test; copy exact text, values and signatures from the issue and that test |
| P26 | Patch breaks imports or names and is never run (5 tasks) | C16 | After the last edit, `python3 -c import module` for each changed file plus the targeted test |
| P27 | Exploration eats the budget; 15 empty patches | C17 | Trigger, not a deadline: if you reach your 12th call with no source edit, the next call must be an edit of your best candidate, then test and refine; never read the same range twice |
| P28 | `submit_patch` loops, 200-500 calls (11 traces, 4,293 calls) | C18 | On BudgetExceeded or patch_size 0, stop and reply in one sentence; never submit twice in a row; submit once, then stop |
| P24 | `write_file` rejects /tmp, about 1 wasted call per trace (56 of 67 calls) | C14 | Create /tmp scripts with `run_command` and a heredoc, never `write_file` |
| P29 | Prompt too long (8.2 KB), many rules ignored (notes, deadlines, outline) | C19 | Prompt rewritten to about 370 words; notes, S1-S4 prose, repro red/green wording, and `find.py` removed |

## What was changed in itr-3

| File | Edit |
|---|---|
| `prompts/system.md` | Rewritten: Budget, Find, Before you edit, Edit, Check then submit. Keeps the `edit_file` python3-replace fallback (malformed calls 413 -> 20 in itr-2) and the heredoc form. |
| `skills/file-outline/SKILL.md` | Outline only (no `find.py`); keeps the `args`-only rule. |
| `skills/file-outline/scripts/outline.py` | Unchanged from itr-2. |
| `skills/repo-navigation/SKILL.md` | Step 3 uses `git grep -n` and the outline skill. |
| `eval_config.yaml`, `configs/sampling.yaml` | Run settings above. |
| `tests/test_outline.py` | Unchanged (12 cases). |

## Dropped from the earlier itr-3 draft

- `find.py` (search that labels hits with the enclosing function) and its 10 tests: finding the file was not the problem (gold file opened in 86-88% of traces at call 3). It was never committed and is deleted from this folder; only the design notes in the old `changes.md` draft described it.
- Notes in /tmp/notes.md (written by 25% of itr-2 traces, mostly after the edit), the S1-S4 prose, the red/green repro wording, and the "first edit by call 10/18/24" tiers.

## Checked before running

- Prompt: 2,263 bytes (itr-2: 8,234). Compiles with `adk_submission` (six tools plus both skills); the graph-tool text is gone from the instruction.
- 12 outline tests pass. `eval_config.yaml` and `sampling.yaml` parse with the new values.
- In `swebench-sandbox:latest` (Python 3.13): the python3 replace snippet changes a file; the heredoc repro writes `/tmp/repro.py` and the assertion fails as intended.
- The repro needs the repo on the path when run from `/tmp`: `python3 /tmp/repro.py` gave `ModuleNotFoundError` for a repo module; `PYTHONPATH=. python3 /tmp/repro.py` works. Only 1 of 203 repro runs in itr-2 hit this (installed packages are importable), so the prompt does not mention it.

## Not yet done / risks

- No remote run. Compare with itr-2 on: resolve rate, empty patches (16), first source edit (median 12), traces that read a test before the first edit (6 of 76), import/name breaks (5), `submit_patch` loops (11 traces), rejected `write_file` calls (56), calls to first edit.
- The 5 SSL-failure `requests` tasks cannot pass; the practical ceiling on this split is about 71 of 76.
- The "12th call with no edit" rule is a call-count trigger. Timing rules ("by call 15") were ignored in itr-2 (median first edit stayed at 12-13), so check whether this form moves it. It is kept because the empty-patch group is the largest fixable one.
- Single run on 76 tasks: a 2-task change is noise.

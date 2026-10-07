# itr-4 changes over itr-3

Baseline: `src/akshay/itr-3` (Kaggle run: 17/76). Evidence: `src/akshay/findings/README.md`, section "itr-3 observations". Only `prompts/system.md` changed (2,319 -> 2,826 bytes). Config, skills and tests are the same as itr-3.

## Problems and changes

| ID | Problem (itr-3 run) | Change | Prompt line |
|---|---|---|---|
| P30 | The always-on test step added about 2 reads and 2 greps before the first edit (median first edit call 19 vs 14-16) and pushed the model into test files; 14 traces read a test first (3 in itr-2) | C20: read a test only when the issue names an exact message, value or signature; skip tests otherwise | "Only if the issue names an exact message, value or signature you must match, look at the test for it ... Otherwise skip tests and go to the source." |
| P31 | Patches touching `tests/` made the grader's test patch fail to apply (`rich_3043`, `fastapi_14492`); the rule was five words after the long test block was dropped | C21: explicit ban with the reason, and a rule for a failing test | "Never edit or add files under tests/ (or any test_*.py). If a test disagrees with your fix, fix the source, not the test." |
| P32 | "Submit when it passes, or when the same tests failed before your edit" gave `fastapi_14492` its reason to edit the test | C22: the rule now says a failing test means the source fix is wrong; ignore only a failure that existed before the edit | "Submit when your repro and the targeted test pass. A test that fails because of your change means the source fix is wrong ..." |
| P33 | Nothing removed test edits that had already been made | C23: last step before `submit_patch` reverts tracked test files and deletes new ones under `tests/` | `git checkout -- tests/ 2>/dev/null; git clean -fdq tests/ 2>/dev/null; git diff --stat` |
| P34 | The itr-3 rewrite dropped "go directly from the problem statement to the relevant files" (present in base, itr-1, itr-2) | C24: one sentence restored in the opening line | "Go directly from the problem statement to the relevant source files; do not wander." |
| P37 | Wrong fix after running tests rose from 6 (base) to 14 (itr-3); many patch only the path the issue names or are too thin. Sanyam's itr-2 prompt (`src/sanyam/itr-2`, no results in the repo yet) carries three rules we lacked | C27, borrowed from Sanyam S3, S4 and his anti-pattern list: `git grep` callers and fix the shared function once, reuse existing helpers; "minimal" means fewest places, not skipping specified behaviour; never search outside `/workspace`, and treat ModuleNotFoundError as environment | "Before changing a function, list its callers with `git grep -n 'function_name' -- '*.py' | head -20` and fix the shared function once ..." |
| P36 | The repro rarely fails for the issue's reason. First runs with a result (53): 28 clean, 11 exit 0 with the failure caught and printed by the script, 14 failed. Of the 39 that exited 0, 27 scripts are print-only (no `assert`) and 12 wrap the test in `try/except`; none use `sys.exit(1)` | C26: the script must `assert actual == expected, actual` for the behaviour the issue expects, with no `try/except` around it | "The script must `assert` the behaviour the issue expects (`assert actual == expected, actual`), with no `try/except` around it, so the bug makes it exit non-zero." |
| P35 | Context summaries fire at about 14.3k prompt tokens (118 of 182 summaries) and cost 7.6% of all model turns; the first one lands before the first edit in 37 of 65 tasks | C25: `read_file` always with both `start_line` and `end_line`, at most 80 lines apart (was 120), so the prompt stays under the summary threshold longer | "Read only the lines you need with `read_file`: always pass both `start_line` and `end_line`, at most 80 lines apart." |

Unchanged from itr-3: the 12th-call edit trigger, the heredoc repro (never `write_file` for /tmp), the python3-replace fallback for `edit_file`, the `submit_patch` stop rule.

**Run settings now match Sanyam itr-2** (`eval_config.yaml` and `configs/sampling.yaml` are identical): 28 tool calls (itr-3 had 36), 8 minutes, 240 s command timeout, 80 turns, 8192 output tokens, temperature 0.2. The prompt budget line says 28. At about 9.4 s per call 28 calls fit in 8 minutes. `agent.yaml` differs only in agent name, `adapter` (dropped by the notebook builder) and our two skills.

**First remote run of itr-4: the 14 itr-3 failures that any run has ever solved** (base, akshay, rishav, sanyam itr-1 to itr-3): fastapi_14303, 14349, 14360, 14419, 14485, 14492, 14605, 14851, 5624; requests_7205; rich_3043, 3480, 3777, 4075. `fastapi_14851` passes with an empty patch in earlier runs, so it says little. The other 45 itr-3 failures were never solved by any run and are not in this run.

## Evaluated and rejected: "after a summary, run `git diff --stat; ls /tmp`" (rule C)

Idea: after every context summary, restore the edit state from disk in one command instead of a `/tmp/notes.md` note. Measured on the 76 itr-3 traces (`logs/remote/akshay/itr-3`):

| Measure | Value |
|---|---|
| Summary events | 182 (73 of 76 tasks), 7.6% of all model turns |
| Summaries before any source edit (no diff or repro to restore) | 75 |
| Summaries after an edit (rule could help) | 107 |
| Calls in the 6 after a post-edit summary that were exact repeats or re-reads of an edited file | 45 (0.42 per summary) |
| Calls the rule would spend | 107 (one per post-edit summary) |
| Net calls, if it removed all 45 wasted calls | **-62** (about -0.8 per task) |
| Net calls, if it removed half | about -85 |
| Tasks that gain (saved > spent) / lose | 6 / 50 of 76 |
| Unresolved tasks that hit the cap or timeout, have a patch and 3 or more wasted post-edit calls | 2 of 41 |

Verdict: rejected. The waste it targets is small, the rule costs a call every time, and it does nothing for the pre-edit summaries where most of the re-reading happens (36% re-reads and 27% re-greps in the 6 calls after a pre-edit summary).

The cheaper substitute is C25 (read cap of 80 lines). Read sizes in itr-3: 713 successful reads, median 50 lines, 251 reads (35%) over 80 lines, about 437,900 tokens of read output in total. It costs no call. Of the 251 long reads, 119 asked for no range (the default top 150 lines), 71 asked for a range over 80 lines, and 61 gave only `start_line` and so received 150 lines. The cap in the prompt reaches the 71; the 119 and 61 need the model to pass both `start_line` and `end_line`. Its effect on the summary count is unproven.

## Notes.md: why it was dropped (itr-1 and itr-2 data)

| | itr-1 | itr-2 |
|---|---|---|
| Tasks that wrote a note | 32 of 76 | 20 of 76 |
| Note written as a separate tool call | 15 | 7 |
| First note at call (median) / first edit (note writers) | 16.5 / 13 | 17.5 / 11.5 |
| Note before the first edit / only after | 14 / 17 | 6 / 14 |
| Resolved with notes / without | 10 of 32 / 10 of 44 | 7 of 20 / 15 of 56 |
| Note before the first summary | 11 | 7 |
| Repeated-call rate after a summary, early notes vs none | 17% vs 22% | 10% vs 8.5% |

Notes did not reduce repeated work after a summary and did not raise the resolve rate. The CAUSE line named a gold file in 25 of 32 and 19 of 20 note writers, but only 8 of 25 and 7 of 19 of those resolved. The itr-3 summary text also keeps little: 116 summaries, 3 name a line number, 58% of the files read are still named.

## Checked before running

- Prompt compiles with `adk_submission` (six tools plus both skills); 12 outline tests pass.
- C23 tested on a throwaway git repo: an edited test file is restored, a new `tests/test_new.py` is removed, and the source change stays. `git clean` is limited to `tests/`.
- `submit_patch` diffs with `git add -N .` and `git diff`, so a new untracked test file would have been in the patch without the clean step.

## Result of the first run (Kaggle `akshayggupta1/gemma4-akshay-itr4`, version `v1`, 14 tasks)

Traces: `logs/remote/akshay/itr-4/train/`. About 88 min of tasks plus about 12 min of vLLM start-up. **Resolved 2 of 14**, but `fastapi_14851` passes with an empty patch, so **1 real fix** (`rich_3043`).

How to download: `kaggle kernels output` and `kernels status` return an unrelated cancelled version. Use the SDK with `version_label='v1'` (`ApiListKernelSessionOutputRequest`), which lists all 81 files; the plain CLI returns only 11 and no traces.

### Why 14 tasks are a weak test

The 14 are itr-3 failures that some run ever solved, so itr-3 scores 0 by construction and the other runs are inflated. Four of them (`fastapi_14303`, `14349`, `14485`, `14605`) fail collection in itr-3 and itr-4 with a missing `dirty_equals` or `inline_snapshot` in the grader sandbox, while itr-1 solved all four and itr-2 three of four. They are dropped from the fair comparison.

| Set | base | itr-1 | itr-2 | itr-3 | itr-4 | rishav itr-1 | sanyam itr-1 |
|---|---|---|---|---|---|---|---|
| All 14 | 9 | 7 | 8 | 0 | 2 | 10 | 8 |
| Fair 9 (no blocked, no `14851`) | 6 | 2 | 4 | 0 | **1** | 6 | 5 |
| The 4 blocked | 2 | 4 | 3 | 0 | 0 | 3 | 2 |

Fair 9 = `fastapi_14360`, `14419`, `14492`, `5624`, `requests_7205`, `rich_3043`, `3480`, `3777`, `4075`. itr-4 resolved only `rich_3043` on it. Still a bad result, but the gap to base (6 of 9) is 5 tasks, not 12.

### Score per change

| Change | Target metric | itr-3 | itr-4 | Verdict |
|---|---|---|---|---|
| C20 read a test only if the issue names an exact value | tasks reading or grepping tests before the first edit | 3 of 14 (6 reads) | 1 of 14 (1 read) | **Worked** |
| C21/C22 never edit tests | test file in the final patch | 3 (`fastapi_14492`, `requests_7205`, `rich_3043`) | 1 (`fastapi_14492`) | **Half worked**; `fastapi_14492` edited the test at call 16 |
| C23 revert tests before submit | `git checkout -- tests/` run | 0 | **0 of 9 submitting tasks** | **Not followed**; `git diff --stat` also 0 |
| C24 go directly to source | median first edit call | 11 | 9.5 | Moved the right way, but 8 of 14 tasks never edited |
| C25 80-line reads with both bounds | reads with both bounds; first summary | n/a; call 11 | 102 of 137 (74%); call 16 | **Worked**: first summary 5 calls later; summaries per task 2.3 vs 2.2 |
| C26 assert in repro, no `try/except` | first repro run fails | 14 of 53 | 5 of 9 with a result | Moved, but 5 of 10 repros still have `try/except`; calls on `/tmp` scripts 8% to 11% |
| C27 callers, fewest places, stay in `/workspace` | patch files; commands outside `/workspace` | median 2 files; 0 | median 1 file; 3 | Fewer files; outside-`/workspace` commands went up |
| 28-call cap (Sanyam config) | tasks at 28 or more calls | 6 of 14 | 8 of 14 | **Hurt**: 3 tasks ended at the cap with no patch; `fastapi_14485` hit `BudgetExceeded` on its edit |

### How each of the 14 ended

| Outcome | Tasks |
|---|---|
| Missing module in the grader (environment) | `fastapi_14303`, `14349`, `14485`, `14605` |
| Resolved | `rich_3043`; `fastapi_14851` (empty patch) |
| Never edited source: budget or timeout | `fastapi_14360` (8-min timeout), `fastapi_5624`, `rich_4075` (28 calls) |
| Wrong or incomplete patch | `requests_7205` (1 test still fails), `rich_3777` (new `is_interactive` property has no setter; 96 tests fail), `rich_3480` (patch `list(text._spans)`; its test run timed out at 730 s) |
| Edited a test, grader patch conflict | `fastapi_14492` |
| Edit at call 7, patch empty | `fastapi_14419` (call 7 has no recorded result; no later output shows the new text; 2 upstream tests fail with `2 == 1`) |

### Findings

1. **The run mostly measures the environment and the cap, not the prompt.** 4 of 14 are environment-blocked and 3 more ran out of calls or time without a single source edit. The 7 that reached a wrong or empty patch are the only ones that test the new rules.
2. **The 12th-call rule is not followed.** Edited by call 12: 3 of 14; after: 3; never: 8 (itr-3: 2 of 53 followed it).
3. **Repro scripts crowd out edits.** 41 of 361 calls (11%) write or run a `/tmp` script, up from 5-8%. `fastapi_5624` spent 7 calls on them (5 errors) and never edited; `rich_3480` and `requests_7205` spent 7 and 5.
4. **Error share rose**: 12% of calls returned an error (itr-3 8%). `fastapi_14485` sent `python3 -c "...; def ..."` three times (invalid syntax); `fastapi_14419` sent 2 file paths with backticks in them.
5. **23 of 361 calls (6%) have no recorded result**, 20 of them right before a context summary. Edits fall in this group (`fastapi_14419` call 7, two others), so an edit can be lost without any signal.
6. **`git diff --stat` and the test revert were never run.** The pre-submit line in the prompt is ignored, as the itr-2 "step before submit" rules were.
7. **The cap mattered.** 15 of 49 earlier resolved runs on these tasks used more than 28 calls (median 26).

### What to do next

- Separate the cap from the prompt: run the fair 9 plus the 4 blocked at 36 calls with this prompt. If the fair-9 count stays near 1, the prompt is the problem.
- Candidate removals, in order: C26 (repro crowding), then the C23 line (never followed). Keep C20, C25 and the C21 wording.
- Do not reword the 12th-call rule again; it has been ignored twice.

## Not yet done / risks

- One run on 14 selected tasks. Treat every difference below 3 tasks as noise.
- C20 relies on the model judging "the issue names an exact message". In `fastapi_14492` the model read the test at call 12 and then edited it at call 16.
- The 8-minute limit is unchanged. Two tasks timed out at 8 min (`fastapi_14360` after 23 calls, `rich_3480` after 19 calls with a 730 s test run), so 28 calls at about 20 s per call does not fit on slow tasks.

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

# Akshay: what works and what does not (itr-1, itr-2, itr-3, itr-4)

> **Format rule (always follow):** keep this file short. Only these sections, in this order: Results, Working, Not working, Lessons, Untested. One line per point with a number. No tables of per-tool detail, no long explanations; put detail in `sources.md` or the analysis files.

Short summary of every analysis so far. Train split, 76 tasks, one run per iteration.
Where each number comes from: `sources.md` in this folder.

## Results

| | base | itr-1 | itr-2 | itr-3 (Kaggle) | itr-4 (Kaggle, 14 tasks only) |
|---|---|---|---|---|---|
| Resolved | 20 | 20 | 22 | 17 | 2 of 14 (1 real fix; `fastapi_14851` has an empty patch) |
| Resolved, 9 unblocked tasks of the 14 | 6 | 2 | 4 | 0 | 1 |
| Empty patch | 12 | 18 | 16 | 18 | 6 of 14 |
| Hit the call cap | 19 | 39 | 34 | 27 (of 36) | 8 of 14 (of 28) |
| Median first edit call (62 tasks all runs edited) | 16 | 15 | 14 | 19 | 9.5 (10 of 14 tasks edited; not comparable) |

- The score has not moved beyond noise: 27 tasks were solved by at least one run, only 13 by all three.
- itr-2 gained 6 tasks over base and lost 4.
- itr-3 ran on Kaggle, not the train sandbox: 31 of 76 tasks were never scored fairly (17 missing test package `inline_snapshot`/`dirty_equals`/`rich._unicode_data`, 12 session timeouts, 2 test-patch conflicts). Compare itr-3 to earlier runs only on the rest. On the 60 tasks not blocked: base 17, itr-1 15, itr-2 19, itr-3 17.
- itr-4 ran on Kaggle on 14 tasks only (the itr-3 failures that any run ever solved): 2 resolved, of which `fastapi_14851` passes with an empty patch, so 1 real fix (`rich_3043`).
- Those 14 are a weak test: itr-3 scores 0 by construction, and 4 of them (`fastapi_14303`, `14349`, `14485`, `14605`) fail collection on a missing test package in itr-3 and itr-4. On the other 9: base 6, itr-1 2, itr-2 4, itr-3 0, itr-4 1, rishav itr-1 6, sanyam itr-1 5.
- Failure cases on all 76 tasks (base / itr-1 / itr-2 / itr-3): wrong fix without a pytest run 27 / 18 / 17 / 3; wrong fix after running tests 6 / 11 / 9 / 14; patch broke collection 7 / 6 / 7 / 18; empty patch 9 / 13 / 9 / 5; timeout 2 / 5 / 8 / 14. Of itr-3's 18 collection errors, 14 are a missing package.

## Working

- **Removing tools that returned nothing.** All 85 `search_similar_code` calls in base were empty. After removing the graph tools and the analyzer, their use fell from 40% of traces to 0%.
- **The `edit_file` fallback (python3 replace).** Malformed edit calls fell from 413 (base) to 123 (itr-1) to 20 (itr-2). Worst single trace: 182 to 6.
- **Concrete one-step rules are followed:**
  - repro written before the first edit: 34% (base) to 61% (itr-2);
  - repro files left in /workspace: 43% to 3%;
  - test files edited: 18% to 2%;
  - a test run between the last edit and submit: 92% (itr-2);
  - no bare pytest: 0% in all runs.
- **Ranged reads of big files (itr-2 prompt rule):**
  - first gold-file reads landing mid-file: 5 to 39;
  - reads returning only the top 150 lines: 51 to 16;
  - median lines per gold read: 106 to 41.
- **Finding the right file is not the problem.** It is opened in 86-88% of traces, at a median of call 3.
- **Edit tool fixed.** The python3 replace fallback cut malformed `edit_file` calls from 413 to 20; 63% of calls now succeed (base 19%). `write_file` still fails on /tmp paths (56 of 67 calls).
- **Heredoc repro rule (itr-3):** 64 of 76 tasks made `/tmp/repro.py` with `cat > ... <<'EOF'`; no `write_file` calls at all.
- **Read only a test the issue names (itr-4 C20):** tasks reading or grepping a test before the first edit fell from 3 to 1 of 14.
- **Two-bound reads, 80 lines (itr-4 C25):** 102 of 137 reads (74%) passed both `start_line` and `end_line`; the median first summary moved from call 11 to call 16.
- **Earlier first edit (itr-4):** median call 9.5 against 11 (itr-3), 13 (itr-2) and 13.5 (itr-1) on the same 14 tasks. Tasks not blocked by the environment are too few to say more.

## Not working

- **Wrong fix in the right file** is the biggest failure: 20 of 54 unresolved itr-2 tasks. Next:
  - opened the right file, never edited source (12);
  - multi-file task, only partly edited (11);
  - never opened the right file (9);
  - opened the right file, edited another one (2).
- **The read-to-edit gap stayed at 7 calls** (median) in base, itr-1 and itr-2. The model still re-reads the file it found (2.3 times per trace) and greps it.
- **Timing rules are ignored.** Median first source edit 12-13 in every run; edits by call 15: 43% in itr-2 vs 48% in base.
- **Notes are mostly skipped.** 25% of itr-2 traces wrote one; about two thirds of notes came after the first edit; almost never read back.
- **Prompt rules not followed in itr-3:** the `python3 -c import` check (12 of 76), `get_status` (1), the outline skill (2), `grep ... | head` (266 of 650 commands). Followed: heredoc repro (64), no `write_file`, one `submit_patch`, no loops.
- **The outline skill is barely used.** 11 of 76 traces; 3 of 70 before reading a big file; 2 of 17 calls rejected (wrong arguments).
- **`submit_patch` loops.** The model keeps calling it after it should stop: 4 traces in itr-1, 11 in itr-2, 200-500 calls each, until the 10-minute timeout. The harness only ends the run on a text reply. 9 of the 11 itr-2 loops started after a budget-exhausted error.
- **Budget mismatch.** Runs used a 25-call cap (`task_pipeline.py --max-tool-calls` default); the itr-1 and itr-2 prompts said 40.
- **Reading the existing test first (itr-3) delays the edit and invites test edits.** Traces reading a test before the first edit: 3 in itr-2, 14 in itr-3. Average calls before the first edit (62 shared tasks): reads 5.4-5.8 to 7.7, greps 4.6-4.8 to 6.4. Median first edit: 14-16 in base, itr-1, itr-2; 19 in itr-3.
- **Test-file edits break scoring.** The grader's test patch fails to apply. Patches touching tests: base 8, itr-1 3, itr-2 2, itr-3 4. `rich_3043` (solved in itr-2) and `requests_7205` edited the tests they had just read; `fastapi_14492` used "submit when it passes" to justify it.
- **Late or no edit in itr-3:** 7 tasks used 30+ calls and never edited (itr-2: 1; base, itr-1: 0). Seconds per call 9.5 vs 6.7-7.3, so 8 minutes buys about 24 calls.
- **Lost vs earlier runs (12 tasks):** 5 missing test package, 2 test files edited, 1 timeout, 1 out of calls, 3 wrong fix. Only `rich_3043` is a clean prompt regression.
- **About half of the repros show no bug on the first run (itr-3):** of 53 first runs with a result, 28 clean, 11 exit 0 after the script caught and printed the failure, 14 failed. 27 of the 39 exit-0 scripts have no `assert`, 12 wrap the test in `try/except`, none use `sys.exit(1)`. A failing first run did not predict a fix (3 of 13 vs 8 of 28).
- **The assert rule (itr-4 C26) moved the number but cost calls:** 5 of 9 first runs failed, yet repro scripts took 11% of calls (5-8% before) and 5 of 10 still used `try/except`. `fastapi_5624` spent 7 calls on them and never edited.
- **The 12th-call edit trigger is ignored:** itr-3, 2 of 53 tasks edited by call 13; itr-4, 3 of 14 edited by call 12, 8 of 14 never edited. Edited by call 12 in itr-3: 8 of 23 resolved; call 13-20: 2 of 12; call 21+: 7 of 31.
- **Context summaries rebuild context (itr-3):** 182 summaries (7.6% of turns), 118 from prompt size above 14.3k tokens, 75 before any edit. After a summary 113 calls were exact repeats and 47 re-reads. Only 3 of 116 summaries name a line number.
- **`notes.md` did not help (itr-1/itr-2):** resolved 10/32 and 7/20 with a note vs 10/44 and 15/56 without; first note at call 16-18, mostly after the first edit.
- **Rejected: "after a summary run `git diff --stat; ls /tmp`".** At most 45 wasted calls to save against 107 calls spent (net about -62); 6 tasks gain, 50 lose.
- **The pre-submit test revert (itr-4 C23) was never run:** `git checkout -- tests/` and `git diff --stat` ran in 0 of 9 submitting tasks. `fastapi_14492` still edited a test file at call 16.
- **The 28-call cap hurt (itr-4):** 8 of 14 tasks used 28 or more calls; 3 ended at the cap with no patch; `fastapi_14485` hit `BudgetExceeded` on its edit. 15 of 49 earlier resolved runs on these tasks used more than 28 calls.
- **Errors and lost edits (itr-4):** 12% of calls returned an error (itr-3 8%); 23 of 361 calls (6%) have no recorded result, 20 of them before a summary. `fastapi_14419` edited at call 7 and ended with an empty patch.
- **Of the 14 itr-4 tasks:** 4 blocked by a missing test package, 3 never edited (`fastapi_14360` timed out, `fastapi_5624` and `rich_4075` ran out of calls), 3 wrong patch (`requests_7205`, `rich_3777`, `rich_3480`), 1 test-patch conflict, 1 empty patch after an edit, 2 resolved.

## Lessons

1. Mechanical fixes work (remove a tool, give a fallback, one concrete command). Timing promises and extra steps do not.
2. The model finds the right file early. The losses are in deciding the fix and committing to it.
3. Keep the prompt, the eval config and the pipeline cap at the same number.
4. A 2-task change on one 76-task run is noise. Judge each change by the metric it targets.
5. Deadline lines did not matter: base (none) edits at call 16, itr-1/itr-2 (with deadlines) at 15 and 14. Itr-3 is late (19) because of the test-first step, not the missing deadlines.
6. Itr-3 also dropped "go directly from the problem statement to the relevant files" and the long test-file block ("do not modify test files", "ignore failing existing tests").
7. The itr-3 and itr-4 results mostly measure the environment: tasks that fail collection on a missing test package (`dirty_equals`, `inline_snapshot`) cannot be fixed by any prompt. Compare runs only on tasks that are not blocked.
8. A run on a hand-picked set of tasks (here the itr-3 failures that someone had solved) is biased in both directions. Read it per change, on the metric the change targets, not as a score.
9. Rules that add a step before the first edit (test-first, repro with assert, callers `git grep`) are paid for in calls. Check the calls-before-first-edit number after every such rule.
10. Rules about the last step before submitting (test revert, import check, `git diff --stat`) are not followed; do not rely on them.
11. Tool traces can miss results (6% of calls in itr-4), so judge an edit by the final patch, not by the trace.

## Untested (itr-4 ran on 14 tasks; these are open)

- Does the prompt or the 28-call cap explain the itr-4 result? Run the 9 unblocked tasks plus the 4 blocked ones at 36 calls with the same prompt. If the 9 stay near 1 resolved, the prompt is the problem.
- Drop the repro-assert rule (C26) and the pre-submit revert line (C23), keep C20, C21 and C25, and compare on the same 9.
- Compare only tasks not blocked by a missing module, a timeout, or the empty-patch pass `fastapi_14851`.
- Three Sanyam itr-2 rules are in itr-4 (callers and shared function, the "minimal" definition, never search outside /workspace). His result is not in the repo, so they cannot be credited yet. Judge them by "wrong fix after running tests" (14 in itr-3) and by commands outside /workspace (3 in itr-4).

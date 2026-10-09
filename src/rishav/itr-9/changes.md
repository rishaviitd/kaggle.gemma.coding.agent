# Rishav / iteration 9 — verification (train “12” bucket)

**Parent:** `src/rishav/itr-1/`

**Target failures (itr-1 train, n=12):** submitted a **library** patch but harness tests still failed — wrong or incomplete fix after submit.

| ID | File | Change |
| --- | --- | --- |
| V1 | `prompts/system.md` | Require one **issue-specific check** (script or targeted pytest). |
| V2 | `prompts/system.md` | Check should **fail before** fix / **pass after**; re-run after each library edit. |
| V3 | `prompts/system.md` | **Do not `submit_patch`** while that check still fails. |
| V4 | `prompts/system.md` | Core objective: no submit without evidence the reported bug is fixed. |

**Not in this iteration:** library-only `git diff --stat` / mandatory submit workflow (see `itr-8`).

**Eval:** Re-run 30-task train; compare resolve count vs itr-1 on the twelve lib-patch failures and overall.

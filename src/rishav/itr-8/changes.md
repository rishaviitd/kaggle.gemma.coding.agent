# Rishav / iteration 8 — workflow (train “7” bucket)

**Parent:** `src/rishav/itr-1/`

**Target failures (itr-1 train, n=7):** repro-only or missing submit — e.g. `fastapi_11355`, `fastapi_14430`, `fastapi_14512`, `fastapi_14605`, `fastapi_14609`, `rich_3469`, `rich_3782`.

| ID | File | Change |
| --- | --- | --- |
| W1 | `prompts/system.md` | Graded fix = **library source**; repro scripts are not the fix. |
| W2 | `prompts/system.md` | Pre-submit: `rm` repro helpers + `git diff --stat` must show package paths. |
| W3 | `prompts/system.md` | Must call `submit_patch` when library fix is ready; anti-pattern for repro-only diff. |
| W4 | `prompts/system.md` | Dropped “8–10 turns” cap; note harness may grade uncommitted edits if submit omitted. |

**Not in this iteration:** fail-then-pass verification gate (see `itr-9`).

**Eval:** Re-run 30-task train; compare resolve count vs itr-1 on the seven task IDs above.

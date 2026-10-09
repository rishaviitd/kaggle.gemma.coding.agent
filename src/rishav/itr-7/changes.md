# Rishav / iteration 7 (itr-1 + trace-backed prompt deltas)

**Parent:** `src/rishav/itr-1/` (same `agent.yaml`, sampling, eval_config)

**Train baseline (30 tasks, agent_trace.json):** itr-1 **11/30** best single run; itr-2–6 **9/30** each; union **12/30**.

## Hypotheses → prompt change (data from `logs/remote/rishav/itr-*/train/*/agent_trace.json` + `results/*.patch`)

| ID | Hypothesis | Evidence | Prompt delta |
| --- | --- | --- | --- |
| H1 | **Do not** adopt itr-4’s forced `_check.py` first call or “stop exploring after call 20” | itr-4 median submit turn **18** vs itr-1 **32**; itr-1-only wins **`rich_3480`**, **`rich_3278`**: itr-4 submitted at turns **33** / **12** with **1** `edit_file` each and **520B** / **321B** patches (fail); itr-1 won with **12** edits, submit **@38** / **@32**, **1281B** / **319B** | Keep itr-1-style grep/read discovery; **no** mandatory first-call repro; budget rule only blocks *new* repro/search after call 20 if **zero** lib edits |
| H2 | **Regex / escape edits via `edit_file` spiral** on failures | **`rich_3278`**: itr-2 **43** `edit_file`, no submit, repro-only patch; itr-6 **64** edits, no submit; itr-1 **12** edits, resolved. Unresolved traces with edit pain: itr-3 **10/21**, itr-6 **8/21** vs itr-1 **5/21** | Python heredoc path after **2** failed `edit_file` or when escapes present (itr-4 §2, **without** itr-4’s full workflow) |
| H3 | **Repro-only patches** (no library edit) | **`fastapi_14430`**: itr-1 **0** edits, **5×** `grep -r`, **4662B** patch = only `repro*.py`; itr-2 **1** edit `fastapi/_compat/v2.py` **710B**, resolved. itr-1 failures: **4** submitted patches with no lib files | Require lib path in `git diff --stat`; repro scripts must not be the only diff |
| H4 | **Test-file edits** hurt scoring | **`fastapi_14791`**: itr-1 patch **483B** utils only (pass); itr-2 patch **4704B** includes **6** `tests/` files (fail) | Strengthen NEVER touch `tests/` |
| H5 | **`grep -r` exploration tax** | **`fastapi_14430`** itr-1: turns 1–2+ use `grep -r`; **7/19** itr-1 failures had **≥3** `grep -r` in trace | Prefer `git grep`; ban `grep -r` |
| H6 | **Late submit with complete fix** beats early wrong submit | **`rich_3480`**: itr-3/5 fail with submit **@26** and smaller patches (**491–900B**); itr-1 submit **@38** wins | Explicit “do not submit while verification still fails”; submit by call **38** |

## Explicitly **not** imported from itr-4–6

- Fixed first tool call (`_check.py` / no grep before repro)
- “After 20th tool call, no new exploration” (itr-4) — replaced with narrower rule in H1
- `load_skill` / how-to-grep (itr-2 system on 14430 win — kept itr-1 agent.yaml without skill churn for this iteration)

## Expected validation

Re-run 30-task train: target **≥12/30** (union ceiling) by recovering **`fastapi_14430`** without losing **`rich_3480`** / **`rich_3278`**.

# Sanyam / iteration 2

**Parent:** `src/rishav/itr-1/`

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| S1 | `prompts/system.md` | Added `edit_file` argument rules and a recovery rule: after the first `edit_file` failure, stop using it and make the change with a short `python3 - <<'EOF'` heredoc doing `s.replace(old, new, 1)`. Backslashes are built with `chr(92)` and backticks are avoided in the replacement. If the heredoc fails, use `sed -i` for single-line changes. | Rishav's itr-1 traces still show edit_file loops from malformed arguments (fastapi_14605: 225 consecutive failures; fastapi_13713: 12; rich_3454: 12; rich_3180: 8). |

Tools, sampling and `eval_config.yaml` match the parent so the prompt rule is the only difference.

## Why this change

- The failure is a tool-call serialisation problem. The model writes `` `,old_string: `` inside the `new_string` value, so the harness reports `old_string` as missing. The model then often resends the identical call for 100+ turns.
- Backticks, a trailing backtick-comma and heavy escaping (`\\\"`, `\\n`, backslash-heavy regex) seem to trigger it. Length does not. This comes from a small sample.
- An earlier draft of S1 said "never put backticks in `new_string`". It was dropped because the model doesn't notice when it is producing the malformed call, so the rule can't be acted on.
- Recoveries that worked in past traces were a `python3 -c` or heredoc `str.replace` (fastapi_13713, rich_4070), reduced escaping (rich_3454) and a shorter retry (rich_3180). Resending the same call never recovered.
- Recovery has to start at the first failure. After `BudgetExceeded` the model only calls `submit_patch` with `{}` repeatedly.
- The sandbox rejects `write_file` to `/tmp`, so the fallback uses `run_command` instead.

## Experiment plan (not yet run)

Test set: loop tasks fastapi_14605, fastapi_13713, fastapi_15763, fastapi_14186, rich_3454, rich_3180. Controls: fastapi_9555, fastapi_9753, rich_3006, requests_6644.

Measure:
- Longest run of consecutive `edit_file` failures per task (baseline: 225 for fastapi_14605, 12 for fastapi_13713 and rich_3454).
- Resolve rate on the loop tasks versus Rishav's itr-1, and on the controls to check for regressions.
- Tool calls used before the first edit lands.

Caveats:
- The sample is small, so results are directional.
- The local pipeline runs at 25 calls and 10 minutes, while `eval_config.yaml` says 10 calls and 1 minute. A 1-minute limit may make recovery too late. Raising the budget is a separate change and is deliberately not part of this iteration.
- Rishav's 17 empty `rich_*` traces need a `--resume` re-run before any comparison against base.

# Sanyam / itr-7

**Parent:** `src/sanyam/itr-2/` (continues the S-numbering from `src/sanyam/itr-2/changes.md`, which ends at S7)

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| S8 | `skills/swe-helpers/scripts/locate.py` | Deterministic issue-to-code locator, stdlib only. BM25 over identifier-split terms plus definition-name, traceback-frame, quoted-literal and path signals; `ast` gives enclosing def/class ranges. Prints the top 5 files with line ranges (about 1.2k chars, about 1 s) and `confidence: high/medium/low`. Read-only; args `--issue <text> [--root /workspace]`. | Replaces the sub-agent locator (it ignored call caps: 10-35 calls, 30-220 s) with a 1-call, 1-second step. |
| S9 | `skills/swe-helpers/scripts/review.py` | Pre-submit check on `git diff HEAD`: empty or no-source edit, syntax error, newly undefined names. Then picks related test files (name mirror, imports of the edited module, changed symbols), runs `pytest --collect-only` and `pytest -x -q -p no:cacheprovider` under a 60 s total cap, and re-runs failures on a `git archive HEAD` copy in a temp dir to label each NEW or PRE-EXISTING. Output under 1.5k chars, ends with a one-line VERDICT. Removes any untracked file the test run created; never edits tracked files. | Replaces the sub-agent reviewer. Existing failures no longer mislead; import breakage (8 of 55 itr-1 failures) gets caught. |
| S10 | `skills/swe-helpers/SKILL.md`, `agent.yaml` | New skill, registered via `skills: [skills/swe-helpers]`. | Delivery mechanism. |
| S11 | `prompts/system.md` | Short strict section: `locate.py` as the FIRST action with the full issue text; `review.py` once after the last edit and before `submit_patch`. Reserve 6 final calls instead of 5. | Make the model actually call the helpers. |
| S12 | `prompts/system.md` (section 3) | Check runs as a `python3 - <<'PY'` heredoc via `run_command`, no file (from itr-4 S13). Replaces the itr-2 `/workspace/_check.py` advice. | Untracked files in `/workspace` end up in the patch (verified in the smoke test). |
| S13 | `eval_config.yaml`, `prompts/system.md` | `max_time_minutes` stays 5; prompt budget says 40 calls and 5 minutes. | Matches the hard limits. The itr-2 prompt and config already said 40; only the itr-2 `changes.md` (S7) says 28. |
| S14 | `prompts/system.md` (section 2) | Two bullets from itr-4: "Copy, never invent" (S10, verbatim quoted strings, else match nearest existing message) and "Cover sibling paths" (S11, generic part only, no `docs_src` text). | 9 of 24 reviewed itr-2 failures each were invented strings and partial fixes. |
| S15 | `prompts/system.md` (section 4), `review.py` | Before `submit_patch`, `git status --short`, remove scratch files, never touch `pytest.ini`/`conftest.py` (itr-4 S14). `review.py` also reports `STRAY` (untracked files) and `CONFIG` (edited `pytest.ini`, `conftest.py`, `setup.cfg`, `tox.ini`) as FIX BEFORE SUBMIT. | Keeps stray files out of the patch. Not repo-specific. |

`configs/sampling.yaml` is unchanged from itr-2.

## Measured (local, fastapi_11194 snapshot)

- `locate.py`: 1.1 s, 1,229 chars. `param_functions.py` (File, Form) and `params.py` rank in the top 6, but the actual fix location (`fastapi/dependencies/utils.py`) was NOT in the top 10: a vocabulary mismatch it cannot solve. Prototype recall (top 5 files): train 87%, dev 95%, val 79%.
- `review.py`: 0.1 s on static-only runs. In the Docker sandbox with the real fastapi image: about 6 s including collect-only and the baseline run (the collection errors there are pre-existing and were labelled so).
- Smoke test (Qwen3-4B, plumbing only): both scripts ran inside the sandbox without error, each cost exactly 1 tool call (`load_skill` free), `/tmp` is writable via `run_command`, and a `/workspace/_check.py` scratch file appeared in the patch.

## Open questions

- Gemma's compliance is untested: does it call `locate.py` first with the full issue verbatim, and `review.py` before submit? The 4B model only called `locate.py` unprompted and skipped `review.py`.
- Test selection recall is low (38% of hidden test files in the prototype study), so the runner may often say "no test found". A 60 s cap on a slow container may truncate requests-style suites (partial results are labelled).
- `confidence: high` is unmeasured. A wrong top hit might anchor the model.
- The prompt grows by about 250 tokens.

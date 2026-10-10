# Akshay / iteration 3 — scratch paths, patch hygiene, two-step verification

**Parent:** `src/rishav/itr-9/` (prompt, configs and eval config copied from it; not based on `akshay/itr-2`)

**Evidence (itr-9 train, n=30, 10 resolved):** `gemma4_eval/reports/rishav-itr-9-codex-one/`
- itr-9 prompt said "create repro under `/tmp`", but `write_file` rejects `/tmp` (30x `Path traversal detected`). The agent fell back to the repo root and `submit_patch` (`git add -N .`) shipped the scratch files: 14/20 unresolved patches had non-gold files (0/10 resolved); 4 patches were scratch-only.
- 17/18 post-edit checks were the agent's own repro; only 1 ran an existing test file. 8 unresolved runs edited the gold file, passed their repro, and failed hidden tests.
- 43/80 `run_command` "errors" were `grep` exit 1 (no match).

| ID | File | Change | Targets |
| --- | --- | --- | --- |
| S1 | `prompts/system.md` | Scratch files under `.scratch/` + `.git/info/exclude`; `/tmp` removed everywhere | write_file errors, scratch in patch |
| S2 | `prompts/system.md` | Pre-submit `git status --porcelain`; submit once | non-gold files in patch, repeat submits |
| V5 | `prompts/system.md` | Second check: existing test file for the changed module (`-x -q 2>&1 \| tail -30`) | resolve rate after gold edit |
| V6 | `prompts/system.md` | Match exact names/fields; grep every place a new name is used | resolve rate after gold edit |
| T1 | `prompts/system.md` | `grep` exit 1 = no match; single quotes; one `edit_file` retry with more context | run_command / edit_file errors |
| C1 | `prompts/system.md` | Merged duplicate rules (bare-pytest x3, broken-tests x3, no-test-edit x2) | prompt size |

**Size:** 1,025 -> 700 tokens (cl100k proxy; 4,666 -> 2,985 chars). itr-9 per-turn prompt: first turn median ~4.0k, per-run peak median ~14.6k (max 16.3k). The new prompt is ~325 tokens smaller on every turn; pytest output is capped with `tail -30`.

**Checked locally:** `.git/info/exclude` keeps `.scratch/` out of `git add -N .` + `git diff` (stray root files still included). Confirm once in the sandbox, since the harness diffs against `_swegemma_baseline`.

**Eval:** same 30 train tasks. Targets: non-gold files in final patch 14 -> 0; write_file ok >= 90% (59%); repo pytest after last edit in >= 70% of runs; resolved >= 10; median calls of resolved runs ~25.

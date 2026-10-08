# Rishav / iteration 4

**Parent:** `src/rishav/itr-3`

Codebase change only. `agent.yaml`, `eval_config.yaml`, and `configs/sampling.yaml` are unchanged copies of itr-3. No skill and no subagent.

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| R1 | `prompts/system.md` | First action depends on the issue. A caller-visible failure is reproduced with `/workspace/_check.py` before any search; the traceback names the file. A named symbol, or a feature that will not crash, is opened with one `git grep`. A title with no caller action and no symbol is grepped for its concrete words; the model does not invent a reproduction or open the GitHub link. | `fastapi_11355` spent the whole budget searching and never ran the failing route. Titles such as "preserve newlines" name nothing to run and nothing to open. |
| R2 | `prompts/system.md` | Default edit is one function in one file. Another file is in scope only when the same failure is reached through several imports, or the issue names several call sites. New standard-library arguments are guarded with `sys.version_info` when the issue names a Python version. | 91 of 129 reference patches touch one file. `fastapi_14186` is the multi-import case; a stub in one module does not stop the others from importing. |
| R3 | `prompts/system.md` | A failing check counts as progress, so the 12th-call edit is forced only when no file is known yet. The task message budget wins when it is smaller than 40 calls or 5 minutes. | A best-guess edit before the frame is known locks in the wrong patch. |
| R4 | `eval_config.yaml`, `prompts/system.md` | Tool budget 40. Time budget 5 minutes. | The call cap is high enough not to cut a run short. The clock stays at 5 minutes. |
| R5 | `prompts/system.md` | One edit call per change. `edit_file` when both strings are 1–3 lines and contain no backticks or triple quotes. One Python `run_command` when they do. A failure is not retried with the other tool. | On `fastapi_13713` a backtick inside `new_string` dropped `old_string`, and the fallback was a second call on the same change. |
| R6 | `prompts/system.md` | Before `submit_patch`, restore any test file and delete scratch scripts (`_check.py`, `repro.py`, `reproduce_*.py`, `test_*.py`, and the same). `git diff --stat` must list only library files. | Itr-3 left a repro script in 14 one-file tasks and a test edit in 5. Deleting only `_check.py` misses the names the model actually used. |
| R7 | `prompts/system.md` | The first tool call is fixed: `write_file` `_check.py` when a caller can trigger the bug, even if a function is also named. One `git grep` only when nothing can be run. No `grep -r`. | The itr-4 run of `fastapi_11355` opened with seven searches and wrote `_check.py` at call 16. `fastapi_13537` grepped a named function instead of reproducing. |

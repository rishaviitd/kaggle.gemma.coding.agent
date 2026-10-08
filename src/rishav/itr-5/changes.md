# Rishav / iteration 5

**Parent:** `src/rishav/itr-3`

`agent.yaml` and `configs/sampling.yaml` are unchanged copies of itr-3. The itr-4 prompt changes are not in this variant.

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| R1 | `prompts/system.md` | Before `submit_patch`, restore any test file and delete scratch scripts (`repro.py`, `reproduce_*.py`, `test_*.py`, and the same). `git diff --stat` must list only library files. | Itr-3 left a repro script in 14 one-file tasks and a test edit in 5. |
| R2 | `eval_config.yaml`, `prompts/system.md` | Tool budget 40. Time budget 5 minutes. | The call cap is high enough not to cut a run short. The clock stays at 5 minutes. |

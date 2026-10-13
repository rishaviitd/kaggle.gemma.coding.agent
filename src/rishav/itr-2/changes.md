# Rishav / iteration 2

**Parent:** `src/rishav/itr-1/`

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| R5 | `prompts/system.md` | Clarified required `edit_file` arguments and exact-match retry behavior. | Reduce malformed and stale `old_string` calls. |
| R6 | `prompts/system.md` | Clarified workspace-relative paths and disallowed `/tmp` writes. | Prevent path traversal failures when creating reproduction files. |

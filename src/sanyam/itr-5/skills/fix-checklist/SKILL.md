---
name: fix-checklist
description: Checklist to run before and after editing, to avoid partial fixes, invented strings and weak self-checks. Load before the first edit.
---

# Fix checklist

Before you edit:
1. Write down, from the issue text only, the exact expected behavior, exception type, and any quoted strings.
2. `git grep` the function you are about to change. List its callers and its sibling versions (sync and async, other classes, other example variants).
3. If a helper that does the job already exists, reuse it.

While you edit:
4. Copy quoted strings character for character. If the issue gives no wording, copy the style of the nearest existing message. Never invent wording or behavior.
5. Apply the fix to every sibling path from step 2 that shares the bug.
6. Do not touch test files, `pytest.ini` or `conftest.py`.

To verify:
7. Run a `python3 - <<'PY'` heredoc check that uses the issue's own example and asserts the expected result from step 1. It must fail without your edit and pass with it. Do not create files in `/workspace`.
8. Run only the one existing test file for the module. If something that passed before now fails, fix your change.
9. Run `git status --short` and remove stray files before submitting.

---
name: repo-rich
description: Conventions for fixing issues in the rich terminal-rendering repository (rich/ package, exact-output tests). Load before reading code in a rich task.
---

# rich repository

- Library code is in `rich/`. Tests are in `tests/`, usually `tests/test_<module>.py` for `rich/<module>.py`.
- Most tests compare exact rendered text, often with ANSI escape codes. A fix that changes spacing, wrapping, padding or style by one character fails. Reproduce with a small `Console(file=io.StringIO(), width=..., force_terminal=True)` check and compare the exact string.
- Many renderables share code: `Text`, `Segment`, `Style`, `Table`, `Panel`, `Syntax`, `Markdown` and `Console` all go through `Segment` and `Console.render`. Before patching one renderable, `git grep` the shared function and fix the cause once.
- Keep existing parameter names, defaults and public signatures. Do not change a default unless the issue says to.
- Run only the one matching test file (`pytest tests/test_<module>.py -k <name>`).

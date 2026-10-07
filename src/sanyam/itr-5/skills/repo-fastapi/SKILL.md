---
name: repo-fastapi
description: Conventions for fixing issues in the fastapi repository (library code, docs_src examples, tests). Load before reading code in a fastapi task.
---

# fastapi repository

- Library code is in `fastapi/`. Tests are in `tests/`. Documentation example code is in `docs_src/`.
- Many `docs_src/` examples come in variants for different Python versions and styles (for example `tutorial001.py`, `tutorial001_py39.py`, `tutorial001_an.py`, `tutorial001_an_py310.py`). When one is wrong, `ls` the folder and fix every variant with the same bug.
- Tasks about documentation examples are graded on executable code in `docs_src/`, not on the markdown under `docs/`.
- A behavior bug in `fastapi/` often has a twin: the sync and async paths, or the request and the response paths (for example `fastapi/routing.py`, `fastapi/dependencies/utils.py`, `fastapi/openapi/utils.py`). Check the neighbouring function before you finish.
- Keep exception types, HTTP status codes and error `detail` strings exactly as the issue states. When unspecified, copy the wording of the nearest existing `HTTPException` or validation error.
- Find the test file with `find tests -name "*<topic>*.py"`, and run only that file.

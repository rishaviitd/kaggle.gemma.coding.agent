---
name: repo-requests
description: Conventions for fixing issues in the requests HTTP library (src/requests, tests that may need network). Load before reading code in a requests or httpx task.
---

# requests (and httpx) repository

- requests library code is in `src/requests/` (`models.py`, `sessions.py`, `adapters.py`, `utils.py`, `auth.py`, `cookies.py`, `exceptions.py`). Tests are in `tests/`, mainly `tests/test_requests.py` and `tests/test_utils.py`.
- Many tests in `tests/test_requests.py` need a local test server or network. Prefer `tests/test_utils.py` or a `-k` filter on one test; do not chase failures that come from the environment or a connection error.
- Prefer an offline check: build a `requests.Request(...).prepare()` or call the helper in `requests.utils` directly, and assert on the result.
- New behavior goes through the existing exception classes in `src/requests/exceptions.py`. Reuse them. Keep compatibility helpers (`requests.compat`) in place when the surrounding code uses them.
- httpx tasks (`encode/httpx`, `httpx/` package) follow the same approach: fix the shared function, assert with a small offline check, run one test file.

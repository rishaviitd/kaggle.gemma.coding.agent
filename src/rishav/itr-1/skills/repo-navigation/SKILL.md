---
name: repo-navigation
description: A repeatable workflow for locating, understanding, and validating fixes in unfamiliar repositories.
---

# Repository navigation

Use this workflow when investigating a task in `/workspace`:

1. Read the issue carefully and identify the expected behavior, constraints, and likely affected area.
2. Inspect the repository structure and relevant project configuration with `run_command`; use `read_file` for targeted source and test files.
3. Search for the implicated symbols and call sites. Use `get_code_neighbors`, `search_similar_code`, and `get_code_subgraph` when the task's graph data supports them.
4. Delegate focused, read-only exploration to the configured code analyzer when useful. Treat its findings as leads and verify them in the source.
5. Find the narrowest relevant tests and inspect nearby tests to understand conventions before editing.
6. Make the smallest coherent change. Run focused tests with `run_command`, then inspect the diff and status.
7. Call `submit_patch` only after checking that the diff contains the intended fix and no unrelated changes.

Prefer evidence from the checked-out repository over assumptions. If graph search is unavailable or unhelpful, continue with ordinary file and text search.

---
name: swe-helpers
description: Two read-only helper scripts. locate.py ranks likely files for an issue; review.py checks the final diff and runs related tests.
---

# swe-helpers

Each script costs 1 tool call and finishes in seconds. Neither writes into `/workspace`.

1. First action of a task: `run_skill_script` with `skill_name: swe-helpers`, `file_path: scripts/locate.py`, `args: {"issue": "<full issue text>"}`. Returns the top files with line ranges.
2. After your last edit, before `submit_patch`: `file_path: scripts/review.py`, `args: {}`. Reports empty diff, syntax errors, undefined names, and related test results (new vs pre-existing failures). Ends with a one-line verdict.

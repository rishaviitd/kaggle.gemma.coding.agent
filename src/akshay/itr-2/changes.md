# itr-2 changes over itr-1

Baseline: `src/akshay/itr-1` as committed on `main`. Everything from itr-1 is kept unchanged (C2, C5, C6, C8, C9, S1-S4, 40 calls / 10 minutes); see `src/akshay/itr-1/changes.md`.
Evidence: `src/base/analysis/localization_read_to_edit_gap.md` (Option 1).

## Problems (P)

| ID | Problem | Severity |
|---|---|---|
| P20 | Late first edit: reads the right file, commits too late (commit failure, not find failure) | High |
| P21 | Wrong part of a big file: a plain `read_file` returns lines 1-150 (mostly imports), so the model re-reads and greps the file it already found | High |

P21 numbers (76 base train traces, 46 that read and then edited a gold file):
- First gold read had no range in 42 of 46; cut at 150 lines in 51 of 66 reads.
- Gold files: median 784 lines, 56 of 66 over 150 lines.
- Between first gold read and first gold edit: 120 re-reads of the gold file (31%), 110 greps (28%, 47 of them on the gold file itself).
- Median read-to-edit gap: 6 calls resolved, 10 unresolved.

## Changes (C)

| ID | Change | Type | Targets |
|---|---|---|---|
| C10 | File outline skill: `run_skill_script` on `file-outline/scripts/outline.py` lists every class and function with its line range; prompt says to outline any Python file over 150 lines, then `read_file` one range of at most 120 lines, or about ±40 lines around a grep `file:line` | skill + prompt | P21, P20 |
| C11 | Remove the graph tools (`search_similar_code`, `get_code_neighbors`, `get_code_subgraph`) and the `code_analyzer_agent` sub-agent; point file finding at `git grep -n` | config + prompt | P22 |

P22 = Dead tools waste calls: in the 76 base traces, `search_similar_code` returned `count: 0` in all 85 calls (24 traces used it as the first call and never found the gold file that way); `code_analyzer_agent` returned `""` in 3 of 3; `get_code_subgraph` was empty in 1 of 1; `get_code_neighbors` worked 5 of 7, only with fully qualified names. Same direction as rishav/itr-1 (commit `1433017`), which also dropped `search_similar_code` for grep.

Why a skill and not a change to `read_file`: submissions are YAML only. Tools come from the fixed `swegemma` registry, `tools/` is not shipped, and no callback can rewrite tool results. The agent is already offered `run_skill_script` (in the tool list of all 152 itr-1 traces). It runs `python3` inside the workspace container and counts as one tool call, the same as `read_file`.

## What was changed in itr-2

| Change | File | Edit |
|---|---|---|
| C10 | `skills/file-outline/SKILL.md` | New skill: when to use it, and one exact `run_skill_script` example plus the follow-up `read_file`. |
| C10 | `skills/file-outline/scripts/outline.py` | Standard library only. `outline.py <path> [--symbol NAME] [--root /workspace]`. Prints `L<start>-<end> <kind> <Qualified.name><signature>`, one per definition; decorators included in the range; functions nested inside functions skipped. Over 120 definitions: names only; hard cap 300 lines; lines cut at 100 characters. Syntax errors fall back to a regex outline (start lines only). Missing or non-Python files return a one-line hint. |
| C10 | `agent.yaml` | `skills/file-outline` added after `skills/repo-navigation`. |
| C10 | `prompts/system.md` | In "1. Identify Target Files Immediately": the `search_similar_code` bullet (0 useful results in 85 base calls) is replaced by two bullets: outline-then-range for Python files over 150 lines, and ±40 lines around a known grep `file:line`. |
| C11 | `agent.yaml` | Removed `get_code_neighbors`, `search_similar_code`, `get_code_subgraph` and the `agent_tool` entry for `sub_agents/code_analyzer.yaml`. |
| C11 | `sub_agents/code_analyzer.yaml`, `prompts/analyzer.md` | Deleted (no longer referenced). |
| C11 | `prompts/system.md` | New bullet in step 1: find files with targeted `git grep -n ... \| head -30`; graph tools are not available, ignore any section that mentions them. The harness still adds a "Code Intelligence Tools" section to the task prompt when graph data exists (`swegemma/harness/agent_runner.py` line 176); a submission cannot turn that off. |
| C11 | `skills/repo-navigation/SKILL.md` | Steps 3-4 now use `git grep -n`, the `file-outline` skill and ranged `read_file` instead of graph tools and the analyzer. |

## Supporting edits (not changes in their own right)

- `tests/test_outline.py`: 12 unittest cases for `outline.py`. Run with `python3 -m unittest discover -s src/akshay/itr-2/tests -p 'test_*.py'`.
- `scripts/task_pipeline.py`: `prepare_agent` also skips `tests` when copying the submission, so test files are not shipped.

## Checked before running

- Unit tests: 12 pass.
- Real files (installed fastapi 0.141.1, rich 15.0.0, requests 2.34.2): the outline is 1-13% of the file's characters (`fastapi/routing.py` 5.8k vs 256k, `rich/console.py` 4.1k vs 101k), and covers the whole file. `--symbol is_terminal` gives `L930-976`, which is the real `@property` + `def` range.
- `adk_submission` `validate_directory` and `discover_skills` accept the folder; both skills are found.
- The ADK `run_skill_script` wrapper code, run in `swebench-sandbox:latest` (Python 3.13.15) with files at `/workspace`, prints the expected outline and range.

## Not yet done

- No remote run. Compare with itr-1 on: read-to-edit gap, re-reads of the gold file, number of outline calls, resolve rate.
- Risk: the model may ignore the skill; the prompt rule is the only push. Ranged `read_file` calls can still lose `start_line` to the argument-key bug (Part A: 20 of 343 ranged reads in base).

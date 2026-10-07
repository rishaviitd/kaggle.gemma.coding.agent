# Sanyam / itr-5

**Parent:** `src/sanyam/itr-2/` (continues the S-numbering from `src/sanyam/itr-4/changes.md`, which ends at S14)

Same ideas as itr-4, but delivered through four skills and one read-only sub-agent (`locator`) instead of one long prompt. `configs/sampling.yaml` is identical to itr-2. `src/sanyam/itr-6/` adds a second sub-agent (`reviewer`) on top of this.

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| S15 | `agent.yaml` | Adds `skills:` (`repo-fastapi`, `repo-rich`, `repo-requests`, `fix-checklist`) and one `agent_tool` entry (`locator`) with `skip_summarization: true`, the pattern from `src/base/agent.yaml`. The sub-agent YAML follows `src/base/sub_agents/code_analyzer.yaml` (`name`, `description`, `model`, `instruction: !include ../prompts/...`, `tools`, `generate_content_config: !include ../configs/sampling.yaml`). | Moves per-task guidance out of the system prompt. With `skip_summarization: true` the sub-agent's reply is returned as is, with no extra summarising model turn. |
| S16 | `prompts/system.md` | Rewritten and shorter (about 550 words). Keeps the `edit_file` heredoc fallback, the heredoc check, targeted-tests-only, anti-patterns and the budget rules. Tells the agent to `load_skill` the repo skill first and call `locator` once first. Step 5 is a self re-read of the diff against the issue, then `git status`, cleanup and `submit_patch`. Adds read rules from `src/base/analysis/localization_read_to_edit_gap.md`: read a range of about 40 lines around a match, never a file over 300 lines without a range, and read around grep's `file:line` instead of re-reading from the top. | The prompt only says when to use each helper. In base traces 42 of 46 first reads of the edited file had no line range and about 31% of the calls before the first edit were re-reads. |
| S17 | `sub_agents/locator.yaml`, `prompts/locator.md` | Read-only by instruction (`run_command`, `read_file`), at most 6 calls. Prompt shape follows base's `analyzer.md` (role, instructions, structured report), reporting edit line ranges, root cause, suggested change, same-bug sibling paths, a reusable helper, the test file and quoted strings. | Targets partial and wrong-location fixes without growing the main context. |
| S18 | `prompts/locator.md`, `prompts/system.md` | Search rules borrowed from `src/rishav/itr-2/skills/how-to-grep`: at most three terms in one `git grep -nE` pattern, exit status 1 means no match, no near-identical re-searches once a candidate appears, no re-reading a file without a line range. The system prompt also says not to call `list_skills`. | Rishav's itr-2 run with that skill resolved 20 of 76 (Rishav's itr-1 without it: 23; our itr-2: 25), so only the cheap search rules were taken, not the skill. |
| S19 | `skills/repo-fastapi`, `skills/repo-rich`, `skills/repo-requests`, `skills/fix-checklist` | Repo conventions (same facts as itr-4 S12, in more detail) and a before/while/verify checklist (same rules as itr-4 S10, S11, S13, S14). | Loaded only when relevant. |
| S20 | `eval_config.yaml` | `max_tool_calls` 28 to 36 and `max_time_minutes` 8 to 5. `timeout_seconds` 240 and `max_turns` 80 unchanged. Prompt budget text matches: edit by call 14, last 5 reserved, no new exploration after call 31. | `locator` calls (up to 6) draw on the same budget. Skill loads appear free, but the prompt conservatively tells the agent they count. |

## Why this change

- Tests whether guidance as skills and one sub-agent beats the same guidance inline (itr-4), without changing the base model or sampling.
- One sub-agent only, to limit the extra model turns under the 5-minute cap. The `locator` was kept because it can cut the re-reading that takes about 31% of the calls before the first edit; a pre-submit `reviewer` only adds work (tested in itr-6).
- Sub-agents run in their own context, so large `read_file` outputs stay out of the main agent's history.

## Verified in the wheels (`swegemma` 0.2.7, `adk-submission` 0.2.12)

- Tool calls count only through `@budget_gated` tools sharing one `tool_calls_used` counter. Sub-agents get the same tools, so their calls count.
- `max_time_minutes` is checked against agent wall-clock time, so every helper turn costs time.
- Skills compile into ADK's `SkillToolset` and are appended to the agent's tools.

## Verified in existing traces (`logs/remote/rishav`, `logs/remote/akshay`)

- `SkillToolset` exposes `load_skill` and `run_skill_script`. Rishav's runs called `load_skill` 74 times and Akshay's `run_skill_script` 21 times, so Gemma does load skills when told to.
- Skill calls look free. In 14 sampled Rishav itr-2 runs the final `tool_calls` equals the count of other calls (for example 8 of 8, 7 of 7, 25 of 25), not that count plus the skill load. Evidence only, not a proof.
- The graph tools (`search_similar_code`, `get_code_neighbors`, `get_code_subgraph`) used in base's `code_analyzer` mostly return empty results (`"results": [], "count": 0` in the sampled base, Akshay and Rishav calls), so the locator uses `git grep` instead and has no adapter (`tool_lora` in base is not available to us).
- No existing trace contains an `agent_tool` call, so sub-agent behavior is still untested.

## Not verified

- Whether `agent_tool` with `skip_summarization: true` returns the sub-agent's text correctly to the main agent. Test with one run before a full run.
- Whether sub-agent calls count against `max_tool_calls` (they should, from the shared counter) and how much wall-clock time a sub-agent turn adds. Rough estimate: about 8.6 s per model call in itr-2, so the `locator` may add around a minute of the 5.
- Repo-skill contents are written from general knowledge of the repos, not checked against every task.

## Experiment plan (not yet run)

- First run 3 tasks (one each of fastapi, rich, requests) and read the traces: did the agent load skills and call `locator`, and how many calls and seconds did it use?
- Then run the 76 train tasks. Compare with itr-2 (25 of 76), itr-4 and itr-6: resolve rate, calls used, runs ending on the budget or the 5-minute limit, time per task.
- Check wall-clock time: 129 tasks must finish within the 12-hour limit.

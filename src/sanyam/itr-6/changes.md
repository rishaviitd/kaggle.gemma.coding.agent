# Sanyam / itr-6

**Parent:** `src/sanyam/itr-5/` (continues the S-numbering from `src/sanyam/itr-5/changes.md`, which ends at S20)

itr-5 plus a second read-only sub-agent, `reviewer`, called once before `submit_patch`. Everything else (skills, `locator`, search rules, read rules, 36 tool calls, 5 minutes) matches itr-5. `diff -r` of itr-5 and itr-6 shows only the rows below.

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| S21 | `agent.yaml` | Adds a second `agent_tool` entry, `reviewer` (`sub_agents/reviewer.yaml`), with `skip_summarization: true`. | Gives a second look at the diff before submit. |
| S22 | `sub_agents/reviewer.yaml`, `prompts/reviewer.md` | Read-only by instruction (`run_command`, `read_file`), at most 3 calls. Reads `git status --short` and `git diff HEAD` against the issue and reports missing sibling fixes, invented strings or signatures, unimplemented behavior, regression risk and stray files, in under 120 words, or "OK, no problems found". | Aimed at the "misread or invented" and "partial" failure patterns (9 of 24 reviewed itr-2 failures each). |
| S23 | `prompts/system.md` | Adds the `reviewer` helper (call once after the check and targeted test pass, fix real problems, at most twice). Workflow step 5 calls `reviewer` instead of the self re-read. Budget text: last 6 calls reserved (including the reviewer), no new exploration after call 29. | Keeps the reviewer inside the 36-call budget. |

## Why this change

- Comparing itr-5 and itr-6 isolates the effect of the `reviewer`, since the only differences are S21 to S23.
- Cost to watch: the `reviewer` adds up to 3 tool calls and about 4 model turns (roughly 30 seconds by itr-2's 8.6 s per call), inside a 5-minute cap.
- Risk: the reviewer is the same Gemma model, so it may approve its own mistakes or report false problems that make the main agent break a correct fix.

## Not verified

- Same open points as itr-5: whether `agent_tool` returns the sub-agent's text correctly, and how much time sub-agent turns add. No existing trace contains an `agent_tool` call.
- Whether the main agent acts on the reviewer's report or ignores it.

## Experiment plan (not yet run)

- Run after itr-5's 3-task test. Run the same 76 train tasks as itr-5.
- Compare with itr-5: resolve rate, runs ending on the 5-minute limit, calls used, and how often a reviewer report led to an edit. Read the traces of tasks the reviewer changed the outcome of in either direction.

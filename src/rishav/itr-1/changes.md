# Rishav / iteration 1

**Parent:** `src/base/`

| ID | File | Change | Purpose |
| --- | --- | --- | --- |
| R1 | `agent.yaml` | Removed `get_code_neighbors` and `get_code_subgraph`. | Test whether a smaller tool surface reduces exploration overhead. |
| R2 | `agent.yaml` | Removed the `code_analyzer_agent` subagent. | The baseline train traces used it in only 3 of 76 tasks. |
| R3 | `agent.yaml` | Removed the `repo-navigation` skill declaration. | Test the agent without skill-tool instructions. |
| R4 | `agent.yaml`, `prompts/system.md` | Removed `search_similar_code`; use targeted `grep` for unknown files. | Semantic search repeatedly returned empty results in the baseline traces. |

`prompts/system.md` matches the current parent baseline; the earlier test-guided prompt experiment was removed before this iteration.

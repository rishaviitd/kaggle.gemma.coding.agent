# Rishav / iteration 1_v3_graph-ignore

Parent: `src/rishav/itr-1_v2/`. Display name: Rishav v3 graph-ignore.

Only behavioral change: the system prompt explicitly lists the six registered tools, forbids calls to search_similar_code/get_code_neighbors/get_code_subgraph, and tells the agent to ignore the harness-injected graph-tool instructions and use targeted git grep/grep instead.

Agent YAML, sampling, evaluation limits and original prompt content are otherwise unchanged:330 active seconds,40 counted tools,80 turns,240-second command/grading timeout,8192 output tokens,temperature0.2,top_p0.95. Native DeepSeek reasoning is high; the provider does not enforce Gemma's exact4096-token thinking budget.

The existing `itr-1_v3` temperature1/five-minute experiment is preserved separately. No installed harness or graph assets were modified. The new run retains the original20-task DeepSeek results separately and has no batch failure cutoff.

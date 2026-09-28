(() => {
  const h = React.createElement;

  function FlowNode({ title, children }) {
    return h("article", { className: "flow__node" },
      h("h4", null, title),
      h("div", { className: "flow__details" }, children)
    );
  }

  function Phase({ number, title, nodes }) {
    const steps = [];
    nodes.forEach((node, index) => {
      if (index) steps.push(h("span", { className: "flow__arrow", key: `arrow-${index}`, "aria-hidden": "true" }, "→"));
      steps.push(h(FlowNode, { title: node.title, key: node.title }, node.content));
    });
    return h("section", { className: "flow__phase" },
      h("h3", null, h("span", { className: "flow__number" }, number), title),
      h("div", { className: "flow__row" }, steps)
    );
  }

  window.WhatToDoSlide = function WhatToDoSlide({ number, total }) {
    const content = h("div", { className: "workflow" },
      h("div", { className: "workflow__command" },
        h("span", { className: "workflow__label" }, "RUN"),
        h("code", null, ".venv/bin/python scripts/task_pipeline.py --task-id fastapi_11194 --api-base <vLLM_URL> --model gemma4")
      ),
      h(Phase, {
        number: "01",
        title: "Patch generation",
        nodes: [
          {
            title: "Inputs",
            content: h("p", null, "Issue + repo snapshot; AST graph and embeddings support code search.")
          },
          {
            title: "Gemma 4 + prompts",
            content: h("p", null, "src/agent.yaml · src/prompts/system.md · src/prompts/analyzer.md · src/sub_agents/code_analyzer.yaml")
          },
          {
            title: "Docker agent sandbox",
            content: h(React.Fragment, null,
              h("p", null, "Container A · /workspace"),
              h("code", { className: "flow__tool-sequence" }, "read_file → get_code_neighbors → search_similar_code → … → edit_file → run_command")
            )
          },
          {
            title: "Submit patch",
            content: h("code", { className: "flow__tool-sequence" }, "submit_patch → agent_patch")
          }
        ]
      }),
      h(Phase, {
        number: "02",
        title: "Patch evaluation",
        nodes: [
          {
            title: "Fresh Docker sandbox",
            content: h("p", null, "Container B starts from the clean base snapshot.")
          },
          {
            title: "Prepare verification",
            content: h("p", null, "Apply agent_patch · restore protected tests · apply test_patch")
          },
          {
            title: "Run checks",
            content: h("p", null, "Run pytest; validate the JUnit report. Both must pass.")
          },
          {
            title: "Score + artifacts",
            content: h(React.Fragment, null,
              h("p", null, "Resolved / not resolved → resolution rate"),
              h("code", { className: "flow__tool-sequence" }, "results/remote-baseline/fastapi_11194.patch · .json")
            )
          }
        ]
      })
    );

    return h(window.SlideLayout, {
      eyebrow: "Overview",
      title: "Coding Agent Workflow",
      content,
      number,
      total
    });
  };
})();

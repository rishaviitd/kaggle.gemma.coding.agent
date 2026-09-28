(() => {
  const h = React.createElement;
  const tools = ["read_file", "get_code_neighbors", "search_similar_code", "edit_file", "run_command", "submit_patch"];

  function StepCard({ title, children }) {
    return h("section", { className: "workflow__card" },
      h("h3", null, title),
      children
    );
  }

  window.WhatToDoSlide = function WhatToDoSlide({ number, total }) {
    const content = h("div", { className: "workflow" },
      h("div", { className: "workflow__command" },
        h("span", { className: "workflow__label" }, "RUN"),
        h("code", null, ".venv/bin/python scripts/task_pipeline.py --task-id fastapi_11194 --api-base <vLLM_URL> --model gemma4")
      ),
      h("div", { className: "workflow__steps" },
        h(StepCard, { title: "Inputs" },
          h("p", null, "Gemma 4 model + issue"),
          h("p", null, "Repository snapshot"),
          h("p", null, "AST graph + embeddings + wheels")
        ),
        h("span", { className: "workflow__arrow", "aria-hidden": "true" }, "→"),
        h(StepCard, { title: "Agent tool calls" },
          h("div", { className: "workflow__tools" }, tools.map((tool) => h("code", { key: tool }, tool)))
        ),
        h("span", { className: "workflow__arrow", "aria-hidden": "true" }, "→"),
        h(StepCard, { title: "Tested output" },
          h("p", null, "Patch verified in sandbox"),
          h("code", { className: "workflow__path" }, "results/remote-baseline/fastapi_11194.patch"),
          h("code", { className: "workflow__path" }, "results/remote-baseline/fastapi_11194.json")
        )
      )
    );

    return h(window.SlideLayout, {
      eyebrow: "Task understanding",
      title: "From issue to verified patch",
      content,
      number,
      total
    });
  };
})();

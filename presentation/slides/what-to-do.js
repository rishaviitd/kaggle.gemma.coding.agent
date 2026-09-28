(() => {
  const h = React.createElement;

  function Phase({ number, title, children }) {
    return h("section", { className: "workflow__phase" },
      h("h3", null, h("span", { className: "workflow__number" }, number), title),
      children
    );
  }

  window.WhatToDoSlide = function WhatToDoSlide({ number, total }) {
    const content = h("div", { className: "workflow" },
      h("div", { className: "workflow__command" },
        h("span", { className: "workflow__label" }, "RUN"),
        h("code", null, ".venv/bin/python scripts/task_pipeline.py --task-id fastapi_11194 --api-base <vLLM_URL> --model gemma4")
      ),
      h("div", { className: "workflow__phases" },
        h(Phase, { number: "01", title: "Patch generation" },
          h("p", null, "Gemma 4 works from the issue and repository snapshot; AST graph + embeddings power code search."),
          h("div", { className: "workflow__sequence" },
            h("code", null, "read_file → get_code_neighbors → search_similar_code"),
            h("span", { className: "workflow__ellipsis" }, "…"),
            h("code", null, "edit_file → run_command → submit_patch")
          ),
          h("p", { className: "workflow__note" }, "The harness captures the submitted patch as agent_patch.")
        ),
        h(Phase, { number: "02", title: "Patch evaluation" },
          h("div", { className: "workflow__sequence" },
            h("code", null, "Fresh sandbox → apply agent_patch → reset protected tests → apply test_patch → pytest + JUnit")
          ),
          h("p", null, "Pass when pytest succeeds and the JUnit report validates."),
          h("p", { className: "workflow__note" }, "Result: resolved / not resolved · resolution rate"),
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

(() => {
  const h = React.createElement;

  function Field({ label, value }) {
    return h("div", { className: "task-view__field" },
      h("span", null, label),
      h("code", null, value || "—")
    );
  }

  function Panel({ title, value, wide = false }) {
    return h("section", { className: `task-view__panel${wide ? " task-view__panel--wide" : ""}` },
      h("h3", null, title),
      h("pre", { tabIndex: 0 }, value || "—")
    );
  }

  window.TaskVisualizationSlide = function TaskVisualizationSlide({ number, total }) {
    const [task, setTask] = React.useState(null);
    const [error, setError] = React.useState("");

    React.useEffect(() => {
      fetch("data/tasks.jsonl")
        .then((response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          return response.text();
        })
        .then((text) => {
          const record = text.split("\n")
            .filter(Boolean)
            .map((line) => JSON.parse(line))
            .find((item) => item.instance_id === "fastapi_11194");
          if (!record) throw new Error("fastapi_11194 was not found");
          setTask(record);
        })
        .catch((caught) => setError(caught.message));
    }, []);

    const content = !task
      ? h("div", { className: "task-view__loading" }, error || "Loading data/tasks.jsonl …")
      : h("div", { className: "task-view" },
          h("section", { className: "task-view__metadata" },
            h(Field, { label: "instance_id", value: task.instance_id }),
            h(Field, { label: "repo", value: task.repo }),
            h(Field, { label: "base_commit", value: task.base_commit }),
            h(Field, { label: "created_at", value: task.created_at })
          ),
          h("div", { className: "task-view__content" },
            h(Panel, { title: "problem_statement", value: task.problem_statement, wide: true }),
            h(Panel, { title: "patch", value: task.patch }),
            h(Panel, { title: "test_patch", value: task.test_patch })
          ),
          task.hints_text
            ? h(Panel, { title: "hints_text", value: task.hints_text })
            : null
        );

    return h(window.SlideLayout, {
      eyebrow: "Training task record",
      title: "fastapi_11194",
      content,
      number,
      total
    });
  };
})();

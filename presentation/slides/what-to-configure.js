window.WhatToConfigureSlide = function WhatToConfigureSlide({ number, total }) {
  const h = React.createElement;

  const items = [
    {
      title: "System and analyzer prompts",
      file: "src/prompts/system.md, analyzer.md",
      bullets: [
        "Main: fast, minimal fix in 8–10 turns.",
        "Run targeted tests, never the full suite.",
        "Never edit tests. Always submit a patch.",
        "Analyzer: root cause, files, lines, fix."
      ]
    },
    {
      title: "Sub-agents via agent_tool",
      file: "src/sub_agents/code_analyzer.yaml",
      bullets: [
        "Analyzer is a read-only investigator.",
        "Own prompt, adapter (tool_lora), sampling.",
        "Same Gemma 4 model as the root agent.",
        "skip_summarization: report passes through."
      ]
    },
    {
      title: "Tool selection per agent",
      file: "src/agent.yaml, tools:",
      bullets: [
        "Root: all 9 harness tools plus analyzer.",
        "Files: read, edit, write, run_command.",
        "Graph: neighbors, similar code, subgraph.",
        "Analyzer: read_file and 3 graph tools only."
      ]
    },
    {
      title: "Skills: repo_navigation workflow",
      file: "src/skills/repo_navigation/SKILL.md",
      bullets: [
        "Steps 1–2: read issue, inspect repo.",
        "Step 3: search symbols via graph tools.",
        "Step 4: delegate read-only, verify leads.",
        "Steps 5–7: test, minimal edit, submit."
      ]
    },
    {
      title: "Sampling and thinking budget",
      file: "src/configs/sampling.yaml",
      bullets: [
        "temperature: 0.2, top_p: 0.95.",
        "max_output_tokens: 16384.",
        "thinking_budget: 4096 tokens.",
        "include_thoughts: on, shared by both agents."
      ]
    },
    {
      title: "LoRA adapters per agent",
      file: "src/adapters/main_lora/, tool_lora/",
      bullets: [
        "main_lora: root. tool_lora: analyzer.",
        "PEFT: r 4, alpha 8, dropout 0.",
        "Targets q_proj, o_proj in layer 0.",
        "Config only. No weights added yet."
      ]
    },
    {
      title: "Per-task limits in eval_config",
      file: "src/eval_config.yaml",
      bullets: [
        "max_tool_calls: 10.",
        "max_time_minutes: 1, max_turns: 50.",
        "timeout_seconds: 60 per command.",
        "Harness defaults are much higher."
      ]
    }
  ];

  const card = (item, index) => h("article", { className: "config__card", key: item.title },
    h("h3", null, h("span", { className: "flow__number" }, String(index + 1).padStart(2, "0")), item.title),
    h("ul", null, item.bullets.map((bullet) => h("li", { key: bullet }, bullet))),
    h("code", { className: "config__file" }, item.file)
  );

  const fixedCard = h("article", { className: "config__card config__card--fixed", key: "fixed" },
    h("h3", null, "Fixed by competition"),
    h("ul", null,
      [
        "Model: gemma-4-31b-it-qat-w4a16-ct.",
        "9 harness tools plus a budget gate.",
        "One Docker sandbox per task.",
        "A fresh container verifies the patch.",
        "Offline run on 4x Kaggle L4 GPUs.",
        "Hidden tests on ~120 private tasks.",
        "Score: resolution rate from 0 to 1."
      ].map((bullet) => h("li", { key: bullet }, bullet))
    )
  );

  const content = h("div", { className: "config" }, items.map(card).concat(fixedCard));

  return h(window.SlideLayout, {
    eyebrow: "Overview",
    title: "What we can configure",
    content,
    number,
    total
  });
};

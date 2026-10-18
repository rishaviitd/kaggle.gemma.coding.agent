window.WhatToConfigureSlide = function WhatToConfigureSlide({ number, total }) {
  const h = React.createElement;

  const items = [
    {
      title: "System and analyzer prompts",
      file: "src/prompts/system.md, analyzer.md",
      bullets: [
        "Role, rules and output format per agent.",
        "Strategy: how to search, test and fix.",
        "Example: never edit tests.",
        "Example: aim for a fix in 8–10 turns."
      ]
    },
    {
      title: "Sub-agents via agent_tool",
      file: "src/sub_agents/code_analyzer.yaml",
      bullets: [
        "Add specialist agents the root can call.",
        "Each has its own prompt, tools and sampling.",
        "Optional LoRA adapter per sub-agent.",
        "Example: a read-only code analyzer."
      ]
    },
    {
      title: "Tool selection per agent",
      file: "src/agent.yaml, tools:",
      bullets: [
        "Choose which tools each agent may use.",
        "Give sub-agents fewer, safer tools.",
        "Example: root gets all 9 harness tools.",
        "Example: analyzer gets read_file and graph tools."
      ]
    },
    {
      title: "Skills: repo_navigation workflow",
      file: "src/skills/repo_navigation/SKILL.md",
      bullets: [
        "Reusable step-by-step workflows in markdown.",
        "Edit the steps to change how work is done.",
        "Example: repo_navigation, a 7-step workflow.",
        "Example: read issue, search, test, fix, submit."
      ]
    },
    {
      title: "Sampling and thinking budget",
      file: "src/configs/sampling.yaml",
      bullets: [
        "Randomness: temperature and top_p.",
        "Maximum output tokens per reply.",
        "Thinking budget and whether thoughts show.",
        "Example: temperature 0.2, thinking 4096 tokens."
      ]
    },
    {
      title: "LoRA adapters per agent",
      file: "src/adapters/main_lora/, tool_lora/",
      bullets: [
        "A small add-on adapter for each agent.",
        "Set rank, alpha, dropout and target layers.",
        "Example: main_lora for root, tool_lora for analyzer.",
        "Currently config only, no trained weights."
      ]
    },
    {
      title: "Per-task limits in eval_config",
      file: "src/eval_config.yaml",
      bullets: [
        "Cap tool calls, turns and total time.",
        "Set a timeout for each command.",
        "Example: 10 tool calls, 60 s per command.",
        "Harness defaults are higher."
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

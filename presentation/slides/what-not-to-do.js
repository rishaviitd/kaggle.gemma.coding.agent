window.WhatNotToDoSlide = function WhatNotToDoSlide({ number, total }) {
  return React.createElement(window.SlideLayout, {
    eyebrow: "Overview",
    title: "What we do not have to do",
    points: [
      "Add or edit tools: src/tools/; built-in tools ship in wheelhouse/swegemma-0.2.7-py3-none-any.whl.",
      "Build or edit the harness: guide at context/harness.md; implementation is in that wheel.",
      "Build another evaluator: local entry point is scripts/evaluate.py; Kaggle scores submissions."
    ],
    number,
    total
  });
};

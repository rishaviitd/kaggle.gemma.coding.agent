window.WhatNotToDoSlide = function WhatNotToDoSlide({ number, total }) {
  return React.createElement(window.SlideLayout, {
    eyebrow: "Overview",
    title: "What we do not have to do",
    points: [
      "Add or edit tools: adapters are in src/tools/; the harness supplies built-in tools.",
      "Build or edit the harness: see context/harness.md; its runtime package is wheelhouse/swegemma-0.2.7-py3-none-any.whl (not a model input).",
      "Build another evaluator: local entry point is scripts/evaluate.py; Kaggle scores submissions."
    ],
    number,
    total
  });
};

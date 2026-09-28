window.WhatToDoSlide = function WhatToDoSlide({ number, total }) {
  return React.createElement(window.SlideLayout, {
    eyebrow: "Task understanding",
    title: "Build a useful coding agent",
    points: [
      "Post-train Gemma 4 with reinforcement learning.",
      "Use harness tools to inspect code and make changes.",
      "Test fixes against real software issues."
    ],
    number,
    total
  });
};

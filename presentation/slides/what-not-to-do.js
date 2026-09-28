window.WhatNotToDoSlide = function WhatNotToDoSlide({ number, total }) {
  return React.createElement(window.SlideLayout, {
    eyebrow: "Overview",
    title: "What we do not have to do",
    points: [
      "Add or edit tools; the competition harness provides them.",
      "Build or modify the competition harness.",
      "Create a separate evaluation system; Kaggle evaluates submissions."
    ],
    number,
    total
  });
};

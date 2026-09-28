window.WhatNotToDoSlide = function WhatNotToDoSlide({ number, total }) {
  return React.createElement(window.SlideLayout, {
    eyebrow: "Overview · What not to do",
    title: "Avoid unreliable shortcuts",
    points: [
      "Don’t rely on unsupported internet access at runtime.",
      "Don’t submit without checking the required format.",
      "Don’t assume a patch works without running tests."
    ],
    number,
    total
  });
};

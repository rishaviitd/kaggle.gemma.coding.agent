(() => {
  const h = React.createElement;

  window.SlideLayout = function SlideLayout({ eyebrow, title, points, number, total }) {
    return h("main", { className: "deck" },
      h("section", { className: "slide" },
        h("p", { className: "slide__eyebrow" }, eyebrow),
        h("h1", { className: "slide__title" }, title),
        h("hr", { className: "slide__rule" }),
        h("ol", { className: "slide__points" }, points.map((point) => h("li", { key: point }, point))),
        h("footer", { className: "slide__footer" },
          h("span", null, "GEMMA 4 · DEVELOPER AGENT"),
          h("span", null, `${number} / ${total}`)
        )
      )
    );
  };
})();

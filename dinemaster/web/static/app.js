// DineMaster: one shared tooltip for every chart mark that has data-tip, and the pivot's scroll position.
(() => {
  const tip = document.getElementById("tip");
  let shown = null;

  function place(x, y) {
    const pad = 12, w = tip.offsetWidth, h = tip.offsetHeight;
    let left = x + 14, top = y - h - 12;
    if (left + w > innerWidth - pad) left = x - w - 14;
    if (left < pad) left = pad;
    if (top < pad) top = y + 18;
    tip.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  }

  function show(el, x, y) {
    if (shown !== el) {
      const [head, ...rest] = el.dataset.tip.split("\n");
      tip.textContent = "";
      const b = document.createElement("b");
      b.textContent = head;
      tip.append(b, rest.length ? "\n" + rest.join("\n") : "");
      tip.hidden = false;
      shown = el;
    }
    place(x, y);
  }

  function hide() {
    tip.hidden = true;
    shown = null;
    document.querySelectorAll(".chart .on").forEach((el) => el.classList.remove("on"));
  }

  if (tip) {
    document.addEventListener("pointerover", (e) => {
      const el = e.target.closest ? e.target.closest("[data-tip]") : null;
      if (el) show(el, e.clientX, e.clientY);
      else if (shown && e.pointerType === "mouse") hide();
    });
    document.addEventListener("pointermove", (e) => {
      if (shown && e.pointerType === "mouse") place(e.clientX, e.clientY);
    });
    document.addEventListener("pointerdown", (e) => {
      const el = e.target.closest ? e.target.closest("[data-tip]") : null;
      if (el) show(el, e.clientX, e.clientY);
      else hide();
    });
    addEventListener("scroll", hide, { passive: true });
    document.addEventListener("mouseleave", hide);

    // Keyboard: focus a chart, then the arrow keys step through its marks.
    document.addEventListener("keydown", (e) => {
      const fig = e.target.closest ? e.target.closest("figure.chart") : null;
      if (!fig) return;
      if (e.key === "Escape") return hide();
      const step = { ArrowRight: 1, ArrowLeft: -1, Home: "first", End: "last" }[e.key];
      if (step === undefined) return;
      const svg = [...fig.querySelectorAll("svg")].find((s) => getComputedStyle(s).display !== "none");
      const marks = svg ? [...svg.querySelectorAll("[data-tip]")] : [];
      if (!marks.length) return;
      e.preventDefault();
      let i = marks.findIndex((m) => m.classList.contains("on"));
      if (step === "first") i = 0;
      else if (step === "last") i = marks.length - 1;
      else i = i < 0 ? (step > 0 ? 0 : marks.length - 1) : Math.min(marks.length - 1, Math.max(0, i + step));
      hide();
      marks[i].classList.add("on");
      const box = marks[i].getBoundingClientRect();
      show(marks[i], box.left + box.width / 2, box.top + Math.min(box.height / 2, 40));
    });
    document.addEventListener("focusout", (e) => {
      if (e.target.closest && e.target.closest("figure.chart")) hide();
    });
  }

  // A range input with class "slider" paints its filled part from --fill.
  document.querySelectorAll("input.slider").forEach((input) => {
    const paint = () => {
      const min = Number(input.min || 0), max = Number(input.max || 100);
      input.style.setProperty("--fill", `${((Number(input.value) - min) / (max - min || 1)) * 100}%`);
    };
    input.addEventListener("input", paint);
    paint();
  });

  // Keep the current pivot item in view when the pivot scrolls sideways.
  const pivot = document.querySelector(".pivot");
  const current = pivot && pivot.querySelector("[aria-current]");
  if (current) pivot.scrollLeft = Math.max(0, current.offsetLeft - pivot.offsetLeft - 16);
})();

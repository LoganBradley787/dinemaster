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
  function paintSliders(root) {
    root.querySelectorAll("input.slider").forEach((input) => {
      const paint = () => {
        const min = Number(input.min || 0), max = Number(input.max || 100);
        input.style.setProperty("--fill", `${((Number(input.value) - min) / (max - min || 1)) * 100}%`);
      };
      input.addEventListener("input", paint);
      paint();
    });
  }
  paintSliders(document);

  // Choices inside a page (a toggle, a picker, a small GET form) change only the query string.
  // Fetch that version and swap it in where you are, so the page does not reload and jump to the top.
  // Without this script the same links and forms still work as ordinary page loads.
  const main = document.getElementById("main");
  let swapping = 0;

  function samePage(url) {
    return url.origin === location.origin && url.pathname === location.pathname && url.search !== location.search;
  }

  // The control you used, found again in the new page: same id, or the same place among its kind.
  function locate(el) {
    if (el.id) return { id: el.id };
    const kind = el.matches("a") ? "a" : el.tagName.toLowerCase();
    const group = el.closest(".seg, form, .part, section") || main;
    const groups = [...main.querySelectorAll(".seg, form, .part, section")];
    return { kind, group: groups.indexOf(group), index: [...group.querySelectorAll(kind)].indexOf(el) };
  }

  function find(where) {
    if (where.id) return document.getElementById(where.id);
    const group = where.group < 0 ? main : [...main.querySelectorAll(".seg, form, .part, section")][where.group];
    return group ? [...group.querySelectorAll(where.kind)][where.index] || null : null;
  }

  async function swapTo(url, from) {
    const turn = ++swapping;
    const where = from ? locate(from) : null;
    const before = from ? from.getBoundingClientRect().top : 0;
    try {
      const res = await fetch(url, { headers: { "X-Requested-With": "fetch" } });
      if (!res.ok) throw new Error(res.status);
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      const next = doc.getElementById("main");
      if (!next) throw new Error("no main");
      if (turn !== swapping) return; // a later choice already won
      hide();
      main.classList.add("swapped"); // no entry animation the second time
      main.replaceChildren(...next.childNodes);
      for (const part of [".appbar-top", ".pivot"]) {
        const fresh = doc.querySelector(part), old = document.querySelector(part);
        if (fresh && old) old.replaceChildren(...fresh.childNodes);
      }
      document.title = doc.title;
      history.replaceState(null, "", url);
      // Scripts that arrive inside the new content do not run on their own.
      main.querySelectorAll("script").forEach((old) => {
        const script = document.createElement("script");
        script.textContent = old.textContent;
        old.replaceWith(script);
      });
      paintSliders(main);
      followFetch();
      const again = where && find(where);
      if (again) {
        scrollBy(0, again.getBoundingClientRect().top - before);
        if (again.focus) again.focus({ preventScroll: true });
      }
    } catch (e) {
      location.href = url; // fall back to an ordinary page load
    }
  }

  if (main && window.fetch && window.DOMParser && history.replaceState) {
    main.addEventListener("click", (e) => {
      if (e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const link = e.target.closest ? e.target.closest("a[href]") : null;
      if (!link || link.target || link.hasAttribute("download")) return;
      const url = new URL(link.href);
      if (!samePage(url)) return; // a link that only jumps to a heading has the same query and is left alone
      e.preventDefault();
      swapTo(url, link);
    });
    main.addEventListener("submit", (e) => {
      const form = e.target;
      if (e.defaultPrevented || (form.method || "get").toLowerCase() !== "get") return;
      const url = new URL(form.action || location.href);
      url.search = new URLSearchParams(new FormData(form)).toString();
      if (url.origin !== location.origin || url.pathname !== location.pathname) return;
      e.preventDefault();
      swapTo(url, document.activeElement && form.contains(document.activeElement) ? document.activeElement : form);
    });
  }

  // While a fetch runs (the button's, or the one the app starts by itself when it opens): ask how it is
  // going every two seconds, show its lines where the page lists them, and reload when it ends.
  // A page swapped in by a choice (below) may show a fetch that started meanwhile, so it calls followFetch too.
  let following = false;
  function followFetch() {
    if (following || !document.querySelector("[data-fetch]")) return;
    following = true;
    setTimeout(follow, 2000);
  }
  async function follow() {
    if (!document.querySelector("[data-fetch]")) return (following = false); // a swapped-in page with no fetch running
    try {
      const s = await (await fetch("/data/fetch-status", { cache: "no-store" })).json();
      if (s.state !== "running") return location.reload();
      const list = document.querySelector(".fetch-lines");
      if (list) {
        list.textContent = "";
        for (const line of s.lines) {
          const li = document.createElement("li");
          li.textContent = line;
          list.append(li);
        }
      }
    } catch (e) { /* the server may be busy; ask again */ }
    setTimeout(follow, 2000);
  }
  followFetch();

  // Keep the current pivot item in view when the pivot scrolls sideways.
  const pivot = document.querySelector(".pivot");
  const current = pivot && pivot.querySelector("[aria-current]");
  if (current) pivot.scrollLeft = Math.max(0, current.offsetLeft - pivot.offsetLeft - 16);
})();

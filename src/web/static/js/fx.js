// Visual effects runtime: effect mode (full / subtle / off), number ticker, spotlight cards, particle network
// background, cover-art accent colour. Decorative only: honours prefers-reduced-motion, pauses while the
// window is hidden and never touches layout. Styles live in /static/fx.css.
"use strict";

const FX = (function () {
  const reduced = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false, addEventListener() {} };
  const MODES = ["full", "subtle", "off"];

  function preferred() {
    const m = UI.store.get("fx", "subtle");
    return MODES.includes(m) ? m : "subtle";
  }
  // The OS "reduce motion" setting always wins over the chosen mode.
  function mode() { return reduced.matches ? "off" : preferred(); }

  // ---------------------------------------------------------------- particle network (canvas)
  const networks = new Set();

  function network(canvas) {
    const ctx = canvas.getContext("2d");
    const pts = [];
    const mouse = { x: -1e3, y: -1e3 };
    let w = 0, h = 0, raf = 0, last = 0, running = false;

    function resize() {
      const r = canvas.getBoundingClientRect();
      w = Math.max(1, Math.round(r.width)); h = Math.max(1, Math.round(r.height));
      canvas.width = w; canvas.height = h;                 // DPR 1: this is ambient, not a chart
      const target = Math.min(46, Math.round((w * h) / 9000));
      while (pts.length < target) pts.push({ x: Math.random() * w, y: Math.random() * h, vx: (Math.random() - .5) * .35, vy: (Math.random() - .5) * .35 });
      pts.length = Math.min(pts.length, target);
    }

    function accent() {
      const raw = getComputedStyle(document.documentElement).getPropertyValue("--np").trim().split(/\s+/).join(",");
      return raw || "29,185,84";
    }

    function frame(t) {
      if (!running) return;
      raf = requestAnimationFrame(frame);
      if (t - last < 33) return;                           // ~30fps is plenty
      last = t;
      if (canvas.offsetParent === null) return;            // tab not visible: skip drawing
      ctx.clearRect(0, 0, w, h);
      const rgb = accent();
      for (const p of pts) {
        p.x += p.vx; p.y += p.vy;
        if (p.x < 0 || p.x > w) p.vx *= -1;
        if (p.y < 0 || p.y > h) p.vy *= -1;
      }
      for (let i = 0; i < pts.length; i++) {
        const a = pts[i];
        ctx.fillStyle = `rgba(${rgb},.75)`;
        ctx.beginPath(); ctx.arc(a.x, a.y, 1.4, 0, 6.283); ctx.fill();
        for (let j = i + 1; j < pts.length; j++) {
          const b = pts[j], d = Math.hypot(a.x - b.x, a.y - b.y);
          if (d < 110) { ctx.strokeStyle = `rgba(${rgb},${(1 - d / 110) * .35})`; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
        }
        const dm = Math.hypot(a.x - mouse.x, a.y - mouse.y);
        if (dm < 150) { ctx.strokeStyle = `rgba(${rgb},${(1 - dm / 150) * .6})`; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(mouse.x, mouse.y); ctx.stroke(); }
      }
    }

    const ctrl = {
      start() { if (running) return; running = true; resize(); raf = requestAnimationFrame(frame); },
      stop() { running = false; cancelAnimationFrame(raf); ctx.clearRect(0, 0, w, h); },
      isRunning: () => running
    };
    const host = canvas.parentElement;
    host.addEventListener("pointermove", (e) => { const r = canvas.getBoundingClientRect(); mouse.x = e.clientX - r.left; mouse.y = e.clientY - r.top; });
    host.addEventListener("pointerleave", () => { mouse.x = mouse.y = -1e3; });
    if (window.ResizeObserver) new ResizeObserver(() => { if (running) resize(); }).observe(host);
    networks.add(ctrl);
    return ctrl;
  }

  // ---------------------------------------------------------------- mode
  let netControllers = [];

  function apply() {
    const m = mode();
    document.body.classList.remove("fx-full", "fx-subtle", "fx-off");
    document.body.classList.add(`fx-${m}`);
    document.body.dataset.fx = m;
    document.querySelectorAll('input[name="fx"]').forEach((r) => { r.checked = (r.value === preferred()); });
    const hint = document.getElementById("fx-reduced-hint");
    if (hint) hint.classList.toggle("hidden", !reduced.matches);
    netControllers.forEach((c) => (m === "full" ? c.start() : c.stop()));
  }

  function setMode(m) {
    if (!MODES.includes(m)) return;
    UI.store.set("fx", m);
    apply();
  }

  // ---------------------------------------------------------------- number ticker
  const running = new WeakMap();

  function tick(el, to, duration = 700) {
    if (!el) return;
    to = Number(to);
    const from = Number(el.dataset.value);
    el.dataset.value = String(to);
    if (!isFinite(to)) { el.textContent = String(to); return; }
    cancelAnimationFrame(running.get(el));
    if (!isFinite(from) || from === to || mode() === "off" || document.hidden || el.offsetParent === null) { el.textContent = String(to); return; }
    const t0 = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - t0) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = String(Math.round(from + (to - from) * eased));
      if (p < 1) running.set(el, requestAnimationFrame(step)); else el.textContent = String(to);
    };
    running.set(el, requestAnimationFrame(step));
  }

  function setNumber(id, value) { tick(document.getElementById(id), value); }

  // ---------------------------------------------------------------- spotlight
  function initSpotlight() {
    let frame = 0, ev = null;
    document.addEventListener("pointermove", (e) => {
      if (document.body.classList.contains("fx-off")) return;
      ev = e;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const card = ev.target.closest && ev.target.closest(".fx-card");
        if (!card) return;
        const r = card.getBoundingClientRect();
        card.style.setProperty("--mx", `${ev.clientX - r.left}px`);
        card.style.setProperty("--my", `${ev.clientY - r.top}px`);
      });
    }, { passive: true });
  }

  // ---------------------------------------------------------------- cover-art accent colour
  const accentCache = new Map();

  function setAccent(rgb) {
    document.documentElement.style.setProperty("--np", rgb);
  }

  function resetAccent() {
    document.documentElement.style.removeProperty("--np");
  }

  // Average the saturated pixels of the cover, then lift it so it stays vivid on a dark UI.
  function coverAccent(url) {
    if (!url) { resetAccent(); return Promise.resolve(null); }
    if (accentCache.has(url)) { const c = accentCache.get(url); c ? setAccent(c) : resetAccent(); return Promise.resolve(c); }
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        try {
          const c = document.createElement("canvas");
          c.width = c.height = 24;
          const g = c.getContext("2d", { willReadFrequently: true });
          g.drawImage(img, 0, 0, 24, 24);
          const d = g.getImageData(0, 0, 24, 24).data;
          let r = 0, gr = 0, b = 0, wsum = 0;
          for (let i = 0; i < d.length; i += 4) {
            const mx = Math.max(d[i], d[i + 1], d[i + 2]), mn = Math.min(d[i], d[i + 1], d[i + 2]);
            const sat = mx === 0 ? 0 : (mx - mn) / mx;
            const wgt = sat * sat + 0.02;
            r += d[i] * wgt; gr += d[i + 1] * wgt; b += d[i + 2] * wgt; wsum += wgt;
          }
          let rgb = [r / wsum, gr / wsum, b / wsum];
          const lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255;
          if (lum < 0.45) { const k = 0.45 / Math.max(lum, 0.05); rgb = rgb.map((v) => Math.min(255, v * Math.min(k, 3))); }
          const out = rgb.map((v) => Math.round(v)).join(" ");
          accentCache.set(url, out);
          setAccent(out);
          resolve(out);
        } catch (_) { accentCache.set(url, null); resetAccent(); resolve(null); }
      };
      img.onerror = () => { accentCache.set(url, null); resetAccent(); resolve(null); };
      img.src = url;
    });
  }

  // ---------------------------------------------------------------- init
  function init() {
    document.addEventListener("visibilitychange", () => document.body.classList.toggle("is-hidden", document.hidden));
    reduced.addEventListener("change", apply);
    netControllers = Array.from(document.querySelectorAll("canvas.fx-net")).map(network);
    initSpotlight();
    apply();
  }

  return { mode, preferred, setMode, apply, tick, setNumber, coverAccent, resetAccent, init };
})();

function setFxChoice(value) {
  FX.setMode(value);
  UI.toast(`Visual effects: ${{ full: "Full", subtle: "Subtle", off: "Off" }[value]}`, "info", 1500);
}

document.addEventListener("DOMContentLoaded", () => FX.init());

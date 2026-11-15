// 공용 색 선택기. 어느 화면에서든 쓸 수 있다. 스타일은 style.css의 .cp-*.
//
// 쓰는 법: const cp = ColorPicker.attach(input, { value: "#151822", fallback: "#151822", allowEmpty: true, onChange: (v) => … })
//   → input을 감싼 요소(cp.el)를 화면에 넣는다. cp.setValue(v), cp.getValue()
//   value는 '#rrggbb' 또는 ''(비움 = 기본값 fallback을 쓴다는 뜻, allowEmpty일 때만).
//
// 입력 칸에 #으로 시작하는 색 코드를 바로 쳐도 되고(#abc, #a1b2c3, # 없이 a1b2c3도 됨), 왼쪽 색 견본을 누르면
// 색 영역(채도·밝기)과 색상 막대, 자주 쓰는 색, 스포이드(지원하는 브라우저)로 고르는 창이 열린다. 고르는 즉시 반영된다.
(() => {
  const PRESETS = ["#000000", "#151822", "#292e41", "#475067", "#67738e", "#8491a7", "#a3abbd", "#d7dce5",
    "#003fe5", "#2f6fed", "#0a3d91", "#0e7490", "#0f766e", "#15803d", "#b45309", "#b91c1c"];

  // '#abc' · 'a1b2c3' · '#A1B2C3' → '#a1b2c3'. 색 코드가 아니면 null
  function norm(v) {
    const m = String(v ?? "").trim().toLowerCase().match(/^#?([0-9a-f]{3}|[0-9a-f]{6})$/);
    if (!m) return null;
    return "#" + (m[1].length === 6 ? m[1] : [...m[1]].map((c) => c + c).join(""));
  }
  const toRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const toHex = (rgb) => "#" + rgb.map((x) => Math.round(x).toString(16).padStart(2, "0")).join("");
  function rgbToHsv([r, g, b]) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), d = max - Math.min(r, g, b);
    let hue = 0;
    if (d) hue = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return { h: (hue * 60 + 360) % 360, s: max ? d / max : 0, v: max };
  }
  function hsvToRgb({ h, s, v }) {
    const f = (n) => { const k = (n + h / 60) % 6; return (v - v * s * Math.max(0, Math.min(k, 4 - k, 1))) * 255; };
    return [f(5), f(3), f(1)];
  }
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return el;
  }

  let openOne = null; // 한 번에 하나만 연다

  function attach(input, opts = {}) {
    const allowEmpty = !!opts.allowEmpty;
    const fallback = norm(opts.fallback) || "#000000";
    let value = norm(opts.value) || (allowEmpty ? "" : fallback);
    const onChange = opts.onChange || (() => {});
    const label = opts.label || "색";

    input.classList.add("cp-input");
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.maxLength = 7;
    input.placeholder = allowEmpty ? `기본 ${fallback}` : "#000000";
    input.setAttribute("aria-label", `${label} 색 코드 (#으로 시작)`);
    const chip = h("span", { class: "cp-chip" });
    const swatch = h("button", { type: "button", class: "cp-swatch", "aria-haspopup": "dialog", "aria-label": `${label} 색 고르기`, title: "눌러서 색 고르기" }, chip);
    const clear = allowEmpty ? h("button", { type: "button", class: "cp-clear", "aria-label": `${label} 기본값으로`, title: "기본값으로" }, "×") : null;
    const wrap = h("div", { class: "cp-field" });
    input.replaceWith?.(wrap);
    wrap.append(swatch, input);
    if (clear) wrap.append(clear);

    const effective = () => value || fallback;
    function paint() {
      chip.style.background = effective();
      wrap.classList.toggle("is-default", !value);
      if (clear) clear.hidden = !value;
    }
    // 값 확정 (keepText: 입력 칸에 치는 중이면 글자를 덮어쓰지 않는다)
    function commit(v, { keepText = false, fromHsv = false } = {}) {
      value = v;
      if (!keepText) input.value = v;
      wrap.classList.remove("is-invalid");
      input.removeAttribute("aria-invalid");
      paint();
      if (pop) syncPop(fromHsv);
      onChange(v);
    }
    function typed(text, { final = false } = {}) {
      const t = text.trim();
      if (!t && allowEmpty) return commit("", { keepText: !final });
      const v = norm(t);
      if (v) return commit(v, { keepText: !final });
      wrap.classList.add("is-invalid");
      input.setAttribute("aria-invalid", "true");
      input.title = "예) #1a2b3c 또는 #abc";
    }
    input.addEventListener("input", () => typed(input.value));
    input.addEventListener("change", () => typed(input.value, { final: true }));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); typed(input.value, { final: true }); }
      else if (e.key === "ArrowDown" || (e.key === "Enter" && e.altKey)) { e.preventDefault(); open(); }
    });
    clear?.addEventListener("click", () => { commit(""); input.focus(); });

    // ---------------- 고르는 창
    let pop = null, hsv = null, hexEl = null, svEl = null, svThumb = null, hueEl = null, hueThumb = null, presetBox = null;

    function syncPop(keepHsv = false) {
      if (!pop) return;
      const cur = effective();
      if (!keepHsv || !hsv) {
        const next = rgbToHsv(toRgb(cur));
        if (hsv && (next.s === 0 || next.v === 0)) next.h = hsv.h; // 회색·검정은 색상을 잃지 않게
        hsv = next;
      }
      svEl.style.backgroundColor = `hsl(${hsv.h} 100% 50%)`;
      svThumb.style.left = `${hsv.s * 100}%`;
      svThumb.style.top = `${(1 - hsv.v) * 100}%`;
      svThumb.style.background = cur;
      svThumb.setAttribute("aria-valuetext", `채도 ${Math.round(hsv.s * 100)}%, 밝기 ${Math.round(hsv.v * 100)}%`);
      hueThumb.style.left = `${(hsv.h / 360) * 100}%`;
      hueThumb.setAttribute("aria-valuenow", String(Math.round(hsv.h)));
      if (document.activeElement !== hexEl) hexEl.value = value || "";
      hexEl.placeholder = allowEmpty ? `기본 ${fallback}` : "#000000";
      for (const b of presetBox.children) b.setAttribute("aria-pressed", String(b.dataset.color === value));
    }
    // 끌기: 색 영역은 채도(가로)·밝기(세로), 색상 막대는 가로
    function dragger(el, onPoint) {
      el.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        el.setPointerCapture(e.pointerId);
        const move = (ev) => {
          const r = el.getBoundingClientRect();
          onPoint(clamp((ev.clientX - r.left) / r.width, 0, 1), clamp((ev.clientY - r.top) / r.height, 0, 1));
        };
        move(e);
        const up = () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); el.removeEventListener("pointercancel", up); };
        el.addEventListener("pointermove", move);
        el.addEventListener("pointerup", up);
        el.addEventListener("pointercancel", up);
        el.querySelector("[tabindex]")?.focus({ preventScroll: true });
      });
    }
    function setHsv(part) {
      hsv = { ...hsv, ...part };
      commit(toHex(hsvToRgb(hsv)), { fromHsv: true });
    }

    function place() {
      if (!pop) return;
      if (window.matchMedia("(max-width: 600px)").matches) { pop.style.left = pop.style.top = ""; return; } // 휴대폰: 아래에서 올라오는 시트
      const r = wrap.getBoundingClientRect();
      const w = pop.offsetWidth, ph = pop.offsetHeight;
      const vw = document.documentElement.clientWidth, vh = window.innerHeight;
      const left = clamp(r.left, 8, vw - w - 8);
      const top = r.bottom + 6 + ph > vh - 8 && r.top - 6 - ph > 8 ? r.top - 6 - ph : r.bottom + 6;
      pop.style.left = `${left}px`;
      pop.style.top = `${top}px`;
    }

    function open() {
      if (pop) return;
      if (openOne && openOne !== api) openOne.close();
      openOne = api;
      hsv = null;
      svThumb = h("span", { class: "cp-sv-thumb", tabindex: "0", role: "slider", "aria-label": "채도·밝기 (방향키)" });
      svEl = h("div", { class: "cp-sv" }, svThumb);
      hueThumb = h("span", { class: "cp-hue-thumb", tabindex: "0", role: "slider", "aria-label": "색상", "aria-valuemin": "0", "aria-valuemax": "360" });
      hueEl = h("div", { class: "cp-hue" }, hueThumb);
      dragger(svEl, (x, y) => setHsv({ s: x, v: 1 - y }));
      dragger(hueEl, (x) => setHsv({ h: Math.min(x * 360, 359.9) }));
      svThumb.addEventListener("keydown", (e) => {
        const d = e.shiftKey ? 0.1 : 0.01;
        const step = { ArrowLeft: [-d, 0], ArrowRight: [d, 0], ArrowUp: [0, d], ArrowDown: [0, -d] }[e.key];
        if (!step) return;
        e.preventDefault();
        setHsv({ s: clamp(hsv.s + step[0], 0, 1), v: clamp(hsv.v + step[1], 0, 1) });
      });
      hueThumb.addEventListener("keydown", (e) => {
        const d = e.shiftKey ? 10 : 1;
        const step = { ArrowLeft: -d, ArrowDown: -d, ArrowRight: d, ArrowUp: d }[e.key];
        if (!step) return;
        e.preventDefault();
        setHsv({ h: (hsv.h + step + 360) % 360 });
      });
      hexEl = h("input", { type: "text", class: "cp-hex", maxlength: "7", spellcheck: "false", autocomplete: "off", "aria-label": "색 코드 (#으로 시작)" });
      hexEl.addEventListener("input", () => {
        const t = hexEl.value.trim();
        if (!t && allowEmpty) return commit("");
        const v = norm(t);
        hexEl.classList.toggle("is-invalid", !v);
        if (v) commit(v);
      });
      hexEl.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); close(true); } });
      presetBox = h("div", { class: "cp-presets", role: "group", "aria-label": "자주 쓰는 색" },
        PRESETS.map((c) => h("button", { type: "button", class: "cp-preset", "data-color": c, style: `background:${c}`, title: c, "aria-label": c,
          onclick: () => commit(c) })));
      const eyedrop = "EyeDropper" in window ? h("button", { type: "button", class: "cp-link", onclick: async () => {
        try { const r = await new window.EyeDropper().open(); const v = norm(r.sRGBHex); if (v) commit(v); } catch { /* 취소 */ }
      } }, "화면에서 고르기") : null;
      pop = h("div", { class: "cp-pop", role: "dialog", "aria-label": `${label} 색 고르기` },
        svEl, hueEl,
        h("div", { class: "cp-row" }, h("span", { class: "cp-hash", "aria-hidden": "true" }, "코드"), hexEl),
        presetBox,
        h("div", { class: "cp-foot" },
          allowEmpty ? h("button", { type: "button", class: "cp-link", onclick: () => commit("") }, "기본값으로") : null,
          eyedrop,
          h("button", { type: "button", class: "cp-link is-strong", onclick: () => close(true) }, "완료")));
      pop.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); close(true); } });
      const back = h("div", { class: "cp-back", onpointerdown: () => close() });
      document.body.append(back, pop);
      pop._back = back;
      wrap.classList.add("is-open");
      syncPop();
      place();
      window.addEventListener("resize", place);
      window.addEventListener("scroll", place, true);
      svThumb.focus({ preventScroll: true });
    }
    function close(focusSwatch = false) {
      if (!pop) return;
      pop._back.remove();
      pop.remove();
      pop = null;
      wrap.classList.remove("is-open");
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      if (openOne === api) openOne = null;
      if (focusSwatch) swatch.focus();
    }
    swatch.addEventListener("click", () => (pop ? close(true) : open()));

    input.value = value;
    paint();
    const api = {
      el: wrap, open, close,
      getValue: () => value,
      setValue: (v) => { value = norm(v) || (allowEmpty ? "" : fallback); input.value = value; paint(); syncPop(); },
    };
    return api;
  }

  window.ColorPicker = { attach, norm };
})();

// 공용 날짜 선택기 (연월·날짜). 어느 화면에서든 쓸 수 있다. 스타일은 style.css의 .dp-*.
//
// 쓰는 법
//   1) 스크립트로: const dp = DatePicker.attach(input, { mode: "month" | "date", value: "2021-08", onChange: (v) => …,
//                                                      startView: "years", max: "2026-11-14", min: "1900-01-01" });
//      → input을 감싼 요소(dp.el)를 화면에 넣는다. dp.setValue(v), dp.getValue()
//   2) HTML로: <input name="start" data-datepicker="month" value="2021-08"> — 화면이 열릴 때 자동으로 붙고,
//      값(YYYY-MM / YYYY-MM-DD)은 숨은 input(같은 name)으로 폼과 함께 보내진다. data-dp-max, data-dp-min, data-dp-start-view도 쓸 수 있다.
//
//   3) 기간(시작~끝): const r = DatePicker.attachRange({ mode, value: "2021-08~2024-02", allowSame: true, openEnd: true, onChange })
//      → r.el(시작·끝 두 칸), r.validate()(문제가 있으면 안내 글, 없으면 ""), r.start / r.end (각 칸의 attach 결과)
//      시작을 고르면 끝의 달력은 시작 이후만, 끝을 고르면 시작은 끝 이전만 고를 수 있다. 직접 친 값은 validate()가 순서를 검사한다.
//      openEnd면 끝을 비워 '현재'(진행 중)로 둘 수 있다.
//
// 값은 YYYY-MM(연월) / YYYY-MM-DD(날짜)로 주고받고, 화면에는 2021.08 / 2021.08.15로 보인다.
// 입력 칸에 직접 쳐도 된다 (2021.8, 2021-08, 202108, 2021.08.15, 20210815 등). 달력은 달력 버튼·칸 클릭·↓ 키로 열고,
// 방향키로 움직이고 Enter로 고르고 Esc로 닫는다. 제목(2026년 11월)을 누르면 월 → 연도 단위로 넓혀 빨리 고를 수 있다.
(() => {
  const WEEK = ["일", "월", "화", "수", "목", "금", "토"];
  const pad = (n) => String(n).padStart(2, "0");
  const todayParts = () => { const d = new Date(); return { y: d.getFullYear(), m: d.getMonth() + 1, d: d.getDate() }; };
  const daysIn = (y, m) => new Date(y, m, 0).getDate();
  const iso = (mode, y, m, d) => (mode === "month" ? `${y}-${pad(m)}` : `${y}-${pad(m)}-${pad(d)}`);
  const shown = (v) => (v ? v.replaceAll("-", ".") : "");

  // 사람이 친 글자 → YYYY-MM(-DD). 알아볼 수 없으면 null
  function parse(text, mode) {
    const t = String(text || "").trim();
    if (!t) return "";
    let m = t.match(/^(\d{4})\D*(\d{1,2})(?:\D*(\d{1,2}))?\D*$/) || t.match(/^(\d{4})(\d{2})(\d{2})?$/);
    if (!m) return null;
    const y = +m[1], mo = +m[2], d = m[3] ? +m[3] : 1;
    if (y < 1900 || y > 2100 || mo < 1 || mo > 12 || d < 1 || d > daysIn(y, mo)) return null;
    if (mode === "date" && !m[3]) return null;
    return iso(mode, y, mo, d);
  }
  function split(v) {
    const m = String(v || "").match(/^(\d{4})-(\d{2})(?:-(\d{2}))?$/);
    return m ? { y: +m[1], m: +m[2], d: m[3] ? +m[3] : 1 } : null;
  }
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

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
  const ICON = '<svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><rect x="3" y="4.5" width="14" height="12.5" rx="2"/><path d="M3 8.5h14M7 2.8v3.4M13 2.8v3.4"/></svg>';

  let openPicker = null; // 한 번에 하나만 연다

  function attach(input, opts = {}) {
    const mode = opts.mode === "date" ? "date" : "month";
    let value = parse(opts.value, mode) || (split(opts.value) ? opts.value : "");
    const onChange = opts.onChange || (() => {});
    let min = opts.min || "", max = opts.max || "";
    const strict = opts.strictTyping !== false; // false: 직접 친 값은 범위 밖이어도 받는다 (기간 입력은 validate()가 알려 줌)
    const inRange = (v) => (!min || cmp(v, min.slice(0, v.length)) >= 0) && (!max || cmp(v, max.slice(0, v.length)) <= 0);

    input.classList.add("dp-input");
    input.type = "text";
    input.autocomplete = "off";
    input.inputMode = "numeric";
    if (!input.placeholder) input.placeholder = mode === "month" ? "YYYY.MM" : "YYYY.MM.DD";
    input.setAttribute("aria-haspopup", "dialog");
    input.value = shown(value);
    const toggle = h("button", { type: "button", class: "dp-toggle", "aria-label": "달력 열기", title: "달력에서 고르기" });
    toggle.innerHTML = ICON;
    const wrap = h("div", { class: "dp-field" });
    input.replaceWith?.(wrap);
    wrap.append(input, toggle);

    // ---------------- 값 확정
    function commit(v, { focus = false } = {}) {
      value = v;
      input.value = shown(v);
      wrap.classList.remove("is-invalid");
      onChange(v);
      if (focus) input.focus();
    }
    input.addEventListener("input", () => wrap.classList.remove("is-invalid"));
    input.addEventListener("change", () => {
      const v = parse(input.value, mode);
      if (v === null || (v && strict && !inRange(v))) {
        wrap.classList.add("is-invalid");
        input.title = mode === "month" ? "예) 2021.08" : "예) 2021.08.15";
        return;
      }
      commit(v);
    });

    // ---------------- 달력
    let pop = null, view = "days", cur = null, keyListener = null;
    const today = todayParts();

    function open(startView) {
      if (openPicker && openPicker !== api) openPicker.close();
      openPicker = api;
      const base = split(parse(input.value, mode) || value) || null;
      cur = base ? { ...base } : { ...today };
      // 값이 없고 오늘이 고를 수 있는 범위 밖이면 범위의 첫(끝) 달에서 연다 (고를 날짜가 하나도 없는 달이 보이지 않게)
      if (!base) {
        const t = iso("date", today.y, today.m, today.d);
        if (min && cmp(t.slice(0, min.length), min) < 0) cur = { ...split(min) };
        else if (max && cmp(t.slice(0, max.length), max) > 0) cur = { ...split(max) };
      }
      view = mode === "month" ? "months" : "days";
      if (startView === "years" || (!base && opts.startView === "years")) view = "years";
      if (!base && opts.startView === "years" && opts.startYear) cur.y = opts.startYear;
      pop = h("div", { class: "dp-pop", role: "dialog", "aria-label": mode === "month" ? "연월 선택" : "날짜 선택" });
      wrap.append(pop);
      wrap.classList.add("is-open");
      document.documentElement.classList.add("dp-open-doc");
      draw(true);
      if (pop.getBoundingClientRect().right > document.documentElement.clientWidth - 8) pop.classList.add("is-right"); // 오른쪽 끝 칸이면 왼쪽으로 펼침
      setTimeout(() => document.addEventListener("pointerdown", outside), 0);
      keyListener = (e) => keydown(e);
      pop.addEventListener("keydown", keyListener);
    }
    function close(focusInput = false) {
      if (!pop) return;
      pop.remove();
      pop = null;
      wrap.classList.remove("is-open");
      document.documentElement.classList.remove("dp-open-doc");
      document.removeEventListener("pointerdown", outside);
      if (openPicker === api) openPicker = null;
      if (focusInput) input.focus();
    }
    function outside(e) {
      if (pop && !wrap.contains(e.target)) close();
    }

    function headerTitle() {
      if (view === "days") return `${cur.y}년 ${cur.m}월`;
      if (view === "months") return `${cur.y}년`;
      const start = cur.y - (cur.y % 12);
      return `${start} – ${start + 11}`;
    }
    function shift(dir) {
      if (view === "days") {
        cur.m += dir;
        if (cur.m < 1) { cur.m = 12; cur.y -= 1; }
        if (cur.m > 12) { cur.m = 1; cur.y += 1; }
      } else cur.y += dir * (view === "months" ? 1 : 12);
      cur.d = Math.min(cur.d, daysIn(cur.y, cur.m));
      draw(true);
    }
    function zoomOut() {
      if (view === "days") view = "months";
      else if (view === "months") view = "years";
      draw(true);
    }

    function draw(focusSelected) {
      const sel = split(value);
      const grid = h("div", { class: `dp-grid is-${view}`, role: "grid" });
      const cell = (label, { selected, today: isToday, outside: out, disabled, onpick, key, aria }) =>
        h("button", {
          type: "button", class: `dp-cell${selected ? " is-selected" : ""}${isToday ? " is-today" : ""}${out ? " is-outside" : ""}`,
          disabled, "data-key": key, "aria-label": aria || label, "aria-pressed": selected ? "true" : "false", tabindex: "-1",
          onclick: onpick,
        }, label);
      if (view === "days") {
        WEEK.forEach((w, i) => grid.append(h("span", { class: `dp-week${i === 0 ? " is-sun" : i === 6 ? " is-sat" : ""}`, text: w, "aria-hidden": "true" })));
        const first = new Date(cur.y, cur.m - 1, 1).getDay();
        const prevDays = daysIn(cur.y, cur.m - 1 || 12);
        for (let i = 0; i < 42; i++) {
          let y = cur.y, m = cur.m, d = i - first + 1, out = false;
          if (d < 1) { m -= 1; if (m < 1) { m = 12; y -= 1; } d += prevDays; out = true; }
          else if (d > daysIn(cur.y, cur.m)) { d -= daysIn(cur.y, cur.m); m += 1; if (m > 12) { m = 1; y += 1; } out = true; }
          const v = iso("date", y, m, d);
          const dow = i % 7;
          const btn = cell(String(d), {
            selected: sel && sel.y === y && sel.m === m && sel.d === d, today: today.y === y && today.m === m && today.d === d,
            outside: out, disabled: !inRange(v), key: v, aria: `${y}년 ${m}월 ${d}일`,
            onpick: () => { commit(v); close(true); },
          });
          if (dow === 0) btn.classList.add("is-sun");
          if (dow === 6) btn.classList.add("is-sat");
          grid.append(btn);
        }
      } else if (view === "months") {
        for (let m = 1; m <= 12; m++) {
          const v = iso("month", cur.y, m);
          grid.append(cell(`${m}월`, {
            selected: sel && sel.y === cur.y && sel.m === m, today: today.y === cur.y && today.m === m,
            disabled: !inRange(v), key: v, aria: `${cur.y}년 ${m}월`,
            onpick: () => {
              if (mode === "month") { commit(v); close(true); return; }
              cur.m = m; cur.d = Math.min(cur.d, daysIn(cur.y, m)); view = "days"; draw(true);
            },
          }));
        }
      } else {
        const start = cur.y - (cur.y % 12);
        for (let y = start; y < start + 12; y++) {
          grid.append(cell(`${y}`, {
            selected: sel && sel.y === y, today: today.y === y,
            disabled: (min && y < +min.slice(0, 4)) || (max && y > +max.slice(0, 4)), key: String(y),
            onpick: () => { cur.y = y; view = "months"; draw(true); },
          }));
        }
      }
      const isNow = mode === "month" ? "이번 달" : "오늘";
      const nowValue = iso(mode, today.y, today.m, today.d);
      pop.replaceChildren(
        h("div", { class: "dp-head" },
          h("button", { type: "button", class: "dp-nav", "aria-label": "이전", onclick: () => shift(-1) }, "‹"),
          h("button", { type: "button", class: "dp-title", disabled: view === "years", onclick: zoomOut, "aria-label": `${headerTitle()} (눌러서 넓게 보기)` },
            headerTitle(), view !== "years" ? h("span", { class: "dp-caret", "aria-hidden": "true" }, "▾") : null),
          h("button", { type: "button", class: "dp-nav", "aria-label": "다음", onclick: () => shift(1) }, "›")),
        grid,
        h("div", { class: "dp-foot" },
          h("button", { type: "button", class: "dp-link", onclick: () => { commit(""); close(true); } }, "지우기"),
          inRange(nowValue) ? h("button", { type: "button", class: "dp-link is-strong", onclick: () => { commit(nowValue); close(true); } }, isNow) : null));
      if (focusSelected) {
        const target = grid.querySelector(".dp-cell.is-selected:not([disabled])") || grid.querySelector(".dp-cell.is-today:not([disabled])")
          || grid.querySelector(".dp-cell:not(.is-outside):not([disabled])");
        target?.setAttribute("tabindex", "0");
        target?.focus({ preventScroll: true });
      }
    }

    function keydown(e) {
      if (e.key === "Escape") { e.preventDefault(); close(true); return; }
      if (e.key === "PageUp" || e.key === "PageDown") { e.preventDefault(); shift(e.key === "PageUp" ? -1 : 1); return; }
      const cells = [...pop.querySelectorAll(".dp-cell")];
      const i = cells.indexOf(document.activeElement);
      if (i < 0) return;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); cells[i].click(); return; }
      const cols = view === "days" ? 7 : 4;
      const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols, ArrowDown: cols }[e.key];
      if (!step) return;
      e.preventDefault();
      let j = i + step;
      while (cells[j] && cells[j].disabled) j += step;
      if (cells[j]) cells[j].focus();
      else shift(step > 0 ? 1 : -1);
    }

    toggle.addEventListener("click", () => (pop ? close(true) : open()));
    input.addEventListener("click", () => { if (!pop) open(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || (e.key === "Enter" && e.altKey)) { e.preventDefault(); open(); }
      else if (e.key === "Escape") close();
      else if (e.key === "Enter") { e.preventDefault(); input.dispatchEvent(new Event("change")); }
    });
    // 직접 입력하기 시작하면 달력은 닫는다 (모바일에서 키보드와 겹치지 않게)
    input.addEventListener("beforeinput", () => close());

    const api = {
      el: wrap, input, open, close,
      getValue: () => value,
      setValue: (v) => { value = parse(v, mode) || ""; input.value = shown(value); },
      setLimits: (lim) => { min = lim.min || ""; max = lim.max || ""; if (pop) draw(false); },
      isInvalid: () => wrap.classList.contains("is-invalid"),
    };
    return api;
  }

  // 다음·이전 달(연월) 또는 날(날짜)
  function step(v, mode, d) {
    const p = split(v);
    if (!p) return "";
    const dt = mode === "month" ? new Date(p.y, p.m - 1 + d, 1) : new Date(p.y, p.m - 1, p.d + d);
    return iso(mode, dt.getFullYear(), dt.getMonth() + 1, dt.getDate());
  }

  // 기간 입력: 시작 ~ 끝 두 칸. 값은 '시작~끝' (둘 다 비면 '')
  function attachRange(opts = {}) {
    const mode = opts.mode === "date" ? "date" : "month";
    const allowSame = opts.allowSame !== false;
    const openEnd = !!opts.openEnd;
    const [a0, b0] = String(opts.value || "").split("~");
    const onChange = opts.onChange || (() => {});
    const startInput = h("input", { id: opts.id || null, "aria-label": `${opts.label || "기간"} 시작` });
    const endInput = h("input", { "aria-label": `${opts.label || "기간"} 끝${openEnd ? " (비우면 현재)" : ""}`, placeholder: openEnd ? "현재" : null });
    const emit = () => {
      const a = start.getValue(), b = end.getValue();
      onChange(a || b ? `${a}~${b}` : "");
    };
    // 시작을 고르면 끝은 그 뒤만, 끝을 고르면 시작은 그 앞만 (같은 값 허용 여부에 따라 한 칸 밀기)
    const limit = () => {
      const a = start.getValue(), b = end.getValue();
      end.setLimits({ min: a ? (allowSame ? a : step(a, mode, 1)) : "" });
      start.setLimits({ max: b ? (allowSame ? b : step(b, mode, -1)) : "" });
    };
    const start = attach(startInput, { mode, value: a0 || "", strictTyping: false, onChange: () => { limit(); emit(); } });
    const end = attach(endInput, { mode, value: b0 || "", strictTyping: false, onChange: () => { limit(); emit(); } });
    limit();
    const el = h("div", { class: `dr-field${openEnd ? " is-open-end" : ""}`, role: "group", "aria-label": opts.label || "기간" },
      start.el, h("span", { class: "dr-sep", "aria-hidden": "true" }, "~"), end.el);
    function validate() {
      const a = start.getValue(), b = end.getValue();
      if (start.isInvalid() || end.isInvalid()) return mode === "month" ? "날짜를 확인해 주세요 (예: 2021.08)" : "날짜를 확인해 주세요 (예: 2021.08.15)";
      if (!a && b) return "시작을 입력해 주세요.";
      if (a && !b && !openEnd) return "끝을 입력해 주세요.";
      if (a && b && b < a) return "끝이 시작보다 빠릅니다.";
      if (a && b && a === b && !allowSame) return "시작과 끝이 같을 수 없습니다.";
      return "";
    }
    return { el, start, end, validate, getValue: () => { const a = start.getValue(), b = end.getValue(); return a || b ? `${a}~${b}` : ""; } };
  }

  // data-datepicker 속성이 있는 input에 자동으로 붙인다 (서버가 그린 폼용). 실제 값은 같은 name의 숨은 input으로 보낸다
  function enhance(root = document) {
    for (const input of root.querySelectorAll("input[data-datepicker]:not(.dp-input)")) {
      const hidden = h("input", { type: "hidden", name: input.name, value: input.value });
      input.removeAttribute("name");
      const dp = attach(input, {
        mode: input.dataset.datepicker, value: input.value, min: input.dataset.dpMin, max: input.dataset.dpMax,
        startView: input.dataset.dpStartView, onChange: (v) => { hidden.value = v; },
      });
      dp.el.append(hidden);
    }
  }
  window.DatePicker = { attach, attachRange, enhance, parse };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => enhance());
  else enhance();
})();

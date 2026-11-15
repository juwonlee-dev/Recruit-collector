// 이력서 작성 화면 (templates/resume_edit.html). 템플릿 배치(행·칸)대로 위젯의 입력 칸을 그리고, 고칠 때마다 잠시 뒤 자동 저장한다.
// data = { 위젯 배치 ID: [항목] }, 항목 = { 칸 key: 값 } (태그는 글 목록). 반복 위젯은 항목을 추가·삭제·순서 변경할 수 있다.
// 입력 검사: 이메일 형식, 기간(시작~끝) 순서, 예전 방식의 시작·끝 칸 순서. 틀린 칸은 빨갛게 표시하고 아래에 이유를 보여 준다.
//   저장(자동·수동)은 그대로 하되(쓰던 내용을 잃지 않게), 미리보기·PDF는 틀린 칸을 고칠 때까지 막고 그 칸으로 이동한다.
// 전화번호는 숫자만 저장하고, 입력 칸에서는 치는 대로 하이픈을 붙여 보여 준다 (010-1234-5678, 02-123-4567 …,
//   서버 web/resumes.py tel_digits·format_tel과 같은 규칙. 문서에도 하이픈을 붙여 보여 준다).
// 사용자 글은 value/textContent로만 넣는다.
(() => {
  const app = document.getElementById("re-app");
  const stateEl = document.getElementById("re-state");
  if (!app || !stateEl) return;
  const S = JSON.parse(stateEl.textContent);
  const data = S.data;
  const titleEl = document.getElementById("re-title");
  const statusEl = document.getElementById("re-status");
  const SAVE_DELAY = 1200;

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  // ------------------------------------------------------------ 자동 저장
  let timer = null;
  let pending = false;
  let inflight = null;
  function setStatus(text, kind = "") {
    statusEl.textContent = text;
    statusEl.dataset.kind = kind;
  }
  function touch() {
    pending = true;
    setStatus("입력 중…", "dirty");
    clearTimeout(timer);
    timer = setTimeout(flush, SAVE_DELAY);
  }
  async function flush() {
    clearTimeout(timer);
    if (inflight) await inflight;
    if (!pending) return true;
    pending = false;
    setStatus("저장 중…");
    inflight = (async () => {
      try {
        const res = await fetch(`/api/resumes/${S.id}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: titleEl.value, data }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok || body.ok === false) throw new Error(body.message || `HTTP ${res.status}`);
        const bad = checkAll().length;
        setStatus(`저장했습니다 ${new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}${bad ? ` · 확인할 칸 ${bad}개` : ""}`, bad ? "dirty" : "ok");
        return true;
      } catch (e) {
        pending = true; // 다음 입력이나 버튼에서 다시 시도
        setStatus(`저장하지 못했습니다: ${e.message}`, "error");
        return false;
      } finally {
        inflight = null;
      }
    })();
    return inflight;
  }
  window.addEventListener("beforeunload", (e) => {
    if (pending || inflight) e.preventDefault();
  });
  // 수동 저장 (자동 저장과 같은 길): 바뀐 것이 없어도 지금 내용을 한 번 더 저장해 '저장했습니다'를 확인시킨다
  const saveBtn = document.getElementById("re-save");
  async function saveNow() {
    if (saveBtn.disabled) return;
    saveBtn.disabled = true;
    pending = true;
    try { await flush(); } finally { saveBtn.disabled = false; }
  }
  saveBtn.addEventListener("click", saveNow);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "s") {
      e.preventDefault();
      saveNow();
    }
  });
  // ------------------------------------------------------------ 입력 검사
  // 검사하는 칸의 감싸개(.re-field)에 _check()를 달아 둔다 (항목을 다시 그려도 화면에 있는 칸만 모인다)
  function checkAll() {
    return [...app.querySelectorAll(".re-field[data-check]")].filter((w) => w._check());
  }
  function blockIfInvalid() {
    const bad = checkAll();
    if (!bad.length) return false;
    const first = bad[0];
    first.scrollIntoView({ block: "center", behavior: "smooth" });
    first.querySelector("input, select, textarea")?.focus({ preventScroll: true });
    const name = first.querySelector(".re-label")?.textContent.replace(/\s*\*$/, "") || "입력 칸";
    window.PdfDownload?.toast(`'${name}' 칸을 확인해 주세요${bad.length > 1 ? ` (외 ${bad.length - 1}개)` : ""}: ${first._message}`, true);
    return true;
  }

  // 미리보기·PDF는 저장된 내용으로 만들므로 먼저 저장한다 (틀린 칸이 있으면 먼저 고치도록)
  document.getElementById("re-preview").addEventListener("click", async (e) => {
    if (blockIfInvalid()) { e.preventDefault(); return; }
    if (!pending && !inflight) return;
    e.preventDefault();
    const href = e.currentTarget.href;
    const win = window.open("", "_blank");
    if (await flush() && win) win.location = href;
    else win?.close();
  });
  // PDF: 바뀐 내용을 먼저 저장하고, 그 자리에서 받는다 (static/pdf_download.js가 버튼에 진행 상태 표시)
  document.getElementById("re-pdf").addEventListener("click", (e) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    if (blockIfInvalid()) return;
    window.PdfDownload.start(e.currentTarget, { before: () => (pending || inflight ? flush() : true) });
  });
  titleEl.value = S.title;
  titleEl.addEventListener("input", touch);

  // ------------------------------------------------------------ 입력 칸
  let seq = 0;
  const EMAIL = /^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$/;
  // 전화번호 하이픈 (숫자·하이픈·공백·괄호만 있을 때, +82 같은 국제번호는 그대로)
  function formatTel(v) {
    if (!v.trim() || !/^[\d\-\s().]+$/.test(v)) return v;
    let d = v.replace(/\D/g, "");
    if (/^1[5-9]\d{2}/.test(d) && d.length <= 8) return d.length <= 4 ? d : `${d.slice(0, 4)}-${d.slice(4)}`;
    const head = d.startsWith("02") ? 2 : /^050\d/.test(d) ? 4 : 3;
    d = d.slice(0, head + 8);
    const p = d.slice(0, head), rest = d.slice(head);
    if (!rest) return p;
    if (rest.length <= 3) return `${p}-${rest}`;
    if (rest.length <= 7) return `${p}-${rest.slice(0, 3)}-${rest.slice(3)}`;
    return `${p}-${rest.slice(0, 4)}-${rest.slice(4)}`;
  }
  // 저장할 값: 숫자만 (국제번호·글이 섞이면 그대로)
  const telDigits = (v) => (/^[\d\-\s().]+$/.test(v.trim()) ? v.replace(/\D/g, "") : v.trim());
  // 치는 중에 하이픈을 넣어도 커서가 같은 숫자 뒤에 머물게
  function applyTel(el) {
    const before = el.value;
    const caret = el.selectionStart ?? before.length;
    const digitsBefore = before.slice(0, caret).replace(/\D/g, "").length;
    const after = formatTel(before);
    if (after === before) return;
    el.value = after;
    let pos = 0, seen = 0;
    while (pos < after.length && seen < digitsBefore) { if (/\d/.test(after[pos])) seen++; pos++; }
    try { el.setSelectionRange(pos, pos); } catch { /* 초점 없음 */ }
  }

  function input(field, entry) {
    const id = `re-f${++seq}`;
    const value = entry[field.key];
    let el;
    let control = null; // 입력 칸을 감싼 요소 (날짜 선택기·주소 검색)
    let check = null; // 문제가 있으면 안내 글을 돌려주는 검사
    if ((field.type === "month" || field.type === "date") && field.range) {
      // 기간: 시작 ~ 끝 (static/datepicker.js attachRange)
      const r = window.DatePicker.attachRange({
        mode: field.type, value: value || "", id, label: field.label, allowSame: field.allow_same !== false, openEnd: !!field.open_end,
        onChange: (v) => { entry[field.key] = v; touch(); recheck(); },
      });
      el = r.start.input;
      control = r.el;
      check = r.validate;
    } else if (field.type === "textarea") {
      el = h("textarea", { id, rows: 4, maxlength: 20000, placeholder: field.placeholder || null });
      el.value = value || "";
      const grow = () => { el.style.height = "auto"; el.style.height = `${el.scrollHeight + 2}px`; };
      el.addEventListener("input", grow);
      requestAnimationFrame(grow);
    } else if (field.type === "select") {
      el = h("select", { id }, h("option", { value: "" }, field.placeholder || "선택"),
        (field.options || []).map((o) => h("option", { value: o }, o)));
      if (value && !(field.options || []).includes(value)) el.append(h("option", { value }, value)); // 템플릿에서 지운 선택지
      el.value = value || "";
    } else if (field.type === "month" || field.type === "date") {
      // 공용 날짜 선택기 (static/datepicker.js). 생년월일(나이 표시)은 연도부터 고르고 오늘 이후는 막는다
      el = h("input", { id, placeholder: field.placeholder || null });
      const t = new Date();
      const today = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
      const dp = window.DatePicker.attach(el, {
        mode: field.type, value: value || "",
        startView: field.age ? "years" : null, startYear: field.age ? t.getFullYear() - 30 : null, max: field.age ? today : null,
        onChange: (v) => { entry[field.key] = v; touch(); control.dispatchEvent(new Event("dp-change", { bubbles: true })); },
      });
      control = dp.el;
      // 직접 친 날짜가 틀리면 저장되지 않으므로(이전 값 유지) 이유를 보여 주고 미리보기·PDF를 막는다
      check = () => (dp.isInvalid() ? (field.type === "month" ? "날짜를 확인해 주세요 (예: 2021.08)" : "날짜를 확인해 주세요 (예: 2021.08.15)") : "");
      el.addEventListener("change", () => recheck());
    } else {
      const type = { email: "email", tel: "tel", url: "url", number: "number" }[field.type] || "text";
      el = h("input", { id, type, maxlength: type === "text" || type === "url" ? 20000 : field.type === "tel" ? 40 : null,
        placeholder: field.placeholder || (field.type === "email" ? "name@example.com" : null),
        inputmode: field.type === "tel" ? "tel" : field.type === "email" ? "email" : null,
        autocomplete: { email: "email", tel: "tel", address: "street-address" }[field.type] || "off" });
      el.value = field.type === "tags" ? (value || []).join(", ") : (value || "");
      // 주소: 카카오 주소 검색 버튼 (static/address.js). 고른 뒤 상세 주소를 이어 쓴다
      if (field.type === "address") control = window.AddressSearch.attach(el);
      if (field.type === "tel") el.value = formatTel(el.value);
      if (field.type === "email") check = () => (el.value.trim() && !EMAIL.test(el.value.trim()) ? "이메일 형식이 아닙니다 (예: name@example.com)." : "");
    }
    if (field.type !== "month" && field.type !== "date") {
      el.addEventListener("input", () => {
        if (field.type === "tel") applyTel(el);
        entry[field.key] = field.type === "tags" ? el.value.split(",").map((x) => x.trim()).filter(Boolean)
          : field.type === "email" ? el.value.trim() : field.type === "tel" ? telDigits(el.value) : el.value;
        touch();
        if (wrap.classList.contains("is-invalid")) recheck(); // 고치는 중이면 맞는 순간 바로 풀어 준다
      });
      el.addEventListener("change", () => recheck());
    }
    if (field.type === "select") el.addEventListener("change", () => { entry[field.key] = el.value; touch(); });
    const error = h("span", { class: "re-error", role: "alert", hidden: true });
    const wrap = h("div", { class: `re-field${field.emphasis ? " is-emphasis" : ""}`, style: `grid-column: span ${field.span || 12}`, "data-key": field.key },
      h("label", { for: id, class: "re-label" }, field.label, field.required ? h("b", { class: "rb-req", "aria-label": "필수" }, " *") : null),
      control || el,
      field.help ? h("span", { class: "re-help" }, field.help) : null,
      error);
    function recheck() { wrap._check?.(); }
    if (check) addCheck(wrap, check, error);
    return wrap;
  }
  // 칸에 검사를 단다: _check()는 틀렸으면 true (빨간 표시·안내 글도 바꾼다)
  function addCheck(wrap, check, error) {
    const prev = wrap._checkFn; // 같은 칸에 검사가 또 붙으면(시작·끝 순서 + 날짜 형식) 앞 검사부터
    if (prev) { const next = check; check = () => prev() || next(); }
    wrap._checkFn = check;
    wrap.dataset.check = "";
    wrap._check = () => {
      const msg = check();
      wrap._message = msg;
      wrap.classList.toggle("is-invalid", !!msg);
      wrap.querySelectorAll("input").forEach((x) => (msg ? x.setAttribute("aria-invalid", "true") : x.removeAttribute("aria-invalid")));
      error.textContent = msg;
      error.hidden = !msg;
      return !!msg;
    };
    requestAnimationFrame(() => wrap._check()); // 이미 저장된 값이 틀렸으면 처음부터 보여 준다
  }
  // 예전 방식(시작·끝 연월·날짜 칸 두 개를 문서 왼쪽 기간으로): 끝이 시작보다 빠르면 끝 칸에 알린다
  function pairCheck(config, entry, grid) {
    if (["grid", "cards"].includes(config.display || "grid")) return;
    const pair = config.fields.filter((f) => f.slot === "side" && (f.type === "month" || f.type === "date") && !f.range).slice(0, 2);
    if (pair.length < 2) return;
    const [s, e] = pair;
    const wrap = grid.querySelector(`.re-field[data-key="${e.key}"]`);
    if (!wrap) return;
    addCheck(wrap, () => (entry[s.key] && entry[e.key] && entry[e.key] < entry[s.key] ? `${e.label}이(가) ${s.label}보다 빠릅니다.` : ""),
      wrap.querySelector(".re-error"));
    grid.addEventListener("dp-change", () => wrap._check());
  }

  function defaultEntry(config) {
    const e = {};
    for (const f of config.fields) e[f.key] = f.type === "tags" ? (f.default || "").split(",").map((x) => x.trim()).filter(Boolean) : (f.default || "");
    return e;
  }

  function formWidget(item) {
    const config = item.config;
    const entries = data[item.id] || (data[item.id] = config.repeatable ? [] : [defaultEntry(config)]);
    const box = h("section", { class: "re-widget" });
    const title = config.title || item.name;
    const list = h("div", { class: "re-entries" });
    const draw = () => {
      list.replaceChildren();
      entries.forEach((entry, i) => {
        const grid = h("div", { class: "re-grid" }, config.fields.map((f) => input(f, entry)));
        pairCheck(config, entry, grid);
        if (!config.repeatable) return list.append(grid);
        const move = (d) => {
          const j = i + d;
          [entries[i], entries[j]] = [entries[j], entries[i]];
          draw();
          touch();
        };
        list.append(h("div", { class: "re-entry" },
          h("div", { class: "re-entry-head" },
            h("span", { class: "re-entry-no", text: `${title} ${i + 1}` }),
            h("button", { type: "button", class: "rb-btn", title: "위로", "aria-label": `${title} ${i + 1} 위로`, disabled: i === 0, onclick: () => move(-1) }, "↑"),
            h("button", { type: "button", class: "rb-btn", title: "아래로", "aria-label": `${title} ${i + 1} 아래로`, disabled: i === entries.length - 1, onclick: () => move(1) }, "↓"),
            h("button", { type: "button", class: "rb-btn is-danger", onclick: () => {
              const filled = Object.values(entry).some((v) => (Array.isArray(v) ? v.length : String(v).trim()));
              if (filled && !window.confirm(`${title} ${i + 1}을(를) 지울까요?`)) return;
              entries.splice(i, 1);
              draw();
              touch();
            } }, "삭제")),
          grid));
      });
      if (config.repeatable && !entries.length) list.append(h("p", { class: "muted small re-none", text: "아직 없습니다. 아래 버튼으로 추가하세요." }));
    };
    draw();
    box.append(h("h2", { class: "re-widget-title", text: title }), list);
    if (config.repeatable) {
      box.append(h("button", { type: "button", class: "rb-btn re-add", onclick: () => {
        if (entries.length >= 50) return setStatus("항목은 50개까지 추가할 수 있습니다", "error");
        entries.push(defaultEntry(config));
        draw();
        touch();
        list.lastElementChild?.querySelector("input, textarea, select")?.focus();
      } }, `+ ${config.add_label || `${title} 추가`}`));
    }
    return box;
  }

  function widget(item) {
    if (item.kind === "form") return formWidget(item);
    if (item.kind === "heading") return h("div", { class: `re-static rb-pv-heading is-${item.config.size || "lg"}`, style: `text-align: ${item.config.align || "left"}`, text: item.config.text });
    if (item.kind === "note") return h("p", { class: "re-static rb-pv-note", style: `text-align: ${item.config.align || "center"}`, text: item.config.text });
    return h("hr", { class: "re-static rb-pv-divider" });
  }

  const rows = S.layout.rows || [];
  for (const row of rows) {
    app.append(h("div", { class: "re-row" }, row.cols.map((col) =>
      h("div", { class: "re-col", style: `grid-column: span ${col.span}` }, col.items.map(widget)))));
  }
  if (!rows.length) app.append(h("p", { class: "muted", text: "템플릿에 위젯이 없습니다." }));
})();

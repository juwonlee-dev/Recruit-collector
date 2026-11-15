// 이력서 템플릿 편집기와 위젯 편집기 (templates/resume_builder.html, 서버: web/resumes_web.py).
//
// 템플릿 배치: layout.rows[행].cols[칸].items[위젯]. 행은 위에서 아래로 쌓이고, 한 행은 12칸 격자를 여러 칸으로 나눈다.
//  - 왼쪽 위젯 목록에서 위젯을 칸 안이나 행 사이로 끌어다 놓는다 (터치 화면은 위젯의 '추가' 버튼 → 맨 아래 새 행).
//  - 배치한 위젯도 끌어서 다른 칸·행으로 옮긴다 (버튼으로 위·아래 이동도 가능).
//  - 칸 사이의 손잡이를 끌거나 −/+ 버튼으로 칸 너비를 바꾸고, '칸 나누기'로 한 행을 가로로 나눈다.
//  - 위젯을 누르면 오른쪽에서 설정(입력 칸, 힌트, 예시 텍스트 등)을 고친다. 템플릿 안의 위젯은 복사본이라 원본에 영향이 없고,
//    '내 위젯으로 저장' / '원본 위젯에 반영'으로 위젯 목록에 저장할 수 있다.
//  - 아무 위젯도 고르지 않으면 오른쪽에 '문서 스타일'(템플릿 전체 글꼴·본문 크기·색)이, 위젯을 고르면 그 위젯의 설정과
//    '스타일'(이 위젯만 글꼴·크기·색)이 보인다. 색은 static/colorpicker.js, 날짜 예시 텍스트는 static/datepicker.js.
//  - 미리보기는 저장하지 않은 지금 상태를 새 창으로 보낸다 (POST /resumes/preview).
// 화면에 사용자 글을 넣을 때는 textContent만 쓴다 (innerHTML 금지).
(() => {
  const app = document.getElementById("rb-app");
  const stateEl = document.getElementById("rb-state");
  if (!app || !stateEl) return;
  const S = JSON.parse(stateEl.textContent);
  // 선택지는 [키, 이름] 목록으로 온다 (순서 유지) → 키로 찾을 수 있게 객체로 (문자 키 객체는 넣은 순서를 지킨다)
  const M = (x) => (Array.isArray(x) ? Object.fromEntries(x) : x);
  for (const k of ["field_types", "kinds", "displays", "title_styles", "slots"]) S[k] = M(S[k]);
  for (const k of ["fonts", "colors", "aligns", "heading_sizes", "divider_weights", "divider_dashes", "divider_spaces"]) S.style[k] = M(S.style[k]);
  const GRID = S.grid || 12;
  const MAX_COLS = S.limits?.cols || 4;
  const MAX_FIELDS = S.limits?.fields || 30;
  const FIELD_TYPES = S.field_types;
  const KINDS = S.kinds;
  const isTemplate = S.mode === "template";
  const SLOTTED = ["profile", "timeline", "labeled"]; // 입력 칸의 '문서 위치'를 쓰는 표시 형식
  const ST = S.style; // 글꼴·색·크기 선택지 (web/resumes.py)
  const optionEls = (map) => Object.entries(map).map(([k, v]) => h("option", { value: k }, v));

  const nameEl = document.getElementById("rb-name");
  const descEl = document.getElementById("rb-desc");
  const visEl = document.getElementById("rb-visibility");
  const saveBtn = document.getElementById("rb-save");
  const statusEl = document.getElementById("rb-status");
  const publicNote = document.getElementById("rb-public-note");

  // ------------------------------------------------------------ 상태
  const doc = isTemplate
    // theme이 없는 배치(새 빈 템플릿 등)도 처음부터 {}로 둬서, 문서 스타일 패널을 그리는 것만으로 '변경'이 되지 않게
    ? { name: S.name, description: S.description, visibility: S.visibility, layout: { ...S.layout, theme: S.layout.theme || {} } }
    : { name: S.widget.name, description: S.widget.description, visibility: S.widget.visibility,
        kind: S.widget.kind, config: S.widget.config };
  const library = isTemplate ? S.library : [];
  let selected = null; // 선택한 위젯 배치 ID
  let render = () => {}; // 화면 전체를 다시 그림 (아래에서 모드별로 정함)
  let saved = snapshot();
  let saving = false;

  function snapshot() {
    return JSON.stringify(doc);
  }
  const dirty = () => snapshot() !== saved;
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const uid = (p) => p + Math.random().toString(36).slice(2, 9);

  // ------------------------------------------------------------ DOM 도우미
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "style") el.setAttribute("style", v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  const btn = (label, onclick, attrs = {}) => h("button", { type: "button", class: "rb-btn", onclick, ...attrs }, label);

  function setStatus(text, kind = "") {
    statusEl.textContent = text;
    statusEl.dataset.kind = kind;
  }
  function refreshStatus() {
    if (saving) return;
    if (dirty()) setStatus("저장하지 않은 변경이 있습니다", "dirty");
    else if (statusEl.dataset.kind === "dirty") setStatus("");
    publicNote.hidden = visEl.value !== "public";
  }

  // ------------------------------------------------------------ 메타 (이름·설명·공개)
  nameEl.value = doc.name || "";
  descEl.value = doc.description || "";
  visEl.value = doc.visibility || "private";
  nameEl.addEventListener("input", () => { doc.name = nameEl.value; refreshStatus(); });
  descEl.addEventListener("input", () => { doc.description = descEl.value; refreshStatus(); });
  visEl.addEventListener("change", () => { doc.visibility = visEl.value; refreshStatus(); });

  // ------------------------------------------------------------ 저장
  async function request(method, url, body) {
    const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = {};
    try { data = await res.json(); } catch { /* 본문 없음 */ }
    if (!res.ok || data.ok === false) throw new Error(data.message || `저장하지 못했습니다 (HTTP ${res.status})`);
    return data;
  }

  async function save() {
    if (saving) return;
    if (!doc.name.trim()) {
      setStatus("이름을 적어 주세요", "error");
      nameEl.focus();
      return;
    }
    saving = true;
    saveBtn.disabled = true;
    setStatus("저장 중…");
    try {
      if (isTemplate) {
        const data = await request("PUT", `/api/resume-templates/${S.id}`, doc);
        doc.layout = data.layout;
      } else {
        await request("PUT", `/api/resume-widgets/${S.widget.id}`, doc); // 편집 중인 설정 객체는 그대로 둔다
      }
      saved = snapshot();
      setStatus(`저장했습니다 ${new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}`, "ok");
      render();
    } catch (e) {
      setStatus(e.message, "error");
    } finally {
      saving = false;
      saveBtn.disabled = false;
    }
  }
  saveBtn.addEventListener("click", save);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      save();
    }
  });
  window.addEventListener("beforeunload", (e) => {
    if (dirty()) e.preventDefault();
  });
  // 미리보기: 저장하지 않은 지금 상태(배치·스타일)를 새 창으로 보내 문서 모양을 본다. 저장은 따로
  document.getElementById("rb-preview")?.addEventListener("click", (e) => {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    const payload = isTemplate ? { name: doc.name, layout: doc.layout } : { name: doc.name, widget: { kind: doc.kind, config: doc.config } };
    const value = h("input", { type: "hidden", name: "doc" });
    value.value = JSON.stringify(payload);
    const form = h("form", { method: "post", action: "/resumes/preview", target: "_blank", hidden: true }, value);
    document.body.append(form);
    form.submit();
    form.remove();
  });

  // ------------------------------------------------------------ 스타일 입력 도우미 (위젯·문서 스타일 공용)
  // 라벨 대신 div로 감싼 항목: 버튼이 여러 개 든 입력(색 선택기·날짜)은 라벨을 누르면 첫 버튼이 눌리므로
  const block = (label, input, hint) => h("div", { class: "rb-f" }, h("span", { class: "rb-f-label" }, label), input,
    hint ? h("span", { class: "rb-f-hint" }, hint) : null);
  const labeled = (label, input, hint) => h("label", { class: "rb-f" }, h("span", { class: "rb-f-label" }, label), input,
    hint ? h("span", { class: "rb-f-hint" }, hint) : null);
  function selectEl(map, value, onchange) {
    const el = h("select", {}, optionEls(map));
    el.value = String(value);
    el.addEventListener("change", () => onchange(el.value));
    return el;
  }
  function colorEl(value, fallback, label, onchange) {
    return window.ColorPicker.attach(h("input", {}), { value, fallback, allowEmpty: true, label, onChange: onchange }).el;
  }
  // 글꼴 선택 + 그 글꼴로 쓴 보기 글 (inherit: '템플릿 글꼴 따름' 선택지)
  function fontEl(value, onchange, inherit) {
    const sel = h("select", {}, optionEls(inherit ? { "": "템플릿 글꼴 따름", ...ST.fonts } : ST.fonts));
    sel.value = value || (inherit ? "" : ST.default_font);
    const sample = h("div", { class: "rb-font-sample", "aria-hidden": "true", text: "가나다라 홍길동 · Resume 2026" });
    const paint = () => { sample.hidden = !sel.value; sample.style.fontFamily = sel.value ? ST.font_css[sel.value] : ""; };
    sel.addEventListener("change", () => { paint(); onchange(sel.value); });
    paint();
    return h("div", { class: "rb-f" }, h("label", { class: "rb-f" }, h("span", { class: "rb-f-label" }, "글꼴"), sel), sample);
  }
  // 위젯 스타일의 '비우면 따르는 색' = 지금 템플릿 색 (위젯 편집 화면에서는 기본 색)
  const themeColor = (key) => (isTemplate && doc.layout.theme?.[key]) || ST.colors[key][1];
  const STYLE_COLOR = {
    form: { color: ["글자 색", "text"], title_color: ["제목 색 (위젯 제목·회사명 등)", "head"], line_color: ["선 색", "line"] },
    heading: { color: ["글자 색", "head"] }, note: { color: ["글자 색", "muted"] }, divider: { color: ["선 색", "line"] },
  };

  // ------------------------------------------------------------ 위젯 미리보기 (편집기 안의 모양)
  function widgetPreview(kind, config) {
    const st = config.style || {};
    const font = st.font ? ST.font_css[st.font] : "";
    if (kind === "heading") {
      const el = h("div", { class: `rb-pv-heading is-${config.size || "lg"}`, text: config.text || "제목" });
      Object.assign(el.style, { color: st.color || "", textAlign: config.align || "", fontFamily: font });
      return el;
    }
    if (kind === "note") {
      const el = h("p", { class: "rb-pv-note", text: config.text || "(문구 없음)" });
      Object.assign(el.style, { color: st.color || "", textAlign: config.align || "", fontFamily: font });
      return el;
    }
    if (kind === "divider") {
      const el = h("hr", { class: "rb-pv-divider" });
      el.style.borderTop = `${{ thin: 1, normal: 2, thick: 4 }[config.weight] || 1}px ${config.dash || "solid"} ${st.color || "var(--text-3)"}`;
      el.style.margin = `${{ narrow: 2, normal: 6, wide: 12 }[config.space] || 6}px 0`;
      return el;
    }
    const wrap = h("div", { class: "rb-pv-form" });
    if (font) wrap.style.fontFamily = font;
    if (config.show_title !== false && config.title) {
      wrap.append(h("div", { class: "rb-pv-title", style: st.title_color ? `color: ${st.title_color}` : null }, config.title,
        config.repeatable ? h("span", { class: "rs-repeat", title: "여러 개 입력" }, "반복") : null));
    } else if (config.repeatable) {
      wrap.append(h("span", { class: "rs-repeat" }, "반복"));
    }
    const grid = h("div", { class: "rb-pv-grid" });
    for (const f of config.fields || []) {
      const text = f.default || f.placeholder || "";
      grid.append(h("div", { class: "rb-pv-field", style: `grid-column: span ${f.span || GRID}` },
        h("span", { class: "rb-pv-label" }, f.label || "", f.required ? h("b", { class: "rb-req", "aria-label": "필수" }, " *") : null),
        h("span", { class: `rb-pv-input is-${f.type}${f.default ? " has-default" : ""}`, text: text || (f.range ? `${FIELD_TYPES[f.type]} ~ ${FIELD_TYPES[f.type]}` : FIELD_TYPES[f.type]) || "" })));
    }
    wrap.append(grid);
    if (config.repeatable) wrap.append(h("div", { class: "rb-pv-add", text: `+ ${config.add_label || "항목 추가"}` }));
    return wrap;
  }

  // ------------------------------------------------------------ 위젯 설정 편집기 (오른쪽 / 위젯 편집 화면)
  // target: { kind, config, name }을 가진 객체 (배치한 위젯 또는 위젯 자체). 고치면 onChange()를 부른다.
  function widgetEditor(target, onChange) {
    const box = h("div", { class: "rb-editor" });
    const changed = () => { onChange(); refreshStatus(); };
    const field = (label, input, hint) => h("label", { class: "rb-f" }, h("span", { class: "rb-f-label" }, label), input,
      hint ? h("span", { class: "rb-f-hint" }, hint) : null);
    const text = (obj, key, attrs = {}) => {
      const el = h(attrs.multiline ? "textarea" : "input", { maxlength: attrs.maxlength || 300, rows: attrs.rows || 3, placeholder: attrs.placeholder || "" });
      el.value = obj[key] ?? "";
      el.addEventListener("input", () => { obj[key] = el.value; changed(); });
      return el;
    };
    const check = (obj, key, label, after) => {
      const el = h("input", { type: "checkbox" });
      el.checked = key === "show_title" ? obj[key] !== false : !!obj[key];
      el.addEventListener("change", () => { obj[key] = el.checked; changed(); after?.(); });
      return h("label", { class: "rb-check" }, el, label);
    };

    if (isTemplate) box.append(field("배치 이름", text(target, "name", { maxlength: 100 }), "편집기에서만 보이는 이름입니다."));
    const c = target.config;
    const pick = (label, map, key, fallback, hint) => field(label, selectEl(map, c[key] || fallback, (v) => { c[key] = v; changed(); }), hint);
    if (target.kind === "heading") {
      box.append(field("제목 글자", text(c, "text", { maxlength: 100 })),
        h("div", { class: "rb-f-row" }, pick("크기", ST.heading_sizes, "size", "lg"), pick("정렬", ST.aligns, "align", "left")),
        styleSection(target, changed));
      return box;
    }
    if (target.kind === "note") {
      box.append(field("문구", text(c, "text", { multiline: true, maxlength: 2000, rows: 4 }), "문서에 그대로 나오는 고정 문구입니다 (줄바꿈 그대로)."),
        pick("정렬", ST.aligns, "align", "center"),
        styleSection(target, changed));
      return box;
    }
    if (target.kind === "divider") {
      box.append(h("div", { class: "rb-f-row" }, pick("굵기", ST.divider_weights, "weight", "thin"), pick("모양", ST.divider_dashes, "dash", "solid")),
        pick("위아래 여백", ST.divider_spaces, "space", "normal"),
        styleSection(target, changed));
      return box;
    }

    // 입력 항목(form)
    box.append(field("제목", text(c, "title", { maxlength: 100, placeholder: "예) 경력" })));
    const addLabel = field("추가 버튼 문구", text(c, "add_label", { maxlength: 30, placeholder: "예) 경력 추가" }));
    const syncAdd = () => { addLabel.hidden = !c.repeatable; };
    box.append(h("div", { class: "rb-checks" },
      check(c, "show_title", "문서에 제목 보이기", () => syncDisplay()),
      check(c, "repeatable", "여러 개 입력 (경력·학력처럼 반복)", syncAdd)));
    syncAdd();
    box.append(addLabel);

    // 문서(미리보기·PDF)에 보이는 모양
    const display = h("select", {}, optionEls(S.displays));
    display.value = c.display || "grid";
    const titleStyle = h("select", {}, optionEls(S.title_styles));
    titleStyle.value = c.title_style || "line";
    titleStyle.addEventListener("change", () => { c.title_style = titleStyle.value; changed(); });
    const titleStyleField = field("제목 모양", titleStyle);
    const periodHint = h("span", { class: "rb-f-hint", text: "목록 형식은 '왼쪽 기간' 자리의 기간 칸(또는 연월·날짜 두 칸)을 '시작 ~ 끝'으로 보여 줍니다 (끝이 비면 현재)." });
    const periodChecks = h("div", { class: "rb-checks" },
      check(c, "show_duration", "항목마다 기간 (n년 n개월)"),
      check(c, "show_total", "제목 옆에 기간 합계"));
    // 표시 형식마다 쓰는 옵션만 보인다 (왼쪽 제목 형식은 제목을 왼쪽에 그대로 써서 제목 모양을 쓰지 않는다)
    const syncDisplay = () => {
      const d = c.display || "grid";
      titleStyleField.hidden = c.show_title === false || d === "labeled";
      periodHint.hidden = periodChecks.hidden = d !== "timeline";
    };
    display.addEventListener("change", () => {
      c.display = display.value;
      syncDisplay();
      drawFields(); // 입력 칸의 '문서 위치' 등을 보이거나 숨긴다
      changed();
    });
    box.append(field("문서 표시 형식", display), titleStyleField, periodHint, periodChecks);

    const list = h("div", { class: "rb-fields" });
    const openKeys = new Set(); // 다시 그려도 펼친 칸은 펼친 채로
    const drawFields = () => {
      list.replaceChildren();
      c.fields.forEach((f, i) => list.append(fieldCard(f, i)));
      addBtn.disabled = c.fields.length >= MAX_FIELDS;
    };
    const fieldCard = (f, i) => {
      const body = h("div", { class: "rb-field-body" });
      const typeSel = h("select", {}, Object.entries(FIELD_TYPES).map(([k, v]) => h("option", { value: k }, v)));
      typeSel.value = f.type || "text";
      const options = h("textarea", { rows: 3, maxlength: 2000, placeholder: "한 줄에 하나씩" });
      options.value = (f.options || []).join("\n");
      options.addEventListener("input", () => {
        f.options = options.value.split("\n").map((x) => x.trim()).filter(Boolean);
        if (f.type === "select") drawDefault();
        changed();
      });
      const optionsField = field("선택지", options, "목록에서 선택할 값을 한 줄에 하나씩 적습니다.");
      const defaultBox = h("div");
      // 예시 텍스트도 칸 종류에 맞는 입력으로: 목록은 선택지 중에서, 연월·날짜는 날짜 선택기로
      const drawDefault = () => {
        let el;
        if (f.type === "select") {
          el = selectEl({ "": "(비워 둠)", ...Object.fromEntries((f.options || []).map((o) => [o, o])) }, "", (v) => { f.default = v; changed(); });
          if (f.default && !(f.options || []).includes(f.default)) el.append(h("option", { value: f.default }, `${f.default} (선택지에 없음)`));
          el.value = f.default || "";
        } else if ((f.type === "month" || f.type === "date") && f.range && window.DatePicker) {
          el = window.DatePicker.attachRange({ mode: f.type, value: f.default || "", label: "예시 기간", allowSame: f.allow_same !== false,
            openEnd: !!f.open_end, onChange: (v) => { f.default = v; changed(); } }).el;
        } else if ((f.type === "month" || f.type === "date") && window.DatePicker) {
          el = window.DatePicker.attach(h("input", {}), { mode: f.type, value: f.default || "", onChange: (v) => { f.default = v; changed(); } }).el;
        } else {
          el = text(f, "default", { multiline: f.type === "textarea", rows: 3, maxlength: 5000 });
        }
        defaultBox.replaceChildren(block("예시 텍스트", el,
          f.type === "tags" ? "이력서를 만들면 미리 채워지는 값 (쉼표로 구분)" : "이 템플릿으로 이력서를 만들면 입력 칸에 미리 채워지는 내용입니다."));
      };
      const ageCheck = check(f, "age", "문서에 출생연도와 만 나이로 (생년월일)");
      // 연월·날짜: 한 날짜 / 기간(시작 ~ 끝). 기간이면 시작=끝 허용, 끝을 비우면 '현재'를 고른다
      const rangeCheck = check(f, "range", "기간으로 입력 (시작 ~ 끝)", () => { f.default = ""; syncType(); });
      const sameBox = h("input", { type: "checkbox" });
      sameBox.checked = f.allow_same !== false; // 예전에 저장한 칸은 값이 없음 = 허용
      sameBox.addEventListener("change", () => { f.allow_same = sameBox.checked; changed(); });
      const sameCheck = h("label", { class: "rb-check" }, sameBox, "시작과 끝이 같아도 됨");
      const openEndCheck = check(f, "open_end", "끝을 비우면 '현재' (재직·재학·진행 중)");
      const dateChecks = h("div", { class: "rb-checks" }, rangeCheck, sameCheck, openEndCheck);
      const syncType = () => {
        const isDate = f.type === "month" || f.type === "date";
        optionsField.hidden = f.type !== "select";
        ageCheck.hidden = f.type !== "date" || !!f.range;
        dateChecks.hidden = !isDate;
        sameCheck.hidden = openEndCheck.hidden = !isDate || !f.range;
        drawDefault();
      };
      typeSel.addEventListener("change", () => {
        const before = f.type;
        f.type = typeSel.value;
        // 종류가 바뀌면 그 종류에 맞지 않는 예시 텍스트는 비운다 (연월 칸에 '홍길동'이 들어가지 않게)
        if (f.default && (before === "tags") !== (f.type === "tags")) f.default = "";
        if (f.default && (f.type === "month" || f.type === "date") && !window.DatePicker?.parse(f.default, f.type)) f.default = "";
        syncType();
        head.querySelector(".rb-field-type").textContent = FIELD_TYPES[f.type];
        changed();
      });
      const slotSel = h("select", {}, optionEls(S.slots));
      slotSel.value = f.slot || "info";
      const slotField = field("문서 위치", slotSel, "프로필·목록·왼쪽 제목 형식에서 이 칸이 놓이는 자리입니다.");
      const d = c.display || "grid";
      slotField.hidden = !SLOTTED.includes(d);
      const span = h("select", {}, Array.from({ length: GRID }, (_, k) => GRID - k).map((n) => h("option", { value: n }, `${n}/${GRID}${n === GRID ? " (한 줄 전체)" : n === GRID / 2 ? " (절반)" : ""}`)));
      span.value = String(f.span || GRID);
      span.addEventListener("change", () => { f.span = Number(span.value); changed(); });
      // '문서에서 크게'는 기본 형식에서만, '이름 숨기기'는 이름이 같이 보이는 자리(기본 형식, 이름·값 한 줄, 이름: 값, 본문)에서만 쓴다
      const emphasisCheck = check(f, "emphasis", "문서에서 크게 (이름 등)");
      const hideLabelCheck = check(f, "hide_label", "문서에서 이름 숨기기");
      const syncSlot = () => {
        emphasisCheck.hidden = d !== "grid";
        hideLabelCheck.hidden = d === "cards" || (SLOTTED.includes(d) && !["info", "line", "body"].includes(f.slot || "info"));
      };
      slotSel.addEventListener("change", () => { f.slot = slotSel.value; syncSlot(); changed(); });
      syncSlot();
      body.append(
        field("이름", text(f, "label", { maxlength: 60 })),
        h("div", { class: "rb-f-row" }, field("종류", typeSel), field("너비", span, d === "grid" ? "작성 화면과 문서의 너비" : "작성 화면의 너비")),
        slotField,
        field("힌트", text(f, "placeholder", { maxlength: 300 }), "입력 칸이 비어 있을 때 흐리게 보이는 안내입니다."),
        defaultBox,
        field("도움말", text(f, "help", { maxlength: 300 }), "입력 칸 아래에 작게 보이는 설명입니다."),
        optionsField,
        dateChecks,
        h("div", { class: "rb-checks" }, check(f, "required", "필수 표시"), emphasisCheck, hideLabelCheck, ageCheck),
      );
      syncType();
      const head = h("div", { class: "rb-field-head" },
        h("button", { type: "button", class: "rb-field-toggle", "aria-expanded": String(openKeys.has(f.key)),
          onclick: (e) => {
            const open = card.classList.toggle("is-open");
            if (open) openKeys.add(f.key); else openKeys.delete(f.key);
            e.currentTarget.setAttribute("aria-expanded", String(open));
          } },
        h("span", { class: "rb-field-name", text: f.label || `항목 ${i + 1}` }), h("span", { class: "rb-field-type muted small", text: FIELD_TYPES[f.type] || "" })),
        btn("↑", () => move(i, -1), { title: "위로", "aria-label": "위로", disabled: i === 0 }),
        btn("↓", () => move(i, 1), { title: "아래로", "aria-label": "아래로", disabled: i === c.fields.length - 1 }),
        btn("삭제", () => {
          if (c.fields.length <= 1) return setStatus("입력 칸은 하나 이상 있어야 합니다", "error");
          c.fields.splice(i, 1);
          drawFields();
          changed();
        }, { class: "rb-btn is-danger" }));
      const card = h("div", { class: `rb-field${openKeys.has(f.key) ? " is-open" : ""}` }, head, body);
      body.querySelector("input")?.addEventListener("input", (e) => { head.querySelector(".rb-field-name").textContent = e.target.value || `항목 ${i + 1}`; });
      return card;
    };
    const move = (i, d) => {
      const j = i + d;
      [c.fields[i], c.fields[j]] = [c.fields[j], c.fields[i]];
      drawFields();
      changed();
    };
    const addBtn = btn("+ 입력 칸 추가", () => {
      const key = uid("f");
      c.fields.push({ key, label: "새 항목", type: "text", placeholder: "", default: "", help: "", span: GRID, slot: "info" });
      openKeys.add(key);
      drawFields();
      list.lastElementChild?.querySelector("input")?.focus();
      changed();
    }, { class: "rb-btn rb-add-field" });
    box.append(h("div", { class: "rb-f-label rb-fields-title", text: "입력 칸" }), list, addBtn, styleSection(target, changed));
    drawFields();
    syncDisplay();
    return box;
  }

  // 위젯 스타일 (이 위젯만 글꼴·글자 크기·색). 비워 두면 템플릿의 문서 스타일을 따른다
  function styleSection(target, changed) {
    const c = target.config;
    const st = (c.style = c.style && typeof c.style === "object" ? c.style : {});
    const keys = ST.style_keys[target.kind] || [];
    const count = h("span", { class: "rb-style-count" });
    const recount = () => { const n = Object.keys(st).length; count.textContent = n ? `${n}개 바꿈` : ""; count.hidden = !n; };
    const set = (k, v) => {
      if (v === "" || v == null || (k === "size" && Number(v) === 100)) delete st[k];
      else st[k] = v;
      recount();
      changed();
    };
    const body = h("div", { class: "rb-style-body" });
    if (keys.includes("font")) body.append(fontEl(st.font, (v) => set("font", v), true));
    if (keys.includes("size")) {
      const sizes = Object.fromEntries(ST.widget_sizes.map((n) => [n, n === 100 ? "100% (템플릿 크기)" : `${n}%`]));
      body.append(labeled("글자 크기", selectEl(sizes, st.size || 100, (v) => set("size", Number(v))), "템플릿 본문 크기를 기준으로 이 위젯만 크게·작게 합니다."));
    }
    for (const [k, [label, themeKey]] of Object.entries(STYLE_COLOR[target.kind] || {})) {
      if (keys.includes(k)) body.append(block(label, colorEl(st[k] || "", themeColor(themeKey), label, (v) => set(k, v)), "비워 두면 템플릿 색을 따릅니다."));
    }
    body.append(btn("이 위젯 스타일 초기화", () => {
      for (const k of Object.keys(st)) delete st[k];
      const fresh = styleSection(target, changed);
      fresh.open = true;
      box.replaceWith(fresh);
      changed();
    }, { class: "rb-btn rb-theme-reset" }));
    recount();
    const box = h("details", { class: "rb-style", open: Object.keys(st).length > 0 }, h("summary", {}, target.kind === "divider" ? "스타일 (선 색)" : "스타일 (글꼴·글자 크기·색)", count), body);
    return box;
  }

  // ------------------------------------------------------------ 위젯 편집 화면
  if (!isTemplate) {
    const target = { kind: doc.kind, config: doc.config };
    const preview = h("div", { class: "rb-widget-preview" });
    const drawPreview = () => {
      doc.config = target.config;
      preview.replaceChildren(h("div", { class: "rb-panel-title", text: "미리보기 (이력서 작성 화면의 입력 칸)" }),
        h("div", { class: "rb-item is-static" }, widgetPreview(target.kind, target.config)));
    };
    app.append(h("div", { class: "rb-widget-layout" },
      h("section", { class: "rb-panel" }, h("div", { class: "rb-panel-title" }, `${KINDS[doc.kind]} 위젯 설정`), widgetEditor(target, drawPreview)),
      preview));
    render = drawPreview;
    drawPreview();
    refreshStatus();
    return;
  }

  // ------------------------------------------------------------ 템플릿 편집기
  const layout = () => doc.layout;
  const palette = h("aside", { class: "rb-palette", "aria-label": "위젯 목록" });
  const canvas = h("div", { class: "rb-canvas", "aria-label": "템플릿 배치" });
  const inspector = h("aside", { class: "rb-inspector", "aria-label": "선택한 위젯 설정" });
  app.append(h("div", { class: "rb-layout" }, palette, h("div", { class: "rb-canvas-wrap" }, canvas), inspector));

  function findItem(id) {
    for (const [ri, row] of layout().rows.entries())
      for (const [ci, col] of row.cols.entries()) {
        const ii = col.items.findIndex((x) => x.id === id);
        if (ii >= 0) return { ri, ci, ii, row, col, item: col.items[ii] };
      }
    return null;
  }
  const instanceOf = (w) => ({ id: uid("w"), source_id: w.id, name: w.name, kind: w.kind, config: clone(w.config) });
  const newRow = (items = []) => ({ id: uid("r"), cols: [{ id: uid("c"), span: GRID, items }] });

  function changed(rerenderInspector = false) {
    // 빈 칸만 남은 행(칸 1개, 위젯 없음)은 그대로 둔다: 사용자가 일부러 만든 자리일 수 있다
    renderCanvas();
    if (rerenderInspector) renderInspector();
    refreshStatus();
  }

  // ---------------- 위젯 목록 (왼쪽)
  let paletteQuery = "";
  const GROUPS = [["기본 위젯", (w) => w.builtin], ["내 위젯", (w) => w.mine], ["공개 위젯", (w) => !w.builtin && !w.mine]];
  const summary = (w) => (w.kind === "form" ? w.config.fields.map((f) => f.label).join(" · ") : (w.description || ""));
  // 이름·설명·입력 칸 이름으로 찾는다
  const matches = (w, q) => !q || `${w.name} ${w.description} ${summary(w)}`.toLowerCase().includes(q);

  // ---------------- 위젯 고르기 창: 빈 칸을 누르거나(PC·휴대폰) 휴대폰의 '+ 위젯' 버튼으로 연다. 고르면 onPick(위젯)
  function openPicker({ title, hint, onPick }) {
    const back = document.activeElement;
    const search = h("input", { type: "search", class: "rb-search", placeholder: "위젯 이름이나 항목으로 찾기", "aria-label": "위젯 찾기" });
    const list = h("div", { class: "rb-picker-list" });
    const layer = h("div", { class: "rb-picker-layer", role: "dialog", "aria-modal": "true", "aria-label": title });
    const close = () => {
      layer.remove();
      document.removeEventListener("keydown", onKey);
      document.documentElement.classList.remove("rb-picker-open");
      back?.focus?.({ preventScroll: true });
    };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    const draw = () => {
      list.replaceChildren();
      const q = search.value.trim().toLowerCase();
      for (const [label, test] of GROUPS) {
        const items = library.filter((w) => test(w) && matches(w, q));
        if (!items.length) continue;
        list.append(h("div", { class: "rb-group", text: label }));
        for (const w of items) {
          list.append(h("button", { type: "button", class: "rb-picker-item", onclick: () => { close(); onPick(w); } },
            h("span", { class: "rb-pal-main" }, h("b", { text: w.name }), " ", h("span", { class: "rb-kind", text: KINDS[w.kind] }),
              h("span", { class: "rb-pal-desc", text: summary(w) })),
            h("span", { class: "rb-picker-add", "aria-hidden": "true" }, "추가")));
        }
      }
      if (!list.childElementCount) list.append(h("p", { class: "muted small rb-picker-none", text: "맞는 위젯이 없습니다." }));
    };
    search.addEventListener("input", draw);
    layer.addEventListener("pointerdown", (e) => { if (e.target === layer) close(); });
    layer.append(h("div", { class: "rb-picker" },
      h("div", { class: "rb-picker-head" },
        h("div", {}, h("b", { text: title }), hint ? h("div", { class: "muted small", text: hint }) : null),
        h("button", { type: "button", class: "rb-picker-close", "aria-label": "닫기", onclick: close }, "×")),
      search, list));
    document.body.append(layer);
    document.documentElement.classList.add("rb-picker-open");
    document.addEventListener("keydown", onKey);
    draw();
    // 휴대폰에서는 키보드가 바로 올라와 목록을 가리지 않게 검색 칸에 자동으로 커서를 두지 않는다
    if (!window.matchMedia("(hover: none)").matches) search.focus();
    else list.querySelector("button")?.focus({ preventScroll: true });
  }
  function addToColumn(ri, ci, w) {
    const inst = instanceOf(w);
    layout().rows[ri].cols[ci].items.push(inst);
    selected = inst.id;
    changed(true);
    canvas.querySelector(`[data-id="${inst.id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }
  // '+ 위젯' 버튼: 고른 위젯이 있으면 그 아래에, 없으면 맨 아래 새 행으로
  function addFromFab(w) {
    const inst = instanceOf(w);
    const at = selected && findItem(selected);
    if (at) at.col.items.splice(at.ii + 1, 0, inst);
    else layout().rows.push(newRow([inst]));
    selected = inst.id;
    changed(true);
    canvas.querySelector(`[data-id="${inst.id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  function renderPalette() {
    const search = h("input", { type: "search", class: "rb-search", placeholder: "위젯 찾기", "aria-label": "위젯 찾기" });
    search.value = paletteQuery;
    search.addEventListener("input", () => { paletteQuery = search.value; drawList(); });
    const listBox = h("div", { class: "rb-palette-list" });
    const drawList = () => {
      listBox.replaceChildren();
      const q = paletteQuery.trim().toLowerCase();
      for (const [label, test] of GROUPS) {
        const items = library.filter((w) => test(w) && matches(w, q));
        if (!items.length) continue;
        listBox.append(h("div", { class: "rb-group", text: label }));
        for (const w of items) {
          const card = h("div", { class: "rb-pal-item", draggable: "true", title: "끌어서 배치하세요" },
            h("div", { class: "rb-pal-main" },
              h("b", { text: w.name }), " ", h("span", { class: "rb-kind", text: KINDS[w.kind] }),
              h("div", { class: "rb-pal-desc", text: w.kind === "form" ? w.config.fields.map((f) => f.label).join(" · ") : (w.description || "") })),
            btn("추가", () => {
              const inst = instanceOf(w);
              layout().rows.push(newRow([inst]));
              selected = inst.id;
              changed(true);
              canvas.querySelector(`[data-id="${inst.id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
            }, { class: "rb-btn rb-pal-add", title: "맨 아래에 새 행으로 추가", "aria-label": `${w.name} 맨 아래에 추가` }));
          card.addEventListener("dragstart", (e) => startDrag(e, { type: "new", widget: w }));
          card.addEventListener("dragend", endDrag);
          listBox.append(card);
        }
      }
      if (!listBox.childElementCount) listBox.append(h("p", { class: "muted small", text: "맞는 위젯이 없습니다." }));
    };
    palette.replaceChildren(h("div", { class: "rb-panel-title", text: "위젯" }), search, listBox,
      h("p", { class: "rb-tip muted small", text: "위젯을 오른쪽 문서의 칸 안이나 행 사이로 끌어다 놓으세요. 새 위젯은 이력서 > 위젯에서 만듭니다." }));
    drawList();
  }

  // ---------------- 끌어서 놓기
  let drag = null; // { type: "new", widget } | { type: "move", id }
  const marker = h("div", { class: "rb-marker", "aria-hidden": "true" });

  function startDrag(e, info) {
    drag = info;
    e.dataTransfer.effectAllowed = info.type === "new" ? "copy" : "move";
    e.dataTransfer.setData("text/plain", info.type === "new" ? info.widget.name : info.id);
    requestAnimationFrame(() => app.classList.add("is-dragging"));
  }
  function endDrag() {
    drag = null;
    app.classList.remove("is-dragging");
    marker.remove();
    canvas.querySelectorAll(".is-over").forEach((x) => x.classList.remove("is-over"));
  }
  function dropIndex(colEl, y) {
    const items = [...colEl.querySelectorAll(":scope > .rb-items > .rb-item")];
    const i = items.findIndex((el) => { const r = el.getBoundingClientRect(); return y < r.top + r.height / 2; });
    return i < 0 ? items.length : i;
  }
  function takeDragged() {
    if (drag.type === "new") return instanceOf(drag.widget);
    const at = findItem(drag.id);
    if (!at) return null;
    at.col.items.splice(at.ii, 1);
    return at.item;
  }
  function dropInto(ri, ci, index) {
    const target = layout().rows[ri].cols[ci];
    if (drag.type === "move") {
      const at = findItem(drag.id);
      if (at && at.ri === ri && at.ci === ci && at.ii < index) index -= 1; // 같은 칸 안에서 아래로 옮기면 빠진 자리만큼
    }
    const item = takeDragged();
    if (!item) return;
    target.items.splice(index, 0, item);
    selected = item.id;
  }
  function dropNewRow(index) {
    const item = takeDragged();
    if (!item) return;
    layout().rows.splice(index, 0, newRow([item]));
    selected = item.id;
  }
  canvas.addEventListener("dragover", (e) => {
    if (!drag) return;
    const gap = e.target.closest(".rb-gap");
    const col = e.target.closest(".rb-col");
    canvas.querySelectorAll(".is-over").forEach((x) => x.classList.remove("is-over"));
    if (gap) {
      e.preventDefault();
      gap.classList.add("is-over");
      marker.remove();
    } else if (col) {
      e.preventDefault();
      col.classList.add("is-over");
      const itemsEl = col.querySelector(":scope > .rb-items");
      const ref = itemsEl.querySelectorAll(":scope > .rb-item")[dropIndex(col, e.clientY)] || null;
      if (marker.parentNode !== itemsEl || marker.nextElementSibling !== ref) itemsEl.insertBefore(marker, ref);
    }
    if (e.dataTransfer) e.dataTransfer.dropEffect = drag.type === "new" ? "copy" : "move";
  });
  canvas.addEventListener("drop", (e) => {
    if (!drag) return;
    e.preventDefault();
    const gap = e.target.closest(".rb-gap");
    const col = e.target.closest(".rb-col");
    marker.remove();
    if (gap) dropNewRow(Number(gap.dataset.index));
    else if (col) dropInto(Number(col.dataset.row), Number(col.dataset.col), dropIndex(col, e.clientY));
    endDrag();
    changed(true);
  });

  // ---------------- 칸 너비 (손잡이 끌기)
  function startResize(e, ri, ci, gridEl) {
    e.preventDefault();
    const cols = layout().rows[ri].cols;
    const a = cols[ci], b = cols[ci + 1];
    const start = { x: e.clientX, a: a.span, b: b.span };
    const unit = gridEl.getBoundingClientRect().width / GRID;
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    app.classList.add("is-resizing");
    const onMove = (ev) => {
      let d = Math.round((ev.clientX - start.x) / unit);
      d = Math.max(1 - start.a, Math.min(start.b - 1, d));
      if (a.span === start.a + d) return;
      a.span = start.a + d;
      b.span = start.b - d;
      gridEl.children[ci].style.gridColumn = `span ${a.span}`;
      gridEl.children[ci + 1].style.gridColumn = `span ${b.span}`;
      gridEl.children[ci].querySelector(".rb-col-size").textContent = `${a.span}/${GRID}`;
      gridEl.children[ci + 1].querySelector(".rb-col-size").textContent = `${b.span}/${GRID}`;
    };
    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      app.classList.remove("is-resizing");
      changed();
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  }
  function nudge(ri, ci, d) {
    const cols = layout().rows[ri].cols;
    const n = ci < cols.length - 1 ? ci + 1 : ci - 1; // 이웃 칸과 너비를 주고받는다
    if (n < 0) return;
    if (cols[ci].span + d < 1 || cols[n].span - d < 1) return;
    cols[ci].span += d;
    cols[n].span -= d;
    changed();
  }
  function splitRow(ri) {
    const cols = layout().rows[ri].cols;
    if (cols.length >= MAX_COLS) return setStatus(`한 행은 ${MAX_COLS}칸까지 나눌 수 있습니다`, "error");
    const free = GRID - cols.reduce((s, c) => s + c.span, 0);
    if (free > 0) {
      cols.push({ id: uid("c"), span: free, items: [] });
    } else {
      const wi = cols.reduce((m, c, i) => (c.span > cols[m].span ? i : m), 0);
      if (cols[wi].span < 2) return setStatus("나눌 수 있는 칸이 없습니다", "error");
      const half = Math.floor(cols[wi].span / 2);
      cols[wi].span -= half;
      cols.splice(wi + 1, 0, { id: uid("c"), span: half, items: [] });
    }
    changed();
  }
  function removeCol(ri, ci) {
    const row = layout().rows[ri];
    if (row.cols.length === 1) return removeRow(ri);
    const [col] = row.cols.splice(ci, 1);
    const n = Math.max(0, ci - 1); // 왼쪽 칸(없으면 오른쪽)이 너비와 위젯을 넘겨받는다
    row.cols[n].span += col.span;
    row.cols[n].items.push(...col.items);
    changed();
  }
  function removeRow(ri) {
    const row = layout().rows[ri];
    const count = row.cols.reduce((s, c) => s + c.items.length, 0);
    if (count && !window.confirm(`이 행의 위젯 ${count}개도 함께 지울까요?`)) return;
    if (row.cols.some((c) => c.items.some((x) => x.id === selected))) selected = null;
    layout().rows.splice(ri, 1);
    changed(true);
  }
  function moveRow(ri, d) {
    const rows = layout().rows;
    const j = ri + d;
    if (j < 0 || j >= rows.length) return;
    [rows[ri], rows[j]] = [rows[j], rows[ri]];
    changed();
  }
  // 위젯을 앞·뒤로: 같은 칸 안에서 한 칸씩, 칸 끝이면 이전·다음 행으로 넘어간다
  function moveItem(id, d) {
    const at = findItem(id);
    if (!at) return;
    const { ri, ci, ii, col } = at;
    const j = ii + d;
    if (j >= 0 && j < col.items.length) {
      [col.items[ii], col.items[j]] = [col.items[j], col.items[ii]];
    } else {
      const rows = layout().rows;
      const nr = ri + d;
      if (nr < 0 || nr >= rows.length) return;
      col.items.splice(ii, 1);
      const targetCol = rows[nr].cols[Math.min(ci, rows[nr].cols.length - 1)];
      if (d < 0) targetCol.items.push(at.item);
      else targetCol.items.unshift(at.item);
    }
    changed();
    canvas.querySelector(`[data-id="${id}"]`)?.scrollIntoView({ block: "nearest" });
  }
  function removeItem(id) {
    const at = findItem(id);
    if (!at) return;
    at.col.items.splice(at.ii, 1);
    if (selected === id) selected = null;
    changed(true);
  }
  function duplicateItem(id) {
    const at = findItem(id);
    if (!at) return;
    const copy = { ...clone(at.item), id: uid("w") };
    at.col.items.splice(at.ii + 1, 0, copy);
    selected = copy.id;
    changed(true);
  }

  // ---------------- 문서 (가운데)
  function renderCanvas() {
    const rows = layout().rows;
    const out = [gapEl(0)];
    rows.forEach((row, ri) => {
      const grid = h("div", { class: "rb-grid" });
      row.cols.forEach((col, ci) => {
        const items = h("div", { class: "rb-items" });
        for (const it of col.items) items.append(itemEl(it));
        if (!col.items.length) {
          items.append(h("button", { type: "button", class: "rb-empty",
            onclick: () => openPicker({ title: "이 칸에 위젯 추가", hint: `${ri + 1}행 ${ci + 1}번째 칸`, onPick: (w) => addToColumn(ri, ci, w) }) },
          h("span", { class: "rb-empty-plus", "aria-hidden": "true" }, "+"), " 위젯 추가",
          h("span", { class: "rb-empty-sub" }, "눌러서 고르거나 끌어다 놓으세요")));
        }
        const colEl = h("div", { class: "rb-col", "data-row": ri, "data-col": ci, style: `grid-column: span ${col.span}` },
          h("div", { class: "rb-col-head" },
            h("span", { class: "rb-col-size", title: "칸 너비 (12칸 기준)", text: `${col.span}/${GRID}` }),
            row.cols.length > 1 ? btn("−", () => nudge(ri, ci, -1), { title: "좁게", "aria-label": "칸 좁게" }) : null,
            row.cols.length > 1 ? btn("+", () => nudge(ri, ci, 1), { title: "넓게", "aria-label": "칸 넓게" }) : null,
            row.cols.length > 1 ? btn("칸 삭제", () => removeCol(ri, ci), { title: "이 칸을 지우고 위젯은 옆 칸으로 옮깁니다" }) : null),
          items);
        if (ci < row.cols.length - 1) {
          const handle = h("div", { class: "rb-resize", title: "끌어서 칸 너비 조절", "aria-hidden": "true" });
          handle.addEventListener("pointerdown", (e) => startResize(e, ri, ci, grid));
          colEl.append(handle);
        }
        grid.append(colEl);
      });
      const used = row.cols.reduce((s, c) => s + c.span, 0);
      if (used < GRID) grid.append(h("div", { class: "rb-col-free", style: `grid-column: span ${GRID - used}`, text: "빈 공간" }));
      out.push(h("div", { class: "rb-row" },
        h("div", { class: "rb-row-bar" },
          h("span", { class: "rb-row-name", text: `${ri + 1}행` }),
          btn("↑", () => moveRow(ri, -1), { title: "행 위로", "aria-label": `${ri + 1}행 위로`, disabled: ri === 0 }),
          btn("↓", () => moveRow(ri, 1), { title: "행 아래로", "aria-label": `${ri + 1}행 아래로`, disabled: ri === rows.length - 1 }),
          btn("칸 나누기", () => splitRow(ri), { title: "이 행을 가로로 한 칸 더 나눕니다", disabled: row.cols.length >= MAX_COLS }),
          btn("행 삭제", () => removeRow(ri), { class: "rb-btn is-danger" })),
        grid), gapEl(ri + 1));
    });
    if (!rows.length) {
      out.splice(1, 0, h("button", { type: "button", class: "rb-empty is-big",
        onclick: () => openPicker({ title: "위젯 추가", hint: "첫 행에 놓입니다", onPick: (w) => { layout().rows.push(newRow([instanceOf(w)])); changed(true); } }) },
      h("span", { class: "rb-empty-plus", "aria-hidden": "true" }, "+"), " 위젯 추가",
      h("span", { class: "rb-empty-sub" }, "눌러서 고르거나 위젯 목록에서 끌어다 놓으세요")));
    }
    out.push(btn("+ 빈 행 추가", () => { layout().rows.push(newRow()); changed(); }, { class: "rb-btn rb-add-row" }));
    canvas.replaceChildren(...out);
  }
  function gapEl(index) {
    return h("div", { class: "rb-gap", "data-index": index }, h("span", { text: "새 행으로 놓기" }));
  }
  function itemEl(it) {
    const el = h("div", { class: `rb-item${it.id === selected ? " is-selected" : ""}`, "data-id": it.id, draggable: "true", tabindex: "0",
      role: "button", "aria-pressed": String(it.id === selected), "aria-label": `${it.name} 선택` },
      h("div", { class: "rb-item-head" },
        h("span", { class: "rb-grip", "aria-hidden": "true", text: "⠿" }),
        h("span", { class: "rb-item-name", text: it.name }),
        h("span", { class: "rb-kind", text: KINDS[it.kind] }),
        h("span", { class: "rb-item-tools" },
          btn("↑", (e) => { e.stopPropagation(); moveItem(it.id, -1); }, { title: "위로", "aria-label": "위로" }),
          btn("↓", (e) => { e.stopPropagation(); moveItem(it.id, 1); }, { title: "아래로", "aria-label": "아래로" }),
          btn("복제", (e) => { e.stopPropagation(); duplicateItem(it.id); }, { title: "바로 아래에 복제" }),
          btn("삭제", (e) => { e.stopPropagation(); removeItem(it.id); }, { class: "rb-btn is-danger" }))),
      h("div", { class: "rb-item-body" }, widgetPreview(it.kind, it.config)));
    el.addEventListener("click", () => select(it.id));
    el.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === el) { e.preventDefault(); select(it.id); } });
    el.addEventListener("dragstart", (e) => { e.stopPropagation(); startDrag(e, { type: "move", id: it.id }); });
    el.addEventListener("dragend", endDrag);
    return el;
  }
  // 템플릿 전체 스타일 (layout.theme): 기본값과 다른 것만 남긴다
  function themePanel() {
    const t = (doc.layout.theme = doc.layout.theme && typeof doc.layout.theme === "object" ? doc.layout.theme : {});
    const set = (k, v, isDefault) => {
      if (isDefault) delete t[k];
      else t[k] = v;
      refreshStatus();
    };
    const box = h("div", { class: "rb-editor" });
    box.append(fontEl(t.font, (v) => set("font", v, v === ST.default_font), false));
    // 본문 글자 크기 (pt): 다른 글자(이름·제목 등)도 같은 비율로 커지고 작아진다
    const size = h("input", { type: "number", step: "0.1", min: ST.size_min, max: ST.size_max, inputmode: "decimal", "aria-label": "본문 글자 크기 (pt)" });
    size.value = String(t.size || ST.base_size);
    const apply = (v) => {
      const n = Math.round(Math.min(Math.max(v, ST.size_min), ST.size_max) * 10) / 10;
      size.value = String(n);
      set("size", n, n === ST.base_size);
    };
    size.addEventListener("change", () => { const n = parseFloat(size.value); if (Number.isFinite(n)) apply(n); else size.value = String(t.size || ST.base_size); });
    box.append(block("본문 글자 크기 (pt)", h("div", { class: "rb-size" },
      btn("−", () => apply((t.size || ST.base_size) - 0.5), { "aria-label": "글자 작게" }), size,
      btn("+", () => apply((t.size || ST.base_size) + 0.5), { "aria-label": "글자 크게" })),
    `기본 ${ST.base_size}pt (사람인과 같은 크기). 이름·제목 등도 같은 비율로 바뀝니다. ${ST.size_min}~${ST.size_max}pt`));
    for (const [k, [label, def]] of Object.entries(ST.colors)) {
      box.append(block(`${label} 색`, colorEl(t[k] || "", def, label, (v) => set(k, v, !v || v === def))));
    }
    box.append(btn("기본 스타일로 되돌리기", () => {
      if (!Object.keys(t).length) return;
      doc.layout.theme = {};
      renderInspector();
      refreshStatus();
    }, { class: "rb-btn rb-theme-reset", title: "글꼴·크기·색을 처음(사람인과 같은 모양)으로" }));
    return box;
  }

  function select(id) {
    selected = id;
    canvas.querySelectorAll(".rb-item").forEach((x) => {
      const on = x.dataset.id === id;
      x.classList.toggle("is-selected", on);
      x.setAttribute("aria-pressed", String(on));
    });
    renderInspector();
    if (window.matchMedia("(max-width: 1100px)").matches) inspector.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  // ---------------- 선택한 위젯 설정 (오른쪽)
  function renderInspector() {
    const at = selected && findItem(selected);
    if (!at) {
      inspector.replaceChildren(h("div", { class: "rb-panel-title", text: "문서 스타일" }),
        h("p", { class: "muted small", text: "템플릿 전체의 글꼴·글자 크기·색입니다. 위젯을 누르면 그 위젯의 설정과 위젯별 스타일을 고칩니다." }),
        themePanel(),
        h("div", { class: "rb-panel-title rb-help-title", text: "배치 도움말" }),
        h("ul", { class: "rb-help small muted" },
          h("li", { text: "칸 사이의 세로 손잡이를 끌거나 −/+ 로 칸 너비를 바꿉니다 (12칸 기준)." }),
          h("li", { text: "'칸 나누기'로 한 행을 가로로 나눠 위젯을 나란히 둡니다." }),
          h("li", { text: "행 사이의 점선 위에 놓으면 새 행이 생깁니다." }),
          h("li", { text: "Ctrl+S로 저장합니다." })));
      return;
    }
    const it = at.item;
    const source = library.find((w) => w.id === it.source_id);
    const back = btn("← 문서 스타일", () => {
      select(null);
    }, { class: "rb-btn rb-insp-back", title: "위젯 선택을 풀고 템플릿 전체 스타일을 고칩니다" });
    const actions = h("div", { class: "rb-inspector-actions" },
      btn("내 위젯으로 저장", async () => {
        try {
          const data = await request("POST", "/api/resume-widgets", { name: it.name, description: "", visibility: "private", kind: it.kind, config: it.config });
          library.push(data.widget);
          it.source_id = data.widget.id;
          renderPalette();
          changed(true);
          setStatus(`'${data.widget.name}'을(를) 내 위젯에 저장했습니다`, "ok");
        } catch (e) { setStatus(e.message, "error"); }
      }, { title: "이 설정을 위젯 목록에 새 위젯으로 저장해 다른 템플릿에서도 씁니다" }),
      source?.mine ? btn("원본 위젯에 반영", async () => {
        if (!window.confirm(`내 위젯 '${source.name}'을(를) 지금 설정으로 바꿀까요? 이미 이 위젯을 넣은 다른 템플릿은 바뀌지 않습니다.`)) return;
        try {
          const data = await request("PUT", `/api/resume-widgets/${source.id}`, { name: source.name, description: source.description, visibility: source.visibility, kind: it.kind, config: it.config });
          Object.assign(source, data.widget);
          renderPalette();
          setStatus(`'${source.name}' 위젯을 고쳤습니다`, "ok");
        } catch (e) { setStatus(e.message, "error"); }
      }) : null);
    inspector.replaceChildren(back,
      h("div", { class: "rb-panel-title" }, "위젯 설정 ", h("span", { class: "rb-kind", text: KINDS[it.kind] })),
      source ? h("p", { class: "muted small", text: `원본: ${source.name}${source.builtin ? " (기본 위젯)" : source.mine ? " (내 위젯)" : " (공개 위젯)"} · 여기서 고친 내용은 이 템플릿에만 적용됩니다.` }) : null,
      widgetEditor(it, () => {
        const el = canvas.querySelector(`[data-id="${it.id}"]`);
        if (el) {
          el.querySelector(".rb-item-name").textContent = it.name;
          el.querySelector(".rb-item-body").replaceChildren(widgetPreview(it.kind, it.config));
        }
      }),
      actions);
  }

  const fab = h("button", { type: "button", class: "rb-fab", "aria-label": "위젯 추가",
    onclick: () => {
      const at = selected && findItem(selected);
      openPicker({ title: "위젯 추가", hint: at ? `선택한 '${at.item.name}' 아래에 놓입니다` : "맨 아래 새 행으로 놓입니다", onPick: addFromFab });
    } }, h("span", { "aria-hidden": "true" }, "+"), " 위젯");
  app.append(fab);

  render = () => {
    renderPalette();
    renderCanvas();
    renderInspector();
    refreshStatus();
  };
  render();
})();

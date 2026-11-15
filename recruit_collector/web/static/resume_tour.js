// 템플릿·위젯 편집 화면 둘러보기 (단계별 안내).
//   처음 여는 편집 화면에서 한 번 자동으로 뜨고(브라우저에 '봤음'을 기록), 위쪽 '사용법' 버튼으로 언제든 다시 본다.
//   각 단계는 화면의 실제 요소를 밝게 강조하고 옆에 설명을 띄운다. 그 요소가 지금 화면 구성에 없으면(예: 휴대폰에서 숨긴 위젯 목록)
//   가운데 안내로 보여 준다. PC는 강조한 곳 옆 말풍선, 휴대폰(600px 이하)은 화면 아래 시트로 띄우고 강조한 곳이 시트에 가리지 않게 스크롤한다.
//   편집 내용은 바꾸지 않는다 (입력 칸 하나를 펼쳐 보이는 것만).
(() => {
  const app = document.getElementById("rb-app");
  const btn = document.getElementById("rb-tour");
  if (!app || !btn) return;
  let S = {};
  try { S = JSON.parse(document.getElementById("rb-state").textContent); } catch { /* 상태 없음 */ }
  const isTemplate = app.classList.contains("rb-template");
  const kind = isTemplate ? "template" : S.widget?.kind || "form";

  const SHEET = window.matchMedia("(max-width: 600px)"); // 아래 시트로 띄울 화면
  const NARROW = window.matchMedia("(max-width: 1100px)"); // 위젯 목록 대신 '+ 위젯' 버튼을 쓰는 화면
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");

  // ------------------------------------------------------------ 요소 찾기
  const shown = (el) => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  const $ = (sel, root = document) => [...root.querySelectorAll(sel)].find(shown) || null;
  // .rb-f(이름표 + 입력) 중 이름표가 text인 것
  const byLabel = (text, root = app) => [...root.querySelectorAll(".rb-f")]
    .find((f) => shown(f) && f.querySelector(":scope > .rb-f-label")?.textContent.trim() === text) || null;
  const openField = () => $(".rb-field.is-open", app);
  // 입력 칸 하나를 펼쳐 둔다 (힌트·예시 텍스트·도움말을 보여 주려고)
  const ensureOpenField = () => { if (!openField()) $(".rb-field-toggle", app)?.click(); };

  const metaEls = () => [$(".ed-bar .rb-meta-name"), $(".ed-bar .rb-meta-desc"), $(".ed-bar .rb-meta-vis")].filter(Boolean);

  // ------------------------------------------------------------ 단계
  // target: 요소 또는 요소 배열을 돌려주는 함수 (여러 개면 감싸는 상자를 강조). title·text는 문자열 또는 () => 문자열
  const templateSteps = [
    { title: "템플릿 만들기", text: "템플릿은 이력서의 틀입니다. 위젯(경력·학력 같은 항목)을 행과 칸에 배치해 만들고, 저장한 뒤 이 틀로 이력서를 씁니다. 주요 기능을 차례로 보여 드릴게요." },
    { target: () => metaEls(),
      title: "이름·설명·공개",
      text: "이름과 설명은 목록에서 템플릿을 구분하는 용도입니다. '공개'로 바꾸면 로그인한 다른 사용자도 보고 복사해 쓸 수 있으니 개인정보는 넣지 마세요." },
    { target: () => (NARROW.matches ? $(".rb-fab") : $(".rb-palette")),
      title: () => (NARROW.matches ? "'+ 위젯' 버튼" : "위젯 목록"),
      text: () => (NARROW.matches
        ? "'+ 위젯'을 눌러 넣을 위젯을 고릅니다. 문서에서 위젯을 먼저 선택해 두면 그 아래에, 아니면 맨 아래 새 행에 놓입니다."
        : "'추가'를 누르면 맨 아래 새 행에 놓이고, 끌어다 놓으면 원하는 칸 안이나 행 사이에 놓입니다. 기본 위젯·내 위젯·공개 위젯을 검색해 쓸 수 있어요.") },
    { target: () => $(".rb-row", app), title: "행과 칸",
      text: () => (SHEET.matches
        ? "문서는 행이 위에서 아래로 쌓여 만들어집니다. '칸 나누기'로 한 행에 위젯을 나란히 둘 수 있어요(12칸 기준). 휴대폰에서는 나눈 칸이 세로로 보이고, 너비는 −/+ 버튼과 'n/12' 표시로 정합니다. ↑↓로 행 순서를 바꿉니다."
        : "문서는 행이 위에서 아래로 쌓여 만들어집니다. '칸 나누기'로 한 행에 위젯을 나란히 둘 수 있어요(12칸 기준). 칸 사이 손잡이를 끌거나 −/+로 너비를 바꾸고, ↑↓로 행 순서를 바꿉니다.") },
    { target: () => $(".rb-item", app), title: "위젯 고치기",
      text: () => `위젯을 누르면 ${NARROW.matches ? "배치 아래" : "오른쪽"}에 설정이 열립니다. 제목·입력 칸·스타일을 바꿀 수 있고, 여기서 고친 내용은 이 템플릿에만 적용됩니다(원본 위젯은 그대로). ↑↓·복제·삭제 버튼으로 옮기거나 지웁니다.` },
    { target: () => $(".rb-inspector"), title: "문서 스타일",
      text: "위젯을 선택하지 않았을 때는 이곳에서 템플릿 전체의 글꼴·글자 크기·색을 정합니다. 위젯 하나만 다르게 하려면 그 위젯 설정 아래의 '스타일'을 여세요." },
    { target: () => $(".ed-bar .rb-meta-actions"), title: "미리보기와 저장",
      text: "'미리보기'는 저장하지 않은 지금 모양을 새 창에서 문서(PDF와 같은 모양)로 보여 줍니다. 다 만들었으면 '저장'을 누르세요(PC는 Ctrl+S)." },
    { title: "준비 끝!", text: "저장한 템플릿은 목록의 '이력서 작성'으로 바로 씁니다. 이 안내는 위쪽 '사용법'을 눌러 언제든 다시 볼 수 있어요." },
  ];

  const widgetIntro = {
    title: "위젯 만들기",
    text: "위젯은 이력서의 항목 하나입니다(예: 경력, 자격증). 만든 위젯은 템플릿 편집기의 위젯 목록에 나와 여러 템플릿에서 넣어 쓸 수 있어요.",
  };
  const widgetEnd = {
    title: "준비 끝!",
    text: "저장한 위젯은 템플릿을 편집할 때 위젯 목록에서 넣어 씁니다. 템플릿에 넣는 순간의 내용이 복사되므로, 나중에 여기서 고쳐도 이미 넣은 템플릿은 바뀌지 않아요(필요하면 템플릿에서 지우고 다시 넣으세요). 이 안내는 위쪽 '사용법'으로 다시 볼 수 있습니다.",
  };
  const previewStep = {
    target: () => $(".rb-widget-preview"), title: "미리보기",
    text: "이력서를 쓸 때 보일 입력 칸 모양입니다. 문서(PDF)에 어떻게 찍히는지는 위쪽 '문서 미리보기'로 확인하세요.",
  };
  const metaStep = {
    target: metaEls,
    title: "이름·설명·공개",
    text: "이름은 템플릿 편집기의 위젯 목록에 보이는 이름입니다. '공개'로 바꾸면 다른 사용자도 복사해 쓸 수 있으니 개인정보는 넣지 마세요.",
  };
  const formSteps = [
    widgetIntro,
    metaStep,
    { target: () => { const t = byLabel("제목"); return t ? [t, t.nextElementSibling].filter(shown) : null; }, title: "제목과 반복",
      text: "제목은 문서에 나오는 항목 이름입니다. 경력·학력처럼 여러 번 적는 항목이면 '여러 개 입력'을 켜세요. 이력서를 쓸 때 '+ 추가' 버튼이 생깁니다." },
    { target: () => byLabel("문서 표시 형식"), title: "문서 표시 형식",
      text: "문서에 어떤 모양으로 찍힐지 고릅니다. 기본(이름 아래 값), 목록(왼쪽 기간·오른쪽 내용), 카드 등이 있고, 고른 형식에 맞는 옵션만 아래에 보입니다." },
    { target: () => [$(".rb-fields-title", app), $(".rb-fields", app)].filter(Boolean), title: "입력 칸",
      text: "이력서를 쓸 때 채우는 칸들입니다. 칸을 눌러 펼치면 이름·종류(한 줄 글, 날짜, 전화번호, 이메일 등)·너비를 정하고, ↑↓로 순서를 바꿉니다. '+ 입력 칸 추가'로 늘립니다." },
    { before: ensureOpenField,
      target: () => { const f = openField(); return f ? ["힌트", "예시 텍스트", "도움말"].map((l) => byLabel(l, f)).filter(Boolean) : null; },
      title: "힌트 · 예시 텍스트 · 도움말",
      text: "· 힌트: 칸이 비어 있을 때 흐리게 보이는 안내 (문서에는 안 나옴)\n· 예시 텍스트: 이 위젯으로 이력서를 새로 만들 때 미리 채워지는 내용\n· 도움말: 입력 칸 아래에 작게 보이는 설명" },
    { when: () => !!openField() && !!byLabel("문서 위치", openField()),
      target: () => byLabel("문서 위치", openField() || app), title: "문서 위치",
      text: "목록·프로필·왼쪽 제목 형식에서 이 칸이 문서의 어느 자리(제목, 왼쪽 기간, 본문 등)에 놓일지 정합니다. 날짜 칸은 '기간으로 입력'을 켜면 시작 ~ 끝을 한 칸에 받습니다." },
    { target: () => $(".rb-style", app), title: "스타일",
      text: "이 위젯만 글꼴·글자 크기·색을 바꿀 수 있습니다. 비워 두면 템플릿의 문서 스타일을 따릅니다." },
    previewStep,
    widgetEnd,
  ];
  const simpleText = {
    heading: "제목 위젯은 문서에 큰 글자로 찍히는 제목입니다. 글자·크기·정렬과 스타일(글꼴·색)을 정합니다.",
    note: "고정문구 위젯은 문서에 적은 그대로 찍히는 문구입니다(예: '위 내용은 사실과 다름없습니다'). 정렬과 스타일을 정합니다.",
    divider: "구분선 위젯은 항목 사이를 나누는 가로선입니다. 굵기·모양·위아래 여백과 선 색을 정합니다.",
  };
  const simpleSteps = [
    widgetIntro,
    metaStep,
    { target: () => $(".rb-widget-layout .rb-panel"), title: "설정", text: simpleText[kind] || simpleText.heading },
    previewStep,
    widgetEnd,
  ];

  const steps = isTemplate ? templateSteps : kind === "form" ? formSteps : simpleSteps;
  const key = `rc-tour:${isTemplate ? "template" : kind === "form" ? "widget-form" : "widget-simple"}:v1`;

  // ------------------------------------------------------------ 화면
  const h = (tag, attrs = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v);
    }
    el.append(...kids);
    return el;
  };
  const val = (x) => (typeof x === "function" ? x() : x);

  let layer = null, spot = null, pop = null, spacer = null, list = [], i = 0, els = [], returnFocus = null, raf = 0;

  function start() {
    if (layer) return;
    list = steps.filter((s) => !s.when || s.when() !== false);
    if (!list.length) return;
    returnFocus = document.activeElement;
    layer = h("div", { class: "tour-layer" });
    spot = h("div", { class: "tour-spot", "aria-hidden": "true" });
    pop = h("div", { class: "tour-pop", role: "dialog", "aria-modal": "true", "aria-labelledby": "tour-title", "aria-describedby": "tour-text" });
    layer.append(h("div", { class: "tour-catch" }), spot, pop);
    // 휴대폰 시트가 페이지 맨 아래 요소를 가리지 않도록, 안내하는 동안 페이지 끝에 시트 높이만큼 빈 공간을 둔다
    spacer = h("div", { class: "tour-spacer", "aria-hidden": "true" });
    document.body.append(spacer, layer);
    // 안내 중에는 사용자가 페이지를 스크롤하지 못하게 한다 (안내가 강조할 곳으로 옮기는 스크롤은 그대로 된다).
    //   overflow: hidden은 스크롤바 끌기·휠을, 아래 터치·휠 막기는 overflow를 무시하는 휴대폰 브라우저(iOS)를 막는다
    document.documentElement.classList.add("tour-lock");
    layer.addEventListener("wheel", blockScroll, { passive: false });
    layer.addEventListener("touchmove", blockScroll, { passive: false });
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", queue, { passive: true });
    window.addEventListener("resize", queue);
    show(0);
  }

  function end() {
    if (!layer) return;
    try { localStorage.setItem(key, "1"); } catch { /* 저장 못 함 */ }
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("scroll", queue);
    window.removeEventListener("resize", queue);
    cancelAnimationFrame(raf);
    document.documentElement.classList.remove("tour-lock");
    layer.remove();
    spacer.remove();
    layer = spot = pop = spacer = null;
    // 처음 자동으로 떴을 때는 초점이 어디에도 없었으므로 '사용법' 버튼으로 (다시 볼 곳을 알려 주는 셈)
    (returnFocus && returnFocus !== document.body && document.contains(returnFocus) ? returnFocus : btn).focus?.({ preventScroll: true });
  }

  function show(n) {
    i = Math.max(0, Math.min(list.length - 1, n));
    const step = list[i];
    step.before?.();
    const t = step.target ? step.target() : null;
    els = (Array.isArray(t) ? t : [t]).filter(shown);
    const last = i === list.length - 1;
    const next = h("button", { type: "button", class: "btn-origin tour-next", text: i === 0 ? "시작하기" : last ? "완료" : "다음", onclick: () => (last ? end() : show(i + 1)) });
    pop.replaceChildren(
      h("div", { class: "tour-count", text: `${i + 1} / ${list.length}` }),
      h("h2", { class: "tour-title", id: "tour-title", text: val(step.title) }),
      h("p", { class: "tour-text", id: "tour-text", text: val(step.text) }),
      h("div", { class: "tour-actions" },
        last ? h("span") : h("button", { type: "button", class: "tour-skip", text: "건너뛰기", onclick: end }),
        h("span", { class: "tour-nav" },
          i > 0 ? h("button", { type: "button", class: "device-btn tour-prev", text: "이전", onclick: () => show(i - 1) }) : "",
          next)));
    layer.classList.toggle("is-center", !els.length);
    layer.classList.toggle("is-sheet", SHEET.matches);
    layer.classList.remove("is-top");
    spacer.style.height = SHEET.matches ? `${pop.offsetHeight}px` : "0";
    reveal();
    place();
    next.focus({ preventScroll: true });
  }

  // 강조할 곳이 보이게 스크롤한다 (위쪽 고정 막대와 아래 시트를 뺀 영역 안으로)
  function reveal() {
    if (!els.length || els.some(pinned)) return;
    const r = box();
    const top = (parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--sticky-top")) || 0) + 16;
    const bottom = window.innerHeight - (SHEET.matches ? pop.offsetHeight + 16 : 16);
    const room = bottom - top;
    let dy = 0;
    if (r.height > room) dy = r.top - top; // 다 안 들어가면 위쪽부터
    else if (r.top < top || r.bottom > bottom) dy = r.top - top - (room - r.height) / 2; // 가운데로
    // 멀리 떨어진 곳(화면 1.5배 넘게)은 천천히 미끄러지지 않고 바로 옮긴다
    if (Math.abs(dy) > 1) window.scrollBy({ top: dy, behavior: REDUCED.matches || Math.abs(dy) > window.innerHeight * 1.5 ? "auto" : "smooth" });
  }
  // 고정·붙박이(sticky) 요소 안이면 스크롤하지 않는다 (위쪽 막대, '+ 위젯' 버튼)
  function pinned(el) {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const p = getComputedStyle(e).position;
      if (p === "fixed" || (p === "sticky" && e.matches(".ed-bar"))) return true;
    }
    return false;
  }
  function box() {
    const rs = els.map((e) => e.getBoundingClientRect());
    const left = Math.min(...rs.map((r) => r.left)), top = Math.min(...rs.map((r) => r.top));
    const right = Math.max(...rs.map((r) => r.right)), bottom = Math.max(...rs.map((r) => r.bottom));
    return { left, top, right, bottom, width: right - left, height: bottom - top };
  }
  function queue() { cancelAnimationFrame(raf); raf = requestAnimationFrame(place); }

  function place() {
    if (!layer) return;
    layer.classList.toggle("is-sheet", SHEET.matches);
    if (!els.length || !els.every((e) => document.contains(e))) {
      spot.style.cssText = "display:none";
      pop.style.cssText = "";
      return;
    }
    const pad = 6, gap = 12, edge = 16;
    const r = box();
    spot.style.cssText = `left:${r.left - pad}px;top:${r.top - pad}px;width:${r.width + pad * 2}px;height:${r.height + pad * 2}px`;
    if (SHEET.matches) {
      // 화면에 떠 있는 요소('+ 위젯' 버튼 등)가 아래 시트에 가리면 시트를 위쪽에 띄운다
      pop.style.cssText = "";
      layer.classList.toggle("is-top", els.some(pinned) && r.bottom > window.innerHeight - pop.offsetHeight);
      return;
    }
    // 말풍선: 아래 → 위 → 오른쪽 → 왼쪽, 다 안 되면 화면 아래 구석
    const vw = document.documentElement.clientWidth, vh = window.innerHeight;
    const w = pop.offsetWidth, ph = pop.offsetHeight;
    const clampX = (x) => Math.max(edge, Math.min(vw - w - edge, x));
    const clampY = (y) => Math.max(edge, Math.min(vh - ph - edge, y));
    let x, y;
    if (r.bottom + pad + gap + ph <= vh - edge) { x = clampX(r.left); y = r.bottom + pad + gap; }
    else if (r.top - pad - gap - ph >= edge) { x = clampX(r.left); y = r.top - pad - gap - ph; }
    else if (r.right + pad + gap + w <= vw - edge) { x = r.right + pad + gap; y = clampY(r.top); }
    else if (r.left - pad - gap - w >= edge) { x = r.left - pad - gap - w; y = clampY(r.top); }
    else { x = vw - w - edge; y = vh - ph - edge; }
    pop.style.cssText = `left:${x}px;top:${y}px`;
  }

  // 시트 내용이 길어 시트 안에서 스크롤해야 할 때만 허용
  function blockScroll(e) {
    if (pop.contains(e.target) && pop.scrollHeight > pop.clientHeight) return;
    e.preventDefault();
  }

  function onKey(e) {
    if (!layer) return;
    if (["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown", " "].includes(e.key) && !(e.key === " " && e.target.closest?.("button"))) {
      e.preventDefault(); // 키보드 스크롤도 막는다 (버튼 위 스페이스는 누르기)
      return;
    }
    if (e.key === "Escape") { e.preventDefault(); end(); }
    else if (e.key === "ArrowRight" && !e.target.closest?.("input, textarea, select")) { e.preventDefault(); if (i < list.length - 1) show(i + 1); else end(); }
    else if (e.key === "ArrowLeft" && !e.target.closest?.("input, textarea, select")) { e.preventDefault(); if (i > 0) show(i - 1); }
    else if (e.key === "Tab") {
      // 안내가 떠 있는 동안 초점은 안내 안에서만 돈다
      const f = [...pop.querySelectorAll("button")];
      const at = f.indexOf(document.activeElement);
      e.preventDefault();
      f[(at + (e.shiftKey ? -1 : 1) + f.length) % f.length]?.focus();
    }
  }

  btn.addEventListener("click", start);
  let seen = true;
  try { seen = localStorage.getItem(key) === "1"; } catch { /* 저장소를 못 쓰면 매번 뜨지 않게 */ }
  if (!seen) setTimeout(start, 400); // 편집기가 다 그려진 뒤
})();

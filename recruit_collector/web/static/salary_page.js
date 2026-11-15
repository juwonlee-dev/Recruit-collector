// 연봉·인원 메뉴.
//  - 검색 페이지(/salary): 올댓컴퍼니에서 회사를 검색해 회사명·주소 목록을 보여준다. 최근 검색어는 이 브라우저에만 저장한다.
//  - 회사 페이지(/salary/c/{id}): 연봉 버튼 모달과 같은 화면을 페이지로 그린다 (static/salary.js의 SalaryView).
//  - 비교함: 검색 결과의 '+ 비교'(또는 회사 페이지의 '다른 회사와 비교')로 2~4곳을 담아 비교 페이지(/salary/compare)로 간다.
//    다른 검색어로 찾은 회사끼리도 고를 수 있게 이 브라우저에 기억한다.
// 사이트에서 온 문자열(회사명·주소)은 모두 textContent로 넣는다.
(() => {
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    }
    for (const c of children.flat()) if (c != null) node.append(c);
    return node;
  };

  // ------------------------------------------------------------ 비교함 (2~4곳: [{id, name}]. 색은 담긴 순서의 칸)
  const COMPARE_KEY = "salary.compare";
  const MAX_COMPARE = Number(document.querySelector(".compare-tray")?.dataset.max) || 4;
  const readCompare = () => {
    try {
      const list = JSON.parse(localStorage.getItem(COMPARE_KEY) || "[]");
      return Array.isArray(list)
        ? list.filter((x) => x && /^n?\d+$/.test(x.id) && typeof x.name === "string").slice(0, MAX_COMPARE) : [];
    } catch { return []; }
  };
  const writeCompare = (list) => {
    try { localStorage.setItem(COMPARE_KEY, JSON.stringify(list.slice(0, MAX_COMPARE))); } catch { /* 저장 못 하면 비교함이 비어 보일 뿐 */ }
  };

  // ------------------------------------------------------------ 회사 페이지
  const companyBody = document.querySelector("[data-salary-cid]");
  if (companyBody) {
    const cid = companyBody.dataset.salaryCid;
    const startBtn = document.querySelector(".compare-start");
    const view = window.SalaryView.create(companyBody, () => `/api/salary/company/${encodeURIComponent(cid)}`, (data) => {
      if (!data.company || !startBtn) return;
      // 이 회사를 비교함 첫 번째에 담고 검색으로 돌아가 나머지 회사를 고른다
      startBtn.hidden = false;
      startBtn.onclick = () => {
        writeCompare([{ id: cid, name: data.company.name }, ...readCompare().filter((x) => x.id !== cid)]);
        location.href = "/salary";
      };
    });
    view.start();
    let timer = null;
    window.addEventListener("resize", () => { clearTimeout(timer); timer = setTimeout(view.redraw, 150); });
    // 검색 결과에서 왔으면 뒤로 가기로 돌아가 검색어·결과를 그대로 보여준다
    const back = document.querySelector(".salary-back");
    back?.addEventListener("click", (e) => {
      if (document.referrer.startsWith(`${location.origin}/salary`)) { e.preventDefault(); history.back(); }
    });
    return;
  }

  // ------------------------------------------------------------ 검색 페이지
  const root = document.querySelector(".salary-search");
  if (!root) return;
  const form = root.querySelector("form");
  const input = form.querySelector("input");
  const recentBox = root.querySelector(".recent");
  const results = root.querySelector(".salary-results");
  const tray = document.querySelector(".compare-tray");

  // 최근 검색어: 최대 8개. 새 검색어가 앞에 오고 넘치면 가장 오래된 것부터 지운다.
  const RECENT_KEY = "salary.recent";
  const MAX_RECENT = 8;
  const readRecent = () => {
    try {
      const list = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
      return Array.isArray(list) ? list.filter((q) => typeof q === "string").slice(0, MAX_RECENT) : [];
    } catch { return []; }
  };
  const writeRecent = (list) => {
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, MAX_RECENT))); } catch { /* 저장 못 해도 검색은 된다 */ }
    renderRecent();
  };

  function renderRecent() {
    const list = readRecent();
    recentBox.hidden = !list.length;
    recentBox.replaceChildren(el("span", { class: "recent-label", text: "최근 검색" }),
      ...list.map((q) => {
        const pick = el("button", { type: "button", class: "recent-pick", text: q });
        pick.addEventListener("click", () => { input.value = q; input.focus(); });
        const del = el("button", { type: "button", class: "recent-del", "aria-label": `최근 검색어 ${q} 삭제`, text: "×" });
        del.addEventListener("click", () => writeRecent(readRecent().filter((x) => x !== q)));
        return el("span", { class: "recent-item" }, pick, del);
      }));
  }

  // 비교함 바 (화면 아래 고정)
  function renderTray() {
    const list = readCompare();
    tray.hidden = !list.length;
    document.body.classList.toggle("has-tray", list.length > 0);
    tray.querySelector(".compare-items").replaceChildren(
      ...list.map((c, i) => {
        const del = el("button", { type: "button", class: "compare-del", "aria-label": `비교함에서 ${c.name} 빼기`, text: "×" });
        del.addEventListener("click", () => { writeCompare(readCompare().filter((x) => x.id !== c.id)); renderTray(); });
        return el("span", { class: "compare-chip" }, el("i", { class: `cmp-dot cmp-${i + 1}`, "aria-hidden": "true" }),
          el("span", { class: "compare-name", text: c.name, title: c.name }), del);
      }),
      // replaceChildren은 null을 "null" 글자로 넣으므로 배열로 펼친다
      ...(list.length === 1 ? [el("span", { class: "compare-hint", text: "1곳 이상 더 선택하세요" })]
        : list.length < MAX_COMPARE ? [el("span", { class: "compare-hint", text: `최대 ${MAX_COMPARE}곳` })] : []));
    const go = tray.querySelector(".compare-go");
    go.textContent = list.length >= 2 ? `${list.length}곳 비교하기 →` : "비교하기 →";
    if (list.length >= 2) {
      go.href = `/salary/compare?ids=${list.map((c) => encodeURIComponent(c.id)).join(",")}`;
      go.removeAttribute("aria-disabled");
    } else {
      go.removeAttribute("href");
      go.setAttribute("aria-disabled", "true");
    }
    results.querySelectorAll(".compare-add").forEach(updateAddButton);
  }

  function updateAddButton(btn) {
    const list = readCompare();
    const on = list.some((x) => x.id === btn.dataset.id);
    btn.textContent = on ? "✓ 비교" : "+ 비교";
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", String(on));
    btn.disabled = !on && list.length >= MAX_COMPARE; // 다 차면 다른 회사는 담을 수 없음
  }

  results.addEventListener("click", (e) => {
    const btn = e.target.closest(".compare-add");
    if (!btn || btn.disabled) return;
    const list = readCompare();
    const { id, name } = btn.dataset;
    writeCompare(list.some((x) => x.id === id) ? list.filter((x) => x.id !== id) : [...list, { id, name }]);
    renderTray();
  });

  let controller = null;

  async function search(q, page = 1) {
    controller?.abort();
    controller = new AbortController();
    root.classList.add("is-searched");
    const more = results.querySelector(".salary-more");
    const loading = el("p", { class: "salary-loading" },
      el("span", { class: "spinner", "aria-hidden": "true" }), "올댓컴퍼니에서 검색하는 중입니다…");
    if (page === 1) results.replaceChildren(loading);
    else more?.replaceWith(loading);
    try {
      const res = await fetch(`/api/salary/search?q=${encodeURIComponent(q)}&page=${page}`, { signal: controller.signal });
      const data = await res.json().catch(() => ({ ok: false, message: `요청 실패 (HTTP ${res.status})` }));
      if (!data.ok) throw new Error(data.message || data.detail || "검색하지 못했습니다.");
      loading.remove();
      showResults(data);
    } catch (err) {
      if (err.name === "AbortError") return;
      const msg = err instanceof TypeError ? "조회 서버에 연결하지 못했습니다." : err.message;
      loading.replaceWith(el("p", { class: "notice is-error", text: msg }));
    }
  }

  function showResults(data) {
    let list = results.querySelector("ul");
    if (data.page === 1) {
      if (!data.candidates.length) {
        results.replaceChildren(el("p", { class: "notice is-plain", text: `'${data.query}'(으)로 찾은 회사가 없습니다.` }));
        return;
      }
      list = el("ul", { class: "salary-list" });
      results.replaceChildren(
        ...(data.fallback ? [el("p", { class: "notice is-warn", text: data.fallback })] : []),
        el("p", { class: "muted small", text: `'${data.query}' 검색 결과 · 회사를 누르면 연봉·인원을 봅니다` }), list,
        ...(data.source ? [el("p", { class: "salary-source muted" }, `출처: ${data.source.name} · `,
          el("a", { href: data.source.url, target: "_blank", rel: "noopener noreferrer", text: "공공데이터포털에서 보기 ↗" }))] : []));
    }
    for (const c of data.candidates) {
      const add = el("button", { type: "button", class: "compare-add", "data-id": c.id, "data-name": c.name,
        "aria-label": `${c.name} 비교함에 담기` });
      updateAddButton(add);
      // 즐겨찾기 별: 즐겨찾기를 쓸 수 있으면(로그인 사용 중 로그인) favorite가 true/false로 온다. 누르면 static/favorite.js가 처리
      const fav = typeof c.favorite === "boolean"
        ? el("button", { type: "button", class: c.favorite ? "fav-btn is-on" : "fav-btn", "data-fav-kind": "companies",
          "data-fav-id": c.id, "data-fav-name": c.name, "data-fav-address": c.address || "",
          "aria-pressed": String(c.favorite), "aria-label": `${c.name} 즐겨찾기`, title: c.favorite ? "즐겨찾기 해제" : "즐겨찾기" })
        : null;
      list.append(el("li", { class: "salary-row" }, fav, el("a", { href: `/salary/c/${encodeURIComponent(c.id)}` },
        el("strong", { text: c.name }), el("span", { class: "muted", text: c.address || "주소 없음" })), add));
    }
    if (data.has_next) {
      const more = el("button", { type: "button", class: "salary-more", text: "더 보기" });
      more.addEventListener("click", () => search(data.query, data.page + 1));
      results.append(more);
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) { input.focus(); return; }
    writeRecent([q, ...readRecent().filter((x) => x !== q)]);
    history.replaceState(null, "", `/salary?q=${encodeURIComponent(q)}`); // 회사 페이지에서 뒤로 오면 같은 검색을 다시 보여준다
    search(q);
  });

  renderRecent();
  renderTray();
  const initial = new URLSearchParams(location.search).get("q")?.trim();
  if (initial) {
    root.classList.add("no-motion"); // 뒤로 가기로 돌아온 경우: 이미 위로 올라간 상태로 시작
    input.value = initial;
    search(initial);
    requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("no-motion")));
  } else {
    input.focus();
  }
})();

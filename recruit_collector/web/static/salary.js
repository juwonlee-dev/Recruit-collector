// 회사의 월별 인원·입사자·퇴사자·월평균급여를 차트로 그린다.
//  - 회사명 옆 '연봉' 버튼 → 모달 (/api/postings/{id}/salary: 공고의 회사명·근무지역으로 회사를 찾음)
//  - 연봉·인원 메뉴의 회사 페이지 → 같은 화면을 페이지에 (/api/salary/company/{id}, static/salary_page.js)
// 자료는 올댓컴퍼니를 먼저 보고, 못 가져오면 국민연금 공공데이터로 서버가 바꿔 준다(recruit_collector/salary_sources.py).
// 그 경우 company.fallback(이유)을 위에 알리고, 맨 아래 출처(company.source)를 그 자료로 표시한다.
// 공시 기준 평균연봉(OpenDART 사업보고서)은 차트를 그린 뒤 따로 불러와 카드로 넣는다 (/api/salary/disclosure).
// 사이트에서 온 문자열(회사명·주소 등)은 모두 textContent로 넣는다.
(() => {
  const SVG = "http://www.w3.org/2000/svg";
  const RANGES = [["1y", "1년", 12], ["3y", "3년", 36], ["all", "전체", Infinity]];
  const MATCH = {
    name_address: ["회사명·주소 일치", "is-ok"],
    name: ["회사명 일치 · 주소 다름", "is-warn"],
    manual: ["직접 선택", ""],
  };

  // ------------------------------------------------------------ DOM 도우미
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
  const svg = (tag, attrs = {}) => {
    const node = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };
  const num = (v) => v.toLocaleString("ko-KR");
  const won = (v) => `${num(v)}원`;
  const manwon = (v) => `${num(Math.round(v / 10000))}만원`;

  // ------------------------------------------------------------ 화면 하나 (모달 본문 또는 페이지)
  // urlFor(cid): 불러올 API 주소. cid는 후보 목록에서 다른 회사를 고른 경우에만 주어진다.
  // onLoad(data): 불러온 뒤 호출 (회사 페이지의 '다른 회사와 비교' 버튼용)
  // opts.disclosure === false: 공시 기준 평균연봉을 불러오지 않는다 (로그인 안내용 더미 화면)
  function createView(body, urlFor, onLoad, opts = {}) {
    let state = null; // { data, candidates, range, disclosure }
    let controller = null;

    function start() {
      state = { data: null, candidates: null, range: "3y", disclosure: undefined };
      load();
    }

    async function load(cid) {
      controller?.abort();
      controller = new AbortController();
      body.replaceChildren(el("p", { class: "salary-loading" },
        el("span", { class: "spinner", "aria-hidden": "true" }), "올댓컴퍼니에서 불러오는 중입니다…"),
        el("p", { class: "salary-loading-sub muted", text: "올댓컴퍼니에서 못 가져오면 국민연금 공공데이터에서 찾습니다. 처음 보는 회사는 조금 걸릴 수 있습니다." }));
      try {
        const res = await fetch(urlFor(cid), { signal: controller.signal });
        const data = await res.json().catch(() => ({ ok: false, message: `요청 실패 (HTTP ${res.status})` }));
        if (!data.ok) throw new Error(data.message || data.detail || "조회하지 못했습니다.");
        if (data.candidates) state.candidates = data.candidates; // 직접 선택 시에는 처음 검색한 후보 목록을 유지
        state.data = data;
        state.disclosure = undefined;
        render();
        onLoad?.(data);
        loadDisclosure(data.company);
      } catch (err) {
        if (err.name === "AbortError") return;
        const msg = err instanceof TypeError ? "조회 서버에 연결하지 못했습니다." : err.message; // fetch 자체 실패(서버 꺼짐 등)
        body.replaceChildren(el("p", { class: "notice is-error", text: msg }));
      }
    }

    // 차트 폭은 화면에 붙은 뒤에 잰다
    const redraw = () => body.querySelectorAll(".chart").forEach((node) => node._draw());

    function render() {
      const { data } = state;
      const c = data.company;
      const parts = [];

      if (c?.fallback) parts.push(el("p", { class: "notice is-warn", text: c.fallback }));
      if (!c && data.notice) parts.push(el("p", { class: "notice is-warn", text: data.notice }));
      if (c) {
        const [label, cls] = MATCH[data.match] || ["", ""];
        parts.push(el("div", { class: "salary-company-info" },
          el("div", { class: "salary-company-line" },
            el("strong", { text: c.name }),
            label ? el("span", { class: `match-badge ${cls}`, text: label }) : null),
          el("div", { class: "muted", text: [c.address, c.industry].filter(Boolean).join(" · ") })));
      } else {
        parts.push(el("p", { class: "notice is-plain",
          text: `'${data.query}'(으)로 검색한 결과에 회사명이 같은 회사가 없습니다. 아래 후보에서 직접 고를 수 있습니다.` }));
      }
      parts.push(candidatePicker(c));
      // 공시 기준 평균연봉: 기간 탭(1년·3년·전체)과 관계없는 연간 자료라 탭보다 위에 둔다
      if (c) parts.push(disclosureCard());

      if (c && c.months.length) {
        const months = sliceRange(c.months);
        parts.push(rangeFilter());
        parts.push(tiles(months));
        parts.push(chartCard("총인원", months, [{ key: "headcount", label: "총인원", color: "var(--series-1)" }], (v) => `${num(v)}명`, false));
        parts.push(chartCard("입사자 · 퇴사자", months, [
          { key: "hires", label: "입사자", color: "var(--series-1)" },
          { key: "leaves", label: "퇴사자", color: "var(--series-2)" },
        ], (v) => `${num(v)}명`, true));
        parts.push(chartCard("월평균급여 (추정)", months, [{ key: "salary", label: "월평균급여", color: "var(--series-1)" }], won, false, manwon));
        parts.push(dataTable(months));
      } else if (c) {
        parts.push(el("p", { class: "notice is-plain", text: "이 회사에는 월별 자료가 없습니다." }));
      }

      parts.push(sourceLine(c, data.query));
      body.replaceChildren(...parts.filter(Boolean)); // 후보 목록이 없으면(연봉·인원 메뉴) 고르기 칸을 뺀다
      redraw();
    }

    // ---------------------------------------------------------- 공시 기준 평균연봉 (OpenDART)
    // 회사마다 한 번만 불러오고(기간을 바꿔 다시 그려도 그대로), 오면 카드 자리만 바꾼다
    async function loadDisclosure(company) {
      if (!company || opts.disclosure === false) return;
      state.disclosure = undefined;
      const mine = state;
      try {
        const res = await fetch(`/api/salary/disclosure?name=${encodeURIComponent(company.name)}&address=${encodeURIComponent(company.address || "")}`);
        const data = await res.json().catch(() => ({ ok: false, message: `요청 실패 (HTTP ${res.status})` }));
        mine.disclosure = data.ok ? data : { error: data.message || data.detail || "불러오지 못했습니다." };
      } catch (err) {
        mine.disclosure = { error: err instanceof TypeError ? "조회 서버에 연결하지 못했습니다." : err.message };
      }
      if (mine !== state || state.data?.company !== company) return;  // 그 사이 다른 회사를 골랐으면 버린다
      body.querySelector(".salary-disclosure")?.replaceWith(disclosureCard() || "");
    }

    function disclosureCard() {
      if (opts.disclosure === false) return null;
      const d = state.disclosure;
      const head = el("h3", {}, "공시 기준 평균연봉", el("span", { class: "muted", text: " DART 사업보고서 · 직원 1인 평균 급여" }));
      if (d === undefined) {
        return el("section", { class: "salary-disclosure" }, head,
          el("p", { class: "salary-loading" }, el("span", { class: "spinner", "aria-hidden": "true" }), "전자공시에서 불러오는 중입니다…"));
      }
      if (d.available === false) return el("section", { class: "salary-disclosure", hidden: "" });  // 인증키가 없는 서버
      if (d.error) return el("section", { class: "salary-disclosure" }, head, el("p", { class: "muted small", text: d.error }));
      const info = d.disclosure;
      if (!info) {
        return el("section", { class: "salary-disclosure" }, head,
          el("p", { class: "muted small", text: "사업보고서를 공시하는 회사(상장사 등)로 찾지 못해 공시 기준 평균연봉이 없습니다." }));
      }
      const years = info.years.map((y) => el(y.url ? "a" : "div", {
        class: "disc-year", ...(y.url ? { href: y.url, target: "_blank", rel: "noopener noreferrer", title: `${y.year}년 사업보고서 보기` } : {}) },
        el("span", { class: "disc-y", text: `${y.year}년${y.url ? " ↗" : ""}` }),
        el("b", { text: manwon(y.avg) }),
        el("span", { class: "muted", text: `직원 ${num(y.headcount)}명` })));
      const recent = (state.data.company?.months || []).filter((m) => m.salary != null).at(-1);
      const notes = [`'${info.corp_name}'${info.stock_code ? `(상장 ${info.stock_code})` : ""} 사업보고서의 직원 1인 평균 급여(부문·성별 직원 수로 가중 평균)입니다.`];
      if (recent) notes.push(`참고: 최근 월평균급여(추정, ${recent.ym}) × 12 = 연 약 ${manwon(recent.salary * 12)}`);
      return el("section", { class: "salary-disclosure" }, head, el("div", { class: "disc-years" }, years),
        el("p", { class: "muted small", text: notes.join(" · ") }),
        el("p", { class: "salary-source muted", text: `출처: ${info.source}` }));
    }

    // 맨 아래 출처: 회사 자료가 온 곳 (올댓컴퍼니 또는 국민연금 공공데이터)
    function sourceLine(c, query) {
      const src = c?.source || { key: "allthatcompany", name: "올댓컴퍼니", url: c?.url,
        note: "국민연금 가입 사업장 자료 기반 추정치. 실제 급여와 다를 수 있습니다" };
      const href = c ? src.url : `https://allthatcompany.com/search?q=${encodeURIComponent(query)}`;
      return el("p", { class: "salary-source muted" }, `출처: ${src.name} (${src.note}) · `,
        el("a", { href, target: "_blank", rel: "noopener noreferrer",
          text: src.key === "nps" ? "공공데이터포털에서 보기 ↗" : "올댓컴퍼니에서 보기 ↗" }));
    }

    function candidatePicker(current) {
      const list = state.candidates || [];
      if (!list.length) return null;
      const select = el("select", { "aria-label": "다른 회사 선택" });
      if (!current) select.append(el("option", { value: "", text: "회사를 선택하세요" }));
      for (const cand of list) {
        const mark = cand.name_match && cand.address_match ? "● " : cand.name_match ? "○ " : "";
        const opt = el("option", { value: cand.id, text: `${mark}${cand.name} — ${cand.address}` });
        if (current && cand.id === current.id) opt.selected = true;
        select.append(opt);
      }
      if (current && !list.some((x) => x.id === current.id)) select.prepend(el("option", { value: current.id, text: current.name, selected: "" }));
      select.addEventListener("change", () => { if (select.value) load(select.value); });
      return el("label", { class: "salary-picker" },
        el("span", { class: "muted", text: `다른 회사 (검색 결과 ${list.length}곳 · ●회사명·주소 일치 ○회사명 일치)` }), select);
    }

    function sliceRange(all) {
      const n = RANGES.find(([k]) => k === state.range)[2];
      return Number.isFinite(n) ? all.slice(-n) : all;
    }

    function rangeFilter() {
      const group = el("div", { class: "seg", role: "group", "aria-label": "기간" });
      for (const [key, label] of RANGES) {
        const b = el("button", { type: "button", text: label, "aria-pressed": String(state.range === key) });
        b.addEventListener("click", () => { state.range = key; render(); });
        group.append(b);
      }
      const c = state.data.company;
      return el("div", { class: "salary-filter" }, group,
        el("span", { class: "muted", text: `${c.months[0].ym} ~ ${c.months.at(-1).ym} 자료 중` }));
    }

    return { start, redraw, abort: () => controller?.abort() };
  }

  // 비교 페이지(static/salary_compare.js)도 같은 차트·도우미를 쓴다
  window.SalaryView = { create: createView, chartCard, el, num, won, manwon };

  // ------------------------------------------------------------ 공유하기 = 링크 복사 (연봉·인원 회사 페이지·회사 비교 페이지의 .share-btn)
  // 누를 때의 주소를 복사한다. 클립보드 API는 https(또는 localhost)에서만 쓸 수 있어, http 접속에서는 예전 방식(execCommand)으로
  // 복사하고, 그래도 안 되면 주소를 직접 복사할 수 있게 보여준다.
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); return true; } catch { /* 아래 방식으로 */ }
    }
    const ta = el("textarea", { readonly: "", style: "position:fixed;top:0;left:0;opacity:0" });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }
  document.querySelectorAll(".share-btn").forEach((btn) => btn.addEventListener("click", async () => {
    const url = location.href;
    if (await copyText(url)) {
      btn.textContent = "✓ 링크 복사됨";
      btn.classList.add("is-done");
      clearTimeout(btn._t);
      btn._t = setTimeout(() => { btn.textContent = "공유하기"; btn.classList.remove("is-done"); }, 2000);
    } else {
      window.prompt("아래 링크를 복사하세요", url);
    }
  }));

  // ------------------------------------------------------------ 연봉 버튼 → 모달
  const modal = document.getElementById("salary-modal");
  // 로그인 사용 중 비로그인(data-locked): 올댓컴퍼니를 조회하지 않고, 실제 화면과 같은 구성을 더미 자료로 그린다.
  // 본문은 흐리게 하고 inert로 막아 두었으므로(base.html) 동작하는 것은 닫기(×)와 로그인 링크뿐이다
  if (modal?.hasAttribute("data-locked")) {
    const dummy = JSON.stringify(dummySalary());
    const view = createView(modal.querySelector(".salary-body"), () => `data:application/json,${encodeURIComponent(dummy)}`,
      undefined, { disclosure: false });
    document.addEventListener("click", (e) => {
      const btn = e.target.closest(".salary-btn");
      if (!btn) return;
      modal.querySelector(".salary-company").textContent = btn.dataset.company || "";
      modal.showModal();
      view.start();
    });
    modal.addEventListener("close", () => view.abort());
  } else if (modal) {
    let postingId = null;
    const compare = compareButton(modal);
    const view = createView(modal.querySelector(".salary-body"),
      (cid) => `/api/postings/${postingId}/salary` + (cid ? `?cid=${encodeURIComponent(cid)}` : ""),
      (data) => compare?.show(data.company));
    document.addEventListener("click", (e) => {
      const btn = e.target.closest(".salary-btn");
      if (!btn) return;
      modal.querySelector(".salary-company").textContent = btn.dataset.company || "";
      postingId = btn.dataset.posting;
      compare?.show(null);
      modal.showModal();
      view.start();
    });
    modal.addEventListener("close", () => view.abort());
  }

  // 로그인 안내 모달 뒤에 깔 더미 자료 (실제 회사·수치가 아님). 최근 36개월, 늘 같은 모양
  function dummySalary() {
    const now = new Date();
    const months = [];
    for (let i = 36; i >= 1; i--) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      const t = 36 - i;
      months.push({
        ym: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`,
        headcount: Math.round(48 + t * 0.5 + 4 * Math.sin(t / 3)),
        hires: Math.max(0, Math.round(1.6 + 1.3 * Math.sin(t * 1.7) + Math.sin(t * 0.45))),
        leaves: Math.max(0, Math.round(1.2 + 1.1 * Math.cos(t * 2.3) + 0.8 * Math.sin(t * 0.7))),
        salary: Math.round((3200000 + t * 12000 + 120000 * Math.sin(t / 2)) / 1000) * 1000,
      });
    }
    const company = { id: "0", name: "예시 주식회사", address: "서울특별시 ○○구 ○○로 123", industry: "소프트웨어 개발",
      url: "#", months };
    return { ok: true, query: "예시", match: "name_address", company,
      candidates: [{ id: "0", name: company.name, address: company.address, name_match: true, address_match: true }] };
  }

  // ------------------------------------------------------------ 연봉 모달의 '비교하기' (연봉·인원 메뉴를 쓸 수 있을 때만 그려짐)
  // 연봉·인원 메뉴의 비교함(static/salary_page.js와 같은 저장 키, 이 브라우저에 저장)에 모달의 회사를 담는다.
  //  - 2곳 이상이 되면 비교 분석 페이지로 갈지 묻는다.
  //  - 가득 찼으면 담긴 회사 중 하나를 골라 교체한 뒤(같은 칸·같은 색) 똑같이 묻는다.
  function compareButton(modal) {
    const btn = modal.querySelector(".salary-compare-btn");
    if (!btn) return null;
    const KEY = "salary.compare";
    const MAX = Number(modal.dataset.compareMax) || 4;
    const goModal = document.getElementById("compare-go-modal");
    const fullModal = document.getElementById("compare-full-modal");
    const okBtn = fullModal.querySelector(".compare-replace-ok");
    let company = null; // { id, name }
    let pending = null; // 교체 대기 중인 회사

    const read = () => {
      try {
        const list = JSON.parse(localStorage.getItem(KEY) || "[]");
        return Array.isArray(list)
          ? list.filter((x) => x && /^n?\d+$/.test(x.id) && typeof x.name === "string").slice(0, MAX) : [];
      } catch { return []; }
    };
    const write = (list) => {
      try { localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX))); return true; } catch { return false; }
    };
    const has = (list) => list.some((x) => x.id === company.id);

    function refresh() {
      const inList = company && has(read());
      btn.classList.toggle("is-in", Boolean(inList));
      btn.textContent = inList ? "✓ 비교함에 담김" : "+ 비교하기";
      btn.title = inList ? "비교함에 담겨 있습니다. 누르면 비교 분석으로 갈 수 있습니다." : "연봉·인원 메뉴의 비교함에 담습니다";
    }

    const row = (c, i, isNew) => el("li", { class: isNew ? "is-new" : "" },
      el("i", { class: `cmp-dot cmp-${i + 1}`, "aria-hidden": "true" }),
      el("span", { class: "compare-modal-name", text: c.name, title: c.name }),
      isNew ? el("span", { class: "compare-modal-tag", text: "방금 담음" }) : null);

    // 담은 뒤: 2곳 이상이면 비교 분석으로 갈지 묻는다. 1곳뿐이면 버튼 표시만 바꾼다
    function afterAdd(list, added) {
      refresh();
      if (list.length < 2) return;
      goModal.querySelector("#compare-go-title").textContent = added ? "비교함에 담았습니다" : "이미 비교함에 있습니다";
      goModal.querySelector(".compare-go-text").textContent = `비교함에 ${list.length}곳이 있습니다. 비교 분석 페이지로 이동할까요?`;
      goModal.querySelector(".compare-modal-list").replaceChildren(...list.map((c, i) => row(c, i, added && c.id === company.id)));
      goModal.querySelector(".compare-go-link").href = `/salary/compare?ids=${list.map((c) => encodeURIComponent(c.id)).join(",")}`;
      goModal.showModal();
    }

    btn.addEventListener("click", () => {
      if (!company) return;
      const list = read();
      if (has(list)) { afterAdd(list, false); return; }
      if (list.length < MAX) {
        list.push(company);
        if (write(list)) afterAdd(list, true);
        return;
      }
      // 가득 참: 교체할 회사를 고른다
      pending = company;
      // 안내 한 줄, 다음 줄에 새 회사 이름(굵게)과 고를 일
      fullModal.querySelector(".compare-full-text").replaceChildren(
        `비교함에는 최대 ${MAX}곳까지 담을 수 있습니다.`, el("br"),
        el("strong", { class: "compare-full-name", text: company.name }), " 대신 뺄 회사를 고르세요.");
      const box = fullModal.querySelector(".compare-replace-list");
      box.replaceChildren(box.querySelector("legend"), ...list.map((c, i) => el("label", {},
        el("input", { type: "radio", name: "compare-replace", value: c.id }),
        el("i", { class: `cmp-dot cmp-${i + 1}`, "aria-hidden": "true" }),
        el("span", { class: "compare-modal-name", text: c.name, title: c.name }))));
      okBtn.disabled = true;
      fullModal.showModal();
      box.querySelector("input")?.focus(); // 닫기(×) 대신 첫 회사부터 고르게
    });
    fullModal.addEventListener("change", () => { okBtn.disabled = !fullModal.querySelector("input:checked"); });
    okBtn.addEventListener("click", () => {
      const picked = fullModal.querySelector("input:checked")?.value;
      if (!picked || !pending) return;
      const list = read().map((c) => (c.id === picked ? pending : c)).filter((c, i, all) => all.findIndex((x) => x.id === c.id) === i);
      fullModal.close();
      if (write(list)) afterAdd(list, true);
    });
    fullModal.addEventListener("close", () => { pending = null; });

    return {
      // 회사를 찾으면 버튼을 보이고, 불러오는 중이거나 회사가 없으면 숨긴다
      show(c) {
        company = c ? { id: String(c.id), name: c.name } : null;
        btn.hidden = !company;
        if (company) refresh();
      },
    };
  }

  function tiles(months) {
    const last = months.at(-1);
    const first = months[0];
    const salaries = months.filter((m) => m.salary != null);
    const lastSal = salaries.at(-1);
    const firstSal = salaries[0];
    const sum = (k) => months.reduce((a, m) => a + m[k], 0);
    const delta = (a, b, fmt) => {
      const d = a - b;
      return d === 0 ? "기간 내 변동 없음" : `기간 내 ${d > 0 ? "+" : "−"}${fmt(Math.abs(d))}`;
    };
    const tile = (label, value, sub) => el("div", { class: "stat salary-stat" },
      el("div", { class: "stat-label", text: label }),
      el("div", { class: "stat-value", text: value }),
      el("div", { class: "stat-sub muted", text: sub }));
    return el("div", { class: "salary-tiles" },
      tile(`총인원 (${last.ym})`, `${num(last.headcount)}명`, delta(last.headcount, first.headcount, (v) => `${num(v)}명`)),
      tile(`월평균급여 (${lastSal ? lastSal.ym : "-"})`, lastSal ? manwon(lastSal.salary) : "-",
        lastSal && firstSal && firstSal !== lastSal
          ? `기간 내 ${((lastSal.salary / firstSal.salary - 1) * 100).toFixed(1).replace(/^(?!-)/, "+")}%` : "추정치"),
      tile("입사자 합계", `${num(sum("hires"))}명`, `월평균 ${num(Math.round(sum("hires") / months.length))}명`),
      tile("퇴사자 합계", `${num(sum("leaves"))}명`, `월평균 ${num(Math.round(sum("leaves") / months.length))}명`));
  }

  function dataTable(months) {
    const rows = [...months].reverse().map((m) => el("tr", {},
      el("th", { scope: "row", text: m.ym }),
      el("td", { text: `${num(m.headcount)}명` }), el("td", { text: `${num(m.hires)}명` }),
      el("td", { text: `${num(m.leaves)}명` }), el("td", { text: m.salary != null ? won(m.salary) : "-" })));
    return el("details", { class: "salary-table" }, el("summary", { text: "표로 보기" }),
      el("div", { class: "table-scroll" }, el("table", {},
        el("thead", {}, el("tr", {}, ...["기준년월", "총인원", "입사자", "퇴사자", "월평균급여(추정)"].map((t) => el("th", { scope: "col", text: t })))),
        el("tbody", {}, rows))));
  }

  // ------------------------------------------------------------ 선 차트 (한 차트에 y축 하나)
  const monthIndex = (ym) => { const [y, m] = ym.split("-").map(Number); return y * 12 + m - 1; };

  function niceTicks(lo, hi, count = 4) {
    if (lo === hi) { lo = lo > 0 ? lo * 0.9 : 0; hi = hi > 0 ? hi * 1.1 : 1; }
    const raw = (hi - lo) / count;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => s >= raw);
    const start = Math.floor(lo / step) * step;
    const ticks = [];
    for (let v = start; v <= hi + step * 1e-9; v += step) ticks.push(v);
    if (ticks.at(-1) < hi) ticks.push(ticks.at(-1) + step);
    return ticks;
  }

  // 범례·툴팁의 계열 표시: 점선 계열(dash)은 표시도 점선으로 (색이 비슷한 계열을 선 모양으로도 구분)
  const swatch = (s) => el("i", { style: s.dash
    ? `background:repeating-linear-gradient(90deg, ${s.color} 0 4px, transparent 4px 7px)`
    : `background:${s.color}` });

  // series: [{ key, label, color, short?, dash? }]. short는 선 끝 이름표, dash는 점선 패턴 (예: "6 4")
  function chartCard(title, months, series, fmt, fromZero, axisFmt = fmt) {
    const box = el("div", { class: "chart", tabindex: "0", role: "img",
      "aria-label": `${title} 월별 추이 차트. 좌우 화살표로 월을 이동할 수 있습니다.` });
    const tip = el("div", { class: "chart-tip", hidden: "" });
    const legend = series.length > 1
      ? el("div", { class: "chart-legend" }, series.map((s) => el("span", {}, swatch(s), s.label)))
      : null;
    const card = el("section", { class: "chart-card" }, el("h3", { text: title }), legend, box);
    box.append(tip);

    box._draw = () => {
      box.querySelector("svg")?.remove();
      const W = box.clientWidth || 600, H = 170;
      const m = { t: 10, r: 52, b: 24, l: 58 }; // 오른쪽 여백은 끝 이름표 자리. 차트끼리 x축을 맞추려고 모두 같게 둔다
      const xs = months.map((mo) => monthIndex(mo.ym));
      const x0 = xs[0], x1 = Math.max(xs.at(-1), x0 + 1);
      const values = series.flatMap((s) => months.map((mo) => mo[s.key]).filter((v) => v != null));
      if (!values.length) { box.append(el("p", { class: "muted", text: "자료 없음" })); return; }
      const ticks = niceTicks(fromZero ? 0 : Math.min(...values), Math.max(...values));
      const y0 = ticks[0], y1 = ticks.at(-1);
      const X = (i) => m.l + ((i - x0) / (x1 - x0)) * (W - m.l - m.r);
      const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0 || 1)) * (H - m.t - m.b);
      const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, "aria-hidden": "true" });

      // 격자·y축 눈금
      for (const t of ticks) {
        root.append(svg("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: "grid" }));
        const label = svg("text", { x: m.l - 8, y: Y(t) + 4, "text-anchor": "end", class: "axis" });
        label.textContent = axisFmt(t);
        root.append(label);
      }
      // x축 눈금: 기간에 따라 1월마다(길면 몇 년마다) 또는 분기마다
      const span = x1 - x0;
      const every = span <= 15 ? 3 : span <= 48 ? 12 : 24;
      for (let i = Math.ceil(x0 / every) * every; i <= x1; i += every) {
        const y = Math.floor(i / 12), mo = (i % 12) + 1;
        const label = svg("text", { x: X(i), y: H - 6, "text-anchor": "middle", class: "axis" });
        label.textContent = every < 12 ? `${String(y).slice(2)}.${String(mo).padStart(2, "0")}` : `${y}`;
        root.append(label);
      }
      // 선: 자료가 빠진 달(1개월 넘는 간격·값 없음)에서는 끊는다
      const endLabels = [];
      for (const s of series) {
        let d = "", prev = null;
        months.forEach((mo, k) => {
          const v = mo[s.key];
          if (v == null) { prev = null; return; }
          const gap = prev != null && xs[k] - xs[prev] > 1;
          d += `${prev == null || gap ? "M" : "L"}${X(xs[k]).toFixed(1)},${Y(v).toFixed(1)}`;
          prev = k;
        });
        root.append(svg("path", { d, class: "line", stroke: s.color, ...(s.dash ? { "stroke-dasharray": s.dash } : {}) }));
        // 마지막 값 표시 (두 계열이면 끝에 이름도 붙인다)
        const lastK = months.map((mo) => mo[s.key]).findLastIndex((v) => v != null);
        if (lastK >= 0) {
          root.append(svg("circle", { cx: X(xs[lastK]), cy: Y(months[lastK][s.key]), r: 3.5, fill: s.color, class: "end-dot" }));
          if (series.length > 1) endLabels.push({ x: X(xs[lastK]) + 7, y: Y(months[lastK][s.key]) + 4, text: s.short || s.label }); // short: 끝 이름표용 짧은 이름
        }
      }
      // 끝 이름표: 값이 가까우면 겹치지 않게 위아래로 벌린다
      endLabels.sort((a, b) => a.y - b.y);
      endLabels.forEach((lb, i) => { if (i && lb.y - endLabels[i - 1].y < 13) lb.y = endLabels[i - 1].y + 13; });
      // 아래로 밀려 차트 밖으로 나가면 묶음째 위로 올린다 (계열이 많을 때)
      const over = (endLabels.at(-1)?.y ?? 0) - (H - m.b + 4);
      if (over > 0) endLabels.forEach((lb) => { lb.y = Math.max(m.t + 8, lb.y - over); });
      for (const lb of endLabels) {
        const t = svg("text", { x: lb.x, y: lb.y, class: "direct" });
        t.textContent = lb.text;
        root.append(t);
      }
      // 십자선·툴팁
      const cross = svg("line", { y1: m.t, y2: H - m.b, class: "cross", visibility: "hidden" });
      const dots = series.map((s) => svg("circle", { r: 4.5, fill: s.color, class: "hover-dot", visibility: "hidden" }));
      root.append(cross, ...dots);
      box.prepend(root);

      let cur = null;
      const show = (k) => {
        cur = k;
        const mo = months[k], cx = X(xs[k]);
        cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
        series.forEach((s, j) => {
          const v = mo[s.key];
          dots[j].setAttribute("visibility", v == null ? "hidden" : "visible");
          if (v != null) { dots[j].setAttribute("cx", cx); dots[j].setAttribute("cy", Y(v)); }
        });
        tip.replaceChildren(el("div", { class: "tip-month", text: mo.ym }),
          ...series.map((s) => el("div", { class: "tip-row" },
            swatch(s),
            el("strong", { text: mo[s.key] == null ? "자료 없음" : fmt(mo[s.key]) }),
            el("span", { text: s.label }))));
        tip.hidden = false;
        const tw = tip.offsetWidth;
        tip.style.left = `${Math.min(Math.max(cx - tw / 2, 0), W - tw)}px`;
      };
      const hide = () => {
        cur = null; tip.hidden = true; cross.setAttribute("visibility", "hidden");
        dots.forEach((dt) => dt.setAttribute("visibility", "hidden"));
      };
      const nearest = (px) => {
        const target = x0 + ((px - m.l) / (W - m.l - m.r)) * (x1 - x0);
        let best = 0;
        xs.forEach((v, k) => { if (Math.abs(v - target) < Math.abs(xs[best] - target)) best = k; });
        return best;
      };
      root.addEventListener("pointermove", (e) => show(nearest(e.clientX - root.getBoundingClientRect().left)));
      root.addEventListener("pointerleave", hide);
      box.onkeydown = (e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        const k = cur == null ? months.length - 1 : cur + (e.key === "ArrowRight" ? 1 : -1);
        show(Math.min(Math.max(k, 0), months.length - 1));
      };
      box.onblur = hide;
    };
    return card;
  }
})();

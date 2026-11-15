// 회사 비교 페이지(/salary/compare?ids=1,2,3,4): 회사 2~4곳의 월별 자료를 같은 차트에 겹쳐 그린다.
//  - 자료는 회사별 API(/api/salary/company/{id})로 불러온다 (올댓컴퍼니에서 볼 때마다 가져옴).
//    올댓컴퍼니에서 못 가져온 회사는 서버가 국민연금 공공데이터로 바꿔 주며, 그 이유와 회사별 출처를 표시한다.
//    올댓컴퍼니에 한꺼번에 몰리지 않게 동시에 2곳까지만 요청한다 (두 회사 비교 때와 같은 부담)
//  - 공시 기준 평균연봉(OpenDART 사업보고서)은 차트를 그린 뒤 회사별로 불러와 표로 넣는다 (/api/salary/disclosure)
//  - 기간은 회사들의 자료를 모두 합친 범위. 자료가 없는 달은 그 회사만 비워 둔다 (선이 끊김)
//  - 입사·퇴사는 회사 규모 차이를 없애려고 총인원 대비 비율(%)로 본다
//  - 색은 회사에 붙는다 (처음 불러온 순서의 칸 1~4). 회사를 빼도 남은 회사의 색은 그대로다.
//    4번째 칸(노랑)은 2번째(주황)와 색이 가까워 점선으로도 구분한다
// 사이트에서 온 문자열(회사명·주소 등)은 모두 textContent로 넣는다 (SalaryView.el).
(() => {
  const body = document.querySelector(".compare-body");
  if (!body) return;
  const { chartCard, el, num, won, manwon } = window.SalaryView;
  const RANGES = [["1y", "1년", 12], ["3y", "3년", 36], ["all", "전체", Infinity]];
  const SLOTS = [
    { tag: "A", color: "var(--series-1)" },
    { tag: "B", color: "var(--series-2)" },
    { tag: "C", color: "var(--series-3)" },
    { tag: "D", color: "var(--series-4)", dash: "6 4" },
  ];
  const MAX = Number(body.dataset.max) || 4;
  const CONCURRENCY = 2;

  // [{ id, slot, company }] 화면에 보이는 순서. slot은 색·이름표 칸 (회사에 고정)
  let items = body.dataset.ids.split(",").filter(Boolean).map((id, slot) => ({ id, slot, company: null }));
  let range = "3y";
  let headMode = "count"; // count: 실제 인원, index: 시작 달 = 100
  let tableMetric = "head";
  const disclosures = new Map(); // 회사 ID → undefined(불러오는 중) | { available, disclosure } | { error }

  const monthIndex = (ym) => { const [y, m] = ym.split("-").map(Number); return y * 12 + m - 1; };
  const pct = (v) => `${v.toFixed(1)}%`;
  const signed = (v, fmt) => (v === 0 ? "0" : `${v > 0 ? "+" : "−"}${fmt(Math.abs(v))}`);
  const slotOf = (it) => SLOTS[it.slot];
  const dot = (it) => el("i", { class: `cmp-dot cmp-${it.slot + 1}`, "aria-hidden": "true" });

  // ------------------------------------------------------------ 불러오기
  async function fetchCompany(id) {
    const res = await fetch(`/api/salary/company/${encodeURIComponent(id)}`);
    const data = await res.json().catch(() => ({ ok: false, message: `요청 실패 (HTTP ${res.status})` }));
    if (!data.ok) throw new Error(data.message || data.detail || "조회하지 못했습니다.");
    return data.company;
  }

  async function load() {
    const status = el("span", { text: `회사 ${items.length}곳 자료를 올댓컴퍼니에서 불러오는 중입니다… (0/${items.length})` });
    body.replaceChildren(el("p", { class: "salary-loading" }, el("span", { class: "spinner", "aria-hidden": "true" }), status));
    const failed = [];
    let done = 0, next = 0;
    const worker = async () => {
      while (next < items.length) {
        const it = items[next++];
        try { it.company = await fetchCompany(it.id); } catch (err) {
          failed.push({ it, msg: err instanceof TypeError ? "조회 서버에 연결하지 못했습니다." : err.message });
        }
        status.textContent = `회사 ${items.length}곳 자료를 올댓컴퍼니에서 불러오는 중입니다… (${++done}/${items.length})`;
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, items.length) }, worker));
    items = items.filter((it) => it.company);
    if (items.length < 2) {
      body.replaceChildren(...failed.map(({ msg }) => el("p", { class: "notice is-error", text: msg })),
        el("p", { class: "notice is-plain", text: "비교할 회사가 2곳 이상 필요합니다." }));
      return;
    }
    render(failed.length ? `회사 ${failed.length}곳의 자료를 불러오지 못해 빼고 비교합니다. (${failed[0].msg})` : "");
    if (failed.length) updateUrl();
    loadDisclosures();
  }

  // ------------------------------------------------------------ 공시 기준 평균연봉 (OpenDART)
  async function loadDisclosures() {
    const queue = items.filter((it) => !disclosures.has(it.id));
    const worker = async () => {
      while (queue.length) {
        const it = queue.shift();
        const c = it.company;
        try {
          const res = await fetch(`/api/salary/disclosure?name=${encodeURIComponent(c.name)}&address=${encodeURIComponent(c.address || "")}`);
          const data = await res.json().catch(() => ({ ok: false, message: `요청 실패 (HTTP ${res.status})` }));
          disclosures.set(it.id, data.ok ? data : { error: data.message || data.detail || "불러오지 못했습니다." });
        } catch (err) {
          disclosures.set(it.id, { error: err instanceof TypeError ? "조회 서버에 연결하지 못했습니다." : err.message });
        }
        body.querySelector(".salary-disclosure")?.replaceWith(disclosureCard());
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker));
  }

  function disclosureCard() {
    if (items.every((it) => disclosures.get(it.id)?.available === false)) return el("section", { class: "salary-disclosure", hidden: "" });
    const rows = items.map((it) => {
      const d = disclosures.get(it.id);
      const name = el("th", { scope: "row" }, dot(it), ` ${slotOf(it).tag} ${it.company.name}`);
      if (d === undefined) return el("tr", {}, name, el("td", { colspan: "3", class: "muted", text: "불러오는 중…" }));
      if (d.error) return el("tr", {}, name, el("td", { colspan: "3", class: "muted", text: d.error }));
      const info = d.disclosure;
      if (!info) return el("tr", {}, name, el("td", { colspan: "3", class: "muted", text: "공시 회사로 찾지 못함" }));
      const [y, prev] = info.years;
      const change = prev ? `${((y.avg / prev.avg - 1) * 100).toFixed(1).replace(/^(?!-)/, "+")}% (${prev.year}년 대비)` : "-";
      return el("tr", {}, name,
        el("td", {}, y.url ? el("a", { href: y.url, target: "_blank", rel: "noopener noreferrer", text: `${y.year}년 ${manwon(y.avg)} ↗` })
          : `${y.year}년 ${manwon(y.avg)}`),
        el("td", { text: `${num(y.headcount)}명` }), el("td", { text: change }));
    });
    return el("section", { class: "salary-disclosure" },
      el("h3", {}, "공시 기준 평균연봉", el("span", { class: "muted", text: " DART 사업보고서 · 직원 1인 평균 급여" })),
      el("div", { class: "table-scroll" }, el("table", { class: "disc-table" },
        el("thead", {}, el("tr", {}, ...["회사", "최근 사업연도", "직원 수", "전년 대비"].map((t) => el("th", { scope: "col", text: t })))),
        el("tbody", {}, rows))),
      el("p", { class: "muted small", text: "사업보고서를 공시하는 회사(상장사 등)만 있습니다. 부문·성별 직원 수로 가중 평균한 값입니다." }),
      el("p", { class: "salary-source muted", text: "출처: 금융감독원 전자공시(DART) 사업보고서 직원 현황" }));
  }

  // ------------------------------------------------------------ 자료 맞추기
  // 모든 회사 자료를 합친 기간의 달 목록. 한 회사에 없는 달은 그 회사 값이 null (선이 끊김)
  function mergedMonths() {
    const withData = items.filter((it) => it.company.months.length);
    if (!withData.length) return null;
    const start = withData.map((it) => it.company.months[0].ym).sort()[0];
    const end = withData.map((it) => it.company.months.at(-1).ym).sort().at(-1);
    const maps = items.map((it) => new Map(it.company.months.map((m) => [m.ym, m])));
    const yms = [...new Set(items.flatMap((it) => it.company.months.map((m) => m.ym)))].sort();
    const n = RANGES.find(([k]) => k === range)[2];
    const from = Number.isFinite(n) ? monthIndex(end) - n + 1 : -Infinity;
    const rate = (m, key) => (m && m.headcount ? (m[key] / m.headcount) * 100 : null);
    const rows = yms.filter((ym) => monthIndex(ym) >= from).map((ym) => {
      const row = { ym };
      maps.forEach((map, i) => {
        const m = map.get(ym);
        row[`salary${i}`] = m?.salary ?? null;
        row[`head${i}`] = m?.headcount ?? null;
        row[`hires${i}`] = m?.hires ?? null;
        row[`leaves${i}`] = m?.leaves ?? null;
        row[`hireRate${i}`] = rate(m, "hires");
        row[`leaveRate${i}`] = rate(m, "leaves");
      });
      return row;
    });
    // 총인원 증감률: 회사마다 기간 안의 첫 값 = 100
    items.forEach((_, i) => {
      const base = rows.find((r) => r[`head${i}`])?.[`head${i}`];
      rows.forEach((r) => { r[`headIdx${i}`] = base && r[`head${i}`] != null ? (r[`head${i}`] / base) * 100 : null; });
    });
    return { rows, start, end };
  }

  // ------------------------------------------------------------ 그리기
  function render(notice = "") {
    const parts = [companyCards()];
    if (notice) parts.push(el("p", { class: "notice is-error", text: notice }));
    // 올댓컴퍼니 대신 국민연금 공공데이터로 보여주는 회사
    for (const it of items.filter((x) => x.company.fallback)) {
      parts.push(el("p", { class: "notice is-warn", text: `${slotOf(it).tag} ${it.company.name}: ${it.company.fallback}` }));
    }
    const empty = items.filter((it) => !it.company.months.length);
    if (empty.length) {
      parts.push(el("p", { class: "notice is-plain",
        text: `월별 자료가 없는 회사가 있습니다: ${empty.map((it) => `${slotOf(it).tag} ${it.company.name}`).join(", ")}` }));
    }
    // 공시 기준 평균연봉: 기간 탭(1년·3년·전체)과 관계없는 연간 자료라 탭보다 위에 둔다
    parts.push(disclosureCard());
    const merged = mergedMonths();
    if (merged && items.length - empty.length >= 1) {
      const { rows, start, end } = merged;
      parts.push(rangeFilter(start, end));
      // 자료는 있지만 고른 기간 안에는 없는 회사 (예: 오래전 자료만 있음)
      const outside = items.filter((it, i) => it.company.months.length && !rows.some((r) => r[`head${i}`] != null));
      if (outside.length) {
        parts.push(el("p", { class: "notice is-plain", text: `선택한 기간에 자료가 없는 회사: ${outside.map((it) =>
          `${slotOf(it).tag} ${it.company.name} (${it.company.months[0].ym} ~ ${it.company.months.at(-1).ym} 자료)`).join(", ")}` }));
      }
      parts.push(summary(rows));
      parts.push(chartCard("월평균급여 (추정)", rows, series("salary"), won, false, manwon));
      parts.push(headCard(rows));
      parts.push(chartCard("입사율 (총인원 대비)", rows, series("hireRate"), pct, true));
      parts.push(chartCard("퇴사율 (총인원 대비)", rows, series("leaveRate"), pct, true));
      parts.push(dataTable(rows));
    }
    // 출처: 쓰인 자료마다 안내 한 줄, 다음 줄에 회사별 링크 (올댓컴퍼니 또는 공공데이터포털)
    const srcOf = (it) => it.company.source || { key: "allthatcompany", name: "올댓컴퍼니", url: it.company.url,
      note: "국민연금 가입 사업장 자료 기반 추정치. 실제 급여와 다를 수 있습니다" };
    const used = [...new Map(items.map((it) => [srcOf(it).key, srcOf(it)])).values()];
    parts.push(el("p", { class: "salary-source muted" },
      ...used.flatMap((src, i) => [i ? el("br") : null, `출처: ${src.name} (${src.note})`]),
      el("br"),
      ...items.flatMap((it, i) => [i ? " · " : null,
        el("a", { href: srcOf(it).url, target: "_blank", rel: "noopener noreferrer",
          text: `${slotOf(it).tag} ${srcOf(it).key === "nps" ? "공공데이터포털" : "올댓컴퍼니"} ↗` })])));
    body.replaceChildren(...parts.filter(Boolean));
    redraw();
  }
  const redraw = () => body.querySelectorAll(".chart").forEach((node) => node._draw());

  const series = (key) => items.map((it, i) => ({
    key: `${key}${i}`, label: `${slotOf(it).tag} ${it.company.name}`, short: slotOf(it).tag,
    color: slotOf(it).color, dash: slotOf(it).dash }));

  // 총인원 차트 (실제 인원 / 증감률). 보기를 바꾸면 화면 전체가 아니라 이 카드만 바꾸고 카드 위치로 부드럽게 옮긴다
  function headCard(rows) {
    const card = headMode === "count"
      ? chartCard("총인원", rows, series("head"), (v) => `${num(v)}명`, false)
      : chartCard("총인원 증감 (기간 첫 달 = 100)", rows, series("headIdx"), (v) => v.toFixed(1), false, (v) => v.toFixed(0));
    card.classList.add("compare-head-card");
    card.querySelector("h3").after(el("div", { class: "chart-toggle" },
      segGroup("총인원 보기", [["count", "실제 인원"], ["index", "증감률 (시작=100)"]], headMode, (k) => {
        if (k === headMode) return;
        headMode = k;
        const next = headCard(rows);
        card.replaceWith(next);
        next.querySelector(".chart")._draw();
        next.querySelector(`.chart-toggle [aria-pressed="true"]`)?.focus({ preventScroll: true });  // 키보드 사용자도 같은 자리에서 이어서
        const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        next.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
      })));
    return card;
  }

  const updateUrl = () => history.replaceState(null, "", `/salary/compare?ids=${items.map((it) => encodeURIComponent(it.id)).join(",")}`);

  // 회사 카드: 빼기(3곳 이상일 때), 4곳 미만이면 '회사 추가'(비교함에 지금 회사들을 담고 검색으로)
  function companyCards() {
    const card = (it) => {
      const c = it.company;
      const remove = items.length > 2
        ? el("button", { type: "button", class: "compare-remove", "aria-label": `${c.name} 비교에서 빼기`, title: "비교에서 빼기", text: "×" })
        : null;
      remove?.addEventListener("click", () => { items = items.filter((x) => x !== it); updateUrl(); render(); });
      return el("div", { class: "compare-card" },
        el("div", { class: "compare-card-top" }, dot(it), el("b", { text: slotOf(it).tag }),
          el("a", { href: `/salary/c/${encodeURIComponent(c.id)}`, class: "compare-card-name", text: c.name, title: c.name }), remove),
        el("div", { class: "muted small", text: [c.address, c.industry].filter(Boolean).join(" · ") }));
    };
    const cards = items.map(card);
    if (items.length < MAX) {
      const add = el("button", { type: "button", class: "compare-card compare-card-add" },
        el("span", { class: "compare-add-plus", "aria-hidden": "true", text: "+" }),
        el("span", { text: `회사 추가 (최대 ${MAX}곳)` }));
      add.addEventListener("click", () => {
        try {
          localStorage.setItem("salary.compare", JSON.stringify(items.map((it) => ({ id: it.id, name: it.company.name }))));
        } catch { /* 저장 못 하면 비교함이 비어 있을 뿐 */ }
        location.href = "/salary";
      });
      cards.push(add);
    }
    return el("div", { class: "compare-cards", style: `--n:${cards.length}` }, cards);
  }

  function segGroup(label, options, current, onPick) {
    const group = el("div", { class: "seg", role: "group", "aria-label": label });
    for (const [key, text] of options) {
      const b = el("button", { type: "button", text, "aria-pressed": String(current === key) });
      b.addEventListener("click", () => onPick(key));
      group.append(b);
    }
    return group;
  }

  function rangeFilter(start, end) {
    return el("div", { class: "salary-filter" },
      segGroup("기간", RANGES.map(([k, l]) => [k, l]), range, (k) => { range = k; render(); }),
      el("span", { class: "muted", text: `회사 ${items.length}곳 자료 ${start} ~ ${end} 중` }));
  }


  // 요약: 지표마다 회사별 값. PC는 표 모양(지표 × 회사), 좁은 화면은 지표마다 회사 값을 2열로 (CSS)
  // 두 회사일 때만 차이(A−B) 열을 둔다
  function summary(rows) {
    const stats = items.map((_, i) => {
      const heads = rows.filter((r) => r[`head${i}`] != null);
      const sals = rows.filter((r) => r[`salary${i}`] != null);
      const sum = (k) => rows.reduce((a, r) => a + (r[`${k}${i}`] ?? 0), 0);
      const headSum = sum("head");
      return {
        head: heads.at(-1)?.[`head${i}`] ?? null,
        headDelta: heads.length > 1 ? heads.at(-1)[`head${i}`] - heads[0][`head${i}`] : null,
        salary: sals.at(-1)?.[`salary${i}`] ?? null,
        salaryChange: sals.length > 1 ? (sals.at(-1)[`salary${i}`] / sals[0][`salary${i}`] - 1) * 100 : null,
        hireRate: headSum ? (sum("hires") / headSum) * 100 : null,
        leaveRate: headSum ? (sum("leaves") / headSum) * 100 : null,
      };
    });
    const withDiff = items.length === 2;
    const cols = items.length + (withDiff ? 1 : 0);
    const tagged = (it) => el("span", { class: "cmp-sum-tag" }, dot(it), el("b", { text: slotOf(it).tag }));
    const line = (label, key, fmt, diffFmt) => {
      const vals = stats.map((s) => s[key]);
      const cells = items.map((it, i) => el("div", { class: "cmp-sum-cell", role: "cell" },
        tagged(it), el("span", { class: "cmp-sum-val", text: vals[i] == null ? "-" : fmt(vals[i]) })));
      if (withDiff) {
        const [a, b] = vals;
        cells.push(el("div", { class: "cmp-sum-cell cmp-sum-diff", role: "cell" },
          el("span", { class: "cmp-sum-tag", text: "A−B" }),
          el("span", { class: "cmp-sum-val", text: a == null || b == null || !diffFmt ? "" : signed(a - b, diffFmt) })));
      }
      return el("div", { class: "cmp-sum-row", role: "row" }, el("div", { class: "cmp-sum-label", role: "rowheader", text: label }), cells);
    };
    const headRow = el("div", { class: "cmp-sum-row cmp-sum-head", role: "row" },
      el("div", { class: "cmp-sum-label", role: "columnheader" }),
      items.map((it) => el("div", { class: "cmp-sum-cell", role: "columnheader" },
        el("span", { class: "compare-col" }, dot(it), el("b", { text: slotOf(it).tag }),
          el("span", { class: "compare-col-name", text: it.company.name, title: it.company.name })))),
      withDiff ? el("div", { class: "cmp-sum-cell", role: "columnheader", text: "차이 (A−B)" }) : null);
    return el("div", { class: "cmp-sum", role: "table", "aria-label": "회사별 요약 비교", style: `--cols:${cols}` },
      headRow,
      line("총인원 (최근)", "head", (v) => `${num(v)}명`, (v) => `${num(v)}명`),
      line("기간 내 인원 증감", "headDelta", (v) => signed(v, (x) => `${num(x)}명`), null),
      line("월평균급여 (최근)", "salary", manwon, manwon),
      line("기간 내 급여 증감률", "salaryChange", (v) => signed(v, (x) => `${x.toFixed(1)}%`), (v) => `${v.toFixed(1)}%p`),
      line("월평균 입사율", "hireRate", (v) => `월 ${pct(v)}`, (v) => `${v.toFixed(1)}%p`),
      line("월평균 퇴사율", "leaveRate", (v) => `월 ${pct(v)}`, (v) => `${v.toFixed(1)}%p`));
  }

  // 표로 보기: 지표 하나를 골라 기준년월 × 회사
  const TABLE_METRICS = [
    ["head", "총인원", (v) => `${num(v)}명`],
    ["salary", "월평균급여", won],
    ["hires", "입사자", (v) => `${num(v)}명`],
    ["leaves", "퇴사자", (v) => `${num(v)}명`],
  ];
  function dataTable(rows) {
    const details = el("details", { class: "salary-table" }, el("summary", { text: "표로 보기" }));
    const draw = () => {
      const [key, , fmt] = TABLE_METRICS.find(([k]) => k === tableMetric);
      const tabs = segGroup("표 지표", TABLE_METRICS.map(([k, l]) => [k, l]), tableMetric, (k) => { tableMetric = k; draw(); });
      const tbody = [...rows].reverse().map((r) => el("tr", {},
        el("th", { scope: "row", text: r.ym }),
        ...items.map((_, i) => el("td", { text: r[`${key}${i}`] == null ? "-" : fmt(r[`${key}${i}`]) }))));
      details.replaceChildren(details.querySelector("summary"), el("div", { class: "table-tabs" }, tabs),
        el("div", { class: "table-scroll" }, el("table", {},
          el("thead", {}, el("tr", {}, el("th", { scope: "col", text: "기준년월" }),
            ...items.map((it) => el("th", { scope: "col" }, el("span", { class: "compare-col" }, dot(it),
              el("b", { text: slotOf(it).tag }), el("span", { class: "compare-col-name", text: it.company.name, title: it.company.name })))))),
          el("tbody", {}, tbody))));
    };
    draw();
    return details;
  }

  // 공유하기 버튼(.share-btn, 링크 복사)은 static/salary.js가 처리한다 (회사를 빼면 주소도 바뀌므로 누를 때의 주소를 복사)

  let timer = null;
  window.addEventListener("resize", () => { clearTimeout(timer); timer = setTimeout(redraw, 150); });
  load();
})();

// 수집 기록 상단: 사이트별 최근 실행 그래프 (발견 공고 추이 · 회차별 신규/삭제 · 상태 띠)
// 자료는 runs.html의 <script id="run-charts">(JSON). 세 그래프는 x축(실행 회차)을 같이 쓰고, 올리면 한 회차의 자세한 값을 보여준다.
(() => {
  const dataEl = document.getElementById("run-charts");
  if (!dataEl) return;
  const data = JSON.parse(dataEl.textContent);
  const NS = "http://www.w3.org/2000/svg";
  const STATUS = { SUCCESS: "성공", PARTIAL: "부분", FAILED: "실패", CANCELED: "취소", RUNNING: "진행 중" };
  const TRIGGER = { manual: "수동", schedule: "자동", cli: "CLI" };
  const num = (n) => (n ?? 0).toLocaleString("ko-KR");
  const dur = (s) => (s == null ? "진행 중" : s >= 60 ? `${Math.floor(s / 60)}분 ${s % 60}초` : `${s}초`);

  function svg(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (parent) parent.append(node);
    return node;
  }

  // 눈금: 최솟값~최댓값을 보기 좋은 간격으로 (발견 수는 0부터가 아니라 변화가 보이게)
  function niceRange(lo, hi) {
    if (lo === hi) { lo = Math.max(0, lo - 1); hi += 1; }
    const raw = (hi - lo) / 2;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
    const a = Math.floor(lo / step) * step;
    return { lo: Math.max(0, a), hi: Math.ceil(hi / step) * step, step };
  }

  function draw(box) {
    const points = data[box.dataset.site] || [];
    box.textContent = "";
    if (!points.length) return;
    const W = Math.max(240, box.clientWidth);
    const L = 44, R = 6;
    const LINE = { top: 18, h: 78 }, BARS = { top: LINE.top + 78 + 36, h: 50 }, STRIP = { top: BARS.top + 50 + 10, h: 8 };
    const H = STRIP.top + STRIP.h + 20;
    const n = points.length, band = (W - L - R) / n;
    const cx = (i) => L + band * (i + 0.5);
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "run-svg", role: "img",
      "aria-label": `최근 ${n}회 발견 공고 ${num(points[0].found)}건에서 ${num(points[n - 1].found)}건` });
    box.append(root);

    // 1) 발견 공고 수 (선). 끝까지 돈 실행(성공·부분)만 잇는다 — 취소·실패·진행 중은 도중 값이라 비워 둔다
    const counted = (p) => p.status === "SUCCESS" || p.status === "PARTIAL";
    const founds = points.filter(counted).map((p) => p.found);
    if (!founds.length) founds.push(...points.map((p) => p.found));
    const r = niceRange(Math.min(...founds), Math.max(...founds));
    const yL = (v) => LINE.top + LINE.h - ((v - r.lo) / (r.hi - r.lo)) * LINE.h;
    svg("text", { x: 0, y: 10, class: "run-svg-title" }, root).textContent = "발견 공고";
    for (let v = r.lo; v <= r.hi + 1e-9; v += r.step) {
      svg("line", { x1: L, x2: W - R, y1: yL(v), y2: yL(v), class: "run-grid" }, root);
      svg("text", { x: L - 6, y: yL(v) + 4, class: "run-axis", "text-anchor": "end" }, root).textContent = num(v);
    }
    let d = "", prev = false;
    points.forEach((p, i) => {
      if (!counted(p)) { prev = false; return; }
      d += `${prev ? "L" : "M"}${cx(i).toFixed(1)},${yL(p.found).toFixed(1)}`;
      prev = true;
    });
    svg("path", { d, class: "run-line" }, root);
    const last = n - 1;
    const lastDone = points.map(counted).lastIndexOf(true);
    if (lastDone >= 0) svg("circle", { cx: cx(lastDone), cy: yL(points[lastDone].found), r: 3.5, class: "run-dot" }, root);

    // 2) 회차별 신규(위)·삭제(아래) 막대 — 0을 기준선으로 같은 눈금.
    // 첫 수집(발견한 공고가 모두 신규)은 다른 회차보다 수십 배 커서 눈금에서 빼고, 위를 잘라 '첫 수집'으로 표시한다
    const initial = (p) => p.created > 0 && p.created === p.found;
    const up = Math.max(1, ...points.filter((p) => !initial(p)).map((p) => p.created));
    const down = Math.max(0, ...points.map((p) => p.deleted));
    const span = up + down, zero = BARS.top + (up / span) * BARS.h;
    const yB = (v) => zero - (v / span) * BARS.h;
    const title = svg("text", { x: 0, y: BARS.top - 10, class: "run-svg-title" }, root);
    title.textContent = "신규 · 삭제";
    const lg = svg("g", { transform: `translate(${W - R - 92},${BARS.top - 19})`, class: "run-legend" }, root);
    svg("rect", { x: 0, y: 1, width: 8, height: 8, rx: 2, class: "run-bar-new" }, lg);
    svg("text", { x: 12, y: 9 }, lg).textContent = "신규";
    svg("rect", { x: 46, y: 1, width: 8, height: 8, rx: 2, class: "run-bar-del" }, lg);
    svg("text", { x: 58, y: 9 }, lg).textContent = "삭제";
    svg("text", { x: L - 6, y: BARS.top + 8, class: "run-axis", "text-anchor": "end" }, root).textContent = `+${num(up)}`;
    if (down) svg("text", { x: L - 6, y: BARS.top + BARS.h, class: "run-axis", "text-anchor": "end" }, root).textContent = `−${num(down)}`;
    const bw = Math.max(2, Math.min(12, band * 0.6));
    points.forEach((p, i) => {
      if (initial(p)) {
        svg("rect", { x: cx(i) - bw / 2, y: BARS.top, width: bw, height: zero - BARS.top, rx: 1.5, class: "run-bar-new is-initial" }, root);
        const LABEL_W = 40;  // '첫 수집' 글자 폭
        const covered = points.slice(i + 1).some((q, k) => cx(i + 1 + k) - bw / 2 < cx(i) + bw / 2 + 3 + LABEL_W && yB(q.created) < BARS.top + 12);
        if (!covered) svg("text", { x: cx(i) + bw / 2 + 3, y: BARS.top + 9, class: "run-axis" }, root).textContent = "첫 수집";
      } else if (p.created) svg("rect", { x: cx(i) - bw / 2, y: yB(p.created), width: bw, height: zero - yB(p.created), rx: 1.5, class: "run-bar-new" }, root);
      if (p.deleted) svg("rect", { x: cx(i) - bw / 2, y: zero, width: bw, height: yB(-p.deleted) - zero, rx: 1.5, class: "run-bar-del" }, root);
    });
    svg("line", { x1: L, x2: W - R, y1: zero, y2: zero, class: "run-zero" }, root);

    // 3) 상태 띠 (색 + 아래 요약 글자 + 툴팁)
    points.forEach((p, i) => {
      svg("rect", { x: cx(i) - band / 2 + 1, y: STRIP.top, width: Math.max(1, band - 2), height: STRIP.h, rx: 1.5,
        class: `run-strip is-${p.status.toLowerCase()}` }, root);
    });
    svg("text", { x: L - 6, y: STRIP.top + 8, class: "run-axis", "text-anchor": "end" }, root).textContent = "상태";
    svg("text", { x: L, y: H - 3, class: "run-axis" }, root).textContent = points[0].t;
    svg("text", { x: W - R, y: H - 3, class: "run-axis", "text-anchor": "end" }, root).textContent = points[last].t;

    // 올리면: 세로선 + 툴팁
    const cross = svg("line", { y1: LINE.top - 4, y2: STRIP.top + STRIP.h, class: "run-cross", visibility: "hidden" }, root);
    const hit = svg("rect", { x: L, y: 0, width: W - L - R, height: H, fill: "transparent" }, root);
    const tip = document.createElement("div");
    tip.className = "run-tip";
    tip.hidden = true;
    box.append(tip);
    const show = (e) => {
      const rect = root.getBoundingClientRect();
      const i = Math.min(n - 1, Math.max(0, Math.floor((e.clientX - rect.left - L) / band)));
      const p = points[i];
      cross.setAttribute("x1", cx(i)); cross.setAttribute("x2", cx(i)); cross.setAttribute("visibility", "visible");
      tip.replaceChildren();
      const head = document.createElement("div");
      head.className = "run-tip-head";
      head.textContent = `${p.t}${TRIGGER[p.trigger] ? " · " + TRIGGER[p.trigger] : ""}${initial(p) ? " · 첫 수집" : ""}`;
      const st = document.createElement("span");
      st.className = `pill run-${p.status.toLowerCase()}`;
      st.textContent = STATUS[p.status] || p.status;
      head.append(" ", st);
      tip.append(head);
      for (const [k, v] of [["발견", num(p.found)], ["신규", num(p.created)], ["변경", num(p.updated)], ["삭제", num(p.deleted)], ["소요", dur(p.secs)]]) {
        const row = document.createElement("div");
        row.className = "run-tip-row";
        const a = document.createElement("span"); a.textContent = k;
        const b = document.createElement("b"); b.textContent = v;
        row.append(a, b);
        tip.append(row);
      }
      tip.hidden = false;
      const x = cx(i), tw = tip.offsetWidth;
      tip.style.left = `${Math.min(W - tw, Math.max(0, x < W / 2 ? x + 10 : x - tw - 10))}px`;
      tip.style.top = `${LINE.top}px`;
    };
    hit.addEventListener("pointermove", show);
    hit.addEventListener("pointerdown", show);
    hit.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
  }

  const boxes = [...document.querySelectorAll(".run-chart[data-site]")];
  boxes.forEach(draw);
  // 화면 폭이 바뀌면 글자 크기는 그대로 두고 다시 그린다
  let lastW = boxes.map((b) => b.clientWidth);
  new ResizeObserver(() => {
    boxes.forEach((b, i) => { if (b.clientWidth !== lastW[i]) { lastW[i] = b.clientWidth; draw(b); } });
  }).observe(document.body);
})();

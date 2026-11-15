// 상단 상태 영역(공고 수 요약, 검색 조건별 수집 상태)을 새로고침 없이 주기적으로 갱신한다.
// 검색창과 결과 목록은 건드리지 않는다 (조회 버튼을 누를 때의 데이터 기준).
(() => {
  const RUNNING_INTERVAL = 5_000;  // 수집 중: 진행률을 보기 위해 자주
  const IDLE_INTERVAL = 30_000;    // 대기 중
  const root = document.getElementById("live");
  if (!root) return;

  const $ = (k) => root.querySelector(`[data-k="${k}"]`);
  const fmt = (n) => (n ?? 0).toLocaleString("ko-KR");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const STATUS_LABEL = {
    STARTING: "시작 중", RUNNING: "수집 중", SUCCESS: "완료", PARTIAL: "부분 완료",
    FAILED: "실패", STALE: "중단됨", CANCELED: "취소됨", QUEUED: "대기 중", NEVER: "기록 없음",
  };
  const TRIGGER_LABEL = { manual: "수동", schedule: "자동", cli: "CLI" };
  const ACTIVE = new Set(["STARTING", "RUNNING", "QUEUED"]);  // 지금 수집 버튼을 막는 상태

  function duration(sec) {
    if (sec == null) return "";
    const m = Math.floor(sec / 60), s = sec % 60;
    return m ? `${m}분 ${s}초` : `${s}초`;
  }

  function runButton(x, busySites) {
    if (!x.can_run) return "";
    const busy = busySites.get(x.site_key);
    const label = busy === "QUEUED" ? "대기 중" : busy ? "수집 중" : "지금 수집";
    return `<button type="button" class="run-btn" data-site="${esc(x.site_key)}" ${busy ? "disabled" : ""}
      title="${esc(x.site)}의 검색 조건 전체를 지금 수집합니다">${label}</button>`;
  }

  function searchRow(x, busySites, multi) {
    const running = x.status === "RUNNING";
    const pct = running && x.total ? Math.min(100, Math.round((x.found / x.total) * 100)) : null;
    let body;
    if (running) {
      body = `
        <div class="progress" role="progressbar" aria-valuenow="${pct ?? 0}" aria-valuemin="0" aria-valuemax="100">
          <span style="width:${pct ?? 0}%"></span>
        </div>
        <div class="crawl-line">${fmt(x.found)}${x.total ? ` / ${fmt(x.total)}건` : "건"} · ${x.pages}페이지 · ${duration(x.elapsed_sec)} 경과</div>`;
    } else if (x.status === "STARTING") {
      body = `<div class="crawl-line muted">수집을 준비하고 있습니다…</div>`;
    } else if (x.status === "QUEUED") {
      body = `<div class="crawl-line muted">수집 요청이 대기열에 있습니다. 수집기가 켜지면 시작합니다.</div>`;
    } else if (x.status === "NEVER") {
      body = `<div class="crawl-line muted">아직 수집하지 않음</div>`;
    } else if (x.status === "STALE" || x.status === "CANCELED") {
      body = `
        <div class="crawl-line">${esc(x.started_at)} 시작 · ${fmt(x.found)}건까지 진행</div>
        <div class="crawl-line muted">${x.status === "STALE" ? "진행 기록이 끊겨 중단된 것으로 보입니다" : "수집이 중단되어 취소되었습니다"}. 다시 수집할 수 있습니다.</div>`;
    } else {
      const changes = [
        x.created ? `신규 ${fmt(x.created)}` : "",
        x.updated ? `변경 ${fmt(x.updated)}` : "",
        x.deleted ? `삭제 ${fmt(x.deleted)}` : "",
      ].filter(Boolean).join(" · ") || "변동 없음";
      body = `
        <div class="crawl-line">${esc(x.finished_at)} 완료${x.trigger ? ` (${TRIGGER_LABEL[x.trigger] ?? esc(x.trigger)})` : ""} · ${fmt(x.found)}건 · ${duration(x.elapsed_sec)}</div>
        <div class="crawl-line muted">${changes}</div>`;
    }
    const error = x.error ? `<div class="crawl-line error" title="${esc(x.error)}">${esc(x.error)}</div>` : "";
    // 플랫폼에 크롤러가 여럿이면(사람인 일반 공고·헤드헌팅) 크롤러 이름을 앞에, 아니면 검색 조건 이름만
    const name = multi && x.kind
      ? `<span class="crawl-kind" style="--site: var(--${esc(x.site_key)}, var(--accent))">${esc(x.kind)}</span> <span class="muted">${esc(x.name)}</span>`
      : `<span class="muted">${esc(x.name)}</span>`;
    return `
      <div class="crawl-row ${running ? "is-running" : ""}">
        <div class="crawl-card-top">
          <span class="crawl-name">${name}</span>
          <span class="pill run-${x.status.toLowerCase()}">${STATUS_LABEL[x.status] ?? esc(x.status)}</span>
        </div>
        ${body}${error}
        ${runButton(x, busySites)}
      </div>`;
  }

  // 채용 플랫폼별 카드. 같은 플랫폼의 검색 조건(크롤러)은 한 카드 안에 줄로 나눈다
  function platformCards(searches, busySites, activeBy) {
    const groups = new Map();
    for (const x of searches) {
      const key = x.platform || x.site_key;
      if (!groups.has(key)) groups.set(key, { key, label: x.platform_label || x.site, items: [] });
      groups.get(key).items.push(x);
    }
    return [...groups.values()].map((g) => {
      const running = g.items.some((x) => x.status === "RUNNING");
      const multi = new Set(g.items.map((x) => x.site_key)).size > 1;
      const n = activeBy?.[g.key];
      return `
      <section class="crawl-card ${running ? "is-running" : ""} ${multi ? "is-multi" : ""}" style="--site: var(--${esc(g.key)}, var(--accent))">
        <header class="crawl-card-head">
          <span class="crawl-platform"><i class="site-dot" aria-hidden="true"></i>${esc(g.label)}</span>
          ${n != null ? `<span class="crawl-active">활성 ${fmt(n)}건</span>` : ""}
        </header>
        <div class="crawl-rows">${g.items.map((x) => searchRow(x, busySites, multi)).join("")}</div>
      </section>`;
    }).join("");
  }

  let last = null; // 마지막으로 받은 상태 (자동 수집 켜짐 여부 확인용)

  function render(d) {
    last = d;
    const s = d.summary;
    for (const k of ["active", "new_24h", "updated_24h", "deleted_24h", "deleted"]) $(k).textContent = fmt(s[k]);
    $("active_by_site").textContent = Object.entries(s.active_by_site).map(([k, v]) => `${k} ${fmt(v)}`).join(" · ");
    const busySites = new Map(d.searches.filter((x) => ACTIVE.has(x.status)).map((x) => [x.site_key, x.status]));
    $("searches").innerHTML = d.searches.length
      ? platformCards(d.searches, busySites, s.active_by_platform)
      : `<div class="crawl-line muted">설정된 검색 조건이 없습니다.</div>`;
    $("checked_at").textContent = `${d.checked_at} 갱신`;
    $("next_run_at").textContent = d.running ? ""
      : d.next_run_at ? `· 다음 수집 ${d.next_run_at}`
      : d.schedule_on === false ? "· 자동 수집 꺼짐" : "";
    $("live_dot").classList.toggle("is-running", d.running);
    const alert = $("worker_alert");
    alert.hidden = d.worker_alive !== false;
    alert.textContent = d.worker_alive === false
      ? `수집기가 꺼져 있습니다${d.worker_seen_at ? ` (마지막 신호 ${d.worker_seen_at})` : ""}. 자동 수집과 '지금 수집'은 수집기가 켜지면 진행됩니다.`
      : "";
    root.classList.remove("is-stale");
  }

  let timer = null;
  async function poll() {
    clearTimeout(timer);
    let running = false;
    try {
      const res = await fetch("/api/status", { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      const d = await res.json();
      render(d);
      running = d.running;
    } catch {
      root.classList.add("is-stale");  // 서버에 닿지 않으면 흐리게 표시하고 계속 재시도
    }
    if (!document.hidden) timer = setTimeout(poll, running ? RUNNING_INTERVAL : IDLE_INTERVAL);
  }

  // 수동 수집: 사이트(크롤러 모듈)별로 지금 수집을 요청한다
  function flash(text, isError) {
    const el = $("message");
    el.textContent = text;
    el.classList.toggle("is-error", !!isError);
    clearTimeout(flash.timer);
    flash.timer = setTimeout(() => { el.textContent = ""; }, 6000);
  }

  // 자동 수집이 켜져 있으면 확인 모달을 거친다. 카드는 갱신 때마다 다시 그려지므로 버튼이 아니라 사이트를 기억한다
  const confirmModal = document.getElementById("crawl-confirm");
  let pendingSite = null;

  root.addEventListener("click", (e) => {
    const btn = e.target.closest(".run-btn");
    if (!btn || btn.disabled) return;
    const site = btn.dataset.site;
    if (last?.schedule_on && confirmModal) {
      pendingSite = site;
      const label = last.searches.find((x) => x.site_key === site)?.site || site;
      confirmModal.querySelector(".crawl-confirm-site").textContent = label;
      confirmModal.querySelector(".crawl-confirm-next").textContent = last.next_run_at ? ` (다음 자동 수집 ${last.next_run_at})` : "";
      confirmModal.showModal();
      return;
    }
    startCrawl(site);
  });

  confirmModal?.querySelector(".crawl-confirm-go").addEventListener("click", () => {
    confirmModal.close();
    if (pendingSite) startCrawl(pendingSite);
    pendingSite = null;
  });

  async function startCrawl(site) {
    const btn = [...root.querySelectorAll(".run-btn")].find((b) => b.dataset.site === site);
    if (btn) { btn.disabled = true; btn.textContent = "요청 중"; }
    try {
      const res = await fetch(`/api/crawl/${encodeURIComponent(site)}`, { method: "POST" });
      const d = await res.json().catch(() => ({}));
      flash(d.message || (res.ok ? "수집을 시작했습니다." : `요청 실패 (${res.status})`), !res.ok);
    } catch {
      flash("서버에 연결할 수 없습니다.", true);
    }
    poll();  // 바로 상태를 갱신하고, 수집 중이면 짧은 주기로 계속 확인
  }

  // 탭이 보이지 않을 때는 멈추고, 다시 보이면 즉시 갱신
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); else clearTimeout(timer); });

  const initial = document.getElementById("initial-status");
  if (initial) render(JSON.parse(initial.textContent));
  timer = setTimeout(poll, JSON.parse(initial?.textContent || "{}").running ? RUNNING_INTERVAL : IDLE_INTERVAL);
})();

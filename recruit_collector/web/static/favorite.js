// 공고·회사 즐겨찾기 별 토글과 토스트 알림. 로그인 사용 중 로그인한 사용자에게만 불러온다 (base.html).
//  - 별 버튼: <button class="fav-btn" data-fav-kind="postings|companies" data-fav-id data-fav-name [data-fav-address] aria-pressed>
//    공고 목록(postings.html)과 연봉·인원 검색 결과(salary_page.js)에 있다. 나중에 그려지는 버튼도 받도록 문서에서 클릭을 받는다.
//  - 지정은 PUT, 해제는 DELETE /api/favorites/{kind}/{id}. 누르면 바로 바꿔 보이고, 실패하면 되돌리고 토스트로 알린다.
//  - 뒤로·앞으로 가기로 브라우저가 저장해 둔 화면(bfcache·HTTP 캐시)을 다시 보여줄 때는, 다른 페이지(예: 공고 상세)에서
//    바꾼 상태가 빠져 있으므로 서버에 현재 상태를 물어 별을 맞춘다 (GET /api/favorites/{kind}?ids=).
//  - 공고 즐겨찾기를 지정·해제하면 공고 탭의 '즐겨찾기 N'을 바로 늘리고 줄인다. 즐겨찾기 탭에서 해제하면 그 공고를 목록에서
//    빼고 결과 건수·요약(마감 임박·변경·지난 공고)을 줄인다. 이 페이지가 비면 서버에서 다시 불러온다(앞 페이지 공고가 넘어오도록).
//  - 관심 회사 탭의 회사 칩 ×: 관심 회사에서 빼고(DELETE /api/favorites/companies/{id}), 같은 이름(비교용)의 관심 회사가
//    더 없으면 그 회사의 공고를 목록에서 바로 빼고 결과 건수·NEW 수를 줄인다.
//  - 관심 회사 새 공고의 NEW: 공고 상세를 열거나(서버가 기록) 원문 링크를 누르면(sendBeacon으로 기록) 확인한 것으로 보고
//    목록에서 바로 떼고 탭·메뉴 옆 숫자를 줄인다. 캐시된 화면이 다시 보일 때도 서버에 물어 맞춘다 (GET /api/postings/new).
(() => {
  // ------------------------------------------------------------ 토스트
  let region = null;
  function toast(message, isError = false) {
    if (!region) {
      region = document.createElement("div");
      region.className = "toast-region";
      region.setAttribute("role", "status");
      region.setAttribute("aria-live", "polite");
      document.body.append(region);
    }
    const item = document.createElement("div");
    item.className = isError ? "toast is-error" : "toast";
    item.textContent = message;
    region.append(item);
    while (region.children.length > 3) region.firstElementChild.remove(); // 연달아 누르면 오래된 것부터 지운다
    setTimeout(() => {
      item.classList.add("is-leaving");
      setTimeout(() => item.remove(), 250);
    }, 2200);
  }
  window.showToast = toast;

  // ------------------------------------------------------------ 별 토글
  const LABEL = { postings: "공고", companies: "회사" };

  function paint(btn, on) {
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", String(on));
    btn.title = on ? "즐겨찾기 해제" : "즐겨찾기";
  }

  async function refresh() {
    const buttons = [...document.querySelectorAll(".fav-btn")];
    for (const kind of new Set(buttons.map((b) => b.dataset.favKind))) {
      const mine = buttons.filter((b) => b.dataset.favKind === kind);
      try {
        const ids = [...new Set(mine.map((b) => b.dataset.favId))].join(",");
        const res = await fetch(`/api/favorites/${kind}?ids=${encodeURIComponent(ids)}`, { cache: "no-store" });
        const data = await res.json();
        if (!res.ok || !data.ok) continue;
        const on = new Set(data.favorites);
        for (const b of mine) {
          if (b.dataset.busy) continue;
          const now = on.has(b.dataset.favId);
          if (now !== (b.getAttribute("aria-pressed") === "true")) { paint(b, now); applied(b, kind, now); }
        }
      } catch { /* 맞추지 못하면 저장된 화면 그대로 둔다 */ }
    }
  }

  // ------------------------------------------------------------ 즐겨찾기 탭 목록·숫자
  function bump(node, delta) {
    if (!node) return;
    const v = parseInt(node.textContent.replace(/,/g, ""), 10);
    if (Number.isFinite(v)) node.textContent = Math.max(0, v + delta).toLocaleString("ko-KR");
  }

  function removeFromFavoriteTab(btn) {
    const list = btn.closest('.list[data-view="fav"]');
    const item = btn.closest(".item");
    if (!list || !item) return;
    for (const key of (item.dataset.sum || "").split(" ").filter(Boolean)) {
      const chip = document.querySelector(`.fav-summary [data-sum="${key}"]`);
      bump(chip?.querySelector("b"), -1);
      if (chip && chip.querySelector("b").textContent === "0") chip.classList.remove("is-soon", "is-changed");
    }
    collapseOut(item, list);
  }

  // 목록에서 공고 하나를 접으며 빼고 결과 건수를 줄인다. 이 페이지가 비면 서버에서 다시 불러온다
  function collapseOut(item, list) {
    const total = document.querySelector("[data-total]");
    bump(total, -1);
    const done = () => {
      item.remove();
      // 지난 공고가 더 없으면 구분선도 뺀다
      const divider = list.querySelector(".list-divider");
      if (divider && !divider.nextElementSibling) divider.remove();
      if (!list.querySelector(".item")) {
        // 이 페이지가 비었으면: 남은 즐겨찾기가 있으면 같은(없으면 앞) 페이지를 다시 불러오고, 하나도 없으면 안내를 다시 그린다
        const url = new URL(location.href);
        const page = Number(url.searchParams.get("page") || 1);
        const size = Number(document.querySelector('select[name="size"]')?.value || 10);
        const left = parseInt((total?.textContent || "0").replace(/,/g, ""), 10) || 0;
        const last = Math.max(1, Math.ceil(left / size));
        if (page > last) url.searchParams.set("page", String(last));
        location.replace(url);
      }
    };
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { done(); return; }
    item.style.height = `${item.offsetHeight}px`;
    item.classList.add("is-leaving");
    requestAnimationFrame(() => { item.style.height = "0px"; });
    item.addEventListener("transitionend", (e) => { if (e.propertyName === "height") done(); });
    setTimeout(() => item.isConnected && done(), 600);  // transitionend가 오지 않는 경우
  }

  // ------------------------------------------------------------ 관심 회사 탭: 회사 칩 ×
  document.addEventListener("click", async (e) => {
    const del = e.target.closest(".fav-company-del");
    if (!del || del.disabled) return;
    const chip = del.closest(".fav-company");
    const { companyId: id, name } = del.dataset;
    del.disabled = true;
    try {
      const res = await fetch(`/api/favorites/companies/${encodeURIComponent(id)}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data.detail || data.message || `요청 실패 (HTTP ${res.status})`);
    } catch (err) {
      del.disabled = false;
      toast(err instanceof TypeError ? "조회 서버에 연결하지 못했습니다." : `관심 회사를 빼지 못했습니다. ${err.message}`, true);
      return;
    }
    const key = chip.dataset.key;
    chip.remove();
    const left = document.querySelectorAll(".fav-company");
    document.querySelector(".fav-companies-empty")?.toggleAttribute("hidden", left.length > 0);
    const short = name.length > 24 ? `${name.slice(0, 24)}…` : name;
    toast(`회사 '${short}'을(를) 즐겨찾기에서 해제했습니다`);
    // 같은 이름(비교용)의 관심 회사가 남아 있으면 그 공고는 여전히 관심 회사 공고다
    const list = document.querySelector('.list[data-view="company"]');
    if (!key || !list || [...left].some((c) => c.dataset.key === key)) return;
    const items = [...list.querySelectorAll(".item")].filter((li) => li.dataset.companyKey === key);
    const news = items.filter((li) => li.querySelector(".badge-new")).length;
    for (const sel of ["[data-new-count]", "[data-alert-count]"]) {
      document.querySelectorAll(sel).forEach((n) => { bump(n, -news); n.hidden = n.textContent === "0"; });
    }
    if (!left.length && !items.length) { location.replace(location.href); return; }  // 관심 회사가 없어졌으면 안내를 다시 그린다
    items.forEach((li) => collapseOut(li, list));
  });

  // 공고 즐겨찾기가 바뀐 뒤 (이 화면에서 눌렀거나, 다른 페이지에서 바꾼 것을 캐시된 화면에 맞출 때)
  function applied(btn, kind, on) {
    if (kind !== "postings") return;
    document.querySelectorAll("[data-fav-total]").forEach((n) => bump(n, on ? 1 : -1));
    if (!on) removeFromFavoriteTab(btn);
  }

  // ------------------------------------------------------------ 관심 회사 새 공고의 NEW
  function setCount(selector, n) {
    for (const node of document.querySelectorAll(selector)) {
      node.textContent = n > 99 ? "99+" : String(n);
      node.hidden = n <= 0;
    }
  }

  function dropNew(id) {
    const badges = document.querySelectorAll(`.badge-new[data-new-id="${CSS.escape(id)}"]`);
    if (!badges.length) return;
    badges.forEach((b) => b.remove());
    const minus = (sel) => document.querySelectorAll(sel).forEach((n) => {
      const v = parseInt(n.textContent, 10);
      if (Number.isFinite(v)) { n.textContent = String(Math.max(0, v - 1)); n.hidden = v - 1 <= 0; }
    });
    minus("[data-new-count]");
    minus("[data-alert-count]");
  }

  async function refreshNew() {
    const ids = [...new Set([...document.querySelectorAll(".badge-new[data-new-id]")].map((b) => b.dataset.newId))];
    if (!ids.length && !document.querySelector("[data-new-count], [data-alert-count]")) return;
    try {
      const res = await fetch(`/api/postings/new?ids=${encodeURIComponent(ids.join(","))}`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok || !data.ok) return;
      const still = new Set(data.new.map(String));
      document.querySelectorAll(".badge-new[data-new-id]").forEach((b) => { if (!still.has(b.dataset.newId)) b.remove(); });
      setCount("[data-new-count]", data.count);
      setCount("[data-alert-count]", data.alerts);
    } catch { /* 맞추지 못하면 저장된 화면 그대로 둔다 */ }
  }

  // 공고 제목(상세로 이동) 또는 원문 링크를 누르면 확인한 것. 가운데 클릭·Ctrl 클릭(새 탭)도 포함
  function onOpen(e) {
    const link = e.target.closest("a.title, a.origin");
    if (!link || (e.type === "auxclick" && e.button !== 1)) return;
    const id = link.closest(".item")?.querySelector(".fav-btn[data-fav-kind='postings']")?.dataset.favId;
    if (!id) return;
    if (link.classList.contains("origin")) {  // 상세는 서버가 열 때 기록하므로 원문만 따로 알린다
      try { navigator.sendBeacon(`/api/postings/${encodeURIComponent(id)}/viewed`); } catch { /* 기록 못 하면 NEW가 남을 뿐 */ }
    }
    dropNew(id);
  }
  document.addEventListener("click", onOpen);
  document.addEventListener("auxclick", onOpen);

  window.addEventListener("pageshow", (e) => {
    const nav = performance.getEntriesByType("navigation")[0];
    if (e.persisted || nav?.type === "back_forward") { refresh(); refreshNew(); }
  });

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".fav-btn");
    if (!btn || btn.dataset.busy) return;
    e.preventDefault();
    const { favKind: kind, favId: id, favName: name = "", favAddress: address = "" } = btn.dataset;
    const on = btn.getAttribute("aria-pressed") !== "true"; // 바꿀 상태
    btn.dataset.busy = "1";
    paint(btn, on);
    try {
      const res = await fetch(`/api/favorites/${kind}/${encodeURIComponent(id)}`, {
        method: on ? "PUT" : "DELETE",
        headers: { "Content-Type": "application/json" },
        body: kind === "companies" && on ? JSON.stringify({ name, address }) : undefined,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data.detail || data.message || `요청 실패 (HTTP ${res.status})`);
      const short = name.length > 24 ? `${name.slice(0, 24)}…` : name;
      toast(on ? `${LABEL[kind]} '${short}'을(를) 즐겨찾기에 추가했습니다`
        : `${LABEL[kind]} '${short}'을(를) 즐겨찾기에서 해제했습니다`);
      applied(btn, kind, on);
    } catch (err) {
      paint(btn, !on);
      toast(err instanceof TypeError ? "조회 서버에 연결하지 못했습니다." : `즐겨찾기를 바꾸지 못했습니다. ${err.message}`, true);
    } finally {
      delete btn.dataset.busy;
    }
  });
})();

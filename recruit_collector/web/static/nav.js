// 페이지를 옮길 때의 로딩 표시 (모든 화면 공통).
//  - 같은 사이트 안의 링크를 누르거나 폼을 제출해 새 페이지를 불러오면, 화면 맨 위 진행 막대를 채우기 시작한다.
//  - 상단 메뉴를 누른 경우에는 누른 메뉴를 바로 선택 상태로 바꾼다.
//  - 응답이 조금 늦으면(0.15초~) 본문을 살짝 흐리게 한다 (style.css의 .is-navigating).
//  - 뒤로 가기로 캐시된 페이지가 다시 보이면 표시를 지운다.
//  - 페이지 안의 탭(공고 보기 탭, 설정 탭: [data-tab-nav])을 누르면 누른 탭을 바로 선택 상태로 바꾸고, 탭 아래 내용을 감춘 뒤
//    그 자리에 불러오는 중 표시(스피너 + 자리표시)를 보여준다. data-tab-nav="list"는 공고 목록 모양, "block"은 카드 모양.
// 새 탭·다운로드·다른 사이트·같은 페이지 안 이동(#)·스크립트가 직접 처리한 클릭과 제출은 건드리지 않는다.
// window에서 듣기 때문에 다른 스크립트(document에 등록)가 preventDefault 한 경우를 알 수 있다.
(() => {
  const bar = document.querySelector(".nav-progress");
  if (!bar) return;

  function start(menuLink) {
    if (menuLink) {
      document.querySelectorAll('.topbar nav a[aria-current="page"]').forEach((x) => x.removeAttribute("aria-current"));
      menuLink.setAttribute("aria-current", "page");
    }
    bar.classList.add("is-loading");
    document.documentElement.classList.add("is-navigating");
  }

  // ------------------------------------------------------------ 탭 전환: 탭 아래 영역에 불러오는 중 표시
  let tabState = null; // { nav, current, hidden: [요소], panel }

  function skeleton(kind) {
    const rows = kind === "list" ? 4 : 3;
    const row = kind === "list"
      ? '<div class="skel-item"><i class="skel-line w20"></i><i class="skel-line w60 tall"></i><i class="skel-line w40"></i></div>'
      : '<div class="skel-card"><i class="skel-line w30 tall"></i><i class="skel-line w90"></i><i class="skel-line w70"></i></div>';
    return row.repeat(rows);
  }

  function startTab(nav, link) {
    const current = nav.querySelector('a[aria-current="page"]');
    current?.removeAttribute("aria-current");
    link.setAttribute("aria-current", "page");
    // 탭 아래(같은 부모 안에서 탭 뒤에 오는) 요소들을 감추고 그 자리에 표시를 넣는다. 스크립트·숨은 요소는 그대로.
    // 감추면 페이지가 짧아져 스크롤이 위로 튀므로, 불러오는 동안은 지금 높이를 유지한다
    document.body.style.minHeight = `${document.documentElement.scrollHeight}px`;
    const hidden = [];
    for (let el = nav.nextElementSibling; el; el = el.nextElementSibling) {
      if (el.matches("script, template, dialog")) continue;
      el.classList.add("tab-hidden");
      hidden.push(el);
    }
    const panel = document.createElement("div");
    panel.className = `tab-loading is-${nav.dataset.tabNav || "block"}`;
    panel.setAttribute("role", "status");
    panel.innerHTML = `<p class="tab-loading-msg"><span class="spinner" aria-hidden="true"></span>${link.textContent.trim().replace(/\s*\d+$/, "")} 불러오는 중…</p>
      <div class="skel" aria-hidden="true">${skeleton(nav.dataset.tabNav)}</div>`;
    nav.after(panel);
    tabState = { nav, current, link, hidden, panel };
    document.documentElement.classList.add("is-tab-loading");
  }

  function resetTab() {
    if (!tabState) return;
    const { current, link, hidden, panel } = tabState;
    panel.remove();
    hidden.forEach((el) => el.classList.remove("tab-hidden"));
    link.removeAttribute("aria-current");
    current?.setAttribute("aria-current", "page");
    document.documentElement.classList.remove("is-tab-loading");
    document.body.style.minHeight = "";
    tabState = null;
  }

  window.addEventListener("click", (e) => {
    const a = e.target.closest?.("a[href]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if ((a.target && a.target !== "_self") || a.hasAttribute("download") || a.origin !== location.origin) return;
    if (a.pathname === location.pathname && a.search === location.search) return; // 같은 페이지 (# 이동 포함)
    const tabNav = a.closest("[data-tab-nav]");
    if (tabNav && !tabState) startTab(tabNav, a);
    start(a.closest(".topbar nav") ? a : null);
  });

  window.addEventListener("submit", (e) => {
    const form = e.target;
    if (e.defaultPrevented || (form.target && form.target !== "_self")) return;
    start(null);
  });

  const current = document.querySelector('.topbar nav a[aria-current="page"]'); // 이 페이지의 원래 메뉴
  window.addEventListener("pageshow", (e) => {
    if (!e.persisted) return;
    resetTab();
    bar.classList.remove("is-loading");
    document.documentElement.classList.remove("is-navigating");
    document.querySelectorAll('.topbar nav a[aria-current="page"]').forEach((x) => x.removeAttribute("aria-current"));
    current?.setAttribute("aria-current", "page");
  });
})();

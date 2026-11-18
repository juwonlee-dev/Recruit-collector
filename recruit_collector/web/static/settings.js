// 설정 화면: [data-open] 버튼으로 확인 모달을 연다 (로그인 사용 켜기·끄기). 닫기는 review.js의 공통 처리.
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-open]");
  if (btn && !btn.disabled) document.getElementById(btn.dataset.open)?.showModal();
});

// 사용자 관리: 로그인 기기 칸을 누르면 그 사용자 행 바로 아래의 기기 목록 행을 펼치고 접는다.
// 주소의 #devices-<id>(기기 로그아웃 뒤 돌아온 경우)는 처음부터 펼쳐 둔다.
(() => {
  const toggles = document.querySelectorAll("[data-dev-toggle]");
  if (!toggles.length) return;
  const wrap = toggles[0].closest(".table-wrap");
  const fit = () => wrap.style.setProperty("--panel-w", `${wrap.clientWidth}px`);
  fit();
  window.addEventListener("resize", fit);
  const setOpen = (btn, open) => {
    const row = document.getElementById(btn.getAttribute("aria-controls"));
    btn.setAttribute("aria-expanded", String(open));
    btn.closest("tr").classList.toggle("is-open", open);
    row.classList.toggle("is-open", open);
    row.inert = !open;  // 접혀 있을 때는 안의 버튼에 키보드 포커스가 가지 않게
  };
  toggles.forEach((btn) => btn.addEventListener("click", () => setOpen(btn, btn.getAttribute("aria-expanded") !== "true")));
  const target = location.hash && document.querySelector(`[data-dev-toggle][aria-controls="${CSS.escape(location.hash.slice(1))}"]`);
  if (target) {
    // 처음에는 애니메이션 없이 펼쳐 두고, 그려진 뒤에는 no-anim을 지워 이후 여닫기는 부드럽게
    const animated = [target.closest("tr"), document.getElementById(target.getAttribute("aria-controls"))];
    animated.forEach((el) => el.classList.add("no-anim"));
    setOpen(target, true);
    requestAnimationFrame(() => {
      target.closest("tr").scrollIntoView({ block: "center" });
      requestAnimationFrame(() => animated.forEach((el) => el.classList.remove("no-anim")));
    });
  }
})();

// 설정 화면: 필터 생성을 누르면 끝날 때까지(결과 화면으로 넘어갈 때까지) 키워드 영역을 잠그고 로딩을 표시한다.
(() => {
  const panel = document.getElementById("keyword-panel");
  const form = panel?.querySelector(".gen-form");
  if (!form) return;

  let busy = false;
  form.addEventListener("submit", (e) => {
    if (busy) { e.preventDefault(); return; }  // 두 번 누르기 방지
    busy = true;
    const btn = form.querySelector(".gen-btn");
    btn.innerHTML = '<span class="spinner" aria-hidden="true"></span>생성 중…';
    form.querySelector(".gen-status").textContent = "필터를 생성하는 중입니다. 사이트에서 직무 목록을 받으면 시간이 더 걸릴 수 있습니다.";
    panel.classList.add("is-busy");
    panel.setAttribute("aria-busy", "true");
    // 폼 값(체크박스)이 전송 데이터에 담긴 뒤에 잠근다. 비활성화된 입력은 전송되지 않으므로.
    setTimeout(() => {
      panel.querySelectorAll("button, input, select").forEach((el) => { el.disabled = true; });
    }, 0);
  });

  // 뒤로 가기로 돌아와 페이지가 캐시에서 복원되면 잠금을 푼다
  window.addEventListener("pageshow", (e) => { if (e.persisted) location.reload(); });
})();

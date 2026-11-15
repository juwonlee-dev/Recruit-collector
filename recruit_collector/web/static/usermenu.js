// 상단 사용자 메뉴 (<details class="usermenu">). 여닫기와 키보드(Enter·Space)는 브라우저 기본 동작을 쓰고,
// 바깥을 누르거나 Esc를 누르거나 포커스가 메뉴 밖으로 나가면 닫는다 (Esc면 포커스를 메뉴 버튼으로 돌려준다).
(() => {
  const menu = document.querySelector(".usermenu");
  if (!menu) return;
  const summary = menu.querySelector("summary");
  const close = () => { menu.open = false; };

  document.addEventListener("click", (e) => {
    if (menu.open && !menu.contains(e.target)) close();
  });
  menu.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menu.open) {
      close();
      summary.focus();
    }
  });
  menu.addEventListener("focusout", (e) => {
    if (menu.open && e.relatedTarget && !menu.contains(e.relatedTarget)) close();
  });
  menu.addEventListener("toggle", () => summary.setAttribute("aria-expanded", String(menu.open)));
})();

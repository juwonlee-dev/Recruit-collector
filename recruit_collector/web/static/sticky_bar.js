// 편집 화면의 위쪽 고정 막대 ([data-sticky-bar]: 템플릿·위젯 편집의 이름·설명·공개·미리보기·저장, 이력서 작성의 이름·미리보기·저장·PDF).
// 사이트 머리(.topbar)도 위에 붙어 있고 휴대폰에서는 높이가 바뀌므로, 두 높이를 재서 CSS 변수로 넘긴다.
//   --topbar-h   막대가 붙는 위치 (사이트 머리 높이)
//   --sticky-top 막대 아래 내용이 시작하는 위치 (옆 패널의 sticky 위치, 스크롤 여백에 씀)
// 스크롤해 막대가 붙으면 .is-stuck(아래 그림자)을 붙인다.
(() => {
  const bar = document.querySelector("[data-sticky-bar]");
  if (!bar) return;
  const topbar = document.querySelector(".topbar");
  const root = document.documentElement;
  const update = () => {
    const t = topbar ? topbar.offsetHeight : 0;
    const total = t + bar.offsetHeight;
    root.style.setProperty("--topbar-h", `${t}px`);
    root.style.setProperty("--sticky-top", `${total}px`);
    root.style.scrollPaddingTop = `${total + 8}px`; // 입력 칸으로 이동·스크롤할 때 막대 밑에 가리지 않게
    bar.classList.toggle("is-stuck", bar.getBoundingClientRect().top <= t + 0.5 && window.scrollY > 0);
  };
  if ("ResizeObserver" in window) {
    const ro = new ResizeObserver(update);
    ro.observe(bar);
    if (topbar) ro.observe(topbar);
  } else {
    window.addEventListener("resize", update);
  }
  window.addEventListener("scroll", () => bar.classList.toggle("is-stuck", window.scrollY > 0 && bar.getBoundingClientRect().top <= (topbar?.offsetHeight || 0) + 0.5), { passive: true });
  update();
})();

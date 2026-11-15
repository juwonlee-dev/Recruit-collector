// 회사명 옆 '리뷰' 버튼 → 기업 리뷰 모달. 잡플래닛 데이터는 가져오지 않고 검색 링크만 만든다.
(() => {
  const modal = document.getElementById("review-modal");
  if (!modal) return;
  const SEARCH_URL = "https://www.jobplanet.co.kr/search?query=";

  // 법인 표기는 잡플래닛 회사명에 보통 없으므로 검색어에서 뺀다. 예) (주)카카오 → 카카오
  const searchName = (name) =>
    name.replace(/\(\s*(주|유|사|재)\s*\)|㈜|주식회사|유한회사/g, " ").replace(/\s+/g, " ").trim() || name.trim();

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".review-btn");
    if (btn) {
      const company = btn.dataset.company || "";
      const query = searchName(company);
      modal.querySelector(".review-company").textContent = company;
      modal.querySelector(".review-query").textContent = query;
      modal.querySelector(".review-link").href = SEARCH_URL + encodeURIComponent(query);
      modal.showModal();
      return;
    }
    // 모든 모달 공통: 닫기 버튼, 바깥(배경) 클릭. 모달 안쪽 여백 클릭도 target이 dialog이므로 좌표로 구분한다
    const closeBtn = e.target.closest("[data-close]");
    if (closeBtn) { closeBtn.closest("dialog")?.close(); return; }
    if (e.target.tagName === "DIALOG") {
      const r = e.target.getBoundingClientRect();
      const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
      if (!inside) e.target.close();
    }
  });
})();

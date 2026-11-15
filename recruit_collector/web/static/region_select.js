// 시도·시군구 선택 (공고 검색, 설정 > 검색 필터의 지역 등록).
// 시도를 고르면 #region-codes(공통 지역코드: 시도 → 시군구 목록)로 두 번째 선택지를 바꾼다.
(() => {
  const data = document.getElementById("region-codes");
  if (!data) return;
  const codes = JSON.parse(data.textContent);
  document.querySelectorAll("[data-region-sido]").forEach((sido) => {
    const city = sido.form?.querySelector("[data-region-city]");
    if (!city) return;
    sido.addEventListener("change", () => {
      const cities = codes[sido.value] || [];
      // 시도가 '전체 지역'이면 시군구는 고를 수 없다 (설정 화면은 시도가 항상 선택됨)
      const blank = sido.value ? (sido.required ? "전체" : "전체 시군구") : "시군구";
      city.replaceChildren(new Option(blank, ""), ...cities.map((c) => new Option(c, c)));
      city.disabled = !sido.value;
    });
  });
})();

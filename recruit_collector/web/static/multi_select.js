// 공고 검색의 다중 선택 드롭다운 (경력, 지역). <details class="ms"> 안의 체크박스가 검색 폼과 함께 전송되고,
// 이 스크립트는 요약 문구·닫기와 지역 패널(시도 고르기, 시도 전체 ↔ 시군구 배제, 고른 지역 칩)만 맡는다.
(() => {
  const all = [...document.querySelectorAll("details.ms")];
  if (!all.length) return;

  const checked = (ms) => [...ms.querySelectorAll("input[type=checkbox]:checked")];

  // 2개까지는 이름을 모두, 더 많으면 '첫 이름 외 N곳' (postings.html의 picked_text와 같은 규칙)
  function pickedText(names, empty, unit) {
    if (!names.length) return empty;
    return names.length <= 2 ? names.join(", ") : `${names[0]} 외 ${names.length - 1}${unit}`;
  }

  function update(ms) {
    const picked = checked(ms);
    ms.querySelector(".ms-text").textContent = pickedText(picked.map((i) => i.dataset.label), ms.dataset.empty, ms.dataset.unit);
    ms.classList.toggle("has-value", picked.length > 0);
    if (!ms.classList.contains("ms-region")) return;
    ms.querySelectorAll(".ms-sido").forEach((btn) => {
      const pane = ms.querySelector(`.ms-pane[data-sido="${CSS.escape(btn.dataset.sido)}"]`);
      btn.querySelector(".ms-n").textContent = pane.querySelectorAll("input:checked").length || "";
    });
    const chips = ms.querySelector(".ms-chips");
    chips.replaceChildren(...(picked.length ? picked.map((input) => {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "ms-chip-del";
      del.textContent = "×";
      del.setAttribute("aria-label", `${input.dataset.label} 빼기`);
      del.addEventListener("click", () => { input.checked = false; changed(ms, input); });
      const chip = document.createElement("span");
      chip.className = "ms-chip";
      chip.append(input.dataset.label, del);
      return chip;
    }) : [Object.assign(document.createElement("span"), { className: "ms-none", textContent: "고른 지역이 없으면 전체 지역에서 찾습니다" })]));
    ms.querySelector(".ms-clear").disabled = !picked.length;
  }

  // 시도 전체를 고르면 그 시도의 시군구는 이미 포함되므로 체크를 풀고 고를 수 없게 한다
  function changed(ms, input) {
    if (input.hasAttribute("data-whole")) {
      input.closest(".ms-pane").querySelectorAll("input:not([data-whole])").forEach((c) => {
        if (input.checked) c.checked = false;
        c.disabled = input.checked;
      });
    }
    update(ms);
  }

  function showSido(ms, sido) {
    ms.querySelectorAll(".ms-sido").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.sido === sido)));
    ms.querySelectorAll(".ms-pane").forEach((p) => { p.hidden = p.dataset.sido !== sido; });
  }

  all.forEach((ms) => {
    ms.addEventListener("change", (e) => { if (e.target.matches("input[type=checkbox]")) changed(ms, e.target); });
    ms.addEventListener("toggle", () => {
      if (ms.open) all.forEach((other) => { if (other !== ms) other.open = false; });  // 한 번에 하나만 펼침
    });
    ms.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || !ms.open) return;
      e.preventDefault();
      ms.open = false;
      ms.querySelector("summary").focus();
    });
    ms.querySelectorAll(".ms-sido").forEach((b) => b.addEventListener("click", () => showSido(ms, b.dataset.sido)));
    ms.querySelector(".ms-clear")?.addEventListener("click", () => {
      ms.querySelectorAll("input[type=checkbox]").forEach((c) => { c.checked = false; c.disabled = false; });
      update(ms);
    });
    update(ms);
  });

  // 바깥을 누르면 닫는다 (고른 값은 그대로, 조회 버튼을 눌러야 적용)
  document.addEventListener("click", (e) => {
    all.forEach((ms) => { if (ms.open && !ms.contains(e.target)) ms.open = false; });
  });
})();

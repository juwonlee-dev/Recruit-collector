// 주소 검색 (카카오 우편번호 서비스). 어느 화면에서든 쓸 수 있다. 스타일은 style.css의 .addr-*.
//
// 쓰는 법
//   1) 스크립트로: AddressSearch.open({ onSelect: ({ zonecode, address, text }) => … })
//      text는 문서에 넣기 좋은 형태: "(03938) 서울 마포구 모래내로3길 12 (건물명)"
//   2) HTML로: <input data-address-search> — 옆에 '주소 검색' 버튼이 붙고, 고르면 칸에 채운 뒤 상세 주소를 이어 쓰게 커서를 둔다.
//
// 카카오 스크립트는 처음 검색할 때만 불러온다 (그 전에는 카카오로 아무 요청도 가지 않는다). 키가 필요 없는 공개 서비스다.
(() => {
  const SCRIPT = "https://t1.kakaocdn.net/mapjsapi/bundle/postcode/prod/postcode.v2.js";
  let loading = null;

  function load() {
    if (window.kakao?.Postcode) return Promise.resolve();
    if (!loading) {
      loading = new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = SCRIPT;
        s.async = true;
        s.onload = () => (window.kakao?.Postcode ? resolve() : reject(new Error("주소 검색을 불러오지 못했습니다.")));
        s.onerror = () => { loading = null; reject(new Error("주소 검색을 불러오지 못했습니다. 네트워크를 확인해 주세요.")); };
        document.head.append(s);
      });
    }
    return loading;
  }

  function format(data) {
    // 이용자가 고른 주소(도로명/지번). 도로명이면 카카오 안내대로 법정동(…동·로·가)과 공동주택 이름을 괄호로 붙인다
    const road = data.userSelectedType === "J" ? data.jibunAddress : data.roadAddress;
    const extra = [];
    if (data.userSelectedType === "R") {
      if (data.bname && /[동로가]$/.test(data.bname)) extra.push(data.bname);
      if (data.buildingName && data.apartment === "Y") extra.push(data.buildingName);
    }
    const address = road + (extra.length ? ` (${extra.join(", ")})` : "");
    return { zonecode: data.zonecode, address, text: `(${data.zonecode}) ${address}` };
  }

  function open({ onSelect, query = "" } = {}) {
    const close = () => { layer.remove(); document.removeEventListener("keydown", onKey); back?.focus?.(); };
    const back = document.activeElement;
    const onKey = (e) => { if (e.key === "Escape") close(); };
    const box = document.createElement("div");
    box.className = "addr-box";
    const head = document.createElement("div");
    head.className = "addr-head";
    head.innerHTML = '<b>주소 검색</b><span class="addr-by">카카오 우편번호 서비스</span>';
    const x = document.createElement("button");
    x.type = "button";
    x.className = "addr-close";
    x.setAttribute("aria-label", "닫기");
    x.textContent = "×";
    x.onclick = close;
    head.append(x);
    const frame = document.createElement("div");
    frame.className = "addr-frame";
    frame.textContent = "불러오는 중…";
    box.append(head, frame);
    const layer = document.createElement("div");
    layer.className = "addr-layer";
    layer.setAttribute("role", "dialog");
    layer.setAttribute("aria-modal", "true");
    layer.setAttribute("aria-label", "주소 검색");
    layer.addEventListener("pointerdown", (e) => { if (e.target === layer) close(); });
    layer.append(box);
    document.body.append(layer);
    document.addEventListener("keydown", onKey);
    x.focus();
    load().then(() => {
      frame.textContent = "";
      new window.kakao.Postcode({
        width: "100%", height: "100%", maxSuggestItems: 5,
        oncomplete: (data) => { close(); onSelect?.(format(data)); },
      }).embed(frame, { q: query, autoClose: false });
    }).catch((e) => { frame.textContent = e.message; });
  }

  // data-address-search 속성이 있는 input에 '주소 검색' 버튼을 붙인다
  function attach(input, { onChange } = {}) {
    const wrap = document.createElement("div");
    wrap.className = "addr-field";
    input.replaceWith?.(wrap);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "addr-btn";
    btn.textContent = "주소 검색";
    btn.onclick = () => open({
      query: input.value.replace(/^\(\d{5}\)\s*/, ""),
      onSelect: ({ text }) => {
        input.value = text + " ";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        onChange?.(input.value);
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length); // 상세 주소를 이어서 적게
      },
    });
    wrap.append(input, btn);
    return wrap;
  }
  function enhance(root = document) {
    for (const input of root.querySelectorAll("input[data-address-search]:not([data-addr-ready])")) {
      input.dataset.addrReady = "1";
      attach(input);
    }
  }
  window.AddressSearch = { open, attach, enhance };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => enhance());
  else enhance();
})();

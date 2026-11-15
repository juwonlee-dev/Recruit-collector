// PDF 다운로드 버튼 (이력서 작성·목록·미리보기 공용).
// 링크로 이동하면 파일만 내려받고 페이지는 그대로라 상단 진행 표시(nav.js)가 꺼지지 않고, 만드는 동안 아무 표시도 없다.
// 그래서 그 자리에서 받아(fetch) 내려받기를 시작하고, 버튼에 'PDF 만드는 중…' → '다운로드 완료' / 실패 이유를 보여 준다.
//
// 쓰는 법: <a href="/resumes/1/pdf" data-pdf-download>PDF</a>  또는  PdfDownload.start(링크, { before: async () => 저장됐으면 true })
(() => {
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
    setTimeout(() => {
      item.classList.add("is-leaving");
      setTimeout(() => item.remove(), 250);
    }, isError ? 4000 : 2500);
  }

  function filename(res) {
    const cd = res.headers.get("Content-Disposition") || "";
    const utf8 = cd.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8) {
      try { return decodeURIComponent(utf8[1]); } catch { /* 아래로 */ }
    }
    return (cd.match(/filename="([^"]+)"/i) || [])[1] || "resume.pdf";
  }

  async function start(a, { before } = {}) {
    if (a.getAttribute("aria-busy") === "true") return; // 만드는 중에 또 누르면 무시
    const label = a.innerHTML;
    a.style.minWidth = `${a.offsetWidth}px`; // 글자가 바뀌어도 버튼 폭이 출렁이지 않게
    a.setAttribute("aria-busy", "true");
    a.classList.add("is-busy");
    a.innerHTML = '<span class="spinner" aria-hidden="true"></span> PDF 만드는 중…';
    try {
      if (before && !(await before())) throw new Error("저장하지 못해 PDF를 만들지 않았습니다. 잠시 뒤 다시 눌러 주세요.");
      let res;
      try {
        res = await fetch(a.href, { credentials: "same-origin" });
      } catch {
        throw new Error("서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.");
      }
      if (res.redirected || res.status === 401 || res.status === 403) throw new Error("로그인이 풀렸습니다. 다시 로그인한 뒤 눌러 주세요.");
      if (res.status === 404) throw new Error("이력서를 찾을 수 없습니다.");
      if (!res.ok || !(res.headers.get("Content-Type") || "").includes("pdf")) throw new Error(`PDF를 만들지 못했습니다 (HTTP ${res.status}). 잠시 뒤 다시 시도해 주세요.`);
      const blob = await res.blob();
      const name = filename(res);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      a.innerHTML = '<span aria-hidden="true">✓</span> 다운로드 완료';
      a.classList.add("is-done");
      toast(`'${name}' 파일을 내려받았습니다.`);
      setTimeout(() => {
        a.innerHTML = label;
        a.classList.remove("is-done");
        a.style.minWidth = "";
      }, 2500);
    } catch (e) {
      a.innerHTML = label;
      a.style.minWidth = "";
      toast(e.message, true);
    } finally {
      a.removeAttribute("aria-busy");
      a.classList.remove("is-busy");
    }
  }

  // 새 탭·다른 이름으로 저장(Ctrl·Shift·가운데 버튼)은 브라우저에 맡긴다
  document.addEventListener("click", (e) => {
    const a = e.target.closest?.("a[data-pdf-download]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    start(a);
  });
  window.PdfDownload = { start, toast };
})();

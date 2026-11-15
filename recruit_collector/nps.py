"""국민연금공단 '국민연금 가입사업장 정보' 오픈 API (공공데이터포털). 올댓컴퍼니에서 회사 자료를 못 가져올 때 쓴다.

https://www.data.go.kr/data/3046071/openapi.do — 인증키(DATA_GO_KR_SERVICE_KEY) 필요. 3인 이상 법인사업장만 개방.

- 사업장 검색(getBassInfoSearchV2): 사업장명으로 찾는다. 한 사업장이 달마다 한 행(그 달 자료의 seq)으로 나오며 최근 12개월이 있다.
- 상세(getDetailInfoSearchV2, seq): 가입자수(jnngpCnt)·당월고지금액(crrmmNtcAmt)·업종.
- 기간별 현황(getPdAcctoSttusInfoSearchV2, seq): 신규취득자수(nwAcqzrCnt)·상실가입자수(lssJnngpCnt).
한 달에 두 번 요청하므로 받은 달은 DB(nps_months)에 저장하고 다시 요청하지 않는다.

월평균급여(추정) = 당월고지금액 ÷ 보험료율(근로자+사용자) ÷ 가입자수.
보험료는 기준소득월액 상한까지만 매기므로 고소득자가 많으면 실제보다 낮게 나오고, 기준소득월액은 주로 전년도 소득으로
매년 7월에 다시 정해져 그달 실제 급여와 다르다 (화면에 추정치로 안내).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import unquote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from recruit_collector.models import NpsMonth, utcnow
from recruit_collector.salary import Month, Region, _region, normalize_name, search_query

log = logging.getLogger(__name__)

BASE_URL = "https://apis.data.go.kr/B552015/NpsBplcInfoInqireServiceV2"
SOURCE_URL = "https://www.data.go.kr/data/3046071/openapi.do"
SOURCE_NAME = "국민연금공단 공공데이터 (국민연금 가입사업장 정보)"
REQUEST_GAP = 0.3  # 요청 사이 간격(초). 인증키로 쓰는 공식 API라 채용 사이트 수집(2~4초)보다 짧게 (사용자 결정)
MAX_SEARCH_ROWS = 300
SEARCH_CACHE_SECONDS = 6 * 3600  # 사업장명 검색은 느려서(수 초) 잠시 기억한다. 새 달 자료는 한 달에 한 번 나온다


class NpsError(RuntimeError):
    pass


def contribution_rate(ym: str) -> float:
    """그 달의 국민연금 보험료율 (근로자+사용자 합계). 2025년까지 9%, 2026년부터 해마다 0.5%p씩 올라 2033년 13%."""
    year = int(ym[:4])
    return min(0.13, 0.09 + 0.005 * max(0, year - 2025))


def estimate_salary(notice_amount: int, headcount: int, ym: str) -> int | None:
    if headcount <= 0 or notice_amount <= 0:
        return None
    return round(notice_amount / contribution_rate(ym) / headcount)


@dataclass
class Workplace:
    """사업장 하나 (여러 달의 seq를 묶음). key = 사업자번호 앞 6자리|사업장명(비교용). 이전해 주소가 바뀌어도 같은 사업장"""

    key: str
    name: str
    address: str
    active: bool
    months: dict[str, int] = field(default_factory=dict)  # {YYYY-MM: seq}

    @property
    def latest_seq(self) -> int:
        return self.months[max(self.months)]


def service_key() -> str:
    """공공데이터포털 일반 인증키. Encoding 값(%가 들어 있음)을 넣었어도 원래 값으로 바꿔 쓴다."""
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    return unquote(key) if "%" in key else key


class NpsClient:
    def __init__(self, key: str | None = None, timeout: float = 20):
        self.key = key if key is not None else service_key()
        self._client = httpx.Client(timeout=timeout, headers={"User-Agent": "recruit-collector (personal, non-commercial)"})
        self._lock = threading.Lock()
        self._last = 0.0
        self._searches: dict[str, tuple[float, list[Workplace]]] = {}

    @property
    def ready(self) -> bool:
        return bool(self.key)

    def close(self) -> None:
        self._client.close()

    def _get(self, op: str, **params) -> tuple[list[dict], int]:
        """(items, totalCount). 일시적 오류는 두 번까지 다시 시도한다."""
        if not self.key:
            raise NpsError("국민연금 오픈 API 인증키가 없습니다 (DATA_GO_KR_SERVICE_KEY)")
        query = {"serviceKey": self.key, "dataType": "JSON", **params}
        for attempt in range(3):
            with self._lock:  # 요청 간격은 서버 전체에서 지킨다
                wait = self._last + REQUEST_GAP - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            try:
                resp = self._client.get(f"{BASE_URL}/{op}", params=query)
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError("server error", request=resp.request, response=resp)
                data = resp.json()
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as e:
                if attempt == 2:
                    raise NpsError(f"국민연금 오픈 API 요청 실패 ({type(e).__name__})") from e
                time.sleep(1 + attempt)
                continue
            if "response" not in data:  # 인증키 오류 등 게이트웨이 오류
                header = (data.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader") or {}
                raise NpsError(f"국민연금 오픈 API 오류: {header.get('errMsg') or resp.status_code}")
            header, body = data["response"].get("header") or {}, data["response"].get("body") or {}
            if header.get("resultCode") != "00":
                raise NpsError(f"국민연금 오픈 API 오류: {header.get('resultMsg')}")
            items = (body.get("items") or {}).get("item") if isinstance(body.get("items"), dict) else []
            if isinstance(items, dict):
                items = [items]
            return items or [], int(body.get("totalCount") or 0)
        raise AssertionError("unreachable")

    # ------------------------------------------------------------ 검색
    def search(self, name: str) -> list[Workplace]:
        """사업장명으로 검색해 사업장별로 묶는다 (최근 달 자료가 있는 사업장 먼저)."""
        query = search_query(name)
        hit = self._searches.get(query)
        if hit and time.monotonic() - hit[0] < SEARCH_CACHE_SECONDS:
            return hit[1]
        places = self._search(query)
        self._searches[query] = (time.monotonic(), places)
        if len(self._searches) > 500:  # 오래된 것부터 버린다
            for k in sorted(self._searches, key=lambda k: self._searches[k][0])[:100]:
                self._searches.pop(k, None)
        return places

    def _search(self, query: str) -> list[Workplace]:
        rows: list[dict] = []
        page = 1
        while len(rows) < MAX_SEARCH_ROWS:
            items, total = self._get("getBassInfoSearchV2", wkplNm=query, pageNo=page, numOfRows=100)
            rows += items
            if not items or len(rows) >= total:
                break
            page += 1
        places: dict[str, Workplace] = {}
        for it in rows:
            ym = str(it.get("dataCrtYm") or "")
            if len(ym) != 6 or "seq" not in it:
                continue
            ym = f"{ym[:4]}-{ym[4:]}"
            key = f"{str(it.get('bzowrRgstNo') or '')[:6]}|{normalize_name(str(it.get('wkplNm') or ''))}"[:300]
            wp = places.get(key)
            if wp is None:
                wp = places[key] = Workplace(key, str(it.get("wkplNm") or "").strip(),
                                             str(it.get("wkplRoadNmDtlAddr") or "").strip(), False)
            wp.months[ym] = int(it["seq"])
            if ym == max(wp.months):  # 이름·주소·가입 상태는 가장 최근 달 기준
                wp.name = str(it.get("wkplNm") or wp.name).strip()
                wp.address = str(it.get("wkplRoadNmDtlAddr") or wp.address).strip()
                wp.active = str(it.get("wkplJnngStcd")) == "1"
        return sorted(places.values(), key=lambda w: (max(w.months), w.active), reverse=True)

    # ------------------------------------------------------------ 월별 자료
    def months(self, s: Session, wp: Workplace) -> tuple[list[Month], str]:
        """사업장의 월별 자료와 업종. 저장해 둔 달은 다시 요청하지 않는다."""
        saved = {m.seq: m for m in s.scalars(select(NpsMonth).where(NpsMonth.seq.in_(list(wp.months.values()))))}
        for ym, seq in sorted(wp.months.items()):
            if seq in saved:
                continue
            detail, _ = self._get("getDetailInfoSearchV2", seq=seq)
            period, _ = self._get("getPdAcctoSttusInfoSearchV2", seq=seq)
            d, p = (detail or [{}])[0], (period or [{}])[0]
            row = NpsMonth(seq=seq, workplace=wp.key, name=wp.name, address=wp.address, ym=ym,
                           industry=(d.get("vldtVlKrnNm") or "").strip() or None,
                           headcount=_int(d.get("jnngpCnt")), notice_amount=_int(d.get("crrmmNtcAmt")),
                           hires=_int(p.get("nwAcqzrCnt")), leaves=_int(p.get("lssJnngpCnt")), fetched_at=utcnow())
            s.merge(row)
            s.commit()  # 중간에 실패해도 받은 달은 남긴다
            saved[seq] = row
        rows = sorted(saved.values(), key=lambda m: m.ym)
        months = [Month(m.ym, estimate_salary(m.notice_amount, m.headcount, m.ym), m.hires, m.leaves, m.headcount)
                  for m in rows]
        industry = next((m.industry for m in reversed(rows) if m.industry), "")
        return months, industry or ""


def _int(v) -> int:
    try:
        return int(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0


def choose(places: list[Workplace], name: str, regions: list[Region]) -> tuple[Workplace | None, str | None]:
    """회사명이 같은(법인 표기·띄어쓰기 제외) 사업장 중 주소까지 맞는 곳을 먼저, 가입 중인 곳을 먼저 고른다."""
    target = normalize_name(name)
    same = [w for w in places if target and normalize_name(w.name) == target]
    same.sort(key=lambda w: (w.active, max(w.months)), reverse=True)
    for w in same:
        addr = _region(w.address)
        if any(addr[: len(r)] == r for r in regions):
            return w, "name_address"
    return (same[0], "name") if same else (None, None)


def address_regions(address: str | None) -> list[Region]:
    """올댓컴퍼니 주소에서 시·도와 시·군·구까지만 (도로명은 사이트마다 표기가 달라 비교하지 않는다)."""
    r = _region(address or "")[:2]
    return [r] if r else []

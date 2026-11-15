"""OpenDART(금융감독원 전자공시) — 사업보고서 '직원 현황'의 1인 평균 급여 = 공시 기준 평균연봉.

https://opendart.fss.or.kr — 인증키(OPENDART_API_KEY) 필요. 사업보고서를 내는 회사(상장사·일부 비상장사)만 있다.

- 회사 목록(corpCode.xml): 회사명 → 고유번호. 약 12만 곳이라 DB(dart_corps)에 두고 일주일마다 다시 받는다.
- 회사 개황(company.json): 주소. 같은 이름의 회사가 여럿이면 주소(시·도, 시·군·구)로 고른다.
- 직원 현황(empSttus.json, 사업보고서 11011): 부문·성별 행마다 직원 수(sm)·연간 급여 총액·1인 평균 급여액.
  회사 전체 평균은 '합계' 행이 있으면 그 값, 없으면 행별 1인 평균 급여를 직원 수로 가중 평균한다.
응답은 DB(dart_cache)에 두고 일정 기간 다시 요청하지 않는다 (자료가 없다는 응답도).
"""

from __future__ import annotations

import io
import logging
import os
import re
import threading
import time
import zipfile
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from recruit_collector.models import DartCache, DartCorp, company_key, utcnow
from recruit_collector.salary import Region, _region

log = logging.getLogger(__name__)

API = "https://opendart.fss.or.kr/api"
SOURCE_NAME = "금융감독원 전자공시(DART) 사업보고서 직원 현황"
REPORT_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
REQUEST_GAP = 0.3
CORPS_MAX_AGE = timedelta(days=7)
FOUND_MAX_AGE = timedelta(days=30)  # 공시된 직원 현황 (정정 공시가 드물게 있음)
EMPTY_MAX_AGE = timedelta(days=7)  # 자료 없음 (새 사업보고서가 나올 수 있음)
YEARS = 3  # 최근 몇 사업연도까지
CORPS_UPDATED_KEY = "dart_corps_updated"


class DartError(RuntimeError):
    pass


def api_key() -> str:
    return os.environ.get("OPENDART_API_KEY", "").strip()


def _amount(v) -> int | None:
    digits = re.sub(r"[^\d]", "", str(v or ""))
    return int(digits) if digits else None


def summarize(rows: list[dict]) -> dict | None:
    """직원 현황 행들 → {'avg', 'headcount', 'total'}. 숫자가 없으면 None."""
    is_total = lambda r: any("계" in str(r.get(k) or "") for k in ("fo_bbm", "sexdstn"))
    totals = [r for r in rows if is_total(r)]
    both = [r for r in totals if all("계" in str(r.get(k) or "") for k in ("fo_bbm", "sexdstn"))]
    use = both or totals or rows
    people = [(_amount(r.get("sm")), _amount(r.get("jan_salary_am")), _amount(r.get("fyer_salary_totamt"))) for r in use]
    people = [p for p in people if p[0]]
    if not people:
        return None
    headcount = sum(p[0] for p in people)
    total = sum(p[2] or 0 for p in people) or None
    if all(p[1] for p in people):
        avg = round(sum(p[0] * p[1] for p in people) / headcount)
    elif total:
        avg = round(total / headcount)
    else:
        return None
    return {"avg": avg, "headcount": headcount, "total": total}


class DartClient:
    def __init__(self, key: str | None = None, timeout: float = 30):
        self.key = key if key is not None else api_key()
        self._client = httpx.Client(timeout=timeout)
        self._lock = threading.Lock()
        self._corps_lock = threading.Lock()
        self._last = 0.0

    @property
    def ready(self) -> bool:
        return bool(self.key)

    def close(self) -> None:
        self._client.close()

    def _request(self, path: str, **params) -> httpx.Response:
        if not self.key:
            raise DartError("OpenDART 인증키가 없습니다 (OPENDART_API_KEY)")
        with self._lock:
            wait = self._last + REQUEST_GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        try:
            resp = self._client.get(f"{API}/{path}", params={"crtfc_key": self.key, **params})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise DartError(f"OpenDART 요청 실패 ({type(e).__name__})") from e
        return resp

    def _json(self, s: Session, cache_key: str, path: str, **params) -> dict:
        """캐시를 거친 JSON 응답. status 000(정상)·013(자료 없음)만 저장한다."""
        row = s.get(DartCache, cache_key)
        if row is not None:
            max_age = FOUND_MAX_AGE if row.payload.get("status") == "000" else EMPTY_MAX_AGE
            if utcnow() - row.fetched_at < max_age:
                return row.payload
        data = self._request(path, **params).json()
        status = data.get("status")
        if status not in ("000", "013"):
            raise DartError(f"OpenDART 오류: {data.get('message') or status}")
        s.merge(DartCache(key=cache_key, payload=data, fetched_at=utcnow()))
        s.commit()
        return data

    # ------------------------------------------------------------ 회사 목록
    def ensure_corps(self, s: Session) -> None:
        """회사 목록이 없거나 오래됐으면 받는다 (여러 요청이 겹쳐도 한 번만)."""
        from recruit_collector.web.auth import get_setting, set_setting

        def fresh() -> bool:
            updated = get_setting(s, CORPS_UPDATED_KEY)
            return bool(updated) and utcnow() - datetime.fromisoformat(updated) < CORPS_MAX_AGE

        if fresh():
            return
        with self._corps_lock:
            s.expire_all()
            if fresh():
                return
            content = self._request("corpCode.xml").content
            if content[:2] != b"PK":
                raise DartError("OpenDART 회사 목록을 받지 못했습니다")
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                xml = z.read(z.namelist()[0]).decode("utf-8")
            rows = []
            for item in re.findall(r"<list>(.*?)</list>", xml, re.S):
                get = lambda tag: (re.search(rf"<{tag}>(.*?)</{tag}>", item, re.S) or [None, ""])[1].strip()
                code, name = get("corp_code"), get("corp_name")
                if code and name:
                    rows.append({"corp_code": code, "name": name[:200], "name_key": company_key(name),
                                 "stock_code": get("stock_code") or None, "modify_date": get("modify_date") or None})
            s.execute(delete(DartCorp))
            for i in range(0, len(rows), 5000):
                s.execute(insert(DartCorp), rows[i:i + 5000])
            set_setting(s, CORPS_UPDATED_KEY, utcnow().isoformat())
            s.commit()
            log.info("OpenDART 회사 목록: %d곳", len(rows))

    # ------------------------------------------------------------ 회사 찾기
    def find_corp(self, s: Session, name: str, regions: list[Region]) -> DartCorp | None:
        """회사명이 같은 공시 회사. 여럿이면 주소(시·도, 시·군·구)가 맞는 곳, 그래도 모르면 상장사가 하나일 때 그곳."""
        key = company_key(name)
        if not key:
            return None
        self.ensure_corps(s)
        same = s.scalars(select(DartCorp).where(DartCorp.name_key == key)).all()
        if not same:
            return None
        same = sorted(same, key=lambda c: (bool(c.stock_code), c.modify_date or ""), reverse=True)[:5]
        if regions:
            for c in same:
                info = self._json(s, f"company:{c.corp_code}", "company.json", corp_code=c.corp_code)
                addr = _region(info.get("adres") or "")
                if any(addr[: len(r)] == r for r in regions):
                    return c
        if len(same) == 1:
            return same[0]
        listed = [c for c in same if c.stock_code]
        return listed[0] if len(listed) == 1 else None

    # ------------------------------------------------------------ 직원 현황
    def salary_years(self, s: Session, corp: DartCorp, today: date | None = None) -> list[dict]:
        """최근 사업연도부터 YEARS개까지 [{year, avg, headcount, total, url}] (자료 있는 해만)."""
        year = (today or date.today()).year
        out = []
        for y in range(year - 1, year - 1 - YEARS, -1):
            data = self._json(s, f"emp:{corp.corp_code}:{y}:11011", "empSttus.json",
                              corp_code=corp.corp_code, bsns_year=str(y), reprt_code="11011")
            rows = data.get("list") or []
            sm = summarize(rows) if data.get("status") == "000" else None
            if sm:
                rcept = next((r.get("rcept_no") for r in rows if r.get("rcept_no")), None)
                out.append({"year": y, **sm, "url": REPORT_URL.format(rcept) if rcept else None})
        return out

    def disclosure(self, s: Session, name: str, regions: list[Region]) -> dict | None:
        """회사명·주소로 찾은 공시 기준 평균연봉. 공시 회사가 아니거나 자료가 없으면 None."""
        corp = self.find_corp(s, name, regions)
        if corp is None:
            return None
        years = self.salary_years(s, corp)
        if not years:
            return None
        return {"corp_code": corp.corp_code, "corp_name": corp.name, "stock_code": corp.stock_code,
                "years": years, "source": SOURCE_NAME}

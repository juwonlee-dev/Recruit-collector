"""연봉·인원 자료를 어디서 가져올지 정한다: 올댓컴퍼니를 먼저 보고, 안 되면 국민연금 오픈 API (recruit_collector/nps.py).

- 연봉 모달(공고 회사), 연봉·인원 회사 페이지, 회사 비교, 회사 검색이 모두 여기를 거친다.
- 올댓컴퍼니에서 본 회사(검색 결과·회사 페이지)는 이름·주소를 DB(atc_companies)에 남겨 두고, 그 회사 페이지를
  못 가져오면(삭제된 페이지 404, 요청 실패, 구조 변경) 그 이름·주소로 국민연금 사업장을 찾아 같은 모양의 자료를 만든다.
  삭제된 페이지는 하루 동안 다시 요청하지 않는다.
- 국민연금에서 찾은 회사의 ID는 'n' + 그 달 자료의 seq (예: n6889186). 비교·즐겨찾기에도 그대로 쓴다.
  단, 올댓컴퍼니 회사의 대체 자료로 보여줄 때는 올댓컴퍼니 ID를 그대로 둔다 (같은 회사가 두 ID로 담기지 않도록).
- 모든 회사 자료에는 source(출처 이름·링크·안내)를, 대체 자료로 보여주면 fallback(이유)을 붙인다.
- 공시 기준 평균연봉(OpenDART, recruit_collector/dart.py)은 화면이 따로 불러온다 (느린 공시 조회가 차트를 막지 않도록).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import timedelta

import httpx
from sqlalchemy.orm import sessionmaker

from recruit_collector import nps, salary
from recruit_collector.config import AppConfig
from recruit_collector.dart import DartClient
from recruit_collector.http import HttpClient
from recruit_collector.models import AtcCompany, utcnow
from recruit_collector.salary import Region

log = logging.getLogger(__name__)

GONE_RECHECK = timedelta(days=1)
ATC_NOTE = "국민연금 가입 사업장 자료 기반 추정치. 실제 급여와 다를 수 있습니다"
NPS_NOTE = ("당월고지금액 ÷ 보험료율 ÷ 가입자수로 계산한 추정치. 보험료는 기준소득월액 상한까지만 매겨 고소득자가 많으면 "
            "낮게 나오고, 최근 12개월 자료만 제공됩니다")


class SourceError(RuntimeError):
    """올댓컴퍼니와 국민연금 모두에서 자료를 찾지 못함 (화면에 그대로 보여줄 문구)."""


class _GoneCached(Exception):
    """하루 안에 삭제된 페이지로 확인한 올댓컴퍼니 회사 (다시 요청하지 않음)."""


def _atc_source(url: str) -> dict:
    return {"key": "allthatcompany", "name": "올댓컴퍼니", "url": url, "note": ATC_NOTE}


def _nps_source() -> dict:
    return {"key": "nps", "name": nps.SOURCE_NAME, "url": nps.SOURCE_URL, "note": NPS_NOTE}


def _gone(e: Exception) -> bool:
    """올댓컴퍼니 회사 페이지가 없음(404, 또는 하루 안에 그렇게 확인함)."""
    return isinstance(e, _GoneCached) or (isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404)


def _reason(e: Exception | None, found_nothing: bool = False) -> str:
    if found_nothing:
        return "올댓컴퍼니 검색 결과에 회사명이 같은 회사가 없어"
    if e is not None and _gone(e):
        return "올댓컴퍼니에서 삭제된 회사 페이지라"
    return f"올댓컴퍼니에서 정보를 가져오지 못해({type(e).__name__})"


class SalarySources:
    def __init__(self, config: AppConfig, session_factory: sessionmaker):
        self.config = config
        self.session_factory = session_factory
        self.nps = nps.NpsClient()
        self.dart = DartClient()

    def close(self) -> None:
        self.nps.close()
        self.dart.close()

    # ------------------------------------------------------------ 올댓컴퍼니
    def _atc(self, fn):
        http = HttpClient(self.config.http)
        try:
            return fn(http)
        finally:
            http.close()

    def _remember(self, rows: list[tuple[str, str, str | None]], gone_id: str | None = None) -> None:
        """올댓컴퍼니에서 본 회사의 이름·주소를 남긴다 (나중에 회사 페이지를 못 가져올 때 국민연금에서 찾는 데 씀)."""
        with self.session_factory() as s:
            for cid, name, address in rows:
                row = s.get(AtcCompany, cid)
                if row is None:
                    s.add(AtcCompany(id=cid, name=name[:200], address=(address or "")[:500] or None, seen_at=utcnow()))
                else:
                    row.name, row.seen_at = name[:200], utcnow()
                    row.address = (address or "")[:500] or row.address
                    if cid != gone_id:
                        row.gone_at = None
            if gone_id:
                row = s.get(AtcCompany, gone_id)
                if row is not None:
                    row.gone_at = utcnow()
            s.commit()

    def _known(self, cid: str) -> AtcCompany | None:
        with self.session_factory() as s:
            return s.get(AtcCompany, cid)

    def _atc_company(self, cid: str) -> dict:
        """올댓컴퍼니 회사 페이지. 하루 안에 삭제된 페이지로 확인한 회사는 요청하지 않고 404처럼 실패한다."""
        known = self._known(cid)
        if known and known.gone_at and utcnow() - known.gone_at < GONE_RECHECK:
            raise _GoneCached()
        try:
            c = self._atc(lambda http: salary.fetch_company(http, cid))
        except Exception as e:
            if _gone(e):
                self._remember([], gone_id=cid)
            raise
        self._remember([(c.id, c.name, c.address)])
        return {**asdict(c), "source": _atc_source(c.url)}

    # ------------------------------------------------------------ 국민연금
    def _nps_company(self, wp: nps.Workplace) -> dict:
        with self.session_factory() as s:
            months, industry = self.nps.months(s, wp)
        return {"id": f"n{wp.latest_seq}", "name": wp.name, "address": wp.address, "industry": industry,
                "url": nps.SOURCE_URL, "months": [asdict(m) for m in months], "source": _nps_source()}

    def _nps_find(self, name: str, regions: list[Region]) -> tuple[dict | None, str | None]:
        if not self.nps.ready or not name:
            return None, None
        wp, match = nps.choose(self.nps.search(name), name, regions)
        return (self._nps_company(wp), match) if wp else (None, None)

    def _nps_by_id(self, cid: str) -> dict:
        """'n' + seq로 국민연금 사업장을 다시 찾는다 (그 달 자료의 이름으로 검색해 seq가 든 사업장)."""
        seq = int(cid[1:])
        detail, _ = self.nps._get("getDetailInfoSearchV2", seq=seq)
        if not detail:
            raise SourceError("국민연금 자료에서 이 회사를 찾지 못했습니다 (12개월이 지난 자료일 수 있습니다).")
        for wp in self.nps.search(detail[0].get("wkplNm") or ""):
            if seq in wp.months.values():
                return self._nps_company(wp)
        raise SourceError("국민연금 자료에서 이 회사를 찾지 못했습니다.")

    def _fallback(self, name: str, regions: list[Region], reason: str) -> tuple[dict | None, str | None]:
        """올댓컴퍼니 대신 국민연금 자료. (회사 자료, 맞춘 정도)"""
        try:
            company, match = self._nps_find(name, regions)
        except nps.NpsError as e:
            log.warning("국민연금 자료 조회 실패 (%s): %s", name, e)
            raise SourceError(f"{reason} 국민연금 공공데이터에서도 가져오지 못했습니다. ({e})") from e
        if company:
            company["fallback"] = f"{reason} 국민연금공단 공공데이터로 보여줍니다."
        return company, match

    # ------------------------------------------------------------ 화면별 진입점
    def company(self, cid: str) -> dict:
        """연봉·인원 회사 페이지·회사 비교: {'query','match','candidates','company'}"""
        if cid.startswith("n"):
            try:
                return {"query": "", "match": None, "candidates": None, "company": self._nps_by_id(cid)}
            except nps.NpsError as e:
                raise SourceError(f"국민연금 공공데이터에서 가져오지 못했습니다. ({e})") from e
        try:
            return {"query": "", "match": None, "candidates": None, "company": self._atc_company(cid)}
        except Exception as e:
            known = self._known(cid)
            log.warning("올댓컴퍼니 회사 %s 조회 실패: %s", cid, e)
            reason = _reason(e)
            if not known:
                raise SourceError(f"{reason} 다른 자료에서 찾을 회사 이름을 모릅니다. 연봉·인원에서 회사를 다시 검색해 주세요.") from e
            company, _ = self._fallback(known.name, nps.address_regions(known.address), reason)
            if not company:
                raise SourceError(f"{reason} 국민연금 공공데이터에서도 '{known.name}'을(를) 찾지 못했습니다.") from e
            company["id"] = cid  # 대체 자료여도 올댓컴퍼니 ID를 유지 (비교함·즐겨찾기에서 같은 회사로 보도록)
            return {"query": "", "match": None, "candidates": None, "company": company}

    def posting(self, company: str, location: str | None, raw: dict | None, cid: str | None = None) -> dict:
        """연봉 모달 (공고 회사). cid를 주면 후보 목록에서 고른 회사."""
        query = salary.search_query(company)
        if cid:
            data = self.company(cid)
            return {**data, "query": query, "match": "manual"}
        regions = salary.posting_regions(location, raw)
        candidates, chosen, match, error = None, None, None, None
        gone_id = None  # 고른 올댓컴퍼니 회사 페이지를 못 가져왔으면 그 ID
        try:
            candidates, chosen, match = self._atc(lambda http: salary.search(http, company, regions))
            self._remember([(c.id, c.name, c.address) for c in candidates])
        except Exception as e:
            log.warning("올댓컴퍼니 검색 실패 (%s): %s", company, e)
            error = e
        cand_dicts = [asdict(c) for c in candidates] if candidates is not None else None
        if chosen:
            try:
                return {"query": query, "match": match, "candidates": cand_dicts, "company": self._atc_company(chosen.id)}
            except Exception as e:
                log.warning("올댓컴퍼니 회사 %s 조회 실패: %s", chosen.id, e)
                error, gone_id = e, chosen.id
        reason = _reason(error, found_nothing=error is None)
        try:
            nps_company, nps_match = self._fallback(company, regions, reason)
        except SourceError:
            if error is None:  # 올댓컴퍼니에 같은 이름이 없음 + 국민연금 조회 실패: 지금처럼 후보에서 고르게 한다
                return {"query": query, "match": None, "candidates": cand_dicts, "company": None}
            raise
        if nps_company:
            if gone_id:  # 회사 페이지·비교에서 같은 회사를 열 때와 같은 ID (company()의 대체 자료와 맞춤)
                nps_company["id"] = gone_id
            return {"query": query, "match": nps_match, "candidates": cand_dicts, "company": nps_company}
        if error is not None and cand_dicts is None:
            raise SourceError(f"{reason} 국민연금 공공데이터에서도 회사를 찾지 못했습니다.")
        out = {"query": query, "match": None, "candidates": cand_dicts, "company": None}
        if error is not None:  # 고른 회사가 삭제됨 등: 후보 목록에서 다른 회사를 고를 수 있게 이유를 알린다
            out["notice"] = f"{reason} 자료를 볼 수 없고, 국민연금 공공데이터에서도 찾지 못했습니다."
        return out

    def search(self, query: str, page: int) -> dict:
        """연봉·인원 메뉴의 회사 검색. 올댓컴퍼니 검색이 안 되면 국민연금 사업장 검색 (첫 페이지만)."""
        try:
            rows, has_next = self._atc(lambda http: salary.search_page(http, query, page))
        except Exception as e:
            log.warning("올댓컴퍼니 검색 실패 (%s): %s", query, e)
            if page > 1 or not self.nps.ready:
                raise SourceError(f"{_reason(e)} 검색하지 못했습니다.") from e
            try:
                places = self.nps.search(query)
            except nps.NpsError as e2:
                raise SourceError(f"{_reason(e)} 국민연금 공공데이터에서도 검색하지 못했습니다. ({e2})") from e2
            return {"query": query, "page": 1, "has_next": False, "source": _nps_source(),
                    "fallback": f"{_reason(e)} 국민연금공단 공공데이터에서 찾은 사업장을 보여줍니다.",
                    "candidates": [{"id": f"n{w.latest_seq}", "name": w.name, "address": w.address} for w in places[:30]]}
        self._remember([(r.id, r.name, r.address) for r in rows])
        return {"query": query, "page": page, "has_next": has_next,
                "candidates": [{"id": r.id, "name": r.name, "address": r.address} for r in rows]}

    def disclosure(self, name: str, address: str | None) -> dict | None:
        """공시 기준 평균연봉 (OpenDART). 인증키가 없거나 공시 회사가 아니면 None."""
        if not self.dart.ready or not name:
            return None
        with self.session_factory() as s:
            return self.dart.disclosure(s, name, nps.address_regions(address))

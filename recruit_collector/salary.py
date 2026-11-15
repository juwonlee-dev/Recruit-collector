"""올댓컴퍼니(allthatcompany.com) 회사 연봉·인원 정보 조회.

올댓컴퍼니는 국민연금 가입 사업장 공개 자료로 회사별 월별 인원·입사자·퇴사자·월평균급여(추정)를
보여준다. 공고의 회사명과 근무지역으로 회사를 찾아 그 표를 읽는다. 조회할 때마다 가져오고 저장하지 않는다.

회사 찾기 (검색 결과는 회사명이 비슷한 회사가 여럿 나온다):
  1순위: 회사명이 같고 주소도 같은 회사
  2순위: 주소가 같은 회사가 없으면 회사명이 같은 회사
회사명은 법인 표기((주)·주식회사 등), 괄호 안 내용(영문명·사업장 이름 등)과 공백을 빼고 비교한다.
  예) '주식회사 카카오스타일 (Kakao Style Corp.)' = '(주)카카오스타일'
공고에는 근무지가 시·도와 시·군·구까지만 있으므로(예: '서울 강남구'), 주소는 회사 주소의 앞부분이
공고 근무지역과 같은지로 본다. 예) '서울 강남구' ↔ '서울특별시 강남구 테헤란로'
"""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from recruit_collector.crawlers.base import ParseError
from recruit_collector.http import HttpClient

BASE_URL = "https://allthatcompany.com"
SEARCH_URL = BASE_URL + "/search"
COMPANY_URL = BASE_URL + "/c/{}"
MAX_SEARCH_PAGES = 2  # 검색 결과는 가까운 이름부터 나오므로 앞쪽만 본다 (페이지당 10곳)

# 법인 표기. 회사명 비교와 검색어에서 뺀다.
_LEGAL_RE = re.compile(r"\(\s*(주|유|사|재|합|학)\s*\)|㈜|주식회사|유한회사|유한책임회사|사단법인|재단법인|합자회사|합명회사")
_PAREN_RE = re.compile(r"\([^()]*\)")

SIDO = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
    "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "강원도": "강원",
    "강원특별자치도": "강원", "충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북",
    "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주", "제주도": "제주",
}


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", "", _PAREN_RE.sub(" ", _LEGAL_RE.sub(" ", name or ""))).lower()


def search_query(name: str) -> str:
    """검색어: 법인 표기를 뺀 회사명. 예) (주)카카오 → 카카오"""
    return re.sub(r"\s+", " ", _LEGAL_RE.sub(" ", name or "")).strip() or (name or "").strip()


Region = tuple[str, ...]  # (시·도 약칭, 시·군·구 ...). 예) ("경기", "수원시", "영통구")


def _region(text: str) -> Region:
    tokens = [t for t in text.split() if t != "전체"]
    if not tokens:
        return ()
    return (SIDO.get(tokens[0], tokens[0]), *tokens[1:])


def posting_regions(location: str | None, raw: dict[str, Any] | None = None) -> list[Region]:
    """공고의 근무지역 목록. 여러 곳이면 쉼표로 나뉜다. 리멤버는 원본의 근무지 전체를 쓴다 (목록 표시는 '외 N')."""
    regions: list[Region] = []
    for a in (raw or {}).get("addresses") or []:
        if isinstance(a, dict):
            regions.append(_region(f"{a.get('address_level1') or ''} {a.get('address_level2') or ''}"))
    for part in (location or "").split(","):
        regions.append(_region(re.sub(r"\s*외\s*\d+\s*$", "", part)))
    return [r for r in dict.fromkeys(regions) if r]


def address_matches(regions: list[Region], address: str) -> bool:
    """회사 주소가 공고 근무지역 중 하나로 시작하는지 (시·도는 약칭으로 맞춰 비교)."""
    addr = _region(address)
    return any(addr[: len(r)] == r for r in regions)


@dataclass
class Candidate:
    id: str
    name: str
    address: str
    name_match: bool = False
    address_match: bool = False


@dataclass
class Month:
    ym: str  # YYYY-MM
    salary: int | None  # 월평균급여(추정, 원). 0은 자료 없음으로 본다
    hires: int
    leaves: int
    headcount: int


@dataclass
class CompanyStats:
    id: str
    name: str
    address: str
    industry: str
    url: str
    months: list[Month] = field(default_factory=list)


_SEARCH_ROW_RE = re.compile(r'<a href="/c/(\d+)/[^"]*">([^<]*)</a>\s*</td>\s*<td>\s*([^<]*?)\s*</td>', re.S)


def parse_search(text: str) -> tuple[list[Candidate], bool]:
    """검색 결과 (후보 목록, 다음 페이지 있음)."""
    if 'id="search-table"' not in text:
        raise ParseError("올댓컴퍼니: 검색 결과 표가 없음")
    rows = [Candidate(cid, html.unescape(n).strip(), html.unescape(a).strip()) for cid, n, a in _SEARCH_ROW_RE.findall(text)]
    has_next = bool(re.search(r'<a class="page-link" href="[^"]*page=\d+[^"]*" rel="next"', text))
    return rows, has_next


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _num(fragment: str) -> int:
    digits = re.sub(r"[^\d]", "", _text(fragment))
    if not digits:
        raise ParseError(f"올댓컴퍼니: 숫자가 아님 {fragment!r}")
    return int(digits)


def parse_company(text: str, company_id: str) -> CompanyStats:
    name = re.search(r"<h1>(.*?)</h1>", text, re.S)
    address = re.search(r"주소\s*:\s*<em>(.*?)</em>", text, re.S)
    industry = re.search(r"업종\s*:\s*<em>(.*?)</em>", text, re.S)
    table = re.search(r"<table[^>]*>\s*<tr>\s*<th>기준년월</th>(.*?)</table>", text, re.S)
    if not (name and table):
        raise ParseError("올댓컴퍼니: 회사 정보 또는 월별 표가 없음")
    months = []
    for row in re.findall(r"<tr>(.*?)</tr>", table.group(1), re.S):
        ym = re.search(r"<th>\s*(\d{6})\s*</th>", row)
        cells = re.findall(r"<td>(.*?)</td>", row, re.S)
        if not ym or len(cells) != 4:
            continue
        salary, hires, leaves, headcount = (_num(c) for c in cells)
        months.append(Month(f"{ym.group(1)[:4]}-{ym.group(1)[4:]}", salary or None, hires, leaves, headcount))
    months.sort(key=lambda m: m.ym)
    return CompanyStats(
        id=company_id,
        name=_text(name.group(1)),
        address=_text(address.group(1)) if address else "",
        industry=re.sub(r"\s+", " ", _text(industry.group(1))) if industry else "",
        url=COMPANY_URL.format(company_id),
        months=months,
    )


def choose(candidates: list[Candidate], company: str, regions: list[Region]) -> tuple[Candidate | None, str | None]:
    """후보마다 회사명·주소 일치를 표시하고 고른다. (고른 후보, 'name_address' | 'name' | None)"""
    target = normalize_name(company)
    for c in candidates:
        c.name_match = bool(target) and normalize_name(c.name) == target
        c.address_match = address_matches(regions, c.address)
    for c in candidates:
        if c.name_match and c.address_match:
            return c, "name_address"
    for c in candidates:
        if c.name_match:
            return c, "name"
    return None, None


def search_page(http: HttpClient, query: str, page: int = 1) -> tuple[list[Candidate], bool]:
    """검색 결과 한 페이지 (후보 목록, 다음 페이지 있음). 연봉·인원 메뉴의 회사 검색도 이것을 쓴다."""
    params = {"q": query} if page == 1 else {"q": query, "page": page}
    return parse_search(http.get_text(SEARCH_URL, params=params))


def search(http: HttpClient, company: str, regions: list[Region]) -> tuple[list[Candidate], Candidate | None, str | None]:
    """검색 결과를 앞쪽부터 보며 회사를 고른다. 회사명·주소가 모두 같은 회사를 찾으면 거기서 멈춘다."""
    candidates: list[Candidate] = []
    query = search_query(company)
    for page in range(1, MAX_SEARCH_PAGES + 1):
        rows, has_next = search_page(http, query, page)
        candidates.extend(r for r in rows if r.id not in {c.id for c in candidates})
        chosen, match = choose(candidates, company, regions)
        if match == "name_address" or not has_next:
            break
    return candidates, chosen, match


def fetch_company(http: HttpClient, company_id: str) -> CompanyStats:
    return parse_company(http.get_text(COMPANY_URL.format(company_id)), company_id)


def lookup(http: HttpClient, company: str, location: str | None, raw: dict | None, company_id: str | None = None) -> dict:
    """공고 회사의 연봉·인원 정보. company_id를 주면 검색 없이 그 회사를 가져온다 (모달에서 다른 후보 선택)."""
    if company_id:
        return {"query": search_query(company), "match": "manual", "candidates": None,
                "company": asdict(fetch_company(http, company_id))}
    regions = posting_regions(location, raw)
    candidates, chosen, match = search(http, company, regions)
    return {
        "query": search_query(company),
        "match": match,
        "candidates": [asdict(c) for c in candidates],
        "company": asdict(fetch_company(http, chosen.id)) if chosen else None,
    }

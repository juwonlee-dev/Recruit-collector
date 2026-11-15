"""잡코리아 검색 결과 목록 크롤러.

잡코리아 검색 페이지는 Next.js로 렌더링되며, 화면에 그려지는 공고 데이터가
페이지에 포함된 React Query 상태(JSON)로 함께 내려온다. CSS 클래스(Tailwind)는
자주 바뀌므로 HTML 대신 이 JSON을 파싱한다.
  - JOB_LIST 쿼리: 공고 목록 + 페이지 정보
  - CODES 쿼리: 지역/경력/고용형태 코드 → 표시명 매핑
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from recruit_collector import career
from recruit_collector.crawlers.base import Condition, Crawler, JobOption, Page, ParseError, Posting, RegionOption, career_fields

SEARCH_URL = "https://www.jobkorea.co.kr/Search/"
DETAIL_URL = "https://www.jobkorea.co.kr/Recruit/GI_Read/{}"
# 검색 화면이 직무 선택지를 받아오는 주소 (전체 직무 분류 목록)
JOB_CATALOG_URL = "https://www.jobkorea.co.kr/Search/api/codes/jobClassification"
JOB_SCOPE = "10031"  # 직무 대분류 AI·개발·데이터. 필터 생성 시 이 아래 직무(duty)만 후보로 쓴다

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)
_STATE_RE = re.compile(r'\{"mutations":\[\],"queries":')

ALWAYS_OPEN_FROM_YEAR = "2050"
ALWAYS_OPEN_TEXT = "상시채용"
# 공고를 올린 회원 종류(userRefType). S = 서치펌 (사이트의 '헤드헌팅 공고만 보기' 결과가 모두 S)
HEADHUNTING_REF_TYPE = "S"



@dataclass
class JobkoreaCondition(Condition):
    """잡코리아 검색 조건.

    - locations: 지역 코드. 예) I000=서울 전체, B000=경기 전체 (페이지의 CODES.area 참고)
      잡코리아는 지역 조건을 URL이 아니라 `searchBarLocal` 쿠키로 받는다.
    - duties: 직무 소분류 코드 (duty). 예) 1000229=백엔드개발자
      대분류 코드(예: 10031=AI·개발·데이터)를 넣으면 결과가 0건이 되므로 소분류를 나열해야 한다.
      코드 목록: https://www.jobkorea.co.kr/Search/api/codes/jobClassification
    - career_type: 경력 (careerType). 1=신입, 2=경력, 4=경력무관
    - sort: 정렬 (ord). RegDtDesc=등록일순. 페이지 밀림을 줄이려면 등록일순 권장
    - params: 위에 없는 잡코리아 검색 파라미터를 그대로 전달
    (페이지 크기는 20건 고정, 사이트가 500페이지(10,000건)까지만 제공)
    """

    keyword: str = ""
    locations: list[str] = field(default_factory=list)
    duties: list[str] = field(default_factory=list)
    career_type: str | None = None
    sort: str = "RegDtDesc"
    params: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if len(self.keyword) > 30:
            raise ValueError("JobkoreaCondition.keyword는 30자 이하여야 함 (사이트가 더 긴 검색어를 무시함)")

    def to_params(self, page: int) -> dict[str, str]:
        params = {"stext": self.keyword, "tabType": "recruit", "Page_No": str(page), "ord": self.sort}
        if self.duties:
            params["duty"] = ",".join(map(str, self.duties))
        if self.career_type:
            params["careerType"] = str(self.career_type)
        params.update(self.params)
        return params

    def to_headers(self) -> dict[str, str]:
        return {"Cookie": f"searchBarLocal={','.join(self.locations)}"} if self.locations else {}


class JobkoreaCrawler(Crawler):
    site = "jobkorea"
    condition_class = JobkoreaCondition
    job_filter_field = "duties"
    region_filter_fields = ("locations",)

    def fetch_page(self, condition: JobkoreaCondition, page: int) -> Page:
        html = self.http.get_text(SEARCH_URL, params=condition.to_params(page), headers=condition.to_headers())
        return parse_list_page(html)

    def fetch_job_options(self) -> list[JobOption]:
        return parse_job_options(self.http.get_json(JOB_CATALOG_URL))

    def fetch_region_options(self) -> list[RegionOption]:
        return parse_region_options(self.http.get_text(SEARCH_URL, params={"tabType": "recruit"}))


def parse_region_options(html: str) -> list[RegionOption]:
    """검색 페이지 CODES.area: 부모 코드가 없으면 시도(예: B000=경기), 있으면 시군구(예: B240=안양시 동안구)."""
    area = (_extract_queries(html).get("CODES") or {}).get("area") or []
    if not area:
        raise ParseError("잡코리아: 지역 코드(CODES.area)를 찾을 수 없음")
    top = {c["code"]: c["displayName"] for c in area if not c.get("parentCode")}
    options = [RegionOption(code, name, "", "locations") for code, name in top.items()]
    options += [
        RegionOption(c["code"], top[c["parentCode"]], c["displayName"], "locations")
        for c in area
        if c.get("parentCode") in top and c["displayName"] != "전지역"  # 전지역(I000_ 등)은 시도 코드와 같은 뜻
    ]
    if not top:
        raise ParseError("잡코리아: 지역 코드에 시도가 없음")
    return options


def parse_job_options(codes: list[dict], scope: str = JOB_SCOPE) -> list[JobOption]:
    """대분류 scope 바로 아래 직무 선택지. 대분류 코드 자체로는 검색 결과가 나오지 않아 하위 코드를 쓴다."""
    if not isinstance(codes, list):
        raise ParseError("잡코리아: 직무 분류 응답이 목록이 아님")
    group = next((c["displayName"] for c in codes if c.get("code") == scope), "")
    options = [JobOption(c["code"], c["displayName"], group) for c in codes if c.get("parentCode") == scope]
    if not options:
        raise ParseError(f"잡코리아: 직무 대분류 {scope}의 선택지가 없음")
    return options


def parse_list_page(html: str) -> Page:
    queries = _extract_queries(html)
    job_list = queries.get("JOB_LIST")
    if job_list is None:
        raise ParseError("잡코리아: JOB_LIST 데이터를 찾을 수 없음")
    codes = queries.get("CODES") or {}

    # tagDisplayName 예: "경기>용인시 수지구" → "경기 용인시 수지구"
    area = {c["code"]: c["tagDisplayName"].replace(">", " ") for c in codes.get("area", [])}
    employment = {c["code"]: c["displayName"] for c in codes.get("employment", [])}

    postings = [_to_posting(item, area, employment) for item in job_list["content"]]
    is_last = job_list["pageNumber"] + 1 >= job_list["totalPages"]
    return Page(postings=postings, is_last=is_last, total=job_list["totalElements"])


def _extract_queries(html: str) -> dict[str, Any]:
    payload = "".join(json.loads(f'"{chunk}"') for chunk in _PUSH_RE.findall(html))
    decoder = json.JSONDecoder()
    found: dict[str, Any] = {}
    for m in _STATE_RE.finditer(payload):
        state, _ = decoder.raw_decode(payload, m.start())
        for query in state["queries"]:
            found.setdefault(query["queryKey"][0], query["state"]["data"])
    return found


def _to_posting(item: dict, area: dict, employment: dict) -> Posting:
    gid = str(item["id"])
    emp_codes = [c.split("/")[0] for c in item.get("employmentTypeCodeList") or []]
    period = item.get("applicationPeriod") or {}
    # 상시채용 공고는 마감일이 2070-01-01 같은 먼 미래 날짜로 내려온다
    always_open = (period.get("end") or "")[:4] >= ALWAYS_OPEN_FROM_YEAR
    raw = {k: v for k, v in item.items() if not k.startswith("_internal")}
    return Posting(
        external_id=gid,
        title=item["title"],
        company=item.get("postingCompanyName") or item.get("companyName") or "",
        detail_url=DETAIL_URL.format(gid),
        location=_join(area.get(c, c) for c in item.get("areaCodeList") or []),
        **_career(item.get("careerType"), item.get("careerRange")),
        education=None,  # educationCode 매핑표가 페이지에 없음 (raw에 원본 코드 보존)
        employment_type=_join(employment.get(c, c) for c in emp_codes),
        deadline=ALWAYS_OPEN_TEXT if always_open else ((period.get("end") or "")[:10] or None),
        deadline_at=None if always_open else _parse_dt(period.get("end"), as_date=True),
        posted_at=_parse_dt(item.get("createdAt")),
        headhunting=item.get("userRefType") == HEADHUNTING_REF_TYPE,
        raw=raw,
    )


def _career(career_type, career_range) -> dict[str, Any]:
    """잡코리아 경력 표기와 정규화 값.

    careerType: 1=신입, 2=경력, 3=신입·경력, 4=경력무관
    careerRange: 최소 경력 연수(N년↑). 0 또는 100은 연수 조건 없음
    표기는 잡코리아 화면의 표시 규칙과 동일하게 만든다.
    """
    t = str(career_type)
    years = career_range if isinstance(career_range, int) and 0 < career_range < 100 else None
    suffix = f"{years}년↑" if years else ""
    if t == "1":
        text, c = "신입", career.from_range(0, 0)
    elif t == "2":
        text, c = f"경력{suffix}", career.from_range(years or 1, None)
    elif t == "3":
        text, c = f"신입·경력{suffix}", career.from_range(0, None)
    else:
        text, c = "경력무관", career.ANY
    return {"career": text, **career_fields(c)}


def _parse_dt(value: str | None, as_date: bool = False) -> datetime | date | None:
    """'2026-11-05T11:31:43.87+09:00' → UTC(tz 없음) datetime, as_date면 현지(KST) 날짜."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if as_date:
        return dt.date()
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _join(values) -> str | None:
    return ", ".join(values) or None

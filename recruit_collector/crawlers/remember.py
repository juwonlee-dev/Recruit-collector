"""리멤버 커리어 채용공고 목록 크롤러.

리멤버 목록 페이지(/job/postings)의 HTML에는 공고가 없고, 화면이 내부 JSON 주소
(career-api.rememberapp.co.kr/job_postings/search)에 검색 조건을 POST해 목록을 그린다.
같은 주소를 화면과 같은 조건으로 요청한다. 페이지당 30건.

최신순(starts_at_desc)은 무작위 seed를 쓰는데, 첫 페이지 응답의 seed를 이후 페이지에도
그대로 보내야 페이지 순서가 유지된다 (화면도 같은 방식).
응답에는 공고 본문 전체가 들어 있지만 목록 정보만 적재하고 본문은 버린다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from recruit_collector import career
from recruit_collector.crawlers.base import Condition, Crawler, JobOption, Page, ParseError, Posting, RegionOption, career_fields

SEARCH_URL = "https://career-api.rememberapp.co.kr/job_postings/search"
DETAIL_URL = "https://career.rememberapp.co.kr/job/posting/{}"
# 화면이 검색 필터 선택지(직무·지역 등)를 받아오는 주소. 지역 선택지도 여기서 받는다
JOB_CATALOG_URL = "https://open-profile-api.rememberapp.co.kr/v2/open_profiles/options"
JOB_SCOPE = "SW개발"  # 직무 대분류. 필터 생성 시 이 아래 직무만 후보로 쓴다
PAGE_SIZE = 30
_KST = timezone(timedelta(hours=9))

EDUCATION = {
    "none": "학력무관",
    "high_school": "고졸↑",
    "associate": "초대졸↑",
    "bachelor": "대졸↑",
    "master": "석사↑",
    "doctor": "박사↑",
    "doctorate": "박사↑",
}
# 목록 적재 시 버리는 본문성 필드 (상세 수집 시 별도로 다룰 내용)
HEADHUNTING_TYPE = "internal_headhunter"  # job_posting_type: 리멤버 헤드헌터가 올린 공고 (그 밖에 general, external)
HEAVY_FIELDS = {
    "job_description", "qualifications", "preferred_qualifications", "introduction",
    "recruiting_process", "additional_information", "company_description", "company",
    "application_requirements", "lead_times",
}


@dataclass
class RememberCondition(Condition):
    """리멤버 검색 조건.

    - job_categories: [대분류, 소분류] 목록 (여러 개면 OR). 예) ["SW개발", "백엔드"]
      공고 데이터의 job_categories(level1, level2) 이름과 같다.
    - addresses: 지역 목록 (여러 개면 OR). 예) ["서울특별시"], ["경기도"], ["서울특별시", "강남구"]
    - keywords: 검색어 목록
    - sort: starts_at_desc=최신순
    """

    job_categories: list[list[str]] = field(default_factory=list)
    addresses: list[list[str]] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    sort: str = "starts_at_desc"

    def to_body(self, page: int, seed: int | None) -> dict[str, Any]:
        search: dict[str, Any] = {"include_applied_job_posting": False}
        if self.job_categories:
            search["job_category_names"] = [{"level1": a, "level2": b} for a, b in self.job_categories]
        if self.addresses:
            search["addresses"] = self.addresses
        if self.keywords:
            search["keywords"] = self.keywords
        body: dict[str, Any] = {"search": search, "sort": self.sort, "page": page, "per": PAGE_SIZE}
        if seed is not None:
            body["seed"] = seed
        return body


class RememberCrawler(Crawler):
    site = "remember"
    condition_class = RememberCondition
    job_filter_field = "job_categories"
    region_filter_fields = ("addresses",)

    def fetch_job_options(self) -> list[JobOption]:
        return parse_job_options(self.http.get_json(JOB_CATALOG_URL, params={"version": 2}))

    def fetch_region_options(self) -> list[RegionOption]:
        return parse_region_options(self.http.get_json(JOB_CATALOG_URL, params={"version": 2}))

    def __init__(self, http):
        super().__init__(http)
        self._seed: int | None = None

    def fetch_page(self, condition: RememberCondition, page: int) -> Page:
        if page == 1:
            self._seed = None
        data = self.http.post_json(SEARCH_URL, condition.to_body(page, self._seed))
        meta = data.get("meta") or {}
        if page == 1 and isinstance(meta.get("seed"), int):
            self._seed = meta["seed"]
        return parse_list(data)


def parse_job_options(data: dict, scope: str = JOB_SCOPE) -> list[JobOption]:
    """직무 대분류 scope 아래 직무 선택지. 검색 조건에는 [대분류, 직무] 이름 쌍으로 들어간다."""
    groups = ((data or {}).get("data") or {}).get("job_categories")
    if not isinstance(groups, list):
        raise ParseError("리멤버: 직무 선택지(job_categories)가 없음")
    group = next((g for g in groups if g.get("name") == scope), None)
    if not group or not group.get("subs"):
        raise ParseError(f"리멤버: 직무 대분류 {scope}의 선택지가 없음")
    return [JobOption([scope, sub["name"]], sub["name"], scope) for sub in group["subs"]]


def parse_list(data: dict) -> Page:
    items, meta = data.get("data"), data.get("meta") or {}
    if not isinstance(items, list) or "total_pages" not in meta:
        raise ParseError("리멤버: 응답에 data 목록 또는 페이지 정보가 없음")
    postings = [_to_posting(item) for item in items]
    is_last = meta.get("page", 1) >= meta["total_pages"] or not items
    return Page(postings=postings, is_last=is_last, total=meta.get("total_count"))


def _to_posting(item: dict) -> Posting:
    rid = str(item["id"])
    c = _career(item.get("min_experience"), item.get("max_experience"))
    ends_at = _parse_dt(item.get("ends_at"))
    edu = item.get("education_requirement")
    return Posting(
        external_id=rid,
        title=item.get("title") or "",
        company=(item.get("organization") or {}).get("name") or "",
        detail_url=DETAIL_URL.format(rid),
        location=_location(item),
        career=career.to_text(c),
        **career_fields(c),
        education=EDUCATION.get(edu, edu) or None,
        deadline=f"{ends_at.astimezone(_KST):%Y-%m-%d}" if ends_at else "상시채용",
        deadline_at=ends_at.astimezone(_KST).date() if ends_at else None,
        posted_at=_to_utc(_parse_dt(item.get("starts_at"))),
        headhunting=item.get("job_posting_type") == HEADHUNTING_TYPE,
        raw={k: v for k, v in item.items() if k not in HEAVY_FIELDS},
    )


def _career(min_exp: int | None, max_exp: int | None) -> career.Career:
    """min~max_experience. 둘 다 없으면 경력무관 (리멤버의 '경력 무관' 필터 결과가 정확히 이 공고들).

    최소값만 없으면(~N년 이하) 하한이 없으므로 신입(0년차)부터로 본다.
    (예: '소프트웨어 개발 신입-경력 3년 이하' 공고가 min=None, max=3)
    """
    if min_exp is None and max_exp is None:
        return career.ANY
    return career.from_range(min_exp if min_exp is not None else 0, max_exp)


def _location(item: dict) -> str | None:
    """대표 지역(normalized_address) + 나머지 근무지 수. 예) '서울 광진구 외 1'"""
    norm = item.get("normalized_address") or {}
    first = " ".join(x for x in (norm.get("level1"), norm.get("level2")) if x)
    others = max(0, len(item.get("addresses") or []) - 1)
    if not first:
        return None
    return f"{first} 외 {others}" if others else first


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_KST)


def _to_utc(dt: datetime | None) -> datetime | None:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt else None


def parse_region_options(data: dict) -> list[RegionOption]:
    """addresses: [{name: 서울특별시, subs: [{name: 강남구}, ...]}]. 검색 조건 값은 [시도] 또는 [시도, 시군구]."""
    try:
        addresses = data["data"]["addresses"]
    except (KeyError, TypeError) as e:
        raise ParseError(f"리멤버: 지역 선택지가 없음 ({e!r})") from e
    options = []
    for a in addresses:
        options.append(RegionOption([a["name"]], a["name"], "", "addresses"))
        options += [
            RegionOption([a["name"], s["name"]], a["name"], s["name"], "addresses")
            for s in a.get("subs") or []
            if s["name"] != a["name"]  # 세종특별자치시 > 세종특별자치시
        ]
    return options

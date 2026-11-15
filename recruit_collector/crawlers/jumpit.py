"""점핏(사람인의 개발자 채용 서비스) 채용공고 목록 크롤러.

점핏 목록 화면(/positions)의 HTML에는 공고가 없고, 화면이 내부 JSON 주소
(jumpit-api.saramin.co.kr/api/positions)를 호출해 목록을 그린다. 같은 주소를 화면과 같은
조건으로 요청한다. 페이지당 16건이며, 전체 건수(totalCount)를 넘는 페이지는 빈 목록이 온다.

직무·지역 선택지는 화면이 처음에 부르는 /api/common/code-initialize 에서 받는다.
지역 코드는 사람인과 같은 체계다 (101000=서울 전체, 101010=서울 강남구).
목록 데이터에는 등록일과 고용형태가 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from recruit_collector import career
from recruit_collector.crawlers.base import Condition, Crawler, JobOption, Page, ParseError, Posting, RegionOption, career_fields

LIST_URL = "https://jumpit-api.saramin.co.kr/api/positions"
CODES_URL = "https://jumpit-api.saramin.co.kr/api/common/code-initialize"
DETAIL_URL = "https://jumpit.saramin.co.kr/position/{}"
PAGE_SIZE = 16  # 사이트 고정값 (요청으로 바꿀 수 없음)
ALWAYS_OPEN_TEXT = "상시채용"


@dataclass
class JumpitCondition(Condition):
    """점핏 검색 조건.

    - job_categories: 직무 (jobCategory, 여러 개면 OR). 예) 1=서버/백엔드 개발자, 3=웹 풀스택 개발자
    - location_tags: 지역 (locationTag, 여러 개면 OR). 시도 전체(101000=서울)와 시군구(101010=서울 강남구) 코드
    - sort: 정렬. reg_dt=최신순, popular=인기순
    """

    job_categories: list[int] = field(default_factory=list)
    location_tags: list[int] = field(default_factory=list)
    sort: str = "reg_dt"
    params: dict[str, Any] = field(default_factory=dict)

    def to_params(self, page: int) -> dict[str, Any]:
        params: dict[str, Any] = {"sort": self.sort, "highlight": "false", "page": page}
        # 화면과 같이 같은 키를 여러 번 보낸다 (jobCategory=1&jobCategory=3)
        if self.job_categories:
            params["jobCategory"] = self.job_categories
        if self.location_tags:
            params["locationTag"] = self.location_tags
        params.update(self.params)
        return params


class JumpitCrawler(Crawler):
    site = "jumpit"
    condition_class = JumpitCondition
    job_filter_field = "job_categories"
    region_filter_fields = ("location_tags",)

    def fetch_page(self, condition: JumpitCondition, page: int) -> Page:
        return parse_list(self.http.get_json(LIST_URL, params=condition.to_params(page)), page)

    def fetch_job_options(self) -> list[JobOption]:
        return parse_job_options(self.http.get_json(CODES_URL))

    def fetch_region_options(self) -> list[RegionOption]:
        return parse_region_options(self.http.get_json(CODES_URL))


def _codes(data: dict, key: str) -> list[dict]:
    items = (data.get("result") or {}).get(key)
    if not isinstance(items, list) or not items:
        raise ParseError(f"점핏: 선택지({key})가 없음")
    return items


def parse_job_options(data: dict) -> list[JobOption]:
    """점핏은 개발 직무만 있으므로 모든 직무가 후보다."""
    return [JobOption(int(j["id"]), j["name"], "개발") for j in _codes(data, "jobCategory")]


def parse_region_options(data: dict) -> list[RegionOption]:
    """시도마다 시도 전체(시도와 같은 코드, 예: 101000 서울전체)와 시군구. 전국·기타해외는 뺀다."""
    options = []
    for sido in _codes(data, "locationTag"):
        for c in sido.get("children") or []:
            whole = c["code"] == sido["code"]
            options.append(RegionOption(int(c["code"]), sido["name"], "" if whole else c["name"], "location_tags"))
    return options


def parse_list(data: dict, page: int) -> Page:
    result = data.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("positions"), list):
        raise ParseError("점핏: 응답에 positions 목록이 없음")
    postings = [_to_posting(item) for item in result["positions"]]
    total = result.get("totalCount")
    # 전체 건수를 넘는 페이지는 totalCount=0으로 오므로 이 페이지까지의 건수로 판단한다
    is_last = len(postings) < PAGE_SIZE or not total or page * PAGE_SIZE >= total
    return Page(postings=postings, is_last=is_last, total=total or None)


def _to_posting(item: dict) -> Posting:
    pid = str(item["id"])
    c = _career(item.get("minCareer"), item.get("maxCareer"), item.get("newcomer"))
    closed = _date(item.get("closedAt"))
    always_open = bool(item.get("alwaysOpen")) or closed is None
    return Posting(
        external_id=pid,
        title=item.get("title") or "",
        company=item.get("companyName") or "",
        detail_url=DETAIL_URL.format(pid),
        location=", ".join(item.get("locations") or []) or None,
        career=career.to_text(c),
        **career_fields(c),
        deadline=ALWAYS_OPEN_TEXT if always_open else closed.isoformat(),
        deadline_at=None if always_open else closed,
        headhunting=False,  # 점핏은 기업이 직접 올리는 공고만 있어 모두 일반 공고로 둔다
        raw=item,
    )


def _career(min_years: int | None, max_years: int | None, newcomer: bool | None) -> career.Career:
    """minCareer~maxCareer(년). newcomer면 신입도 지원 가능."""
    if min_years is None:
        return career.Career(0, 0) if newcomer else career.UNKNOWN
    return career.from_range(0 if newcomer else min_years, max_years)


def _date(value: str | None) -> date | None:
    """'2026-12-13T23:59:59'(한국 시각) → 날짜."""
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None

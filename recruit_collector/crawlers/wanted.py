"""원티드 채용공고 목록 크롤러.

원티드 목록 페이지(/wdlist)의 HTML에는 공고가 없고, 화면이 내부 JSON 주소
(/api/chaos/navigation/v1/results)를 호출해 목록을 그린다. 같은 주소를 화면과 같은
조건으로 요청한다. 페이지당 20건이며 다음 페이지 주소는 응답의 links.next로 알 수 있다.

목록 데이터에는 등록일과 마감일이 없다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from recruit_collector import career
from recruit_collector.crawlers.base import Condition, Crawler, JobOption, Page, ParseError, Posting, RegionOption, career_fields

LIST_URL = "https://www.wanted.co.kr/api/chaos/navigation/v1/results"
DETAIL_URL = "https://www.wanted.co.kr/wd/{}"
# 직군 목록 화면. 직군·직무 선택지가 페이지의 __NEXT_DATA__(JSON)로 내려온다.
JOB_CATALOG_URL = "https://www.wanted.co.kr/wdlist/{}"
JOB_SCOPE = 518  # 직군 개발. 필터 생성 시 이 직군의 직무(job_ids)만 후보로 쓴다
PAGE_SIZE = 20  # 사이트 최대값 (더 크게 주면 빈 응답)
NO_UPPER = 100  # annual_to가 이 값이면 상한 없음

EMPLOYMENT_TYPES = {"regular": "정규직", "contract": "계약직", "intern": "인턴", "freelance": "프리랜서"}


@dataclass
class WantedCondition(Condition):
    """원티드 검색 조건.

    - job_group_id: 직군. 518=개발
    - job_ids: 직군 아래 직무 (여러 개면 OR). 예) 872=서버 개발자, 873=웹 개발자, 660=자바 개발자
      목록 페이지(/wdlist/518) HTML의 __NEXT_DATA__.props.pageProps.tags 에서 확인할 수 있다.
    - locations: 지역. 예) seoul.all, gyeonggi.all
    - years: 경력 연차. -1=전체
    - sort: 정렬. job.latest_order=최신순
    """

    job_group_id: int = 518
    job_ids: list[int] = field(default_factory=list)
    locations: list[str] = field(default_factory=lambda: ["all"])
    years: int = -1
    sort: str = "job.latest_order"
    params: dict[str, Any] = field(default_factory=dict)

    def to_params(self, page: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "job_group_id": self.job_group_id,
            "country": "kr",
            "job_sort": self.sort,
            "years": self.years,
            "locations": self.locations,
            "limit": PAGE_SIZE,
            "offset": (page - 1) * PAGE_SIZE,
        }
        if self.job_ids:
            params["job_ids"] = self.job_ids  # 같은 키를 여러 번 보냄 (쉼표로 합치면 422)
        params.update(self.params)
        return params


class WantedCrawler(Crawler):
    site = "wanted"
    condition_class = WantedCondition
    job_filter_field = "job_ids"
    region_filter_fields = ("locations",)

    def fetch_page(self, condition: WantedCondition, page: int) -> Page:
        return parse_list(self.http.get_json(LIST_URL, params=condition.to_params(page)))

    def fetch_job_options(self) -> list[JobOption]:
        return parse_job_options(self.http.get_text(JOB_CATALOG_URL.format(JOB_SCOPE)))

    def fetch_region_options(self) -> list[RegionOption]:
        return parse_region_options(self.http.get_text(JOB_CATALOG_URL.format(JOB_SCOPE)))


_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def parse_region_options(html: str) -> list[RegionOption]:
    """국내 지역: 시도마다 '<시도>.all'(시도 전체)과 시군구 키(예: gyeonggi.anyang-si)."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise ParseError("원티드: __NEXT_DATA__를 찾을 수 없음")
    try:
        countries = json.loads(m.group(1))["props"]["pageProps"]["filterData"]["countries"]
        locations = next(c for c in countries if c.get("key") == "kr")["locations"]
    except (KeyError, TypeError, StopIteration) as e:
        raise ParseError(f"원티드: 지역 목록이 없음 ({e!r})") from e
    return [
        RegionOption(d["key"], loc["display"], "" if d["key"].endswith(".all") else d["display"], "locations")
        for loc in locations
        for d in loc.get("districts") or []
    ]


def parse_job_options(html: str, group_id: int = JOB_SCOPE) -> list[JobOption]:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise ParseError("원티드: __NEXT_DATA__를 찾을 수 없음")
    try:
        groups = json.loads(m.group(1))["props"]["pageProps"]["tags"]["category"]
    except (KeyError, TypeError) as e:
        raise ParseError(f"원티드: 직군 목록이 없음 ({e})") from e
    group = next((g for g in groups if g.get("id") == group_id), None)
    if not group or not group.get("tags"):
        raise ParseError(f"원티드: 직군 {group_id}의 직무 선택지가 없음")
    return [JobOption(int(t["id"]), t["title"], group.get("title", "")) for t in group["tags"]]


def parse_list(data: dict) -> Page:
    items = data.get("data")
    if not isinstance(items, list):
        raise ParseError("원티드: 응답에 data 목록이 없음")
    postings = [_to_posting(item) for item in items]
    # 화면과 같이 links.next 유무로만 다음 페이지를 판단
    has_next = bool((data.get("links") or {}).get("next"))
    return Page(postings=postings, is_last=not has_next)


def _to_posting(item: dict) -> Posting:
    wid = str(item["id"])
    address = item.get("address") or {}
    location = " ".join(x for x in (address.get("location"), address.get("district")) if x) or None
    c = _career(item.get("annual_from"), item.get("annual_to"), item.get("is_newbie"))
    emp = item.get("employment_type")
    return Posting(
        external_id=wid,
        title=item.get("position") or "",
        company=(item.get("company") or {}).get("name") or "",
        detail_url=DETAIL_URL.format(wid),
        location=location,
        career=career.to_text(c),
        **career_fields(c),
        employment_type=EMPLOYMENT_TYPES.get(emp, emp),
        headhunting=False,  # 원티드는 헤드헌팅 공고 구분이 없어 모두 일반 공고로 둔다
        raw=item,
    )


def _career(annual_from: int | None, annual_to: int | None, is_newbie: bool | None) -> career.Career:
    """annual_from~annual_to(100=상한 없음). is_newbie면 신입도 지원 가능.

    원티드 목록에는 '경력무관'을 따로 표시하는 값이 없으므로 범위로만 표현한다.
    """
    if annual_from is None:
        return career.UNKNOWN
    hi = None if annual_to is None or annual_to >= NO_UPPER else annual_to
    lo = 0 if is_newbie else annual_from
    return career.from_range(lo, hi)

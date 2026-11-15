"""모든 사이트 크롤러가 따르는 공통 인터페이스.

새 사이트를 추가하려면:
  1. 이 모듈의 `Crawler`를 상속한 클래스를 만들고 `site`, `condition_class`, `fetch_page`를 구현
  2. `crawlers/__init__.py`의 `CRAWLERS`에 등록
"""

from __future__ import annotations

import dataclasses
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, ClassVar

from recruit_collector.career import Career
from recruit_collector.http import HttpClient

log = logging.getLogger(__name__)


class ParseError(Exception):
    """페이지 구조가 예상과 달라 파싱할 수 없음 (사이트 개편 가능성)."""


def career_fields(c: Career) -> dict[str, Any]:
    """Posting 생성 시 경력 정규화 값을 펼쳐 넣기 위한 헬퍼."""
    return {"career_min": c.min_years, "career_max": c.max_years, "career_any": c.any}


@dataclass
class Posting:
    """사이트 공통 형태로 정규화한 목록 페이지의 공고 1건."""

    external_id: str  # 사이트 내 공고 고유 ID
    title: str
    company: str
    detail_url: str  # 상세 공고 링크 (추후 상세 수집 시 사용)
    location: str | None = None
    career: str | None = None
    education: str | None = None
    employment_type: str | None = None
    # 경력 정규화 (recruit_collector.career 참고): 연차는 0부터, 신입 = 0년차
    career_min: int | None = None  # 최소 연차. None이면 알 수 없음
    career_max: int | None = None  # 최대 연차. None이면 상한 없음
    career_any: bool = False  # 경력무관으로 명시된 공고
    deadline: str | None = None  # 사이트 표기 그대로 (예: "~ 11/15(일)", "채용시")
    deadline_at: date | None = None  # 마감일 (정렬용). 상시채용 등 마감일이 없으면 None
    posted_at: datetime | None = None  # 사이트에 공고가 등록된 시각 (UTC, tz 없음)
    headhunting: bool | None = None  # 헤드헌팅(서치펌) 공고인지. None이면 알 수 없음
    collected_at: datetime | None = None  # 이 공고가 담긴 목록 페이지를 가져온 시각 (UTC, tz 없음). crawl()이 채움
    raw: dict[str, Any] = field(default_factory=dict)  # 사이트 원본 데이터 (정규화하지 않은 필드 보존)


@dataclass
class Page:
    postings: list[Posting]
    is_last: bool
    total: int | None = None  # 사이트가 표시하는 전체 결과 수 (알 수 있으면)


@dataclass
class CrawlResult:
    postings: list[Posting]
    pages: int
    complete: bool  # 마지막 페이지까지 오류 없이 수집했는지. False면 삭제 판정을 하지 않는다.
    error: str | None = None


class Condition:
    """사이트별 검색 조건의 기반 클래스. 하위 클래스는 dataclass로 선언한다.

    설정 파일에 알 수 없는 키가 있으면 오타일 가능성이 높으므로 에러를 낸다.
    """

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        names = {f.name for f in dataclasses.fields(cls)}
        unknown = set(data) - names
        if unknown:
            raise ValueError(f"{cls.__name__}: 알 수 없는 조건 키 {sorted(unknown)} (사용 가능: {sorted(names)})")
        return cls(**data)


def totals_consistent(totals: set[int]) -> bool:
    """수집 중 페이지마다 보고된 전체 건수가 '같은 결과 집합'으로 볼 만큼 가까운지.

    긴 수집(수십 분) 동안 공고 등록·마감으로 건수가 조금씩 변하는 것은 정상이다.
    사람인 세션 쿠키 문제처럼 다른 결과 집합을 받는 경우(119 → 196)만 걸러낸다.
    """
    if len(totals) <= 1:
        return True
    hi, lo = max(totals), min(totals)
    return hi - lo <= max(20, hi * 0.03)


@dataclass
class JobOption:
    """사이트 검색 화면의 직무 필터 선택지 하나 (키워드로 필터를 만들 때 쓰는 매핑 데이터의 원본).

    value는 검색 조건(Condition)의 직무 필드에 그대로 들어가는 값이다.
    """

    value: Any
    name: str
    group: str = ""


@dataclass
class RegionOption:
    """사이트 검색 화면의 지역 필터 선택지 하나 (공통 지역코드와 매핑할 때 쓰는 데이터의 원본).

    value는 검색 조건(Condition)의 지역 필드(field)에 그대로 들어가는 값이다.
    sido는 사이트가 쓰는 시도 이름, name은 시군구 이름이며 빈 문자열이면 그 시도 전체를 뜻한다.
    """

    value: Any
    sido: str
    name: str
    field: str


class Crawler(ABC):
    site: ClassVar[str]  # 크롤러 이름 (검색 필터 파일 config/searches/<site>.yaml, 수집 기록의 사이트)
    # 공고를 저장할 플랫폼(job_postings.site). 비우면 site와 같다. 같은 채용 사이트를 나눠 수집하는 크롤러
    # (예: 사람인(헤드헌팅) saramin_hh → saramin)가 공고를 같은 플랫폼으로 모으는 데 쓴다
    platform: ClassVar[str] = ""
    # 이 크롤러가 늘 같은 값으로 쓰는 검색 조건 (예: 사람인의 기업 형태). 설정의 '필터 생성'이 검색 필터 파일에 이 값을
    # 다시 써 넣고, 수집할 때도 파일 값 대신 이 값을 쓴다. fixed_condition_label은 설정 화면에 보일 설명
    fixed_condition: ClassVar[dict[str, Any]] = {}
    fixed_condition_label: ClassVar[str] = ""
    condition_class: ClassVar[type[Condition]]
    # 키워드로 직무 필터를 만들 때 값을 넣을 검색 조건 필드. None이면 필터 생성을 지원하지 않는다.
    job_filter_field: ClassVar[str | None] = None
    # 등록한 지역으로 지역 필터를 만들 때 값을 넣을 검색 조건 필드. 비어 있으면 지원하지 않는다.
    region_filter_fields: ClassVar[tuple[str, ...]] = ()

    def __init__(self, http: HttpClient):
        self.http = http

    def fetch_job_options(self) -> list[JobOption]:
        """사이트의 직무 필터 선택지를 가져온다 (IT·개발 분류 안의 직무만)."""
        raise NotImplementedError

    def fetch_region_options(self) -> list[RegionOption]:
        """사이트의 지역 필터 선택지(시도 전체와 시군구)를 가져온다."""
        raise NotImplementedError

    @abstractmethod
    def fetch_page(self, condition: Condition, page: int) -> Page:
        """1부터 시작하는 page 번호의 목록 페이지를 가져와 파싱한다."""

    def postprocess(self, postings: list[Posting]) -> None:
        """수집이 끝난 뒤 공고 목록 전체를 보고 보정할 것이 있으면 사이트별로 구현한다."""

    def crawl(
        self,
        condition: Condition,
        max_pages: int,
        on_page: Callable[[int, int, int | None], None] | None = None,
    ) -> CrawlResult:
        """마지막 페이지(또는 max_pages)까지 순회한다.

        on_page(지금까지 페이지 수, 지금까지 수집한 공고 수, 사이트가 보고한 전체 건수)는
        페이지마다 호출되어 진행 상황을 기록하는 데 쓴다.
        """
        result = self.collect_pages(condition, max_pages, on_page)
        self.postprocess(result.postings)
        return result

    def collect_pages(
        self,
        condition: Condition,
        max_pages: int,
        on_page: Callable[[int, int, int | None], None] | None = None,
    ) -> CrawlResult:
        """crawl()의 페이지 순회 부분 (postprocess 전). 검색을 나눠 받는 사이트가 결과를 합칠 때 쓴다."""
        collected: dict[str, Posting] = {}  # 페이지 밀림으로 인한 중복 제거
        totals: set[int] = set()
        page_no = 0
        complete, error = False, None
        try:
            while page_no < max_pages:
                page_no += 1
                page = self.fetch_page(condition, page_no)
                fetched_at = _utcnow()
                for p in page.postings:
                    p.collected_at = fetched_at
                    collected[p.external_id] = p
                if page.total is not None:
                    totals.add(page.total)
                if on_page:
                    on_page(page_no, len(collected), page.total if page.total is not None else (max(totals) if totals else None))
                if page.is_last:
                    if totals_consistent(totals):
                        complete = True
                    else:
                        # 수집 도중 결과 집합이 크게 바뀜 → 다른 결과를 본 것일 수 있으므로 삭제 판정을 하지 않는다
                        log.warning("[%s] 페이지마다 전체 건수가 크게 다름 %s - 이번 실행은 삭제 판정을 건너뜀", self.site, sorted(totals))
                        error = f"전체 건수 불일치 {sorted(totals)}"
                    break
            else:
                log.warning("[%s] max_pages(%d)에 도달해 수집을 중단함 - 이번 실행은 삭제 판정을 건너뜀", self.site, max_pages)
        except Exception as e:
            log.exception("[%s] %d페이지 수집 중 오류", self.site, page_no)
            error = f"{type(e).__name__}: {e}"

        return CrawlResult(list(collected.values()), page_no, complete=complete, error=error)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

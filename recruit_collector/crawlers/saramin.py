"""사람인 검색 결과 목록 크롤러 (서버 렌더링 HTML 파싱)."""

from __future__ import annotations

import dataclasses
import json
import re
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import ClassVar

from bs4 import BeautifulSoup, Tag

from recruit_collector import career
from recruit_collector.crawlers.base import Condition, Crawler, JobOption, Page, ParseError, Posting, RegionOption, career_fields

BASE_URL = "https://www.saramin.co.kr"
SEARCH_URL = BASE_URL + "/zf_user/search/recruit"
DETAIL_URL = BASE_URL + "/zf_user/jobs/relay/view?rec_idx={}"
# 직무별 공고 화면. 직무 선택지 전체가 페이지 안의 JS 옵션(JSON)으로 내려온다.
JOB_CATALOG_URL = BASE_URL + "/zf_user/jobs/list/job-category"
JOB_SCOPE = "2"  # 직무 대분류(cat_mcls) IT개발·데이터. 필터 생성 시 이 분류의 직무 키워드만 후보로 쓴다
KST = timezone(timedelta(hours=9))
# 기업 형태(company_cd) 검색 조건. 목록 HTML에는 헤드헌팅 공고 표시가 없어서 크롤러를 나눈다:
# 사람인(saramin)은 헤드헌팅을 뺀 일반·파견, 사람인(헤드헌팅)(saramin_hh)은 헤드헌팅만 검색한다
# (두 검색의 전체 건수 합 = 기업 형태 조건 없이 검색한 전체 건수)
COMPANY_CD_GENERAL = ["0", "1", "2", "3", "4", "5", "6", "7", "9"]  # 일반 채용정보(0~7) + 파견/대행(9)
COMPANY_CD_HEADHUNTING = ["10"]


@dataclass
class SaraminCondition(Condition):
    """사람인 검색 조건. 코드 값은 사람인 검색 화면 URL에서 확인할 수 있다.

    - locations: 지역 코드 (loc_mcd). 예) 101000=서울 전체, 102000=경기 전체
    - districts: 시군구 코드 (loc_cd). 예) 102320=경기 안양시. locations와 함께 쓰면 OR 조건
    - job_categories: 직무 대분류 코드 (cat_mcls). 예) 2=IT개발·데이터
    - job_keywords: 직무 키워드 코드 (cat_kewd). 여러 개 지정 시 OR. 예) 84=백엔드/서버개발, 87=웹개발
      공고에 붙는 직무 태그 링크(/zf_user/jobs/list/job-category?cat_kewd=...)에서 코드를 확인할 수 있다.
    - careers: 경력 코드 (exp_cd). 예) 1=신입, 2=경력
    - job_types: 고용형태 코드 (job_type). 예) 1=정규직
    - company_types: 기업 형태 코드 (company_cd). 0~7=일반, 9=파견/대행, 10=헤드헌팅.
      사람인·사람인(헤드헌팅) 크롤러가 고정값(fixed_condition)으로 쓰므로 파일에서 바꿔도 수집에는 쓰이지 않는다
    - sort: 정렬 (recruitSort). reg_dt=등록일순. 페이지 밀림을 줄이려면 등록일순 권장
    - params: 위에 없는 사람인 검색 파라미터를 그대로 전달
    """

    keyword: str = ""
    locations: list[str] = field(default_factory=list)
    districts: list[str] = field(default_factory=list)
    job_categories: list[str] = field(default_factory=list)
    job_keywords: list[str] = field(default_factory=list)
    careers: list[str] = field(default_factory=list)
    job_types: list[str] = field(default_factory=list)
    company_types: list[str] = field(default_factory=list)
    sort: str = "reg_dt"
    page_size: int = 100
    params: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        # 마지막 페이지 판정이 "받은 건수 < page_size"에 의존하므로, 사이트가 실제로 지원하는 범위(최대 100)만 허용
        if not 1 <= self.page_size <= 100:
            raise ValueError("SaraminCondition.page_size는 1~100 이어야 함")

    def to_params(self, page: int) -> dict[str, str]:
        params = {
            "searchword": self.keyword,
            "recruitSort": self.sort,
            "recruitPage": str(page),
            "recruitPageCount": str(self.page_size),
        }
        for key, values in (
            ("loc_mcd", self.locations),
            ("loc_cd", self.districts),
            ("cat_mcls", self.job_categories),
            ("cat_kewd", self.job_keywords),
            ("exp_cd", self.careers),
            ("job_type", self.job_types),
            ("company_cd", self.company_types),
        ):
            if values:
                params[key] = ",".join(map(str, values))
        params.update(self.params)
        return params


class SaraminCrawler(Crawler):
    site = "saramin"
    condition_class = SaraminCondition
    job_filter_field = "job_keywords"
    region_filter_fields = ("locations", "districts")

    fixed_condition = {"company_types": COMPANY_CD_GENERAL}
    fixed_condition_label = "헤드헌팅 제외 (일반·파견/대행)"
    headhunting: ClassVar[bool] = False

    def fetch_page(self, condition: SaraminCondition, page: int) -> Page:
        # 기업 형태는 크롤러의 고정값으로 (파일 값이나 params의 company_cd는 쓰지 않는다)
        condition = dataclasses.replace(condition, **self.fixed_condition,
                                        params={k: v for k, v in condition.params.items() if k != "company_cd"})
        html = self.http.get_text(SEARCH_URL, params=condition.to_params(page))
        result = parse_list_page(html, condition.page_size)
        for p in result.postings:
            p.headhunting = self.headhunting
        return result

    def fetch_job_options(self) -> list[JobOption]:
        return parse_job_options(self.http.get_text(JOB_CATALOG_URL, params={"cat_mcls": JOB_SCOPE}))

    def fetch_region_options(self) -> list[RegionOption]:
        return parse_region_options(self.http.get_text(SEARCH_URL))

    def postprocess(self, postings: list[Posting]) -> None:
        infer_posted_at(postings)


class SaraminHeadhuntingCrawler(SaraminCrawler):
    """사람인(헤드헌팅) 공고만 검색한다 (검색 필터 파일 config/searches/saramin_hh.yaml).

    검색 조건은 사람인과 같고, 공고는 플랫폼 '사람인'(job_postings.site = saramin)으로 저장된다.
    """

    site = "saramin_hh"
    platform = "saramin"
    fixed_condition = {"company_types": COMPANY_CD_HEADHUNTING}
    fixed_condition_label = "헤드헌팅만"
    headhunting = True


def infer_posted_at(postings: list[Posting]) -> None:
    """목록에 '수정일'만 표시된 공고의 등록일을 추정한다.

    사람인 공고 ID(rec_idx)는 등록 순서대로 증가한다(등록일순 목록에서 ID와 등록일이 함께 감소함을 확인).
    따라서 ID가 바로 아래인 '등록일' 공고의 날짜가 이 공고 등록일의 하한이다. 그 날짜를 쓰고 raw에 추정임을 표시한다.
    """
    known = sorted((int(p.external_id), p.posted_at) for p in postings if p.posted_at)
    ids = [k[0] for k in known]
    for p in postings:
        if p.posted_at is None:
            i = bisect_left(ids, int(p.external_id)) - 1
            if i >= 0:
                p.posted_at = known[i][1]
                p.raw["posted_at_inferred"] = True


_OPTIONS_RE = re.compile(r"'options'\s*:\s*'(\{.*\})'")


def parse_job_options(html: str, scope: str = JOB_SCOPE) -> list[JobOption]:
    """직무 대분류 scope 안의 직무 키워드(cat_kewd) 선택지. 같은 키워드가 여러 소분류에 있으면 한 번만."""
    m = _OPTIONS_RE.search(html)
    if not m:
        raise ParseError("사람인: 직무 선택지(options)를 찾을 수 없음")
    options: dict[str, JobOption] = {}
    for mcls in json.loads(m.group(1)).get("job_category") or []:
        if str(mcls.get("MCLS_CD_NO")) != scope:
            continue
        for small in mcls.get("SMALL_LIST") or []:
            for kw in small.get("KEYWORD_LIST") or []:
                code = str(kw["KEWD_CD_NO"])
                options.setdefault(code, JobOption(code, kw["KEWD_CD_NM"], small.get("SCLS_CD_NM", "")))
    if not options:
        raise ParseError(f"사람인: 직무 대분류 {scope}의 선택지가 없음")
    return list(options.values())


_AREA_RE = re.compile(r'"area":\{"\d+":\[')


def parse_region_options(html: str) -> list[RegionOption]:
    """검색 페이지에 담긴 지역 선택지 ("area": {시도 코드: [...]}).

    시도 전체(id=loc_mcd, 예: 102000 경기전체)는 locations, 시군구(id=loc_cd, 예: 102320 안양시)는 districts 값이다.
    """
    m = _AREA_RE.search(html)
    if not m:
        raise ParseError("사람인: 지역 선택지(area)를 찾을 수 없음")
    area, _ = json.JSONDecoder().raw_decode(html, m.start() + len('"area":'))
    options: dict[tuple, RegionOption] = {}
    for items in area.values():
        top = next((o for o in items if o.get("id") == "loc_mcd"), None)
        if top is None:
            continue
        sido = top["name"].removesuffix("전체")
        options.setdefault(("locations", top["code"]), RegionOption(top["code"], sido, "", "locations"))
        for o in items:
            if o.get("id") == "loc_cd":
                options.setdefault(("districts", o["code"]), RegionOption(o["code"], sido, o["name"], "districts"))
    return list(options.values())


def parse_list_page(html: str, page_size: int, today: date | None = None) -> Page:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#recruit_info_list")
    if container is None:
        if soup.select_one(".info_no_result"):
            return Page(postings=[], is_last=True)
        raise ParseError("사람인: #recruit_info_list 를 찾을 수 없음")

    today = today or datetime.now(KST).date()
    postings = [_parse_item(item, today) for item in container.select(".item_recruit")]
    total_text = _text(soup.select_one(".cnt_result"))  # 예: "총 3,047건"
    total = int(re.sub(r"\D", "", total_text)) if total_text and re.search(r"\d", total_text) else None
    return Page(postings=postings, is_last=len(postings) < page_size, total=total)


def _parse_item(item: Tag, today: date) -> Posting:
    rec_idx = item.get("value")
    title_a = item.select_one(".job_tit a")
    if not rec_idx or title_a is None:
        raise ParseError("사람인: 공고 ID 또는 제목을 찾을 수 없음")

    # job_condition: [지역, 경력, 학력, 고용형태, (급여)] 순서로 노출됨
    conditions = [_text(s) for s in item.select(".job_condition > span")]
    conditions += [None] * (4 - len(conditions))
    deadline = _text(item.select_one(".job_date .date"))
    registered = _text(item.select_one(".job_sector .job_day"))  # "등록일 26/11/04" 또는 "수정일 26/11/05"

    return Posting(
        external_id=str(rec_idx),
        title=title_a.get("title") or _text(title_a),
        company=_text(item.select_one(".corp_name")) or "",
        detail_url=DETAIL_URL.format(rec_idx),
        location=conditions[0],
        career=conditions[1],
        **career_fields(career.parse_korean(conditions[1])),
        education=conditions[2],
        employment_type=conditions[3],
        deadline=deadline,
        deadline_at=parse_deadline(deadline, today),
        posted_at=parse_registered(registered),
        raw={
            "conditions": [c for c in conditions if c],
            "sectors": [_text(a) for a in item.select(".job_sector a")],
            "registered": registered,
        },
    )


def parse_registered(text: str | None) -> datetime | None:
    """'등록일 26/11/04' → 2026-11-04 00:00 KST (UTC로 변환). '수정일'은 등록일이 아니므로 None.

    목록에는 날짜만 있으므로 시각은 0시로 둔다.
    """
    m = re.fullmatch(r"등록일 (\d{2})/(\d{2})/(\d{2})", text or "")
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    return datetime(2000 + y, mo, d, tzinfo=KST).astimezone(timezone.utc).replace(tzinfo=None)


def parse_deadline(text: str | None, today: date) -> date | None:
    """'~ 11/15(일)' / '오늘마감' / '23시마감' / '내일마감' → 날짜. '채용시', '상시채용', '진행예정' 등은 None.

    연도가 표기되지 않으므로, 오늘보다 한참 이전 날짜가 되면 내년으로 본다(연말에 내년 1월 마감 공고).
    """
    if not text:
        return None
    if "오늘마감" in text or re.search(r"\d+시마감", text):  # "23시마감" = 오늘 해당 시각 마감
        return today
    if "내일마감" in text:
        return today + timedelta(days=1)
    m = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    try:
        d = date(today.year, month, day)
        if d < today - timedelta(days=180):
            d = date(today.year + 1, month, day)
    except ValueError:  # 2/29 등 해당 연도에 없는 날짜
        return None
    return d


def _text(node: Tag | None) -> str | None:
    if node is None:
        return None
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) or None

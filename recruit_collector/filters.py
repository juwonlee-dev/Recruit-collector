"""키워드로 사이트별 검색 필터(직무 필터)를 만든다.

1. 매핑 데이터 만들기 (build_catalog)
   각 크롤러 모듈이 사이트 검색 화면의 직무 선택지(IT·개발 분류 안)를 가져오고, 선택지 이름을
   비교용 핵심어로 나눠 config/catalog/<사이트>.json에 저장한다.
     예) 사람인 '백엔드/서버개발' → {'백엔드', '서버'},  원티드 '자바 개발자' → {'자바'}
2. 필터 생성 (generate)
   키워드 파일의 키워드도 같은 방식으로 핵심어로 바꾸고(동의어 포함), 핵심어가 같은 선택지를
   사이트마다 골라 검색 필터 파일의 직무 필드에 넣는다. max_pages 등 다른 값은 그대로 둔다.
     예) '백엔드 개발' → {'백엔드', '서버'} → 사람인 84, 잡코리아 1000229, 원티드 872, 리멤버 [SW개발, 백엔드]
   등록한 지역이 있으면 같은 방식으로 사이트별 지역 필드도 만든다 (recruit_collector/regions.py).

핵심어는 부분 일치가 아니라 완전 일치로 비교한다. '웹'이 '웹퍼블리셔'나 '웹마스터'까지 잡지 않도록.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from recruit_collector import regions as regions_mod
from recruit_collector.config import AppConfig, read_yaml, write_yaml
from recruit_collector.crawlers import CRAWLERS, platform_of
from recruit_collector.crawlers.base import JobOption
from recruit_collector.http import HttpClient

KST = timezone(timedelta(hours=9))

# 직무 이름 끝에 붙는 일반적인 말. 떼어 내고 남은 부분을 핵심어로 쓴다 (긴 것부터 비교).
GENERIC_SUFFIXES = ("개발자", "개발", "엔지니어링", "엔지니어", "developer", "engineer")
# 사이트마다 다르게 부르는 같은 직무. 키워드 쪽 핵심어를 넓힐 때 쓴다.
SYNONYMS = [
    {"백엔드", "서버", "backend"},
    {"프론트엔드", "프론트", "frontend"},
    {"풀스택", "fullstack"},
    {"java", "자바"},
    {"python", "파이썬"},
    {"안드로이드", "android"},
]
_SPLIT_RE = re.compile(r"[/·,]")
_STRIP_RE = re.compile(r"[\s()\[\]_\-]")

MAX_KEYWORD_LENGTH = 30
_keywords_lock = threading.Lock()


def core_terms(name: str) -> set[str]:
    """이름을 비교용 핵심어로 나눈다. '/', '·', ','로 나뉜 각 부분에서 공백·괄호와 일반 접미어를 뗀다."""
    terms = set()
    for part in _SPLIT_RE.split(name):
        term = _STRIP_RE.sub("", part).lower()
        for suffix in GENERIC_SUFFIXES:
            if term.endswith(suffix) and len(term) > len(suffix):
                term = term[: -len(suffix)]
                break
        if term:
            terms.add(term)
    return terms


def keyword_terms(keyword: str) -> set[str]:
    terms = core_terms(keyword)
    for group in SYNONYMS:
        if terms & group:
            terms |= group
    return terms


# ---------------------------------------------------------------- 키워드 파일

KEYWORDS_HEADER = "# 검색 필터 생성에 쓰는 키워드. 조회 화면의 설정 메뉴에서 등록·삭제할 수 있다.\n"


def load_keywords(config: AppConfig) -> list[str]:
    if not config.keywords_file.exists():
        return []
    return [str(k) for k in read_yaml(config.keywords_file).get("keywords") or []]


def add_keyword(config: AppConfig, keyword: str) -> list[str]:
    keyword = " ".join(keyword.split())
    if not keyword:
        raise ValueError("키워드를 입력해 주세요.")
    if len(keyword) > MAX_KEYWORD_LENGTH:
        raise ValueError(f"키워드는 {MAX_KEYWORD_LENGTH}자 이하로 입력해 주세요.")
    with _keywords_lock:
        keywords = load_keywords(config)
        if keyword.lower() in (k.lower() for k in keywords):
            raise ValueError(f"이미 등록된 키워드입니다: {keyword}")
        keywords.append(keyword)
        write_yaml(config.keywords_file, {"keywords": keywords}, KEYWORDS_HEADER)
        return keywords


def remove_keyword(config: AppConfig, keyword: str) -> list[str]:
    with _keywords_lock:
        keywords = load_keywords(config)
        if keyword not in keywords:
            raise ValueError(f"등록되지 않은 키워드입니다: {keyword}")
        keywords.remove(keyword)
        write_yaml(config.keywords_file, {"keywords": keywords}, KEYWORDS_HEADER)
        return keywords


# ---------------------------------------------------------------- 매핑 데이터

def catalog_path(config: AppConfig, site: str):
    return config.catalog_dir / f"{platform_of(site)}.json"  # 같은 플랫폼의 크롤러는 같은 선택지


def load_catalog(config: AppConfig, site: str) -> dict | None:
    path = catalog_path(config, site)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def build_catalog(config: AppConfig, site: str) -> dict:
    """사이트에서 직무 선택지를 받아 핵심어를 붙여 저장한다."""
    crawler_cls = CRAWLERS[site]
    http = HttpClient(config.http)
    try:
        options = crawler_cls(http).fetch_job_options()
    finally:
        http.close()
    catalog = {
        "site": site,
        "field": crawler_cls.job_filter_field,
        "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
        "options": [{**asdict(o), "terms": sorted(core_terms(o.name))} for o in options],
    }
    path = catalog_path(config, site)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    return catalog


def option_names(config: AppConfig, site: str) -> dict[str, str]:
    """검색 조건의 직무 값 → 선택지 이름 (설정 화면 표시용). 값은 JSON 문자열로 비교한다."""
    catalog = load_catalog(config, site)
    if not catalog:
        return {}
    return {json.dumps(o["value"], ensure_ascii=False): o["name"] for o in catalog["options"]}


# ---------------------------------------------------------------- 필터 생성

@dataclass
class Match:
    value: Any
    name: str
    keywords: list[str] = field(default_factory=list)


@dataclass
class SiteResult:
    site: str
    status: str  # updated | unchanged | no_match | unsupported | error
    filter_field: str | None = None
    matches: list[Match] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)  # 이 사이트에 맞는 직무가 없는 키워드
    message: str = ""
    region_fields: tuple[str, ...] = ()
    region_matches: list[regions_mod.RegionMatch] = field(default_factory=list)
    region_unmatched: list[str] = field(default_factory=list)  # 이 사이트에 맞는 지역이 없는 지역코드


def match_options(keywords: list[str], options: list[dict]) -> tuple[list[Match], list[str]]:
    """키워드마다 핵심어가 같은 선택지를 고른다. 키워드 순서대로, 같은 선택지는 한 번만."""
    matches: dict[str, Match] = {}
    unmatched = []
    for kw in keywords:
        terms = keyword_terms(kw)
        hit = False
        for o in options:
            if terms & set(o["terms"]):
                hit = True
                key = json.dumps(o["value"], ensure_ascii=False)
                matches.setdefault(key, Match(o["value"], o["name"])).keywords.append(kw)
        if not hit:
            unmatched.append(kw)
    return list(matches.values()), unmatched


SITE_FILE_HEADER = (
    "# {label} 검색 필터. 수집을 시작할 때 읽어 메모리에 올려 쓰므로, 바꾼 내용은 다음 수집부터 반영된다.\n"
    "# 직무 필드({field})와 지역 필드({region_fields})는 설정 화면의 '필터 생성'으로 키워드·지역에서 다시 만들 수 있다 (다른 값은 유지).\n"
    "# 조건 키는 recruit_collector/crawlers/{site}.py 의 검색 조건(Condition) 클래스 참고.\n"
    "# name은 삭제 판정 단위이므로 바꾸지 않는 것이 좋다. generated는 마지막 필터 생성 기록 (수집에는 쓰지 않음).\n"
)


def generate(
    config: AppConfig,
    keywords: list[str],
    refresh: bool = False,
    labels: dict | None = None,
    regions: list[str] | None = None,
) -> list[SiteResult]:
    """키워드로 사이트별 직무 필터를, 등록한 지역으로 지역 필터를 만들어 각 검색 필터 파일에 반영한다.

    refresh가 참이거나 매핑 데이터가 없으면 사이트에서 직무·지역 선택지를 새로 받아 온다.
    직무와 지역은 따로 반영한다. 키워드(지역)가 없거나 맞는 값이 하나도 없으면 그 필드는 바꾸지 않는다
    (필터가 비면 전체 공고를 수집하게 되므로).
    """
    labels = labels or {}
    regions = regions or []
    results = []
    for site in config.sites():
        crawler_cls = CRAWLERS.get(site)
        if crawler_cls is None or not (crawler_cls.job_filter_field or crawler_cls.region_filter_fields):
            results.append(SiteResult(site, "unsupported", message="필터 생성을 지원하지 않는 사이트입니다."))
            continue
        field_name = crawler_cls.job_filter_field
        result = SiteResult(site, "no_match", field_name, region_fields=crawler_cls.region_filter_fields)
        try:
            updates: dict[str, list] = {}
            if keywords and field_name:
                catalog = None if refresh else load_catalog(config, site)
                if catalog is None:
                    catalog = build_catalog(config, site)
                result.matches, result.unmatched = match_options(keywords, catalog["options"])
                if result.matches:
                    updates[field_name] = [m.value for m in result.matches]
            if regions and crawler_cls.region_filter_fields:
                region_catalog = None if refresh else regions_mod.load_catalog(config, site)
                if region_catalog is None:
                    region_catalog = regions_mod.build_catalog(config, site)
                result.region_matches, result.region_unmatched = regions_mod.match_regions(
                    regions, region_catalog["options"]
                )
                if result.region_matches:
                    for f in crawler_cls.region_filter_fields:  # 사람인은 시도(locations)와 시군구(districts)가 다른 필드
                        updates[f] = [m.value for m in result.region_matches if m.field == f]
            if updates:
                result.status = _apply(config, site, crawler_cls, updates, keywords, regions, result, labels)
            else:
                result.message = "키워드·지역에 맞는 값이 없어 기존 필터를 유지했습니다."
        except Exception as e:  # 한 사이트의 실패가 다른 사이트 생성을 막지 않도록
            result = SiteResult(site, "error", field_name, message=f"{type(e).__name__}: {e}")
        results.append(result)
    return results


def _apply(config, site, crawler_cls, updates, keywords, regions, result, labels) -> str:
    path = config.site_file(site)
    data = read_yaml(path)

    def as_set(items) -> set[str]:  # 직무·지역 필터는 OR 조건이라 순서는 비교하지 않는다
        return {json.dumps(v, ensure_ascii=False) for v in items or []}

    changed = False
    for search in data.get("searches") or []:
        condition = search.setdefault("condition", {})
        for key, values in updates.items():
            if as_set(condition.get(key)) != as_set(values):
                changed = True
            if values:
                condition[key] = values
            else:  # 사람인에서 시군구만 등록하면 시도 필드는 비운다
                condition.pop(key, None)
        for key, value in crawler_cls.fixed_condition.items():  # 크롤러의 고정 조건 (사람인 기업 형태)
            if condition.get(key) != value:
                changed = True
            condition[key] = list(value) if isinstance(value, list) else value
        crawler_cls.condition_class.from_dict(condition)  # 저장 전에 검색 조건으로 읽히는지 확인
    generated = {"at": datetime.now(KST).isoformat(timespec="seconds"), "keywords": keywords}
    if result.matches:
        generated["matches"] = [{"value": m.value, "name": m.name, "keywords": m.keywords} for m in result.matches]
    generated["regions"] = regions
    if result.region_matches:
        generated["region_matches"] = [
            {"value": m.value, "field": m.field, "name": m.name, "regions": m.regions} for m in result.region_matches
        ]
    data["generated"] = generated
    header = SITE_FILE_HEADER.format(
        label=labels.get(site, site), field=crawler_cls.job_filter_field,
        region_fields=", ".join(crawler_cls.region_filter_fields) or "없음", site=platform_of(site),
    )
    write_yaml(path, data, header)
    return "updated" if changed else "unchanged"

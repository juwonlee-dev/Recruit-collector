"""공통 지역코드와 사이트별 지역 코드 매핑.

1. 공통 지역코드 (config/region_codes.yaml)
   '시도' 또는 '시도 시군구' 형식의 코드 (예: 서울, 경기 안양시). 구가 있는 시는 시 단위로 묶는다.
2. 등록한 지역 (config/regions.yaml)
   설정 화면에서 공통 지역코드 중에서 골라 등록한다. 필터 생성 때 사이트별 지역 필터가 된다.
3. 사이트별 지역 선택지 (config/catalog/<사이트>_regions.json)
   각 크롤러 모듈이 사이트 검색 화면의 지역 선택지를 가져와 저장한다.
     예) 경기 안양시 → 사람인 102320(districts), 잡코리아 B240·B250, 원티드 gyeonggi.anyang-si,
         리멤버 [경기도, 안양시 동안구]·[경기도, 안양시 만안구]

사이트마다 시도 이름이 달라(서울특별시·서울, 경기도·경기) 시도는 region_names.SIDO_KEYS로 맞추고, 시군구는 이름으로 맞춘다.
시 이름과 같은 선택지가 있으면 그것만 쓰고(사람인 '안양시'), 없으면 그 시의 구를 모두 쓴다(잡코리아 '안양시 동안구', '안양시 만안구').
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from recruit_collector.config import AppConfig, read_yaml, write_yaml
from recruit_collector.crawlers import CRAWLERS, platform_of
from recruit_collector.http import HttpClient
from recruit_collector.region_names import OLD_NAMES, sido_key

KST = timezone(timedelta(hours=9))

REGIONS_HEADER = (
    "# 검색 필터 생성에 쓰는 지역. 조회 화면의 설정 메뉴에서 등록·삭제할 수 있다.\n"
    "# 값은 공통 지역코드(region_codes.yaml): '시도'는 시도 전체, '시도 시군구'는 그 시군구.\n"
)
_regions_lock = threading.Lock()


def label(code: str) -> str:
    """화면 표시용: '서울' → '서울 전체', '경기 안양시' → 그대로."""
    return code if " " in code else f"{code} 전체"


# ---------------------------------------------------------------- 공통 지역코드

def load_codes(config: AppConfig) -> dict[str, list[str]]:
    """시도 → 시군구 목록 (파일 순서)."""
    data = read_yaml(config.region_codes_file).get("regions") or {}
    return {str(sido): [str(c) for c in cities or []] for sido, cities in data.items()}


def make_code(config: AppConfig, sido: str, city: str = "") -> str:
    """시도·시군구를 공통 지역코드로. 없는 지역이면 ValueError."""
    sido, city = sido.strip(), city.strip()
    codes = load_codes(config)
    if sido not in codes:
        raise ValueError(f"공통 지역코드에 없는 시도입니다: {sido}")
    if city and city not in codes[sido]:
        raise ValueError(f"공통 지역코드에 없는 시군구입니다: {sido} {city}")
    return f"{sido} {city}" if city else sido


# ---------------------------------------------------------------- 등록한 지역

def load_regions(config: AppConfig) -> list[str]:
    if not config.regions_file.exists():
        return []
    return [str(r) for r in read_yaml(config.regions_file).get("regions") or []]


def add_region(config: AppConfig, sido: str, city: str = "") -> tuple[str, list[str]]:
    """지역을 등록한다. 시도 전체를 등록하면 그 시도의 시군구는 겹치므로 지운다. (등록한 코드, 지운 코드)를 돌려준다."""
    code = make_code(config, sido, city)
    with _regions_lock:
        regions = load_regions(config)
        if code in regions:
            raise ValueError(f"이미 등록된 지역입니다: {label(code)}")
        sido = code.split(" ")[0]
        if " " in code and sido in regions:
            raise ValueError(f"{label(sido)}가 이미 등록되어 있어 {code}는 포함됩니다.")
        removed = [] if " " in code else [r for r in regions if r.startswith(f"{sido} ")]
        regions = [r for r in regions if r not in removed] + [code]
        write_yaml(config.regions_file, {"regions": regions}, REGIONS_HEADER)
        return code, removed


def remove_region(config: AppConfig, code: str) -> list[str]:
    with _regions_lock:
        regions = load_regions(config)
        if code not in regions:
            raise ValueError(f"등록되지 않은 지역입니다: {code}")
        regions.remove(code)
        write_yaml(config.regions_file, {"regions": regions}, REGIONS_HEADER)
        return regions


# ---------------------------------------------------------------- 사이트별 지역 선택지

def catalog_path(config: AppConfig, site: str):
    return config.catalog_dir / f"{platform_of(site)}_regions.json"  # 같은 플랫폼의 크롤러는 같은 선택지


def load_catalog(config: AppConfig, site: str) -> dict | None:
    path = catalog_path(config, site)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def build_catalog(config: AppConfig, site: str) -> dict:
    """사이트에서 지역 선택지를 받아 저장한다."""
    http = HttpClient(config.http)
    try:
        options = CRAWLERS[site](http).fetch_region_options()
    finally:
        http.close()
    catalog = {
        "site": site,
        "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
        "options": [asdict(o) for o in options],
    }
    path = catalog_path(config, site)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    return catalog


def option_label(o: dict) -> str:
    return f"{o['sido']} {o['name']}" if o["name"] else f"{o['sido']} 전체"


def option_names(config: AppConfig, site: str) -> dict[str, str]:
    """검색 조건의 지역 값 → 선택지 이름 (설정 화면 표시용). 값은 JSON 문자열로 비교한다."""
    catalog = load_catalog(config, site)
    if not catalog:
        return {}
    return {json.dumps(o["value"], ensure_ascii=False): option_label(o) for o in catalog["options"]}


# ---------------------------------------------------------------- 매핑

@dataclass
class RegionMatch:
    value: Any
    field: str
    name: str
    regions: list[str] = field(default_factory=list)


def match_regions(codes: list[str], options: list[dict]) -> tuple[list[RegionMatch], list[str]]:
    """공통 지역코드마다 사이트 선택지를 고른다. 코드 순서대로, 같은 선택지는 한 번만."""
    matches: dict[tuple, RegionMatch] = {}
    unmatched = []
    for code in codes:
        sido, _, city = code.partition(" ")
        if not city:
            hits = [o for o in options if sido_key(o["sido"]) == sido and not o["name"]]
        else:
            hits = _city_options(options, sido, city) or [
                o for old_sido, old_city in OLD_NAMES.get(code, []) for o in _city_options(options, old_sido, old_city)
            ]
        if not hits:
            unmatched.append(code)
        for o in hits:
            key = (o["field"], json.dumps(o["value"], ensure_ascii=False))
            matches.setdefault(key, RegionMatch(o["value"], o["field"], option_label(o))).regions.append(code)
    return list(matches.values()), unmatched


def _city_options(options: list[dict], sido: str, city: str) -> list[dict]:
    """시 이름과 같은 선택지가 있으면 그것만, 없으면 그 시의 구 전체."""
    same_sido = [o for o in options if sido_key(o["sido"]) == sido]
    return [o for o in same_sido if o["name"] == city] or [o for o in same_sido if o["name"].split(" ")[0] == city]

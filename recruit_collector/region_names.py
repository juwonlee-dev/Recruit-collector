"""지역 이름 정규화: 사이트마다 다른 시도 이름과 공고의 지역 표기를 공통 지역코드로 바꾼다.

공통 지역코드는 '시도' 또는 '시도 시군구' (예: 서울, 경기 안양시. config/region_codes.yaml).
다른 모듈에 의존하지 않아 models(기존 공고 보정)·sync(수집 반영)·regions(필터 생성)에서 함께 쓴다.
"""

from __future__ import annotations

import re

# 사이트가 쓰는 시도 이름 → 공통 지역코드의 시도. 여기 없는 이름(전국, 해외 등)은 매핑하지 않는다.
SIDO_KEYS = {
    **{k: "서울" for k in ("서울", "서울특별시")},
    **{k: "경기" for k in ("경기", "경기도")},
    **{k: "인천" for k in ("인천", "인천광역시")},
    **{k: "부산" for k in ("부산", "부산광역시")},
    **{k: "대구" for k in ("대구", "대구광역시")},
    **{k: "대전" for k in ("대전", "대전광역시")},
    **{k: "울산" for k in ("울산", "울산광역시")},
    **{k: "세종" for k in ("세종", "세종시", "세종특별자치시")},
    **{k: "강원" for k in ("강원", "강원도", "강원특별자치도")},
    **{k: "충북" for k in ("충북", "충청북도")},
    **{k: "충남" for k in ("충남", "충청남도")},
    **{k: "전북" for k in ("전북", "전라북도", "전북특별자치도")},
    # 사람인·잡코리아는 전남광주 하나, 원티드·리멤버는 광주·전남 두 곳
    **{k: "전남광주" for k in ("전남광주", "광주", "광주광역시", "전남", "전라남도")},
    **{k: "경북" for k in ("경북", "경상북도")},
    **{k: "경남" for k in ("경남", "경상남도")},
    **{k: "제주" for k in ("제주", "제주도", "제주특별자치도")},
}

# 행정구역이 바뀌었는데 옛 이름만 있는 사이트(원티드·리멤버)를 위한 대체 매핑: 공통 지역코드 → 옛 (시도, 시군구).
# 필터 생성에서는 새 이름의 선택지가 없을 때만 쓰고, 공고 지역에서는 옛 이름을 새 코드로도 찾을 수 있게 한다.
# 옛 구역이 더 넓으면 그만큼 넓게 잡힌다 (예: 영종구 → 옛 중구 전체).
OLD_NAMES = {
    "인천 제물포구": [("인천", "중구"), ("인천", "동구")],
    "인천 영종구": [("인천", "중구")],
    "인천 서해구": [("인천", "서구")],
    "인천 검단구": [("인천", "서구")],
    "대구 군위군": [("경북", "군위군")],
}
_NEW_NAMES: dict[str, list[str]] = {}
for _new, _olds in OLD_NAMES.items():
    for _sido, _city in _olds:
        _NEW_NAMES.setdefault(f"{_sido} {_city}", []).append(_new)

_MORE_RE = re.compile(r"\s*외\s*\d+\s*$")  # 리멤버: '경기 성남시 외 1'

# 공고 지역코드의 계산 방식 버전. 바꾸면 DB를 열 때 기존 공고의 지역코드를 다시 계산한다 (models._fill_regions).
CODES_VERSION = "2"
NATIONWIDE = "전국"  # 지역이 '전국'인 공고. 어느 지역으로 찾아도 포함한다


def whole(sido: str) -> str:
    """시도 전체가 근무지인 공고의 표시 코드 (예: '서울 전체'). 그 시도의 어느 시군구로 찾아도 포함한다."""
    return f"{sido} 전체"


def search_codes(sido: str, city: str = "") -> list[str]:
    """지역 검색 시 공고 지역코드에 하나라도 있으면 포함할 코드.

    시도로 찾으면 그 시도의 공고 전부(시군구 공고는 시도 코드도 가짐), 시군구로 찾으면 그 시군구 공고와
    시도 전체 공고(예: '서울전체'). 전국 공고는 어느 지역으로 찾아도 포함한다.
    """
    return [f"{sido} {city}", whole(sido), NATIONWIDE] if city else [sido, NATIONWIDE]


def sido_key(name: str) -> str | None:
    return SIDO_KEYS.get(name.removesuffix("전체").strip())


def location_codes(location: str | None) -> list[str]:
    """공고의 지역 표기 → 공통 지역코드 목록 (시군구가 있으면 그 시도 코드도 함께).

    여러 지역은 쉼표로 나뉘고, 시도 없이 시군구만 있으면 앞에 나온 시도를 이어 쓴다 (사람인).
    시군구 없이 시도만 있으면 시도 전체가 근무지라는 뜻으로 '<시도> 전체' 코드를 더한다.
      '경기 안양시 동안구'                     → [경기, 경기 안양시]
      '서울전체 , 강남구 , 경기 광명시 , 안양시' → [서울, 서울 전체, 서울 강남구, 경기, 경기 광명시, 경기 안양시]
      '경기 성남시 외 1'                       → [경기, 경기 성남시]   (나머지 지역은 표기에 없음)
      '전국'                                   → [전국]
    """
    codes: list[str] = []

    def add(code: str) -> None:
        for c in [code, *_NEW_NAMES.get(code, [])]:
            if c not in codes:
                codes.append(c)

    sido = None
    for part in (location or "").split(","):
        words = _MORE_RE.sub("", part).split()
        if not words:
            continue
        if words[0] == NATIONWIDE:
            add(NATIONWIDE)
            continue
        if key := sido_key(words[0]):
            sido, words = key, words[1:]
        if sido is None:
            continue  # 어느 시도인지 알 수 없는 시군구
        add(sido)
        if words and not sido_key(words[0]) and words[0] not in ("전체", "전지역"):
            add(f"{sido} {words[0]}")  # 구는 시 단위로 묶는다 (안양시 동안구 → 안양시)
        else:  # '서울', '서울전체', '서울 전체', '경기 경기전체'
            add(whole(sido))
    return codes


def encode(codes: list[str]) -> str:
    """DB 저장용: '|서울|서울 강남구|'. LIKE '%|코드|%'로 정확히 찾을 수 있게 양끝에도 구분자를 둔다."""
    return "|" + "|".join(codes) + "|" if codes else ""

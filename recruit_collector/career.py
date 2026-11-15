"""경력(연차) 정규화와 통합 필터.

사이트마다 경력 표기와 검색 필터의 연차 범위가 다르므로, 각 크롤러가 수집 시점에
공고의 경력 조건을 (최소 연차, 최대 연차, 경력무관 여부)로 정규화해 적재한다.
  - 연차는 0부터 시작하며 신입 = 0년차
  - 최대 연차가 None이면 상한 없음 (예: 경력 3년↑ → 3 ~ None)
  - 경력무관은 별도 플래그. 범위로 표현하지 않는다
조회 화면은 이 값으로 사이트에 상관없이 같은 연차 구간 필터를 적용한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Career:
    min_years: int | None = None  # None이면 알 수 없음
    max_years: int | None = None  # None이면 상한 없음 (min_years가 있을 때)
    any: bool = False  # 경력무관으로 명시된 공고


UNKNOWN = Career()
ANY = Career(any=True)

# 조회 화면의 연차 필터: 키 → (라벨, 구간 시작, 구간 끝(None=상한 없음))
BUCKETS: dict[str, tuple[str, int, int | None]] = {
    "0": ("신입", 0, 0),
    "1-3": ("1~3년차", 1, 3),
    "4-6": ("4~6년차", 4, 6),
    "7-9": ("7~9년차", 7, 9),
    "10up": ("10년차 이상", 10, None),  # URL에서 '+'는 공백으로 해석되므로 사용하지 않음
}
ANY_KEY = "any"  # 경력무관 공고만


def from_range(min_years: int, max_years: int | None) -> Career:
    if max_years is not None and max_years < min_years:
        min_years, max_years = max_years, min_years
    return Career(min_years=max(0, min_years), max_years=max_years)


def to_text(c: Career) -> str | None:
    """숫자로만 경력을 주는 사이트의 표시 문구 (사람인·잡코리아 표기와 비슷하게)."""
    if c.any:
        return "경력무관"
    if c.min_years is None:
        return None
    lo, hi = c.min_years, c.max_years
    if lo == 0:
        if hi == 0:
            return "신입"
        return "신입·경력" + (f" ~{hi}년" if hi is not None else "")
    if hi is None:
        return f"경력{lo}년↑"
    return f"경력 {lo}년" if lo == hi else f"경력 {lo}~{hi}년"


def parse_korean(text: str | None) -> Career:
    """'신입', '경력 3~10년', '경력3년↑', '경력5년↓', '신입·경력', '경력무관' 같은 표기를 정규화한다."""
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return UNKNOWN
    if "무관" in t:
        return ANY
    newcomer, experienced = "신입" in t, "경력" in t
    if m := re.search(r"(\d+)~(\d+)년", t):
        return from_range(0 if newcomer else int(m.group(1)), int(m.group(2)))
    if m := re.search(r"(\d+)년(?:↑|이상)", t):
        return from_range(0 if newcomer else int(m.group(1)), None)
    if m := re.search(r"(\d+)년(?:↓|이하)", t):
        return from_range(0 if newcomer else 1, int(m.group(1)))
    if newcomer and experienced:
        return from_range(0, None)
    if newcomer:
        return from_range(0, 0)
    if experienced:
        return from_range(1, None)
    return UNKNOWN

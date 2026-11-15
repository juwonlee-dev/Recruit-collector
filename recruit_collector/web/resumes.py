"""이력서: 위젯·템플릿·이력서의 형식 정의, 입력값 검증, 기본 위젯·템플릿, 문서(미리보기·PDF) 데이터.

구성
  위젯(ResumeWidget)   항목 하나의 정의. kind가 form이면 입력 칸(fields) 목록을 가진다.
                        칸마다 이름(label), 종류(type), 힌트(placeholder: 입력 칸에 흐리게 보이는 안내),
                        예시 텍스트(default: 이 템플릿으로 이력서를 만들면 미리 채워지는 내용), 도움말(help), 너비(span)를 정한다.
  템플릿(ResumeTemplate) 위젯을 배치한 문서 틀. layout = {"rows": [행]}, 행 = {"cols": [칸]}, 칸 = {"span": 1~12, "items": [위젯]}.
                        행은 위에서 아래로 쌓이고, 한 행을 가로로 여러 칸(12칸 격자)으로 나눌 수 있다. 칸 안의 위젯은 세로로 쌓인다.
                        템플릿에 넣은 위젯은 그때의 설정을 복사해 두므로(source_id는 원본 위젯), 원본을 고쳐도 템플릿은 바뀌지 않는다.
  이력서(Resume)        템플릿 배치를 복사해 두고, 입력한 내용을 data = {위젯 배치 ID: [항목]}에 둔다.
                        반복 위젯(경력·학력 등)은 항목이 여러 개, 아니면 항목 하나. 항목 = {칸 key: 값(글, 태그면 글 목록)}.

첨부파일(사진 등) 위젯을 추가할 때는 FIELD_TYPES에 종류를 더하고, 파일은 별도 테이블에 저장한 뒤 값에는 파일 ID만 넣는다
(normalize_value에서 그 종류의 값 형식을 검사). 지금은 글로 입력하는 종류만 있다.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from recruit_collector.models import Resume, ResumeTemplate, ResumeWidget, utcnow

# 입력 칸 종류 → 표시 이름
FIELD_TYPES = {
    "text": "한 줄 글",
    "textarea": "여러 줄 글",
    "month": "연월",
    "date": "날짜",
    "select": "목록에서 선택",
    "tags": "태그 (쉼표로 구분)",
    "email": "이메일",
    "tel": "전화번호",
    "url": "링크",
    "number": "숫자",
    "address": "주소 (카카오 주소 검색)",
}
# 위젯 종류 → 표시 이름
WIDGET_KINDS = {
    "form": "입력 항목",
    "heading": "제목",
    "note": "고정 문구",
    "divider": "구분선",
}
VISIBILITY = {"private": "비공개", "public": "공개"}
# 입력 항목 위젯이 문서(미리보기·PDF)에 보이는 형식
DISPLAYS = {
    "grid": "기본 (이름 아래 값, 너비대로 배치)",
    "profile": "프로필 (이름 크게, 연락처 한 줄)",
    "timeline": "목록 (왼쪽 기간, 오른쪽 제목·내용)",
    "cards": "카드 (칸마다 상자로 나란히)",
    "labeled": "왼쪽 제목 (위젯 제목을 왼쪽에)",
}
TITLE_STYLES = {"line": "밑줄", "bar": "굵은 윗선 + 큰 제목", "plain": "굵은 글씨만"}
# 프로필·목록·왼쪽 제목 형식에서 입력 칸이 놓이는 자리
SLOTS = {
    "title": "제목 (굵게)",
    "meta": "부가정보 (제목 옆 회색, · 로 이음)",
    "badge": "배지 (작은 꼬리표)",
    "side": "왼쪽 기간 (연월·날짜는 ~ 로 이음)",
    "info": "이름·값 한 줄 (| 로 이음)",
    "line": "이름: 값 (한 줄씩)",
    "body": "본문 (여러 줄 그대로)",
}
GRID = 12  # 한 행의 칸 너비 합

# ---------------------------------------------------------------- 스타일 (글꼴·글자 크기·색)
# 템플릿 전체 스타일은 layout["theme"], 위젯별 스타일은 config["style"]에 기본값과 다른 값만 둔다.
# 문서(resume_doc.html)는 글자 크기를 calc(사람인 크기 × var(--k)), 색을 var(--c-…)로 쓰므로 변수만 바꾸면 된다.
# 문서 글꼴 (static/fonts, 모두 SIL OFL). faces = [(굵기, 파일 이름)]. 600이 없는 글꼴은 700으로 보인다
FONTS = {
    "pretendard": {"label": "Pretendard (기본 · 고딕)", "family": "Pretendard", "serif": False,
                   "faces": [(400, "Pretendard-Regular"), (600, "Pretendard-SemiBold"), (700, "Pretendard-Bold")]},
    "nanumgothic": {"label": "나눔고딕", "family": "NanumGothic", "serif": False,
                    "faces": [(400, "NanumGothic-Regular"), (700, "NanumGothic-Bold")]},
    "plexkr": {"label": "IBM Plex Sans KR (고딕)", "family": "IBM Plex Sans KR", "serif": False,
               "faces": [(400, "IBMPlexSansKR-Regular"), (600, "IBMPlexSansKR-SemiBold"), (700, "IBMPlexSansKR-Bold")]},
    "nanummyeongjo": {"label": "나눔명조 (명조)", "family": "NanumMyeongjo", "serif": True,
                      "faces": [(400, "NanumMyeongjo-Regular"), (700, "NanumMyeongjo-Bold")]},
    "gowunbatang": {"label": "고운바탕 (명조)", "family": "GowunBatang", "serif": True,
                    "faces": [(400, "GowunBatang-Regular"), (700, "GowunBatang-Bold")]},
}
DEFAULT_FONT = "pretendard"
BASE_SIZE, SIZE_MIN, SIZE_MAX = 8.7, 7.0, 12.0  # 본문 글자 크기(pt). 기본은 사람인과 같은 8.7pt
# 템플릿 색: key → (이름, 기본값=사람인 색)
THEME_COLORS = {
    "text": ("본문", "#000000"),
    "head": ("제목·이름", "#151822"),
    "muted": ("보조 글자 (항목 이름·부가정보)", "#67738e"),
    "accent": ("강조 (기간 합계·태그)", "#003fe5"),
    "line": ("선·테두리", "#d7dce5"),
}
WIDGET_SIZES = (80, 90, 100, 110, 120, 135, 150)  # 위젯 글자 크기: 템플릿 크기의 %
# 위젯 종류별로 쓸 수 있는 스타일 (color: 입력 항목은 본문, 제목은 제목, 고정 문구는 글자, 구분선은 선 색)
STYLE_KEYS = {"form": ("font", "size", "color", "title_color", "line_color"), "heading": ("font", "size", "color"),
              "note": ("font", "size", "color"), "divider": ("color",)}
ALIGNS = {"left": "왼쪽", "center": "가운데", "right": "오른쪽"}
HEADING_SIZES = {"lg": "크게 (문서 제목)", "md": "보통 (영역 제목)", "sm": "작게"}
DIVIDER_WEIGHTS = {"thin": "얇게", "normal": "보통", "thick": "굵게"}
DIVIDER_DASHES = {"solid": "실선", "dashed": "파선", "dotted": "점선"}
DIVIDER_SPACES = {"narrow": "좁게", "normal": "보통", "wide": "넓게"}

# 크기 제한 (화면·DB를 지키기 위한 상한)
MAX_ROWS, MAX_COLS, MAX_ITEMS, MAX_FIELDS, MAX_OPTIONS, MAX_ENTRIES, MAX_TAGS = 60, 4, 20, 30, 30, 50, 50
LEN = {"name": 100, "desc": 300, "title": 100, "label": 60, "placeholder": 300, "default": 5000, "help": 300,
       "option": 60, "value": 20000, "tag": 60, "text": 2000, "add_label": 30}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")


class Invalid(ValueError):
    """사용자에게 보여 줄 검증 오류."""


# ---------------------------------------------------------------- 검증·정리

def _str(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise Invalid("글자가 와야 하는 곳에 다른 값이 있습니다.")
    return str(value).replace("\r\n", "\n").strip()[:limit]


def _span(value: Any, default: int = GRID) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(n, 1), GRID)


def normalize_field(f: Any, index: int, used: set[str]) -> dict:
    if not isinstance(f, dict):
        raise Invalid("입력 칸 형식이 올바르지 않습니다.")
    ftype = f.get("type") if f.get("type") in FIELD_TYPES else "text"
    key = str(f.get("key") or "")
    if not _KEY_RE.match(key) or key in used:  # 키는 이력서 값과 연결되므로 위젯 안에서 겹치지 않게
        key = f"f{index + 1}"
        while key in used:
            key += "_"
    used.add(key)
    label = _str(f.get("label"), LEN["label"]) or f"항목 {index + 1}"
    is_range = ftype in ("month", "date") and bool(f.get("range"))
    out = {
        "key": key, "label": label, "type": ftype,
        "placeholder": _str(f.get("placeholder"), LEN["placeholder"]),
        "default": _str(f.get("default"), LEN["default"]),
        "help": _str(f.get("help"), LEN["help"]),
        "span": _span(f.get("span")),
        "required": bool(f.get("required")),
        "emphasis": bool(f.get("emphasis")),
        "slot": f.get("slot") if f.get("slot") in SLOTS else "info",
        "hide_label": bool(f.get("hide_label")),
        "age": ftype == "date" and not is_range and bool(f.get("age")),  # 날짜: 문서에 '출생연도 (만 나이)'로 (생년월일)
        # 연월·날짜: 기간(시작~끝)으로 입력. 값은 'YYYY-MM~YYYY-MM'. 시작=끝 허용 여부, 끝을 비우면 '현재'(진행 중)
        "range": is_range,
        "allow_same": f.get("allow_same", True) is not False,
        "open_end": is_range and bool(f.get("open_end")),
    }
    if ftype == "select":
        options = [o for o in (_str(x, LEN["option"]) for x in (f.get("options") or [])[:MAX_OPTIONS]) if o]
        out["options"] = list(dict.fromkeys(options))
    return out


def color(value: Any) -> str:
    """'#1a2b3c' · '#abc' · '1a2b3c' → '#1a2b3c'. 색 코드가 아니면 ''. (문서의 style 속성에 들어가므로 이 형식만 통과)"""
    s = str(value or "").strip().lower()
    m = re.fullmatch(r"#?([0-9a-f]{3}|[0-9a-f]{6})", s)
    if not m:
        return ""
    h = m.group(1)
    return "#" + (h if len(h) == 6 else "".join(c * 2 for c in h))


def normalize_style(kind: str, style: Any) -> dict:
    """위젯 스타일: 그 종류에서 쓰는 값 중 기본값과 다른 것만 남긴다."""
    style = style if isinstance(style, dict) else {}
    out: dict[str, Any] = {}
    for key in STYLE_KEYS[kind]:
        v = style.get(key)
        if key == "font":
            if v in FONTS:
                out[key] = v
        elif key == "size":
            try:
                n = min(max(int(v), 50), 200)
            except (TypeError, ValueError):
                continue
            if n != 100:
                out[key] = n
        elif c := color(v):
            out[key] = c
    return out


def normalize_theme(theme: Any) -> dict:
    """템플릿 스타일 (글꼴·본문 글자 크기·색): 기본값과 다른 것만 남긴다."""
    theme = theme if isinstance(theme, dict) else {}
    out: dict[str, Any] = {}
    if theme.get("font") in FONTS and theme["font"] != DEFAULT_FONT:
        out["font"] = theme["font"]
    try:
        size = float(theme.get("size"))
    except (TypeError, ValueError):
        size = BASE_SIZE
    if math.isfinite(size):
        size = round(min(max(size, SIZE_MIN), SIZE_MAX), 1)
        if size != BASE_SIZE:
            out["size"] = size
    for key, (_, default) in THEME_COLORS.items():
        c = color(theme.get(key))
        if c and c != default:
            out[key] = c
    return out


def _choice(value: Any, choices: dict, default: str) -> str:
    return value if value in choices else default


def normalize_widget(kind: Any, config: Any) -> tuple[str, dict]:
    """위젯 종류와 설정을 검사해 저장할 형태로 정리한다."""
    if kind not in WIDGET_KINDS:
        raise Invalid("알 수 없는 위젯 종류입니다.")
    config = config if isinstance(config, dict) else {}
    style = normalize_style(kind, config.get("style"))
    if kind == "form":
        fields = config.get("fields") or []
        if not isinstance(fields, list) or not fields:
            raise Invalid("입력 항목 위젯에는 입력 칸이 하나 이상 있어야 합니다.")
        if len(fields) > MAX_FIELDS:
            raise Invalid(f"입력 칸은 {MAX_FIELDS}개까지 둘 수 있습니다.")
        used: set[str] = set()
        return kind, {
            "title": _str(config.get("title"), LEN["title"]),
            "show_title": config.get("show_title", True) is not False,
            "repeatable": bool(config.get("repeatable")),
            "add_label": _str(config.get("add_label"), LEN["add_label"]),
            "display": config.get("display") if config.get("display") in DISPLAYS else "grid",
            "title_style": config.get("title_style") if config.get("title_style") in TITLE_STYLES else "line",
            "show_duration": bool(config.get("show_duration")),  # 목록: 항목마다 기간(n년 n개월)
            "show_total": bool(config.get("show_total")),  # 목록: 제목 옆에 기간 합계 (겹치는 기간은 한 번만)
            "fields": [normalize_field(f, i, used) for i, f in enumerate(fields)],
            "style": style,
        }
    if kind == "heading":
        return kind, {"text": _str(config.get("text"), LEN["title"]) or "제목", "size": _choice(config.get("size"), HEADING_SIZES, "lg"),
                      "align": _choice(config.get("align"), ALIGNS, "left"), "style": style}
    if kind == "note":
        return kind, {"text": _str(config.get("text"), LEN["text"]), "align": _choice(config.get("align"), ALIGNS, "center"),
                      "style": style}
    return kind, {"weight": _choice(config.get("weight"), DIVIDER_WEIGHTS, "thin"),
                  "dash": _choice(config.get("dash"), DIVIDER_DASHES, "solid"),
                  "space": _choice(config.get("space"), DIVIDER_SPACES, "normal"), "style": style}


def normalize_layout(layout: Any) -> dict:
    """템플릿 배치를 검사해 정리한다. 행의 칸 너비 합은 12를 넘을 수 없다."""
    rows_in = (layout or {}).get("rows") if isinstance(layout, dict) else None
    if not isinstance(rows_in, list):
        raise Invalid("템플릿 배치 형식이 올바르지 않습니다.")
    if len(rows_in) > MAX_ROWS:
        raise Invalid(f"행은 {MAX_ROWS}개까지 둘 수 있습니다.")
    ids: set[str] = set()

    def new_id(value: Any, prefix: str) -> str:
        v = str(value or "")
        if not _ID_RE.match(v) or v in ids:
            n = len(ids) + 1
            v = f"{prefix}{n}"
            while v in ids:
                n += 1
                v = f"{prefix}{n}"
        ids.add(v)
        return v

    rows = []
    for row in rows_in:
        cols_in = row.get("cols") if isinstance(row, dict) else None
        if not isinstance(cols_in, list) or not cols_in:
            continue
        if len(cols_in) > MAX_COLS:
            raise Invalid(f"한 행은 {MAX_COLS}칸까지 나눌 수 있습니다.")
        cols = []
        for col in cols_in:
            if not isinstance(col, dict):
                raise Invalid("칸 형식이 올바르지 않습니다.")
            items_in = col.get("items") or []
            if not isinstance(items_in, list) or len(items_in) > MAX_ITEMS:
                raise Invalid(f"한 칸에는 위젯을 {MAX_ITEMS}개까지 둘 수 있습니다.")
            items = []
            for it in items_in:
                if not isinstance(it, dict):
                    raise Invalid("위젯 형식이 올바르지 않습니다.")
                kind, config = normalize_widget(it.get("kind"), it.get("config"))
                source = it.get("source_id")
                items.append({
                    "id": new_id(it.get("id"), "w"),
                    "source_id": source if isinstance(source, int) and not isinstance(source, bool) else None,
                    "name": _str(it.get("name"), LEN["name"]) or WIDGET_KINDS[kind],
                    "kind": kind, "config": config,
                })
            cols.append({"id": new_id(col.get("id"), "c"), "span": _span(col.get("span")), "items": items})
        if sum(c["span"] for c in cols) > GRID:
            raise Invalid("한 행의 칸 너비 합이 12를 넘습니다.")
        rows.append({"id": new_id(row.get("id"), "r"), "cols": cols})
    return {"rows": rows, "theme": normalize_theme(layout.get("theme"))}


def iter_items(layout: dict):
    for row in layout.get("rows") or []:
        for col in row.get("cols") or []:
            yield from col.get("items") or []


_DATE_RE = {"month": re.compile(r"^\d{4}-(0[1-9]|1[0-2])$"), "date": re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")}


def _date_part(value: str, ftype: str) -> str:
    v = value.strip()
    return v if _DATE_RE[ftype].match(v) else ""


def split_range(value: Any) -> tuple[str, str]:
    """기간 값 'YYYY-MM~YYYY-MM' → (시작, 끝). 끝이 비면 ''."""
    a, _, b = str(value or "").partition("~")
    return a.strip(), b.strip()


def tel_digits(value: str) -> str:
    """저장용 전화번호: 숫자만 (010-1234-5678 → 01012345678). 하이픈은 보여 줄 때 format_tel로 붙인다.
    숫자·하이픈·공백·괄호·점만 있을 때만 바꾼다 (+82 같은 국제번호나 '내선' 같은 글이 섞이면 적은 그대로)."""
    v = value.strip()
    return re.sub(r"\D", "", v) if re.fullmatch(r"[\d\-\s().]+", v) else v


def format_tel(value: str) -> str:
    """보여 줄 때 하이픈: 010-1234-5678, 02-123-4567, 031-123-4567, 0505-123-4567, 1588-1234.
    숫자·하이픈·공백·괄호만 있을 때만 고친다 (+82 같은 국제번호나 '내선' 같은 글은 그대로). static/resume_editor.js와 같은 규칙."""
    v = value.strip()
    if not v or not re.fullmatch(r"[\d\-\s().]+", v):
        return v
    d = re.sub(r"\D", "", v)
    if re.match(r"^1[5-9]\d{2}", d) and len(d) <= 8:  # 대표번호
        return d if len(d) <= 4 else f"{d[:4]}-{d[4:]}"
    head = 2 if d.startswith("02") else 4 if re.match(r"^050\d", d) else 3
    d = d[:head + 8]
    p, rest = d[:head], d[head:]
    if not rest:
        return p
    if len(rest) <= 3:
        return f"{p}-{rest}"
    if len(rest) <= 7:
        return f"{p}-{rest[:3]}-{rest[3:]}"
    return f"{p}-{rest[:4]}-{rest[4:]}"


def normalize_value(field: dict, value: Any) -> Any:
    if field["type"] in ("month", "date") and field.get("range"):
        a, b = split_range(_str(value, 40))
        a, b = _date_part(a, field["type"]), _date_part(b, field["type"])
        return f"{a}~{b}" if a or b else ""
    if field["type"] == "tel":
        return tel_digits(_str(value, 40))
    if field["type"] == "tags":
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, list):
            return []
        tags = [t for t in (_str(x, LEN["tag"]) for x in value[:MAX_TAGS]) if t]
        return list(dict.fromkeys(tags))
    return _str(value, LEN["value"])


def normalize_data(layout: dict, data: Any) -> dict:
    """이력서 입력값을 배치에 맞춰 정리한다 (배치에 없는 위젯·칸의 값은 버린다)."""
    data = data if isinstance(data, dict) else {}
    out = {}
    for item in iter_items(layout):
        if item["kind"] != "form":
            continue
        entries = data.get(item["id"])
        entries = entries if isinstance(entries, list) else []
        config = item["config"]
        entries = entries[:MAX_ENTRIES] if config["repeatable"] else entries[:1]
        out[item["id"]] = [
            {f["key"]: normalize_value(f, (e if isinstance(e, dict) else {}).get(f["key"])) for f in config["fields"]}
            for e in entries
        ] or ([] if config["repeatable"] else [default_entry(config)])
    return out


def default_entry(config: dict) -> dict:
    """예시 텍스트(default)로 채운 항목 하나."""
    return {f["key"]: normalize_value(f, f["default"]) for f in config["fields"]}


def default_data(layout: dict) -> dict:
    """새 이력서의 처음 값: 위젯마다 예시 텍스트로 채운 항목 하나."""
    return {it["id"]: [default_entry(it["config"])] for it in iter_items(layout) if it["kind"] == "form"}


# ---------------------------------------------------------------- 권한

def visible(row: ResumeWidget | ResumeTemplate, uid: int) -> bool:
    return row.owner_id is None or row.owner_id == uid or row.visibility == "public"


def editable(row: ResumeWidget | ResumeTemplate, uid: int) -> bool:
    return row.owner_id is not None and row.owner_id == uid


def visible_filter(model, uid: int):
    return or_(model.owner_id.is_(None), model.owner_id == uid, model.visibility == "public")


def widget_library(s: Session, uid: int) -> list[ResumeWidget]:
    """템플릿 편집기에서 쓸 수 있는 위젯: 기본 위젯, 내 위젯, 다른 사람의 공개 위젯."""
    return list(s.scalars(select(ResumeWidget).where(visible_filter(ResumeWidget, uid))
                          .order_by(ResumeWidget.owner_id.is_not(None), ResumeWidget.id)))


def widget_json(w: ResumeWidget, uid: int) -> dict:
    return {"id": w.id, "name": w.name, "description": w.description or "", "kind": w.kind, "config": w.config,
            "visibility": w.visibility, "builtin": w.owner_id is None, "mine": editable(w, uid)}


# ---------------------------------------------------------------- 기본 위젯·템플릿

def _f(key, label, type_="text", span=12, placeholder="", default="", help_="", options=None, emphasis=False, required=False,
       slot="info", hide_label=False, age=False, range_=False, open_end=False):
    out = {"key": key, "label": label, "type": type_, "span": span, "placeholder": placeholder, "default": default,
           "help": help_, "emphasis": emphasis, "required": required, "slot": slot, "hide_label": hide_label, "age": age,
           "range": range_, "open_end": open_end, "allow_same": True}
    if options:
        out["options"] = options
    return out


# 기본 위젯: 사람인 이력서의 항목 구성(학력사항, 경력사항, 스킬, 경험/활동/교육, 자격증, 어학시험, 수상내역, 포트폴리오,
# 취업 우대사항, 자기소개서)과 문서 모양(이름+경력 배지 머리, 요약 카드, 왼쪽 기간·오른쪽 제목 목록, 굵은 윗선 섹션 제목,
# 경력 합계, 왼쪽 제목 경력기술서)을 따른다. 글꼴·크기·색은 templates/resume_doc.html.
# 사진·포트폴리오 파일은 첨부파일 위젯이 생기면 추가한다.
# 취업 우대사항의 장애 여부는 건강에 관한 민감정보라 넣지 않는다 (개인정보처리방침에서 민감정보를 적지 말라고 안내).
_TL = {"display": "timeline", "title_style": "bar"}  # 목록 형식 + 굵은 윗선 제목
BUILTIN_WIDGETS: list[dict] = [
    {"key": "profile", "name": "인적사항", "description": "이름·경력 구분·성별·생년·연락처·주소", "kind": "form", "config": {
        "title": "인적사항", "show_title": False, "display": "profile", "fields": [
            _f("name", "이름", span=6, placeholder="홍길동", required=True, slot="title"),
            _f("kind", "구분", "select", 6, options=["신입", "경력"], slot="badge"),
            _f("gender", "성별", "select", 4, options=["남", "여"], slot="meta"),
            _f("birth", "생년월일", "date", 4, slot="meta", age=True, help_="문서에는 출생연도와 만 나이(예: 1995 (31세))로 보입니다."),
            _f("email", "이메일", "email", 6, placeholder="name@example.com", required=True),
            _f("mobile", "휴대폰", "tel", 6, placeholder="010-0000-0000", required=True),
            _f("address", "주소", "address", placeholder="주소 검색을 눌러 찾아 주세요", slot="line",
               help_="검색으로 찾은 뒤 상세 주소를 이어서 적거나, 시·구까지만 적어도 됩니다."),
        ]}},
    {"key": "summary", "name": "요약 카드", "description": "경력·학력·희망연봉·포트폴리오 요약 상자", "kind": "form", "config": {
        "title": "요약", "show_title": False, "display": "cards", "fields": [
            _f("career", "경력", "textarea", 3, placeholder="회사 이름\n총 3년 2개월", help_="첫 줄은 굵게, 다음 줄은 작게 보입니다."),
            _f("education", "학력", "textarea", 3, placeholder="○○대학교\n대학교(4년) 졸업"),
            _f("salary", "희망연봉", "textarea", 3, placeholder="회사내규에 따름"),
            _f("portfolio", "포트폴리오", "textarea", 3, placeholder="GitHub"),
        ]}},
    {"key": "intro", "name": "간략 소개", "description": "소개 글과 대표 링크", "kind": "form", "config": {
        "title": "간략 소개", "title_style": "plain", "fields": [
            _f("intro", "소개", "textarea", placeholder="어떤 일을 해 왔고 무엇을 잘하는지 3~5줄로 적어 보세요.", hide_label=True),
            _f("link", "대표 링크", "url", placeholder="https://github.com/아이디", hide_label=True),
        ]}},
    {"key": "skill", "name": "나의 스킬", "description": "보유 스킬 태그", "kind": "form", "config": {
        "title": "나의 스킬", "title_style": "plain", "fields": [
            _f("skills", "스킬", "tags", placeholder="Java, Spring Boot, MySQL, AWS, Git", help_="쉼표(,)로 구분해 적으면 태그로 나뉩니다.",
               hide_label=True),
        ]}},
    {"key": "career", "name": "경력", "description": "기간·회사·부서·직책·직무·담당업무 (여러 개, 합계 표시)", "kind": "form", "config": {
        "title": "경력", **_TL, "show_duration": True, "show_total": True, "repeatable": True, "add_label": "경력 추가", "fields": [
            _f("period", "재직기간", "month", 6, slot="side", range_=True, open_end=True, help_="재직 중이면 끝을 비워 두세요 (현재로 계산)."),
            _f("company", "회사명", span=6, placeholder="회사 이름", required=True, slot="title"),
            _f("dept", "부서명", span=4, placeholder="개발팀", slot="meta"),
            _f("position", "직급/직책", span=4, placeholder="선임 / 팀원", slot="meta"),
            _f("job", "직무", span=4, placeholder="백엔드/서버개발", slot="meta"),
            _f("salary", "연봉", span=6, placeholder="적지 않아도 됩니다", slot="info"),
            _f("duties", "담당업무", "textarea", default="- \n- ", placeholder="- 맡은 업무와 성과를 한 줄씩", slot="body", hide_label=True),
            _f("note", "비고", span=12, placeholder="예) 휴직 기간 등", slot="line"),
        ]}},
    {"key": "career_desc", "name": "경력기술서", "description": "프로젝트별 경력기술서 (왼쪽 제목)", "kind": "form", "config": {
        "title": "경력기술서", "display": "labeled", "fields": [
            _f("body", "경력기술서", "textarea", hide_label=True, slot="body",
               default="프로젝트명: \n기간: \n역할/인원: \n개요: \n\n기술 스택:\n- \n\n주요 업무:\n- \n\n문제 해결 과정:\n# 문제 1. \n- ",
               help_="프로젝트마다 이름, 기간, 역할, 기술, 주요 업무, 문제 해결 과정을 적어 보세요."),
        ]}},
    {"key": "education", "name": "학력", "description": "기간·졸업 상태·학교·전공·학점 (여러 개)", "kind": "form", "config": {
        "title": "학력", **_TL, "repeatable": True, "add_label": "학력 추가", "fields": [
            _f("period", "재학기간", "month", 6, slot="side", range_=True, open_end=True, help_="재학 중이면 끝을 비워 두세요."),
            _f("status", "졸업 상태", "select", 3, options=["졸업", "졸업예정", "재학중", "휴학중", "수료", "중퇴", "자퇴"], slot="side"),
            _f("level", "학교 구분", "select", 3, options=["고등학교", "대학교(2,3년)", "대학교(4년)", "대학원(석사)", "대학원(박사)"], slot="badge"),
            _f("school", "학교명", span=6, placeholder="○○대학교", required=True, slot="title"),
            _f("major", "전공", span=6, placeholder="컴퓨터공학과", slot="meta"),
            _f("minor", "복수전공·부전공", span=6, placeholder="경영학과", slot="info"),
            _f("gpa", "학점", span=6, placeholder="3.8/4.5", slot="info"),
        ]}},
    {"key": "activity", "name": "경험/활동/교육", "description": "인턴·대외활동·교육이수·해외연수 등 (여러 개)", "kind": "form", "config": {
        "title": "경험/활동/교육", **_TL, "repeatable": True, "add_label": "경험/활동/교육 추가", "fields": [
            _f("period", "기간", "date", 6, slot="side", range_=True, open_end=True, help_="진행 중이면 끝을 비워 두세요."),
            _f("type", "구분", "select", 6, options=["인턴", "대외활동", "교내활동", "동아리", "자원봉사", "교육이수내역", "해외연수", "아르바이트", "기타"], slot="badge"),
            _f("name", "활동명", placeholder="활동·교육 이름", required=True, slot="title"),
            _f("org", "기관/장소", placeholder="회사·기관·단체 이름", slot="line"),
            _f("detail", "내용", "textarea", placeholder="무엇을 했고 무엇을 배웠는지 적어 보세요.", slot="body", hide_label=True),
        ]}},
    {"key": "license", "name": "자격증", "description": "취득월·자격증명·발행처 (여러 개)", "kind": "form", "config": {
        "title": "자격증", **_TL, "repeatable": True, "add_label": "자격증 추가", "fields": [
            _f("date", "취득월", "month", 3, slot="side"),
            _f("name", "자격증명", span=5, placeholder="정보처리기사", required=True, slot="title"),
            _f("issuer", "발행처", span=4, placeholder="한국산업인력공단", slot="meta"),
        ]}},
    {"key": "language", "name": "어학시험", "description": "취득월·시험명·언어·점수 (여러 개)", "kind": "form", "config": {
        "title": "어학시험", **_TL, "repeatable": True, "add_label": "어학시험 추가", "fields": [
            _f("date", "취득월", "month", 3, slot="side"),
            _f("test", "시험명", span=3, placeholder="TOEIC", required=True, slot="title"),
            _f("lang", "언어", "select", 3, options=["영어", "일본어", "중국어", "독일어", "프랑스어", "스페인어", "기타"], slot="meta"),
            _f("score", "점수/급수", span=3, placeholder="900", slot="info"),
        ]}},
    {"key": "award", "name": "수상내역", "description": "수상연월·수상명·수여기관·내용 (여러 개)", "kind": "form", "config": {
        "title": "수상내역", **_TL, "repeatable": True, "add_label": "수상 추가", "fields": [
            _f("date", "수상연월", "month", 3, slot="side"),
            _f("name", "수상명", span=5, placeholder="○○ 해커톤 대상", required=True, slot="title"),
            _f("org", "수여기관", span=4, placeholder="수여한 기관", slot="meta"),
            _f("detail", "내용", "textarea", placeholder="어떤 활동으로 받았는지 짧게 적어 보세요.", slot="body", hide_label=True),
        ]}},
    {"key": "portfolio", "name": "포트폴리오", "description": "구분·링크·설명 (여러 개)", "kind": "form", "config": {
        "title": "포트폴리오", **_TL, "repeatable": True, "add_label": "포트폴리오 추가", "fields": [
            _f("type", "구분", "select", 3, options=["GitHub", "블로그", "웹사이트", "노션", "기타"], slot="badge"),
            _f("url", "링크", "url", 9, placeholder="https://", slot="title"),
            _f("desc", "설명", placeholder="어떤 내용인지 한 줄로", slot="body", hide_label=True),
        ]}},
    {"key": "preference", "name": "취업 우대사항", "description": "보훈·고용지원금·병역", "kind": "form", "config": {
        "title": "취업 우대사항", **_TL, "fields": [
            _f("veteran", "보훈 대상", "select", 4, options=["대상", "비대상"]),
            _f("subsidy", "고용지원금 대상", "select", 4, options=["대상", "비대상"]),
            _f("military", "병역", "select", 4, options=["군필", "미필", "면제", "복무중", "해당없음"]),
            _f("branch", "군별", "select", 4, options=["육군", "해군", "공군", "해병", "사회복무요원", "전문연구요원", "산업기능요원", "기타"]),
            _f("rank", "제대 계급", span=4, placeholder="병장"),
            _f("served", "복무 기간", span=4, placeholder="2015.03 ~ 2016.12"),
        ]}},
    {"key": "desired", "name": "희망근무조건", "description": "고용형태·희망연봉·근무지·직무", "kind": "form", "config": {
        "title": "희망근무조건", **_TL, "fields": [
            _f("type", "고용형태", "select", 4, options=["정규직", "계약직", "인턴", "프리랜서", "무관"]),
            _f("salary", "희망연봉", span=4, placeholder="회사내규에 따름"),
            _f("region", "희망근무지", span=4, placeholder="서울 전체, 경기 성남시"),
            _f("job", "희망직무", "tags", placeholder="백엔드, 서버 개발 (쉼표로 구분)"),
        ]}},
    {"key": "essay", "name": "자기소개서", "description": "제목·내용 (여러 개)", "kind": "form", "config": {
        "title": "자기소개서", **_TL, "repeatable": True, "add_label": "자기소개서 항목 추가", "fields": [
            _f("title", "제목", placeholder="예) 성장과정", default="성장과정", slot="title"),
            _f("body", "내용", "textarea", placeholder="경험을 중심으로 구체적으로 적어 보세요.", slot="body", hide_label=True),
        ]}},
    {"key": "divider", "name": "구분선", "description": "가로 구분선", "kind": "divider", "config": {}},
]


def _layout(by_key: dict[str, ResumeWidget], rows: list[tuple]) -> dict:
    """기본 템플릿 배치: rows = [(행 ID, (칸 너비, [위젯 키…]), …)]"""
    def item(key: str) -> dict:
        w = by_key[key]
        return {"id": f"w_{key}", "source_id": w.id, "name": w.name, "kind": w.kind, "config": w.config}

    def row(rid: str, *cols: tuple[int, list[str]]) -> dict:
        return {"id": rid, "cols": [{"id": f"{rid}c{i}", "span": span, "items": [item(k) for k in keys]}
                                    for i, (span, keys) in enumerate(cols)]}

    return normalize_layout({"rows": [row(rid, *cols) for rid, *cols in rows]})


# 기본 템플릿: (키, 이름, 설명, 배치). 사람인 이력서와 같은 순서·모양
BUILTIN_TEMPLATES: list[tuple[str, str, str, list[tuple]]] = [
    ("basic", "기본 이력서", "기본 이력서입니다.", [
        ("s1", (12, ["profile", "divider"])),
        ("s2", (12, ["summary"])),
        ("s3", (12, ["intro", "divider"])),
        ("s4", (12, ["skill"])),
        ("s5", (12, ["career", "career_desc"])),
        ("s6", (12, ["education"])),
        ("s7", (12, ["activity"])),
        ("s8", (12, ["license"])),
        ("s9", (12, ["language"])),
        ("s10", (12, ["award"])),
        ("s11", (12, ["portfolio"])),
        ("s12", (12, ["preference"])),
        ("s13", (12, ["desired"])),
        ("s14", (12, ["essay"])),
    ]),
]


def ensure_builtins(s: Session) -> None:
    """기본 위젯·템플릿을 넣거나 최신 정의로 맞추고, 정의에서 빠진 것은 지운다 (누구도 고칠 수 없는 행이라 덮어써도 된다).

    지워도 이미 그 위젯을 넣은 템플릿, 그 템플릿으로 만든 이력서는 복사본을 가지고 있어 그대로 남는다.
    """
    now = utcnow()
    existing = {w.builtin_key: w for w in s.scalars(select(ResumeWidget).where(ResumeWidget.builtin_key.is_not(None)))}
    by_key = {}
    for d in BUILTIN_WIDGETS:
        kind, config = normalize_widget(d["kind"], d["config"])
        w = existing.get(d["key"])
        if w is None:
            w = ResumeWidget(builtin_key=d["key"], owner_id=None, visibility="public", created_at=now, updated_at=now,
                             name=d["name"], description=d["description"], kind=kind, config=config)
            s.add(w)
        elif (w.name, w.description, w.kind, w.config) != (d["name"], d["description"], kind, config):
            w.name, w.description, w.kind, w.config, w.updated_at = d["name"], d["description"], kind, config, now
        by_key[d["key"]] = w
    for key, w in existing.items():
        if key not in by_key:
            s.delete(w)
    s.flush()
    keys = set()
    for key, name, desc, rows in BUILTIN_TEMPLATES:
        keys.add(key)
        layout = _layout(by_key, rows)
        t = s.scalar(select(ResumeTemplate).where(ResumeTemplate.builtin_key == key))
        if t is None:
            s.add(ResumeTemplate(builtin_key=key, owner_id=None, visibility="public", name=name, description=desc,
                                 layout=layout, created_at=now, updated_at=now))
        elif (t.name, t.layout, t.description) != (name, layout, desc):
            t.name, t.layout, t.description, t.updated_at = name, layout, desc, now
    for t in s.scalars(select(ResumeTemplate).where(ResumeTemplate.builtin_key.is_not(None))):
        if t.builtin_key not in keys:
            s.execute(update(Resume).where(Resume.template_id == t.id).values(template_id=None))
            s.delete(t)
    s.commit()


# ---------------------------------------------------------------- 문서: 스타일 (CSS 변수)
# resume_doc.html의 :root에 사람인 색이 기본값으로 있고, 바꾼 색만 변수로 덮어쓴다.
# 굵은 윗선·배지 등 따로 고르지 않는 색은 고른 색에서 만든다 (제목 색 → 굵은 윗선, 강조 색 → 연한 태그 테두리 등).

def _mix(a: str, b: str, t: float) -> str:
    """색 a를 t, 색 b를 (1 - t) 비율로 섞는다."""
    pa, pb = ([int(x[i:i + 2], 16) for i in (1, 3, 5)] for x in (a, b))
    return "#" + "".join(f"{round(x * t + y * (1 - t)):02x}" for x, y in zip(pa, pb))


def _color_vars(c: dict) -> dict[str, str]:
    out = {}
    if "text" in c:
        out.update({"--c-text": c["text"], "--c-text2": c["text"]})
    if "head" in c:
        out.update({"--c-head": c["head"], "--c-title": c["head"], "--c-strong": c["head"],
                    "--c-strong-2": _mix(c["head"], "#ffffff", .55), "--c-sub": c["head"]})
    if "muted" in c:
        out["--c-muted"] = c["muted"]
    if "accent" in c:
        out.update({"--c-accent": c["accent"], "--c-accent-soft": _mix(c["accent"], "#ffffff", .2)})
    if "line" in c:
        out.update({"--c-line": c["line"], "--c-soft-line": _mix(c["line"], "#ffffff", .4)})
    return out


def font_family(key: str) -> str:
    f = FONTS.get(key) or FONTS[DEFAULT_FONT]
    fallback = ("'Noto Serif CJK KR', 'Nanum Myeongjo', serif" if f["serif"]
                else "Pretendard, 'Noto Sans CJK KR', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif")
    return f"'{f['family']}', {fallback}"


def _css(v: dict[str, str]) -> str:
    return "; ".join(f"{k}: {val}" for k, val in v.items())


def _theme_k(theme: dict) -> float:
    return theme.get("size", BASE_SIZE) / BASE_SIZE


def theme_style(theme: Any) -> str:
    """문서 전체(body)의 style 속성: 템플릿 스타일 중 바꾼 것만."""
    t = normalize_theme(theme)  # 예전에 저장된 배치도 다시 검사
    v = _color_vars(t)
    if "size" in t:
        v["--k"] = f"{_theme_k(t):.4f}"
    if "font" in t:
        v["font-family"] = font_family(t["font"])
    return _css(v)


def widget_style(kind: str, style: Any, theme_k: float) -> str:
    """위젯 하나의 style 속성 (템플릿 스타일 위에 덮어쓴다)."""
    s = normalize_style(kind, style)
    colors = {}
    if "color" in s:
        colors[{"form": "text", "heading": "head", "note": "muted", "divider": "line"}[kind]] = s["color"]
    if "title_color" in s:
        colors["head"] = s["title_color"]
    if "line_color" in s:
        colors["line"] = s["line_color"]
    v = _color_vars(colors)
    if "size" in s:
        v["--k"] = f"{theme_k * s['size'] / 100:.4f}"
    if "font" in s:
        v["font-family"] = font_family(s["font"])
    return _css(v)


def font_faces(layout: dict) -> list[dict]:
    """문서에서 쓰는 글꼴의 @font-face 목록 (쓰지 않는 글꼴은 PDF를 만들 때 읽지 않도록 뺀다). 기본 글꼴은 대체 글꼴로 늘 넣는다."""
    keys = [DEFAULT_FONT, normalize_theme(layout.get("theme")).get("font", DEFAULT_FONT)]
    keys += [((it.get("config") or {}).get("style") or {}).get("font") for it in iter_items(layout)]
    return [{"family": FONTS[k]["family"], "weight": w, "file": file}
            for k in dict.fromkeys(keys) if k in FONTS for w, file in FONTS[k]["faces"]]


# ---------------------------------------------------------------- 문서 (미리보기·PDF)

def document_rows(layout: dict, data: dict, show_hints: bool = False) -> list[dict]:
    """문서로 그릴 행 목록. 입력 위젯은 값이 있는 칸만, 값이 없는 항목은 빼고 보여 준다.

    show_hints: 템플릿 미리보기에서 빈 칸 대신 힌트(placeholder)를 흐리게 보여 준다.
    """
    theme_k = _theme_k(normalize_theme(layout.get("theme")))
    rows = []
    for row in layout.get("rows") or []:
        cols = []
        for col in row["cols"]:
            items = []
            for it in col["items"]:
                css = widget_style(it["kind"], it["config"].get("style"), theme_k)
                if it["kind"] != "form":
                    items.append({"kind": it["kind"], "config": it["config"], "css": css})
                    continue
                config = it["config"]
                display = config.get("display", "grid")
                entries, spans = [], []
                for entry in data.get(it["id"]) or []:
                    cells = []
                    for f in config["fields"]:
                        value = entry.get(f["key"])
                        hint = False
                        if not value and show_hints and f["placeholder"]:
                            value, hint = f["placeholder"], True
                        cells.append({"field": f, "value": value or "", "hint": hint, "empty": not value,
                                      "text": "" if hint else display_text(f, value)})
                    if display == "grid":
                        view = field_lines(cells)
                    else:
                        view = slot_view(config, cells)
                        if view and view.get("span"):
                            spans.append(view["span"])
                    if view:
                        entries.append(view)
                if entries or show_hints:
                    total = months_text(merged_months(spans)) if config.get("show_total") and spans else ""
                    items.append({"kind": "form", "config": config, "display": display, "entries": entries, "total": total, "css": css,
                                  "has_side": any(f.get("slot") == "side" for f in config["fields"])})
            cols.append({"span": col["span"], "items": items})
        if any(c["items"] for c in cols):
            rest = GRID - sum(c["span"] for c in cols)
            if rest > 0:  # 칸 너비 합이 12보다 작으면 남은 너비를 빈 칸으로 (표가 칸을 늘리지 않게)
                cols.append({"span": rest, "items": []})
            rows.append({"cols": cols})
    return rows


# ---------------------------------------------------------------- 문서: 기간·값 표시

def _ym(value: Any) -> tuple[int, int] | None:
    """'2021-08' 또는 '2021-08-15' → (2021, 8)."""
    m = re.match(r"^(\d{4})-(\d{2})", str(value or ""))
    return (int(m.group(1)), int(m.group(2))) if m and 1 <= int(m.group(2)) <= 12 else None


def _this_month() -> tuple[int, int]:
    today = _today()
    return today.year, today.month


def month_span(start: Any, end: Any) -> tuple[int, int] | None:
    """기간 (시작 월 번호, 끝 월 번호). 끝이 없으면 이번 달까지. 시작·끝 달을 모두 센다 (2021.08~2026.02 = 4년 7개월)."""
    a = _ym(start)
    if not a:
        return None
    b = _ym(end) or _this_month()
    lo, hi = a[0] * 12 + a[1] - 1, b[0] * 12 + b[1] - 1
    return (lo, hi) if hi >= lo else None


def merged_months(spans: list[tuple[int, int]]) -> int:
    """기간들의 합계 개월 수 (겹치는 기간은 한 번만)."""
    total, cur = 0, None
    for lo, hi in sorted(spans):
        if cur and lo <= cur[1] + 1:
            cur = (cur[0], max(cur[1], hi))
            continue
        if cur:
            total += cur[1] - cur[0] + 1
        cur = (lo, hi)
    if cur:
        total += cur[1] - cur[0] + 1
    return total


def months_text(n: int) -> str:
    years, months = divmod(n, 12)
    return " ".join(x for x in (f"{years}년" if years else "", f"{months}개월" if months else "") if x) or "1개월 미만"


def _today() -> date:
    return datetime.now(timezone(timedelta(hours=9))).date()


def man_age(birth: date, today: date | None = None) -> int:
    """만 나이: 올해 생일이 지났으면 (올해 - 출생연도), 아니면 1을 뺀다."""
    today = today or _today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def display_text(field: dict, value: Any) -> Any:
    """문서에 보일 값: 연월·날짜는 2021.08 / 2021.08.15, 나이 표시 날짜는 '1995 (31세)', 태그는 목록 그대로."""
    if not value:
        return ""
    if field["type"] == "tel":
        return format_tel(str(value))
    if field.get("range"):
        a, b = split_range(value)
        end = b.replace("-", ".") if b else ("현재" if field.get("open_end") else "")
        return f"{a.replace('-', '.')} ~ {end}".rstrip(" ~") if a else (f"~ {end}" if end else "")
    if field["type"] == "date" and field.get("age"):
        try:
            birth = date.fromisoformat(str(value))
        except ValueError:
            return value
        return f"{birth.year} ({man_age(birth)}세)"
    if field["type"] in ("month", "date") and re.match(r"^\d{4}-\d{2}(-\d{2})?$", str(value)):
        return str(value).replace("-", ".")
    return value


def slot_view(config: dict, cells: list[dict]) -> dict | None:
    """프로필·목록·카드·왼쪽 제목 형식의 항목 하나: 값이 있는 칸을 자리(slot)별로 모은다. 모두 비면 None.

    목록의 왼쪽(side)은 연월·날짜 칸 두 개를 '시작 ~ 끝'으로 잇고(끝이 비면 '현재'), 나머지 왼쪽 칸은 그 아래 작게 둔다.
    """
    filled = [c for c in cells if not c["empty"]]
    if not filled:
        return None
    view: dict[str, Any] = {slot: [] for slot in SLOTS}
    view["cells"] = filled
    view["all"] = cells  # 카드 형식: 빈 칸은 '-'로
    # 왼쪽 기간: 기간(시작~끝) 칸이 있으면 그것을, 없으면 예전처럼 연월·날짜 칸 두 개를 시작·끝으로
    rng = next((c for c in cells if c["field"].get("slot") == "side" and c["field"].get("range")), None)
    dates = [] if rng else [c for c in cells if c["field"].get("slot") == "side" and c["field"]["type"] in ("month", "date")][:2]
    for c in filled:
        slot = c["field"].get("slot", "info")
        if slot == "side" and (c is rng or any(c is d for d in dates)):
            continue
        view[slot].append(c)
    if rng:
        if not rng["empty"]:
            view["period"] = rng["value"] if rng["hint"] else rng["text"]
            a, b = split_range(rng["value"])
            if not rng["hint"] and a and (b or rng["field"].get("open_end")):
                span = month_span(a, b or None)
                if span:
                    view["span"] = span
                    if config.get("show_duration"):
                        view["duration"] = months_text(span[1] - span[0] + 1)
        return view
    start = dates[0] if dates else None
    end = dates[1] if len(dates) > 1 else None
    if start and not start["empty"]:
        if start["hint"]:
            view["period"] = start["value"]
        else:
            view["period"] = f"{start['text']} ~ " + (end["text"] if end and not end["empty"] and not end["hint"] else "현재" if end else "")
            view["period"] = view["period"].rstrip(" ~")
            span = month_span(start["value"], end["value"] if end and not end["hint"] else None) if end else None
            if span:
                view["span"] = span
                if config.get("show_duration"):
                    view["duration"] = months_text(span[1] - span[0] + 1)
    elif end and not end["empty"]:
        view["period"] = end["value"] if end["hint"] else end["text"]
    return view


def field_lines(cells: list[dict]) -> list[dict]:
    """입력 칸을 순서대로 한 줄(너비 합 12)씩 채워 줄로 나눈다 (편집 화면의 격자 배치와 같은 규칙).

    빈 칸도 줄 안에서는 자리를 지켜 배치가 흐트러지지 않게 하고, 모두 빈 줄은 뺀다.
    rest는 그 줄에서 남는 너비 (빈 자리로 채운다).
    """
    lines, cur, used = [], [], 0
    for c in cells:
        span = c["field"]["span"]
        if cur and used + span > GRID:
            lines.append((cur, used))
            cur, used = [], 0
        cur.append(c)
        used += span
    if cur:
        lines.append((cur, used))
    return [{"cells": line, "rest": GRID - used} for line, used in lines if any(not c["empty"] for c in line)]


def render_pdf(html: str) -> bytes:
    """HTML 문서를 PDF로. WeasyPrint는 시스템 라이브러리(Pango)가 필요해 도커 이미지에서만 쓰므로 여기서 불러온다."""
    from weasyprint import HTML

    return HTML(string=html).write_pdf()


def copy_name(name: str) -> str:
    return (name + " (복사본)")[:LEN["name"]]


def resume_count(s: Session, uid: int) -> int:
    from sqlalchemy import func

    return s.scalar(select(func.count()).select_from(Resume).where(Resume.owner_id == uid)) or 0

import pytest
from fastapi.testclient import TestClient

from recruit_collector import career
from recruit_collector.career import Career
from recruit_collector.crawlers import jobkorea
from recruit_collector.crawlers.base import CrawlResult, Posting, career_fields
from recruit_collector.models import make_session_factory
from recruit_collector.sync import sync_search
from recruit_collector.web.app import create_app


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("신입", Career(0, 0)),
        ("경력", Career(1, None)),
        ("경력3년↑", Career(3, None)),
        ("경력 3~10년", Career(3, 10)),
        ("경력5년↓", Career(1, 5)),
        ("신입·경력", Career(0, None)),
        ("신입·경력 ~3년", Career(0, None)),  # 범위 없이 붙은 표기도 신입 포함으로
        ("신입·경력 1~3년", Career(0, 3)),
        ("경력무관", Career(any=True)),
        ("", Career()),
        (None, Career()),
    ],
)
def test_parse_korean(text, expected):
    assert career.parse_korean(text) == expected


@pytest.mark.parametrize(
    ("career_type", "career_range", "text", "expected"),
    [
        ("1", 0, "신입", Career(0, 0)),
        ("2", 3, "경력3년↑", Career(3, None)),
        ("2", 100, "경력", Career(1, None)),
        ("2", 0, "경력", Career(1, None)),
        ("3", 100, "신입·경력", Career(0, None)),
        ("3", 2, "신입·경력2년↑", Career(0, None)),
        ("4", 0, "경력무관", Career(any=True)),
    ],
)
def test_jobkorea_career(career_type, career_range, text, expected):
    assert jobkorea._career(career_type, career_range) == {"career": text, **career_fields(expected)}


@pytest.fixture
def client(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'c.db'}")
    cases = {
        "신입공고": Career(0, 0),
        "주니어공고": Career(1, 3),
        "삼년이상공고": Career(3, None),
        "시니어공고": Career(8, 12),
        "신입경력공고": Career(0, None),
        "무관공고": Career(any=True),
        "미상공고": Career(),
    }
    items = [
        Posting(str(i), title, "회사", f"u{i}", **career_fields(c)) for i, (title, c) in enumerate(cases.items())
    ]
    with factory() as s:
        sync_search(s, site="saramin", search_name="s", result=CrawlResult(items, 1, True), missing_threshold=1)
        s.commit()
    return TestClient(create_app(factory))


def titles(client, key):
    html = client.get("/", params={"career": key}).text
    return {t for t in ("신입공고", "주니어공고", "삼년이상공고", "시니어공고", "신입경력공고", "무관공고", "미상공고") if t in html}


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("0", {"신입공고", "신입경력공고", "무관공고"}),
        ("1-3", {"주니어공고", "삼년이상공고", "신입경력공고", "무관공고"}),
        ("4-6", {"삼년이상공고", "신입경력공고", "무관공고"}),
        ("7-9", {"삼년이상공고", "시니어공고", "신입경력공고", "무관공고"}),
        ("10up", {"삼년이상공고", "시니어공고", "신입경력공고", "무관공고"}),
        ("any", {"무관공고"}),  # 경력무관만 따로
        ("", {"신입공고", "주니어공고", "삼년이상공고", "시니어공고", "신입경력공고", "무관공고", "미상공고"}),
        # 여러 구간을 고르면 합집합
        (["0", "7-9"], {"신입공고", "삼년이상공고", "시니어공고", "신입경력공고", "무관공고"}),
        (["0", "1-3"], {"신입공고", "주니어공고", "삼년이상공고", "신입경력공고", "무관공고"}),
        (["4-6", "없는값"], {"삼년이상공고", "신입경력공고", "무관공고"}),
    ],
)
def test_career_filter(client, key, expected):
    assert titles(client, key) == expected

import json
from dataclasses import dataclass, field

import pytest
import yaml
from fastapi.testclient import TestClient

from recruit_collector import filters
from recruit_collector.crawlers import jobkorea, remember, saramin, wanted
from recruit_collector.crawlers.base import Condition, Crawler, JobOption, Page
from recruit_collector.models import make_session_factory
from recruit_collector.web.app import create_app
from tests.conftest import make_config

KEYWORDS = ["백엔드 개발", "서버 개발", "유지보수", "웹개발", "풀스택", "Java"]


def test_core_terms_split_and_strip_generic_suffix():
    assert filters.core_terms("백엔드/서버개발") == {"백엔드", "서버"}
    assert filters.core_terms("자바 개발자") == {"자바"}
    assert filters.core_terms("웹 퍼블리셔") == {"웹퍼블리셔"}
    assert filters.core_terms("QA,테스트 엔지니어") == {"qa", "테스트"}
    assert filters.keyword_terms("Java") == {"java", "자바"}
    assert filters.keyword_terms("백엔드 개발") >= {"백엔드", "서버"}


def options(*pairs):
    return [{"value": v, "name": n, "terms": sorted(filters.core_terms(n))} for v, n in pairs]


def test_match_is_exact_on_core_terms():
    opts = options(("84", "백엔드/서버개발"), ("87", "웹개발"), ("88", "웹마스터"), ("91", "퍼블리셔"),
                   ("235", "Java"), ("236", "Javascript"), ("2232", "풀스택"), ("89", "유지보수"))
    matches, unmatched = filters.match_options(KEYWORDS, opts)
    assert [m.value for m in matches] == ["84", "89", "87", "2232", "235"]
    assert matches[0].keywords == ["백엔드 개발", "서버 개발"]
    assert unmatched == []


def test_match_reports_keywords_without_option():
    matches, unmatched = filters.match_options(KEYWORDS, options((872, "서버 개발자"), (873, "웹 개발자"), (660, "자바 개발자")))
    assert [m.value for m in matches] == [872, 873, 660]
    assert unmatched == ["유지보수", "풀스택"]


# ---------------------------------------------------------------- 사이트별 직무 선택지 파싱

def test_parse_saramin_job_options():
    data = {"job_category": [
        {"MCLS_CD_NO": "16", "SMALL_LIST": [{"SCLS_CD_NM": "x", "KEYWORD_LIST": [{"KEWD_CD_NO": "1", "KEWD_CD_NM": "기획"}]}]},
        {"MCLS_CD_NO": "2", "SMALL_LIST": [
            {"SCLS_CD_NM": "직무", "KEYWORD_LIST": [{"KEWD_CD_NO": "84", "KEWD_CD_NM": "백엔드/서버개발"}]},
            {"SCLS_CD_NM": "기술", "KEYWORD_LIST": [{"KEWD_CD_NO": "84", "KEWD_CD_NM": "백엔드/서버개발"},
                                                   {"KEWD_CD_NO": "235", "KEWD_CD_NM": "Java"}]},
        ]},
    ]}
    html = "<script>var x = {\n  'options'   : '" + json.dumps(data, ensure_ascii=False) + "', // 주석에 ' 있음\n};</script>"
    opts = saramin.parse_job_options(html)
    assert [(o.value, o.name, o.group) for o in opts] == [("84", "백엔드/서버개발", "직무"), ("235", "Java", "기술")]


def test_parse_jobkorea_job_options():
    codes = [
        {"code": "10031", "parentCode": "", "displayName": "AI·개발·데이터"},
        {"code": "1000229", "parentCode": "10031", "displayName": "백엔드개발자"},
        {"code": "1000185", "parentCode": "10026", "displayName": "경영·비즈니스기획"},
    ]
    assert jobkorea.parse_job_options(codes) == [JobOption("1000229", "백엔드개발자", "AI·개발·데이터")]


def test_parse_wanted_job_options():
    data = {"props": {"pageProps": {"tags": {"category": [
        {"id": 507, "title": "경영", "tags": [{"id": 1, "title": "x"}]},
        {"id": 518, "title": "개발", "tags": [{"id": 872, "title": "서버 개발자"}]},
    ]}}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script>'
    assert wanted.parse_job_options(html) == [JobOption(872, "서버 개발자", "개발")]


def test_parse_remember_job_options():
    data = {"data": {"job_categories": [
        {"name": "영업", "subs": [{"id": 1, "name": "B2B"}]},
        {"name": "SW개발", "subs": [{"id": 310, "name": "백엔드"}, {"id": 312, "name": "풀스택"}]},
    ]}}
    opts = remember.parse_job_options(data)
    assert [o.value for o in opts] == [["SW개발", "백엔드"], ["SW개발", "풀스택"]]


# ---------------------------------------------------------------- 필터 생성

@dataclass
class FakeCond(Condition):
    jobs: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)


@dataclass
class KindCond(FakeCond):
    kinds: list[str] = field(default_factory=list)


def fake_crawler(site_name, names):
    class Fake(Crawler):
        site = site_name
        condition_class = FakeCond
        job_filter_field = "jobs"
        fetched = 0

        def fetch_page(self, condition, page):
            return Page([], is_last=True)

        def fetch_job_options(self):
            type(self).fetched += 1
            return [JobOption(str(i), n, "개발") for i, n in enumerate(names)]

    return Fake


@pytest.fixture
def two_sites(tmp_path, monkeypatch):
    a = fake_crawler("site_a", ["백엔드/서버개발", "웹개발", "웹마스터"])
    b = fake_crawler("site_b", ["데이터 엔지니어"])
    monkeypatch.setitem(filters.CRAWLERS, "site_a", a)
    monkeypatch.setitem(filters.CRAWLERS, "site_b", b)
    config = make_config(tmp_path, {
        "site_a": [{"name": "a-dev", "max_pages": 7, "condition": {"jobs": ["old"], "locations": ["seoul"]}}],
        "site_b": [{"name": "b-dev", "condition": {"jobs": ["keep"]}}],
    }, keywords=["서버 개발", "웹개발"])
    return config, a, b


def test_generate_updates_job_field_only_and_keeps_other_values(two_sites):
    config, a, b = two_sites
    results = {r.site: r for r in filters.generate(config, filters.load_keywords(config))}

    assert results["site_a"].status == "updated"
    data = yaml.safe_load(config.site_file("site_a").read_text(encoding="utf-8"))
    search = data["searches"][0]
    assert search["condition"] == {"jobs": ["0", "1"], "locations": ["seoul"]}
    assert search["max_pages"] == 7 and search["name"] == "a-dev"
    assert data["generated"]["keywords"] == ["서버 개발", "웹개발"]
    assert config.load_searches("site_a")[0].condition.jobs == ["0", "1"]

    # 맞는 직무가 없는 사이트는 그대로 둔다 (빈 필터 = 전체 수집 방지)
    assert results["site_b"].status == "no_match"
    assert config.load_searches("site_b")[0].condition.jobs == ["keep"]

    # 매핑 데이터는 저장해 두고 다시 쓴다
    assert filters.load_catalog(config, "site_a")["options"][0]["terms"] == ["백엔드", "서버"]
    assert filters.generate(config, ["서버 개발", "웹개발"])[0].status == "unchanged"
    assert filters.generate(config, ["웹개발", "서버 개발"])[0].status == "unchanged"  # 순서만 다름 (OR 조건)
    assert a.fetched == 1
    filters.generate(config, ["웹개발"], refresh=True)
    assert a.fetched == 2


def test_generate_writes_fixed_condition(two_sites):
    config, a, b = two_sites
    a.fixed_condition = {"kinds": ["hh"]}
    a.fixed_condition_label = "헤드헌팅만"
    a.condition_class = KindCond
    results = {r.site: r for r in filters.generate(config, filters.load_keywords(config))}
    assert results["site_a"].status == "updated"
    data = yaml.safe_load(config.site_file("site_a").read_text(encoding="utf-8"))
    assert data["searches"][0]["condition"]["kinds"] == ["hh"]
    # 손으로 바꿔도 다음 생성 때 고정값으로 되돌린다
    text = config.site_file("site_a").read_text(encoding="utf-8").replace("- hh", "- other")
    config.site_file("site_a").write_text(text, encoding="utf-8")
    assert filters.generate(config, filters.load_keywords(config))[0].status == "updated"
    assert yaml.safe_load(config.site_file("site_a").read_text(encoding="utf-8"))["searches"][0]["condition"]["kinds"] == ["hh"]


def test_generate_continues_when_one_site_fails(two_sites, monkeypatch):
    config, a, b = two_sites

    def boom(self):
        raise RuntimeError("blocked")

    monkeypatch.setattr(b, "fetch_job_options", boom)
    results = {r.site: r for r in filters.generate(config, ["웹개발"])}
    assert results["site_b"].status == "error" and "blocked" in results["site_b"].message
    assert results["site_a"].status == "updated"


def test_keywords_add_remove(tmp_path):
    config = make_config(tmp_path, {}, keywords=["Java"])
    assert filters.add_keyword(config, "  백엔드   개발 ") == ["Java", "백엔드 개발"]
    with pytest.raises(ValueError):
        filters.add_keyword(config, "java")  # 대소문자만 다른 중복
    with pytest.raises(ValueError):
        filters.add_keyword(config, "   ")
    assert filters.remove_keyword(config, "Java") == ["백엔드 개발"]
    assert filters.load_keywords(config) == ["백엔드 개발"]


def test_settings_page_and_forms(two_sites, tmp_path):
    config, a, b = two_sites
    client = TestClient(create_app(make_session_factory(f"sqlite:///{tmp_path / 's.db'}"), config))

    page = client.get("/settings")
    assert page.status_code == 200
    assert "서버 개발" in page.text and "a-dev" in page.text and "최대 7페이지" in page.text

    res = client.post("/settings/keywords", data={"keyword": "풀스택"}, follow_redirects=False)
    assert res.status_code == 303 and "err" not in res.headers["location"]
    assert filters.load_keywords(config) == ["서버 개발", "웹개발", "풀스택"]
    res = client.post("/settings/keywords", data={"keyword": "풀스택"}, follow_redirects=False)
    assert "err=1" in res.headers["location"]

    client.post("/settings/keywords/delete", data={"keyword": "풀스택"})
    assert filters.load_keywords(config) == ["서버 개발", "웹개발"]

    page = client.post("/settings/generate", data={})
    assert page.status_code == 200 and "필터를 생성했습니다" in page.text
    assert "반영됨" in page.text and "매칭 없음" in page.text
    assert "백엔드/서버개발 (0)" in page.text  # 직무 값은 매핑 데이터의 이름과 함께 표시


def test_keywords_and_generate_locked_while_generating(two_sites, tmp_path, monkeypatch):
    """필터 생성이 끝나기 전에는 다른 생성·키워드 등록·삭제 요청을 받지 않고, 화면은 로딩 상태로 보인다."""
    import threading

    config, a, b = two_sites
    started, release = threading.Event(), threading.Event()
    real_generate = filters.generate

    def slow_generate(*args, **kwargs):
        started.set()
        release.wait(5)
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(filters, "generate", slow_generate)
    client = TestClient(create_app(make_session_factory(f"sqlite:///{tmp_path / 'l.db'}"), config))

    worker = threading.Thread(target=lambda: client.post("/settings/generate", data={}))
    worker.start()
    try:
        assert started.wait(5)
        page = client.get("/settings").text
        assert "생성 중" in page and 'aria-busy="true"' in page
        for url, data in [("/settings/keywords", {"keyword": "풀스택"}),
                          ("/settings/keywords/delete", {"keyword": "웹개발"}),
                          ("/settings/generate", {})]:
            res = client.post(url, data=data, follow_redirects=False)
            assert "err=1" in res.headers["location"]
        assert filters.load_keywords(config) == ["서버 개발", "웹개발"]
    finally:
        release.set()
        worker.join(5)

    page = client.get("/settings").text
    assert 'aria-busy="false"' in page and "반영됨" in page
    assert client.post("/settings/keywords", data={"keyword": "풀스택"}, follow_redirects=False).headers["location"].count("err") == 0

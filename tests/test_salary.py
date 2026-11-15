import pytest
from fastapi.testclient import TestClient

from recruit_collector import salary
from recruit_collector.crawlers.base import CrawlResult, ParseError, Posting
from recruit_collector.models import make_session_factory
from recruit_collector.sync import sync_search
from recruit_collector.salary_sources import SalarySources
from recruit_collector.web.app import create_app
from tests.conftest import make_config


def search_html(rows, next_page=False):
    trs = "".join(
        f'<tr>\n<td>\n<a href="/c/{cid}/slug">{name}</a>\n</td>\n<td>\n    {addr}\n</td>\n</tr>' for cid, name, addr in rows
    )
    nxt = '<a class="page-link" href="https://allthatcompany.com/search?q=x&amp;page=2" rel="next" aria-label="Next">' if next_page else ""
    return f'<table class="table" id="search-table"><thead><tr><th>회사명</th><th>주소</th></tr></thead>{trs}</table>{nxt}'


COMPANY_HTML = """
<h1>넷마블 주식회사</h1>
<ul><li>주소 : <em>서울특별시 구로구 디지털로26길</em></li><li>업종 : <em>그 외  기타 서비스업</em></li></ul>
<table class="table table-bordered">
  <tr><th>기준년월</th><th>월평균급여(추정)</th><th>입사자</th><th>퇴사자</th><th>총인원</th></tr>
  <tr><th> 201804 </th><td> 4,654,736원 </td><td> 15명 </td><td> 11명 </td><td> 725명 </td></tr>
  <tr><th> 201701 </th><td> 0원 </td><td> 30명 </td><td> 10명 </td><td> 644명 </td></tr>
</table>
"""


def test_normalize_name_and_query():
    assert salary.normalize_name("(주) 넷마블") == salary.normalize_name("넷마블 주식회사") == "넷마블"
    assert salary.normalize_name("㈜ABC") == "abc"
    assert salary.normalize_name("주식회사 카카오스타일 (Kakao Style Corp.)") == salary.normalize_name("(주)카카오스타일")
    assert salary.search_query("메가존클라우드㈜") == "메가존클라우드"


def test_posting_regions_and_address_match():
    regions = salary.posting_regions("경기 과천시, 서울 관악구", None)
    assert regions == [("경기", "과천시"), ("서울", "관악구")]
    assert salary.address_matches(regions, "경기도 과천시 과천대로7길")
    assert not salary.address_matches(regions, "경기도 부천시 길주로")
    # 리멤버는 원본의 근무지 전체를 본다 (목록 표시는 '외 N')
    raw = {"addresses": [{"address_level1": "경기도", "address_level2": "안양시 동안구"}]}
    assert salary.address_matches(salary.posting_regions("서울 광진구 외 1", raw), "경기도 안양시 동안구 시민대로")
    assert salary.posting_regions("서울 전체", None) == [("서울",)]


def test_parse_search_and_company():
    rows, has_next = salary.parse_search(search_html([("1", "넷마블체리(주)", "서울특별시 구로구 디지털로")], next_page=True))
    assert [(r.id, r.name, r.address) for r in rows] == [("1", "넷마블체리(주)", "서울특별시 구로구 디지털로")]
    assert has_next
    with pytest.raises(ParseError):
        salary.parse_search("<html>점검 중</html>")

    c = salary.parse_company(COMPANY_HTML, "393834")
    assert (c.name, c.address, c.industry) == ("넷마블 주식회사", "서울특별시 구로구 디지털로26길", "그 외 기타 서비스업")
    assert [(m.ym, m.salary, m.hires, m.leaves, m.headcount) for m in c.months] == [
        ("2017-01", None, 30, 10, 644),  # 0원은 자료 없음
        ("2018-04", 4654736, 15, 11, 725),
    ]


def candidates():
    return [
        salary.Candidate("1", "넷마블체리(주)", "서울특별시 구로구 디지털로"),
        salary.Candidate("2", "넷마블(주)", "경기도 성남시 분당구"),
        salary.Candidate("3", "넷마블 주식회사", "서울특별시 구로구 디지털로26길"),
    ]


def test_choose_prefers_name_and_address_then_name():
    chosen, match = salary.choose(candidates(), "(주)넷마블", [("서울", "구로구")])
    assert (chosen.id, match) == ("3", "name_address")
    chosen, match = salary.choose(candidates(), "(주)넷마블", [("부산", "해운대구")])
    assert (chosen.id, match) == ("2", "name")  # 주소가 같은 회사가 없으면 회사명이 같은 첫 회사
    assert salary.choose(candidates(), "카카오", [("서울",)]) == (None, None)


class FakeHttp:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get_text(self, url, params=None):
        self.calls.append((url, params))
        if url == salary.SEARCH_URL:
            return self.pages[(params or {}).get("page", 1)]
        return COMPANY_HTML


def test_lookup_reads_next_page_only_until_name_and_address_match():
    http = FakeHttp({
        1: search_html([("2", "넷마블(주)", "경기도 성남시 분당구")], next_page=True),
        2: search_html([("3", "넷마블 주식회사", "서울특별시 구로구 디지털로26길")]),
    })
    data = salary.lookup(http, "(주)넷마블", "서울 구로구", None)
    assert data["match"] == "name_address" and data["company"]["name"] == "넷마블 주식회사"
    assert [c["id"] for c in data["candidates"]] == ["2", "3"]
    assert http.calls[0] == (salary.SEARCH_URL, {"q": "넷마블"})
    assert http.calls[-1][0] == salary.COMPANY_URL.format("3")

    # 직접 고른 회사는 검색 없이 가져온다
    http = FakeHttp({})
    assert salary.lookup(http, "(주)넷마블", None, None, company_id="9")["match"] == "manual"
    assert http.calls == [(salary.COMPANY_URL.format("9"), None)]


def test_salary_api(tmp_path, monkeypatch):
    factory = make_session_factory(f"sqlite:///{tmp_path / 's.db'}")
    with factory() as s:
        p = Posting(external_id="1", title="백엔드", company="(주)넷마블", detail_url="https://example.com/1", location="서울 구로구")
        sync_search(s, site="saramin", search_name="x", result=CrawlResult([p], 1, True), missing_threshold=1)
        s.commit()
    seen = {}

    def fake_posting(self, company, location, raw, cid=None):
        seen.update(company=company, location=location, company_id=cid)
        return {"query": "넷마블", "match": "name", "candidates": [], "company": None}

    monkeypatch.setattr(SalarySources, "posting", fake_posting)
    client = TestClient(create_app(factory, make_config(tmp_path, {})))
    res = client.get("/api/postings/1/salary", params={"cid": "42"}).json()
    assert res["ok"] and seen == {"company": "(주)넷마블", "location": "서울 구로구", "company_id": "42"}
    assert client.get("/api/postings/1/salary", params={"cid": "../x"}).status_code == 400
    assert client.get("/api/postings/999/salary").status_code == 404

    def boom(*a, **k):
        raise RuntimeError("요청 실패 (HTTP 503)")

    monkeypatch.setattr(SalarySources, "posting", boom)
    res = client.get("/api/postings/1/salary")
    assert res.status_code == 502 and not res.json()["ok"]

    html = client.get("/").text
    assert 'class="salary-btn" data-posting="1"' in html and 'id="salary-modal"' in html


def test_salary_menu_pages_and_api(tmp_path, monkeypatch):
    factory = make_session_factory(f"sqlite:///{tmp_path / 's.db'}")
    client = TestClient(create_app(factory, make_config(tmp_path, {})))
    pages = {
        1: search_html([("1", "넷마블체리(주)", "서울특별시 구로구 디지털로")], next_page=True),
        2: search_html([("3", "넷마블 주식회사", "서울특별시 구로구 디지털로26길")]),
    }
    http = FakeHttp(pages)
    monkeypatch.setattr("recruit_collector.salary_sources.HttpClient", lambda cfg: type("H", (), {
        "get_text": staticmethod(http.get_text), "close": staticmethod(lambda: None)})())

    # 메뉴: 공고 바로 오른쪽
    html = client.get("/salary").text
    assert html.index('href="/"') < html.index('href="/salary" aria-current="page"') < html.index('href="/runs"')
    assert 'class="salary-search"' in html and "salary_page.js" in html

    res = client.get("/api/salary/search", params={"q": " (주)넷마블 "}).json()
    assert res == {"ok": True, "query": "넷마블", "page": 1, "has_next": True,
                   "candidates": [{"id": "1", "name": "넷마블체리(주)", "address": "서울특별시 구로구 디지털로",
                                   "favorite": None}]}  # 즐겨찾기는 로그인 사용 중에만 (null이면 별 없음)
    assert client.get("/api/salary/search", params={"q": "넷마블", "page": 2}).json()["candidates"][0]["id"] == "3"
    assert client.get("/api/salary/search", params={"q": "  "}).status_code == 400

    res = client.get("/api/salary/company/393834").json()
    assert res["ok"] and res["match"] is None and res["company"]["name"] == "넷마블 주식회사"
    assert client.get("/api/salary/company/x1").status_code == 400
    assert client.get("/api/salary/company/n12a").status_code == 400

    html = client.get("/salary/c/393834").text
    assert 'data-salary-cid="393834"' in html and 'href="/salary" aria-current="page"' in html
    assert client.get("/salary/c/abc").status_code == 404


def test_salary_compare_page(tmp_path):
    client = TestClient(create_app(make_session_factory(f"sqlite:///{tmp_path / 's.db'}"), make_config(tmp_path, {})))
    # 2~4곳 (?ids=), 예전 두 회사 링크(?a=&b=)도 연다
    html = client.get("/salary/compare", params={"ids": "393834,33810,1,2"}).text
    assert 'data-ids="393834,33810,1,2" data-max="4"' in html and "salary_compare.js" in html
    assert 'href="/salary" aria-current="page"' in html
    assert 'data-ids="393834,33810"' in client.get("/salary/compare", params={"a": "393834", "b": "33810"}).text
    for bad in ({"a": "1", "b": "1"}, {"a": "1"}, {"a": "x", "b": "2"},  # 같은 회사·빠짐·잘못된 ID는 검색으로
                {"ids": "1"}, {"ids": "1,2,3,4,5"}, {"ids": "1,2,1"}, {"ids": "1,,2"}, {"ids": "1,x"}):
        res = client.get("/salary/compare", params=bad, follow_redirects=False)
        assert res.status_code == 303 and res.headers["location"] == "/salary", bad
    assert 'class="compare-tray" data-max="4"' in client.get("/salary").text
    assert 'class="compare-start"' in client.get("/salary/c/1").text

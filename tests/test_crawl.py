from dataclasses import dataclass
from pathlib import Path

from recruit_collector.crawlers.base import Condition, Crawler, Page, Posting


@dataclass
class FakeCondition(Condition):
    pass


class FakeCrawler(Crawler):
    site = "fake"
    condition_class = FakeCondition

    def __init__(self, pages: list[Page | Exception]):
        super().__init__(http=None)
        self.pages = pages

    def fetch_page(self, condition, page):
        result = self.pages[page - 1]
        if isinstance(result, Exception):
            raise result
        return result


def p(pid: str) -> Posting:
    return Posting(external_id=pid, title="t", company="c", detail_url="u")


def test_complete_when_last_page_reached():
    result = FakeCrawler([Page([p("1"), p("2")], False, total=3), Page([p("2"), p("3")], True, total=3)]).crawl(FakeCondition(), 10)
    assert result.complete
    assert sorted(x.external_id for x in result.postings) == ["1", "2", "3"]  # 페이지 밀림 중복 제거


def test_incomplete_when_max_pages_reached():
    result = FakeCrawler([Page([p("1")], False), Page([p("2")], True)]).crawl(FakeCondition(), 1)
    assert not result.complete and result.pages == 1


def test_incomplete_on_error_keeps_collected():
    result = FakeCrawler([Page([p("1")], False), RuntimeError("blocked")]).crawl(FakeCondition(), 10)
    assert not result.complete
    assert [x.external_id for x in result.postings] == ["1"]
    assert "blocked" in result.error


def test_incomplete_when_total_changes_between_pages():
    result = FakeCrawler([Page([p("1")], False, total=119), Page([p("2")], True, total=196)]).crawl(FakeCondition(), 10)
    assert not result.complete


def test_small_total_drift_during_long_crawl_is_tolerated():
    result = FakeCrawler([Page([p("1")], False, total=1919), Page([p("2")], True, total=1931)]).crawl(FakeCondition(), 10)
    assert result.complete



class RecordingHttp:
    """사람인 크롤러가 보낸 검색 파라미터를 기록하고 빈 결과 페이지를 돌려준다."""

    def __init__(self, html):
        self.html, self.params = html, []

    def get_text(self, url, params=None, headers=None):
        self.params.append(params)
        return self.html


def saramin_html():
    return (Path(__file__).parent / "fixtures" / "saramin_list.html").read_text(encoding="utf-8")


def test_saramin_excludes_headhunting_and_hh_crawler_only_headhunting():
    from recruit_collector.crawlers import platform_of
    from recruit_collector.crawlers.saramin import SaraminCondition, SaraminCrawler, SaraminHeadhuntingCrawler

    for cls, cd, hh in ((SaraminCrawler, "0,1,2,3,4,5,6,7,9", False), (SaraminHeadhuntingCrawler, "10", True)):
        http = RecordingHttp(saramin_html())
        page = cls(http).fetch_page(SaraminCondition(keyword="java"), 1)
        assert http.params[0]["company_cd"] == cd and http.params[0]["searchword"] == "java"
        assert page.postings and all(x.headhunting is hh for x in page.postings)
    assert platform_of("saramin_hh") == "saramin" and platform_of("saramin") == "saramin" and platform_of("x") == "x"


def test_saramin_company_types_are_fixed_by_crawler():
    from recruit_collector.crawlers.saramin import SaraminCondition, SaraminHeadhuntingCrawler

    http = RecordingHttp(saramin_html())  # 파일에서 기업 형태를 바꿔도 크롤러 고정값으로 수집
    SaraminHeadhuntingCrawler(http).fetch_page(SaraminCondition(company_types=["0"], params={"company_cd": "0", "x": "1"}), 1)
    assert http.params[0]["company_cd"] == "10" and http.params[0]["x"] == "1"


def test_request_spacing_is_shared_per_host():
    import time

    from recruit_collector.http import HttpClient, HttpSettings

    settings = HttpSettings(user_agent="t", delay_seconds=(0.3, 0.3), respect_robots=False)
    a, b = HttpClient(settings), HttpClient(settings)
    try:
        a._wait("shared.test")
        t = time.monotonic()
        b._wait("shared.test")  # 다른 클라이언트라도 같은 호스트면 기다린다
        assert time.monotonic() - t >= 0.25
        t = time.monotonic()
        b._wait("other.test")  # 다른 호스트는 바로
        assert time.monotonic() - t < 0.1
    finally:
        a.close(), b.close()

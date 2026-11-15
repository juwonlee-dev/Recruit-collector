import threading
import time
from dataclasses import dataclass

from sqlalchemy import func, select

from recruit_collector import runner
from recruit_collector.crawlers.base import Condition, Crawler, Page, Posting
from recruit_collector.models import CrawlRun, JobPosting, make_session_factory
from tests.conftest import make_config

active = 0
max_active = 0
lock = threading.Lock()


@dataclass
class Cond(Condition):
    prefix: str = ""


def make_crawler(site_name: str):
    class Fake(Crawler):
        site = site_name
        condition_class = Cond

        def fetch_page(self, condition, page):
            global active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.2)
            with lock:
                active -= 1
            items = [Posting(f"{condition.prefix}{page}-{i}", "t", "c", "u") for i in range(50)]
            return Page(items, is_last=page == 2)

    return Fake


def test_platform_crawler_saves_postings_under_platform(tmp_path, monkeypatch):
    hh = make_crawler("site_a_hh")
    hh.platform = "site_a"
    monkeypatch.setitem(runner.CRAWLERS, "site_a", make_crawler("site_a"))
    monkeypatch.setitem(runner.CRAWLERS, "site_a_hh", hh)
    factory = make_session_factory(f"sqlite:///{tmp_path / 'pl.db'}")
    config = make_config(tmp_path, {
        "site_a": [{"name": "a1", "max_pages": 5, "condition": {"prefix": "a-"}}],
        "site_a_hh": [{"name": "h1", "max_pages": 5, "condition": {"prefix": "h-"}}],
    })
    runner.run_all(config, factory)
    with factory() as s:
        assert set(s.scalars(select(JobPosting.site))) == {"site_a"}  # 공고는 플랫폼으로
        assert set(s.scalars(select(CrawlRun.site))) == {"site_a", "site_a_hh"}  # 수집 기록은 크롤러로


def test_sites_run_in_parallel_and_all_results_saved(tmp_path, monkeypatch):
    monkeypatch.setitem(runner.CRAWLERS, "site_a", make_crawler("site_a"))
    monkeypatch.setitem(runner.CRAWLERS, "site_b", make_crawler("site_b"))
    factory = make_session_factory(f"sqlite:///{tmp_path / 'p.db'}")
    config = make_config(tmp_path, {
        "site_a": [
            {"name": "a1", "max_pages": 5, "condition": {"prefix": "a1-"}},
            {"name": "a2", "max_pages": 5, "condition": {"prefix": "a2-"}},
        ],
        "site_b": [
            {"name": "b1", "max_pages": 5, "condition": {"prefix": "b1-"}},
            {"name": "b2", "max_pages": 5, "condition": {"prefix": "b2-"}},
        ],
    })

    runner.run_all(config, factory)

    assert max_active == 2  # 사이트 2개가 동시에, 같은 사이트 안에서는 순차로
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(JobPosting)) == 4 * 2 * 50
        assert set(s.scalars(select(CrawlRun.status))) == {"SUCCESS"}

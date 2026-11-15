"""검색 조건을 수집하고 DB에 반영한다.

자동 수집(수집기의 스케줄), CLI 1회 수집, 조회 화면의 수동 수집(수집기가 대기열에서 가져가 실행)이
모두 이 모듈의 같은 함수를 쓴다.
실행 기록(crawl_runs.trigger)에 어떤 경로로 시작됐는지 남긴다.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import sessionmaker

from recruit_collector.config import AppConfig, SearchConfig
from recruit_collector.crawlers import CRAWLERS, platform_of
from recruit_collector.http import HttpClient
from recruit_collector.models import CrawlRun, utcnow
from recruit_collector.sync import sync_search

log = logging.getLogger(__name__)

# 실행 경로 (crawl_runs.trigger)
TRIGGER_SCHEDULE = "schedule"
TRIGGER_CLI = "cli"
TRIGGER_MANUAL = "manual"

# SQLite는 동시에 하나의 쓰기만 허용하므로, 사이트별 스레드의 DB 반영 구간을 직렬화한다.
_db_write_lock = threading.Lock()
DB_WRITE_LOCK = _db_write_lock  # 수집기가 강제 종료할 때 더 반영하지 못하게 잡는다 (worker.py)


def run_all(
    config: AppConfig,
    session_factory: sessionmaker,
    only: set[str] | None = None,
    trigger: str = TRIGGER_SCHEDULE,
) -> None:
    """사이트별로 병렬 수집한다.

    서로 다른 사이트는 동시에 수집하고, 같은 사이트 안에서는 요청 간격을 지키며 순차로 수집한다.
    (같은 사이트에 동시 요청을 보내면 부하·차단 위험이 커지므로)
    검색 필터 파일은 시작할 때 한 번 읽어 메모리에 둔 것을 끝까지 쓴다.
    """
    by_site: dict[str, list[SearchConfig]] = defaultdict(list)
    for search in config.load_searches():
        if not only or search.name in only:
            by_site[search.site].append(search)
    if not by_site:
        return

    with ThreadPoolExecutor(max_workers=len(by_site), thread_name_prefix="crawl") as pool:
        futures = [
            pool.submit(run_site, searches, config, session_factory, trigger) for searches in by_site.values()
        ]
        for f in futures:
            f.result()


def run_site(
    searches: list[SearchConfig], config: AppConfig, session_factory: sessionmaker, trigger: str
) -> None:
    """한 사이트의 검색 조건들을 순차로 수집한다.

    searches는 호출한 쪽이 수집 시작 시점에 검색 필터 파일에서 읽어 둔 것이다. 수집 중에 파일이
    바뀌어도 이 목록은 바뀌지 않으므로 진행 중인 수집에는 영향이 없다.
    """
    http = HttpClient(config.http)  # 사이트(스레드)마다 별도 클라이언트 → 요청 간격도 사이트별로 적용
    try:
        for search in searches:
            try:
                run_search(search, http, session_factory, config.missing_threshold, trigger)
            except Exception:
                # 한 조건의 실패가 다른 조건 수집을 막지 않도록 한다
                log.exception("[%s] 처리 실패", search.name)
    finally:
        http.close()


def run_search(
    search: SearchConfig,
    http: HttpClient,
    session_factory: sessionmaker,
    missing_threshold: int,
    trigger: str = TRIGGER_SCHEDULE,
) -> CrawlRun:
    with session_factory() as session:
        with _db_write_lock:
            now = utcnow()
            run = CrawlRun(
                search_name=search.name, site=search.site, started_at=now, heartbeat_at=now,
                status="RUNNING", trigger=trigger,
            )
            session.add(run)
            session.commit()

        # 오래 걸리는 HTTP 수집은 잠금 밖에서 사이트별로 병렬 진행
        log.info("[%s] 수집 시작 (%s, %s)", search.name, search.site, trigger)

        def on_page(pages: int, found: int, total: int | None) -> None:
            # 조회 화면에서 진행 상황을 볼 수 있도록 페이지마다 기록. heartbeat로 중단 여부도 판단
            with _db_write_lock:
                run.pages, run.found, run.total, run.heartbeat_at = pages, found, total, utcnow()
                session.commit()

        crawler = CRAWLERS[search.site](http)
        result = crawler.crawl(search.condition, search.max_pages, on_page=on_page)

        with _db_write_lock:
            try:
                stats = sync_search(  # 공고는 플랫폼으로 저장 (사람인(헤드헌팅) → 사람인), 수집 기록은 크롤러 이름으로
                    session, site=platform_of(search.site), search_name=search.name, result=result,
                    missing_threshold=missing_threshold, run_id=run.id,
                )
            except Exception as e:
                session.rollback()
                run.status, run.error, run.finished_at = "FAILED", f"동기화 실패: {e}", utcnow()
                session.commit()
                raise

            run.pages = result.pages
            run.found, run.created, run.updated = stats.found, stats.created, stats.updated
            run.reactivated, run.deleted = stats.reactivated, stats.deleted
            run.error = result.error
            run.status = "SUCCESS" if result.complete else ("FAILED" if result.error and not result.postings else "PARTIAL")
            run.finished_at = run.heartbeat_at = utcnow()
            session.commit()

        log.info(
            "[%s] %s: %d페이지, 발견 %d, 신규 %d, 변경 %d, 재등장 %d, 삭제 %d",
            search.name, run.status, run.pages, run.found, run.created, run.updated, run.reactivated, run.deleted,
        )
        return run

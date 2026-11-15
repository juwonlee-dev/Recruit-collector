"""수집기(worker)가 사이트(크롤러 모듈)별 수집을 백그라운드 스레드로 실행한다.

자동 수집·수동 수집 모두 runner.run_site를 쓴다. 실행 기록의 trigger는 조회 화면의 '지금 수집'
요청이면 manual, 스케줄러(web/scheduler.py)가 시작하면 schedule이다.
같은 사이트가 이미 수집 중이면 새로 시작하지 않는다.
  - 이 프로세스에서 시작한 수집: 스레드가 살아 있는지로 판단
  - 다른 프로세스(CLI)의 수집: DB의 RUNNING 기록으로 판단 (중단된 기록은 제외)
"""

from __future__ import annotations

import logging
import threading
import time

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from recruit_collector import runner
from recruit_collector.config import AppConfig
from recruit_collector.crawl_queue import STALE_AFTER, AlreadyRunning, is_stale  # noqa: F401 (다른 모듈이 여기서 가져다 씀)
from recruit_collector.models import CrawlRun, utcnow

log = logging.getLogger(__name__)

CANCELED = "CANCELED"


def cancel_interrupted_runs(session_factory: sessionmaker, reason: str = "수집기가 다시 시작되어 수집이 중단됨") -> int:
    """RUNNING으로 남은 실행 기록을 CANCELED로 정리하고, 정리한 수를 돌려준다. 수집기가 뜰 때와 강제 종료할 때 부른다.

    - 자동(schedule)·수동(manual) 수집은 수집기 안에서 돌므로, 수집기가 다시 뜨거나 강제 종료하면 끝난 것이다.
      (수집기는 하나만 띄운다는 전제)
    - CLI 수집은 다른 프로세스일 수 있으므로 진행 기록이 끊긴(STALE) 것만 정리한다.
    끝난 시각은 마지막 진행 기록 시각으로 둔다 (중단될 때까지 걸린 시간이 보이도록).
    """
    now = utcnow()
    with session_factory() as s:
        canceled = []
        for run in s.scalars(select(CrawlRun).where(CrawlRun.status == "RUNNING")):
            if run.trigger in (runner.TRIGGER_SCHEDULE, runner.TRIGGER_MANUAL):
                run.error = reason
            elif is_stale(run, now):
                run.error = "진행 기록이 끊겨 수집이 중단됨"
            else:
                continue
            run.status, run.finished_at = CANCELED, run.heartbeat_at or run.started_at
            canceled.append(run.id)
        s.commit()
    if canceled:
        log.warning("중단된 수집 기록을 CANCELED로 정리: %s (%s)", canceled, reason)
    return len(canceled)


class CrawlLauncher:
    def __init__(self, config: AppConfig, session_factory: sessionmaker):
        self.config = config
        self.session_factory = session_factory
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    @property
    def sites(self) -> list[str]:
        return self.config.sites()

    def is_running_here(self, site: str) -> bool:
        t = self._threads.get(site)
        return bool(t and t.is_alive())

    def running(self) -> list[str]:
        """이 프로세스에서 수집 중인 사이트."""
        return [site for site, t in self._threads.items() if t.is_alive()]

    def wait(self, timeout: float) -> bool:
        """수집 중인 스레드가 모두 끝날 때까지 최대 timeout초 기다린다. 다 끝났으면 True."""
        deadline = time.monotonic() + timeout
        for t in list(self._threads.values()):
            t.join(max(0.0, deadline - time.monotonic()))
        return not self.running()

    def start(self, site: str, trigger: str = runner.TRIGGER_MANUAL) -> None:
        """사이트의 모든 검색 조건 수집을 백그라운드로 시작한다.

        검색 필터 파일은 지금 읽어 메모리에 올린 것을 수집 끝까지 쓴다.
        KeyError: 설정에 없는 사이트 / AlreadyRunning: 이미 수집 중
        """
        if site not in self.config.sites():
            raise KeyError(site)
        searches = self.config.load_searches(site)
        if not searches:
            raise KeyError(site)
        with self._lock:
            if self.is_running_here(site) or self._running_elsewhere([s.name for s in searches]):
                raise AlreadyRunning(site)
            thread = threading.Thread(
                target=self._run, args=(site, searches, trigger), name=f"{trigger}-crawl-{site}", daemon=True
            )
            self._threads[site] = thread
            thread.start()
        log.info("[%s] 수집 시작 (%s)", site, trigger)

    def _run(self, site, searches, trigger) -> None:
        try:
            runner.run_site(searches, self.config, self.session_factory, trigger)
        except Exception:
            log.exception("[%s] 수집 실패 (%s)", site, trigger)

    def _running_elsewhere(self, names: list[str]) -> bool:
        with self.session_factory() as s:
            latest_ids = select(func.max(CrawlRun.id)).where(CrawlRun.search_name.in_(names)).group_by(CrawlRun.search_name)
            runs = s.scalars(select(CrawlRun).where(CrawlRun.id.in_(latest_ids))).all()
        now = utcnow()
        return any(r.status == "RUNNING" and not is_stale(r, now) for r in runs)

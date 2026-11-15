"""수집기(worker): 자동 수집 스케줄러와 '지금 수집' 대기열을 처리하는 별도 프로세스(도커 컨테이너).

조회 화면(web)과는 DB로만 주고받는다(recruit_collector.crawl_queue). 그래서 조회 화면을 다시 배포해도
진행 중인 수집에는 영향이 없다.

- 시작할 때: 이전 수집기에서 하다 끊긴 수집 기록을 CANCELED로 정리한다.
- 2초마다: 설정 > 스케줄러의 변경을 반영하고, 대기 중인 '지금 수집' 요청을 가져가 실행한다.
- 10초마다: 살아 있다는 신호(app_settings.worker_heartbeat)를 남긴다.
- 종료:
  - SIGTERM(docker compose stop / up으로 재배포): 새 수집은 받지 않고, 진행 중인 수집이 끝날 때까지
    최대 stop_timeout초 기다린다. 그래도 남아 있으면 CANCELED로 정리하고 끝낸다.
  - SIGINT(docker compose kill -s SIGINT worker): 진행 중인 수집을 바로 CANCELED로 정리하고 끝낸다.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from recruit_collector import crawl_queue, runner
from recruit_collector.config import AppConfig
from recruit_collector.models import CrawlRequest, utcnow
from recruit_collector.web import scheduler as sched_mod
from recruit_collector.web.auth import set_setting
from recruit_collector.web.launcher import AlreadyRunning, CrawlLauncher, cancel_interrupted_runs

log = logging.getLogger(__name__)

POLL_SECONDS = 2.0
# docker-compose.yml의 stop_grace_period(10분)보다 짧게: 기다려도 안 끝나면 도커가 강제로 죽이기 전에 정리한다
DEFAULT_STOP_TIMEOUT = 570.0
HEARTBEAT_FILE = os.environ.get("WORKER_HEARTBEAT_FILE")


def _hard_exit(code: int) -> None:
    logging.shutdown()
    os._exit(code)


class Worker:
    def __init__(self, config: AppConfig, session_factory: sessionmaker):
        self.config = config
        self.session_factory = session_factory
        self.launcher = CrawlLauncher(config, session_factory)
        self.scheduler = sched_mod.CrawlScheduler(self.launcher, session_factory, config.timezone, config.cron)
        self._stop = threading.Event()
        self._force = False
        self._last_beat = 0.0
        self._exit = _hard_exit  # 강제 종료 (테스트에서 바꿔 끼운다)

    # ---------------------------------------------------------- 한 번씩 하는 일 (테스트에서도 직접 부른다)
    def startup(self) -> None:
        cancel_interrupted_runs(self.session_factory)
        self.scheduler.sync()
        self.heartbeat(force=True)

    def heartbeat(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_beat < crawl_queue.HEARTBEAT_INTERVAL.total_seconds():
            return
        self._last_beat = now
        with self.session_factory() as s:
            set_setting(s, crawl_queue.HEARTBEAT_KEY, utcnow().isoformat())
            s.commit()
        if HEARTBEAT_FILE:  # 도커 healthcheck용: 파일 수정 시각으로 살아 있는지 본다
            Path(HEARTBEAT_FILE).touch()

    def process_requests(self) -> list[tuple[str, str]]:
        """대기 중인 '지금 수집' 요청을 오래된 순서로 가져가 시작한다. [(사이트, 처리 상태)]"""
        handled = []
        with self.session_factory() as s:
            pending = s.scalars(
                select(CrawlRequest).where(CrawlRequest.status == crawl_queue.PENDING).order_by(CrawlRequest.id)
            ).all()
            for req in pending:
                try:
                    self.launcher.start(req.site, trigger=runner.TRIGGER_MANUAL)
                    status, message = crawl_queue.STARTED, None
                except AlreadyRunning:
                    status, message = crawl_queue.SKIPPED, "이미 수집 중이라 건너뜀"
                except KeyError:
                    status, message = crawl_queue.SKIPPED, "설정에 없는 사이트라 건너뜀"
                except Exception as e:  # 검색 필터 파일 오류 등: 요청을 계속 붙잡고 있지 않도록 처리하고 남긴다
                    log.exception("[%s] 수동 수집을 시작하지 못함", req.site)
                    status, message = crawl_queue.SKIPPED, f"시작하지 못함: {e}"
                if crawl_queue.claim(s, req.id, status, message):
                    handled.append((req.site, status))
                    log.info("[%s] 수동 수집 요청 #%d: %s%s", req.site, req.id, status, f" ({message})" if message else "")
        return handled

    def tick(self) -> None:
        self.scheduler.sync_if_changed()
        self.process_requests()
        self.heartbeat()

    # ---------------------------------------------------------- 종료
    def request_stop(self, force: bool = False) -> None:
        self._force = self._force or force
        self._stop.set()

    def shutdown(self, stop_timeout: float) -> None:
        """새 수집을 멈추고, 진행 중인 수집을 기다리거나(기본) 바로 취소한다(force)."""
        self.scheduler.shutdown()
        running = self.launcher.running()
        if running and not self._force:
            log.info("종료 대기: 진행 중인 수집이 끝날 때까지 최대 %d초 기다림 (%s)", stop_timeout, ", ".join(running))
            deadline = time.monotonic() + stop_timeout
            while self.launcher.running() and not self._force and time.monotonic() < deadline:
                self.heartbeat()
                self.launcher.wait(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        if self.launcher.running():
            reason = "수집기를 강제로 종료하여 중단됨" if self._force else "수집기 종료 대기 시간이 지나 중단됨"
            # 수집 스레드가 결과를 반영하는 중이면 끝날 때까지 기다렸다가, 더는 반영하지 못하게 잠그고 정리한다
            runner.DB_WRITE_LOCK.acquire()
            cancel_interrupted_runs(self.session_factory, reason)
            log.warning("진행 중인 수집을 취소하고 종료: %s", ", ".join(self.launcher.running()))
            self._exit(0)  # 수집 스레드를 기다리지 않고 바로 끝낸다
        log.info("수집기 종료")

    def run(self, stop_timeout: float = DEFAULT_STOP_TIMEOUT) -> None:
        self.startup()
        log.info("수집기 시작 (대기열 확인 %.0f초 간격)", POLL_SECONDS)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # DB가 잠시 끊겨도 수집기는 계속 돈다
                log.exception("수집기 처리 중 오류")
            self._stop.wait(POLL_SECONDS)
        self.shutdown(stop_timeout)


def run_worker(config: AppConfig, session_factory: sessionmaker, stop_timeout: float = DEFAULT_STOP_TIMEOUT) -> None:
    worker = Worker(config, session_factory)

    def on_term(*_):
        if worker._stop.is_set():  # 기다리는 중에 한 번 더 보내면 바로 취소
            worker.request_stop(force=True)
        else:
            log.info("종료 신호(SIGTERM): 진행 중인 수집을 마친 뒤 종료합니다")
            worker.request_stop()

    def on_int(*_):
        log.warning("강제 종료 신호(SIGINT): 진행 중인 수집을 바로 취소합니다")
        worker.request_stop(force=True)

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_int)
    worker.run(stop_timeout)

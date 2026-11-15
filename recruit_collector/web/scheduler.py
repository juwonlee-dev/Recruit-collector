"""자동 수집 스케줄. 설정 > 스케줄러 탭에서 시작·중지하고 주기(cron)를 바꾼다.

- 켜짐 여부와 주기는 DB(app_settings)에 둔다. 조회 화면(web)은 설정만 바꾸고(load/save_* 함수),
  실제 스케줄러(CrawlScheduler)는 수집기(worker) 안에서 돌며 설정이 바뀌면 바로 다시 맞춘다.
  처음 주기는 config.yaml의 schedule.cron.
- 정해진 시각마다 사이트별 수집을 '지금 수집'과 같은 방식(CrawlLauncher)으로 시작한다(trigger=schedule).
  이미 수집 중인 사이트는 건너뛴다.
- 수집 대상 사이트에 부담을 주지 않도록 주기는 30분 이상 간격만 허용한다.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import sessionmaker

from recruit_collector import runner
from recruit_collector.web.auth import get_setting, set_setting
from recruit_collector.web.launcher import AlreadyRunning, CrawlLauncher

log = logging.getLogger(__name__)

ENABLED_KEY = "schedule_enabled"
CRON_KEY = "schedule_cron"
JOB_ID = "collect"
MIN_INTERVAL = timedelta(minutes=30)

# 설정 화면의 주기 선택지 (cron, 설명). 이 밖의 주기는 cron으로 직접 입력한다
PRESETS = [
    ("*/30 * * * *", "30분마다"),
    ("0 * * * *", "1시간마다 (매시 정각)"),
    ("0 */2 * * *", "2시간마다"),
    ("0 */3 * * *", "3시간마다"),
    ("0 */6 * * *", "6시간마다"),
    ("0 */12 * * *", "12시간마다"),
    ("0 9 * * *", "매일 오전 9시"),
    ("0 9,18 * * *", "매일 오전 9시·오후 6시"),
]


def normalize_cron(expr: str) -> str:
    return " ".join((expr or "").split())


def next_times(trigger: CronTrigger, count: int, now: datetime | None = None) -> list[datetime]:
    """앞으로 실행될 시각 count개."""
    times: list[datetime] = []
    prev = None
    current = now or datetime.now(trigger.timezone)
    for _ in range(count):
        t = trigger.get_next_fire_time(prev, current)
        if t is None:
            break
        times.append(t)
        prev, current = t, t + timedelta(seconds=1)
    return times


def parse_cron(expr: str, timezone: str) -> CronTrigger:
    """cron 식(분 시 일 월 요일)을 검사한다. 잘못됐거나 간격이 30분보다 짧으면 ValueError(화면에 보여줄 문구)."""
    expr = normalize_cron(expr)
    if len(expr.split()) != 5:
        raise ValueError("cron은 '분 시 일 월 요일' 다섯 칸이어야 합니다. 예) 0 * * * * (매시 정각)")
    try:
        trigger = CronTrigger.from_crontab(expr, timezone=timezone)
    except ValueError as e:
        raise ValueError(f"cron 식이 올바르지 않습니다: {expr} ({e})") from e
    times = next_times(trigger, 50)
    if not times:
        raise ValueError(f"앞으로 실행될 시각이 없는 cron입니다: {expr}")
    if any(b - a < MIN_INTERVAL for a, b in zip(times, times[1:])):
        raise ValueError("수집 대상 사이트에 부담을 주지 않도록 수집 간격은 30분 이상이어야 합니다.")
    return trigger


def describe(expr: str) -> str:
    return next((label for cron, label in PRESETS if cron == expr), f"직접 입력 ({expr})")


# ---------------------------------------------------------- 설정 (DB). 조회 화면과 수집기가 함께 쓴다
def load_settings(session_factory: sessionmaker, default_cron: str) -> tuple[bool, str]:
    """(켜짐 여부, cron)"""
    with session_factory() as s:
        enabled = get_setting(s, ENABLED_KEY) == "1"
        cron = get_setting(s, CRON_KEY) or normalize_cron(default_cron)
    return enabled, cron


def _save(session_factory: sessionmaker, key: str, value: str) -> None:
    with session_factory() as s:
        set_setting(s, key, value)
        s.commit()


def save_enabled(session_factory: sessionmaker, enabled: bool, timezone: str, default_cron: str) -> None:
    if enabled:
        parse_cron(load_settings(session_factory, default_cron)[1], timezone)  # 저장된 주기가 잘못됐으면 켜지 않는다
    _save(session_factory, ENABLED_KEY, "1" if enabled else "0")


def save_cron(session_factory: sessionmaker, expr: str, timezone: str) -> str:
    expr = normalize_cron(expr)
    parse_cron(expr, timezone)
    _save(session_factory, CRON_KEY, expr)
    return expr


def planned_next_run(enabled: bool, cron: str, timezone: str) -> datetime | None:
    """설정대로라면 다음 자동 수집 시각 (꺼져 있거나 주기가 잘못됐으면 None)."""
    if not enabled:
        return None
    try:
        times = next_times(parse_cron(cron, timezone), 1)
    except ValueError:
        return None
    return times[0] if times else None


class CrawlScheduler:
    def __init__(self, launcher: CrawlLauncher, session_factory: sessionmaker, timezone: str, default_cron: str):
        self.launcher = launcher
        self.session_factory = session_factory
        self.timezone = timezone
        self.default_cron = normalize_cron(default_cron)
        self._scheduler: BackgroundScheduler | None = None
        self._lock = threading.Lock()
        self._applied: tuple[bool, str] | None = None  # 마지막으로 반영한 설정

    # ---------------------------------------------------------- 설정 (DB)
    def settings(self) -> tuple[bool, str]:
        """(켜짐 여부, cron)"""
        return load_settings(self.session_factory, self.default_cron)

    def set_enabled(self, enabled: bool) -> None:
        save_enabled(self.session_factory, enabled, self.timezone, self.default_cron)
        self.sync()

    def set_cron(self, expr: str) -> str:
        expr = save_cron(self.session_factory, expr, self.timezone)
        self.sync()
        return expr

    def sync_if_changed(self) -> bool:
        """DB 설정이 마지막으로 반영한 것과 다르면 다시 맞춘다 (조회 화면에서 바꾼 설정을 수집기가 따라감)."""
        if self.settings() == self._applied:
            return False
        self.sync()
        return True

    # ---------------------------------------------------------- 실행
    def sync(self) -> None:
        """DB 설정대로 작업을 등록하거나 뺀다. 서버 시작 때와 설정을 바꿀 때 부른다."""
        enabled, cron = self.settings()
        with self._lock:
            self._applied = (enabled, cron)
            if not enabled:
                if self._scheduler and self._scheduler.get_job(JOB_ID):
                    self._scheduler.remove_job(JOB_ID)
                    log.info("자동 수집 중지")
                return
            try:
                trigger = parse_cron(cron, self.timezone)
            except ValueError:
                log.exception("저장된 수집 주기가 올바르지 않아 자동 수집을 시작하지 않음: %s", cron)
                return
            if self._scheduler is None:
                self._scheduler = BackgroundScheduler(timezone=self.timezone)
                self._scheduler.start()
            # 수집은 스레드로 시작만 하고 바로 끝나므로 겹침은 런처가 사이트별로 막는다
            self._scheduler.add_job(self._tick, trigger, id=JOB_ID, replace_existing=True,
                                    max_instances=1, coalesce=True, misfire_grace_time=600)
            log.info("자동 수집 시작: cron='%s' (%s)", cron, self.timezone)

    def next_run_at(self) -> datetime | None:
        job = self._scheduler.get_job(JOB_ID) if self._scheduler else None
        return job.next_run_time if job else None

    def shutdown(self) -> None:
        with self._lock:
            if self._scheduler:
                self._scheduler.shutdown(wait=False)
                self._scheduler = None

    def _tick(self) -> None:
        """정해진 시각: 사이트별 수집 시작. 이미 수집 중이면 건너뛴다."""
        for site in self.launcher.sites:
            try:
                self.launcher.start(site, trigger=runner.TRIGGER_SCHEDULE)
            except AlreadyRunning:
                log.info("[%s] 이미 수집 중이라 이번 자동 수집은 건너뜀", site)
            except KeyError:
                pass  # 검색 조건이 없는 사이트
            except Exception:
                log.exception("[%s] 자동 수집을 시작하지 못함", site)

"""조회 화면(web)과 수집기(worker)가 DB로 주고받는 것: 수동 수집 대기열과 수집기 상태.

- 조회 화면은 '지금 수집'을 누르면 대기열(crawl_requests)에 요청을 넣기만 한다.
  수집기가 꺼져 있어도 요청은 남았다가 수집기가 뜨면 실행된다.
- 한 사이트에 대기 중인 요청은 하나뿐이다. 이미 대기 중이거나 수집 중이면 새 요청을 받지 않는다.
- 수집기는 일정 간격으로 살아 있다는 신호를 남긴다. 신호가 끊기면 조회 화면에 '수집기 꺼짐'을 보여준다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import exc, func, select
from sqlalchemy.orm import Session

from recruit_collector.models import CrawlRequest, CrawlRun, utcnow

PENDING, STARTED, SKIPPED = "PENDING", "STARTED", "SKIPPED"

HEARTBEAT_KEY = "worker_heartbeat"  # app_settings: 수집기가 마지막으로 살아 있음을 알린 시각 (UTC ISO)
HEARTBEAT_INTERVAL = timedelta(seconds=10)
WORKER_DOWN_AFTER = timedelta(seconds=60)  # 이 시간 동안 신호가 없으면 수집기가 꺼진 것으로 본다

# RUNNING인데 이 시간 동안 진행 기록(heartbeat)이 없으면 프로세스가 중단된 것으로 본다.
# 페이지 하나에 걸리는 최대 시간(타임아웃 20초 × 재시도 + 백오프)보다 충분히 길게.
STALE_AFTER = timedelta(minutes=10)


class AlreadyQueued(Exception):
    pass


class AlreadyRunning(Exception):
    pass


def is_stale(run: CrawlRun, now: datetime | None = None) -> bool:
    if run.status != "RUNNING":
        return False
    last = run.heartbeat_at or run.started_at
    return (now or utcnow()) - last > STALE_AFTER


def running_sites(s: Session, now: datetime | None = None) -> set[str]:
    """지금 수집 중인 사이트 (중단된 기록은 제외)."""
    now = now or utcnow()
    return {r.site for r in s.scalars(select(CrawlRun).where(CrawlRun.status == "RUNNING")) if not is_stale(r, now)}


def pending_sites(s: Session) -> set[str]:
    return set(s.scalars(select(CrawlRequest.site).where(CrawlRequest.status == PENDING)))


def request_crawl(s: Session, site: str, requested_by: str | None = None) -> CrawlRequest:
    """사이트 수집 요청을 대기열에 넣는다. AlreadyQueued: 이미 대기 중 / AlreadyRunning: 수집 중."""
    if site in pending_sites(s):
        raise AlreadyQueued(site)
    if site in running_sites(s):
        raise AlreadyRunning(site)
    req = CrawlRequest(site=site, status=PENDING, requested_by=requested_by, requested_at=utcnow())
    s.add(req)
    try:
        s.commit()
    except exc.IntegrityError:  # 동시에 두 번 누른 경우: 부분 유니크 인덱스가 하나만 받는다
        s.rollback()
        raise AlreadyQueued(site) from None
    return req


def claim(s: Session, req_id: int, status: str, message: str | None = None) -> bool:
    """대기 중인 요청을 처리 상태로 바꾼다. 다른 곳에서 이미 가져갔으면 False."""
    req = s.get(CrawlRequest, req_id)
    if req is None or req.status != PENDING:
        return False
    req.status, req.handled_at, req.message = status, utcnow(), message
    s.commit()
    return True


def worker_heartbeat(s: Session) -> datetime | None:
    from recruit_collector.web.auth import get_setting

    value = get_setting(s, HEARTBEAT_KEY)
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def worker_alive(s: Session, now: datetime | None = None) -> bool:
    beat = worker_heartbeat(s)
    return beat is not None and (now or utcnow()) - beat <= WORKER_DOWN_AFTER


def oldest_pending_at(s: Session) -> datetime | None:
    return s.scalar(select(func.min(CrawlRequest.requested_at)).where(CrawlRequest.status == PENDING))

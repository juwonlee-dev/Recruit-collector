"""'지금 수집'(조회 화면 → 대기열 → 수집기)과 수집기(worker)의 시작·종료."""

import threading
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import exc, func, select

from recruit_collector import crawl_queue, runner
from recruit_collector.crawlers.base import Condition, Crawler, Page, Posting
from recruit_collector.models import CrawlRequest, CrawlRun, JobPosting, make_session_factory, utcnow
from recruit_collector.web.app import create_app
from recruit_collector.worker import Worker
from tests.conftest import make_config

release = threading.Event()  # 수집을 붙잡아 두었다가 풀어주기 위한 신호


@dataclass
class Cond(Condition):
    prefix: str = "p"


class SlowCrawler(Crawler):
    site = "slow"
    condition_class = Cond

    def fetch_page(self, condition, page):
        release.wait(5)
        return Page([Posting(f"{condition.prefix}{i}", "공고", "회사", "u") for i in range(3)], is_last=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    release.clear()
    monkeypatch.setitem(runner.CRAWLERS, "slow", SlowCrawler)
    factory = make_session_factory(f"sqlite:///{tmp_path / 'm.db'}")
    config = make_config(tmp_path, {"slow": [{"name": "slow-dev", "max_pages": 5}]})
    client = TestClient(create_app(factory, config))
    worker = Worker(config, factory)
    yield client, factory, config, worker
    release.set()
    worker.scheduler.shutdown()
    worker.launcher.wait(5)


def wait_until(predicate, timeout=5.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def latest_run(factory) -> CrawlRun | None:
    with factory() as s:
        return s.scalars(select(CrawlRun).order_by(CrawlRun.id.desc()).limit(1)).first()


def requests(factory) -> list[tuple[str, str]]:
    with factory() as s:
        return [(r.site, r.status) for r in s.scalars(select(CrawlRequest).order_by(CrawlRequest.id))]


def card(client) -> dict:
    return client.get("/api/status").json()["searches"][0]


def test_manual_crawl_goes_through_worker(env):
    """수집기가 살아 있으면: 요청 → 시작 중 → 수집기가 가져가 수집 → 완료. 대기·수집 중에는 다시 요청할 수 없다."""
    client, factory, _, worker = env
    worker.heartbeat(force=True)

    res = client.post("/api/crawl/slow")
    assert res.status_code == 202 and res.json()["ok"] and "곧 시작" in res.json()["message"]
    assert card(client)["status"] == "STARTING" and client.get("/api/status").json()["running"] is True
    res = client.post("/api/crawl/slow")  # 대기 중에는 다시 요청할 수 없다
    assert res.status_code == 409 and "대기 중" in res.json()["message"]
    assert requests(factory) == [("slow", "PENDING")]
    assert latest_run(factory) is None  # 조회 화면은 수집하지 않는다

    assert worker.process_requests() == [("slow", "STARTED")]
    assert wait_until(lambda: (r := latest_run(factory)) is not None and r.status == "RUNNING")
    res = client.post("/api/crawl/slow")  # 수집 중에도 다시 요청할 수 없다
    assert res.status_code == 409 and "수집 중" in res.json()["message"]
    assert card(client)["status"] == "RUNNING"

    release.set()
    assert wait_until(lambda: latest_run(factory).status == "SUCCESS")
    run = latest_run(factory)
    assert run.trigger == "manual" and run.found == 3
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(JobPosting)) == 3
    assert client.post("/api/crawl/slow").status_code == 202  # 끝난 뒤에는 다시 요청 가능


def test_request_waits_in_queue_while_worker_is_down(env):
    """수집기가 꺼져 있으면 대기열에 넣고(대기 중), 그동안 같은 사이트는 다시 넣을 수 없다. 수집기가 뜨면 실행된다."""
    client, factory, _, worker = env
    status = client.get("/api/status").json()
    assert status["worker_alive"] is False and status["worker_seen_at"] is None

    res = client.post("/api/crawl/slow")
    assert res.status_code == 202 and res.json()["queued"] is True and "수집기가 꺼져 있어" in res.json()["message"]
    assert card(client)["status"] == "QUEUED"
    res = client.post("/api/crawl/slow")
    assert res.status_code == 409 and "이미 수집 대기 중" in res.json()["message"]
    assert requests(factory) == [("slow", "PENDING")]

    release.set()
    worker.startup()  # 수집기가 뜸
    assert client.get("/api/status").json()["worker_alive"] is True
    worker.tick()
    assert requests(factory) == [("slow", "STARTED")]
    assert wait_until(lambda: (r := latest_run(factory)) is not None and r.status == "SUCCESS")


def test_one_pending_request_per_site_in_db(env):
    """동시에 눌러도 대기 중인 요청은 사이트당 하나 (DB 부분 유니크 인덱스)."""
    _, factory, _, _ = env
    with factory() as s:
        crawl_queue.request_crawl(s, "slow")
        s.add(CrawlRequest(site="slow", status="PENDING", requested_at=utcnow()))
        with pytest.raises(exc.IntegrityError):
            s.commit()
        s.rollback()
        s.add(CrawlRequest(site="slow", status="STARTED", requested_at=utcnow()))  # 처리된 요청은 여러 개 가능
        s.commit()


def test_unknown_site(env):
    client, _, _, _ = env
    assert client.post("/api/crawl/nope").status_code == 404


def test_running_in_other_process_blocks(env):
    client, factory, _, _ = env
    with factory() as s:  # 수집기나 CLI가 수집 중인 상황
        now = utcnow()
        s.add(CrawlRun(search_name="slow-dev", site="slow", started_at=now, heartbeat_at=now, status="RUNNING"))
        s.commit()
    assert client.post("/api/crawl/slow").status_code == 409


def test_stale_run_is_shown_as_stopped_and_does_not_block(env):
    client, factory, _, _ = env
    with factory() as s:  # 프로세스가 죽어 RUNNING으로 남은 기록
        old = utcnow() - timedelta(minutes=30)
        s.add(CrawlRun(search_name="slow-dev", site="slow", started_at=old, heartbeat_at=old, status="RUNNING"))
        s.commit()
    status = client.get("/api/status").json()
    assert status["searches"][0]["status"] == "STALE"
    assert status["running"] is False
    assert client.post("/api/crawl/slow").status_code == 202


def test_worker_skips_request_when_site_is_already_running(env):
    """요청이 대기 중일 때 자동 수집이 먼저 시작했으면, 수집기는 그 요청을 건너뛴다."""
    client, factory, _, worker = env
    assert client.post("/api/crawl/slow").status_code == 202
    worker.launcher.start("slow", trigger=runner.TRIGGER_SCHEDULE)
    assert wait_until(lambda: (r := latest_run(factory)) is not None and r.status == "RUNNING")
    assert worker.process_requests() == [("slow", "SKIPPED")]
    with factory() as s:
        assert s.scalar(select(CrawlRequest.message)) == "이미 수집 중이라 건너뜀"


def test_restart_cancels_interrupted_runs(env):
    """수집기가 뜰 때: 자동·수동 수집의 RUNNING 기록은 CANCELED로, CLI는 진행 기록이 끊긴 것만 CANCELED로."""
    from recruit_collector.web.launcher import cancel_interrupted_runs

    _, factory, _, _ = env
    now, old = utcnow(), utcnow() - timedelta(minutes=30)
    hb = now - timedelta(seconds=40)
    with factory() as s:
        for name, trigger, at in [("a", "schedule", hb), ("b", "manual", hb), ("c", "cli", now), ("d", "cli", old)]:
            s.add(CrawlRun(search_name=name, site="slow", started_at=at - timedelta(minutes=1), heartbeat_at=at,
                           status="RUNNING", trigger=trigger, pages=3, found=30))
        s.add(CrawlRun(search_name="e", site="slow", started_at=old, finished_at=now, status="SUCCESS", trigger="schedule"))
        s.commit()

    assert cancel_interrupted_runs(factory) == 3
    with factory() as s:
        runs = {r.search_name: r for r in s.scalars(select(CrawlRun))}
    assert {k: r.status for k, r in runs.items()} == {"a": "CANCELED", "b": "CANCELED", "c": "RUNNING", "d": "CANCELED", "e": "SUCCESS"}
    assert runs["a"].finished_at == hb and runs["a"].error == "수집기가 다시 시작되어 수집이 중단됨"
    assert runs["d"].error == "진행 기록이 끊겨 수집이 중단됨" and runs["a"].found == 30
    assert cancel_interrupted_runs(factory) == 0  # 다시 해도 바뀌는 것 없음


def test_web_restart_does_not_touch_runs_but_worker_start_cancels(env):
    """조회 화면을 다시 띄워도 수집 기록은 그대로(수집기가 계속 수집). 수집기가 다시 뜰 때만 CANCELED로 정리."""
    _, factory, config, worker = env
    with factory() as s:
        at = utcnow() - timedelta(seconds=20)
        s.add(CrawlRun(search_name="slow-dev", site="slow", started_at=at, heartbeat_at=at, status="RUNNING", trigger="schedule"))
        s.commit()
    with TestClient(create_app(factory, config)) as client:  # 조회 화면 재시작 (lifespan 포함)
        assert card(client)["status"] == "RUNNING"
    worker.startup()  # 수집기 재시작
    with TestClient(create_app(factory, config)) as client:
        c = card(client)
        assert c["status"] == "CANCELED" and c["can_run"] and c["error"] == "수집기가 다시 시작되어 수집이 중단됨"
        assert "run-canceled" in client.get("/runs").text


def test_graceful_shutdown_waits_for_running_crawl(env):
    """종료 신호: 새 요청은 받지 않고, 진행 중인 수집이 끝나면 종료한다 (CANCELED 아님)."""
    client, factory, _, worker = env
    worker.startup()
    client.post("/api/crawl/slow")
    worker.tick()
    assert wait_until(lambda: (r := latest_run(factory)) is not None and r.status == "RUNNING")

    worker.request_stop()
    done = threading.Thread(target=worker.shutdown, args=(10,))
    done.start()
    done.join(0.3)
    assert done.is_alive()  # 수집이 끝나기를 기다리는 중
    release.set()
    done.join(5)
    assert not done.is_alive() and latest_run(factory).status == "SUCCESS"


def test_forced_shutdown_cancels_running_crawl(env):
    """강제 종료(SIGINT) 또는 기다리는 시간이 지나면: 진행 중인 수집을 바로 CANCELED로 정리하고 끝낸다."""
    client, factory, _, worker = env

    class Exited(Exception):
        pass

    def fake_exit(code):
        runner.DB_WRITE_LOCK.release()  # 실제로는 프로세스가 끝나므로 풀 필요가 없다
        raise Exited(code)

    worker._exit = fake_exit
    worker.startup()
    client.post("/api/crawl/slow")
    worker.tick()
    assert wait_until(lambda: (r := latest_run(factory)) is not None and r.status == "RUNNING")

    worker.request_stop(force=True)
    with pytest.raises(Exited):
        worker.shutdown(stop_timeout=60)
    run = latest_run(factory)
    assert run.status == "CANCELED" and run.error == "수집기를 강제로 종료하여 중단됨"


def test_shutdown_timeout_cancels(env):
    """종료 신호 후 기다리는 시간이 지나면 CANCELED로 정리하고 끝낸다."""
    client, factory, _, worker = env

    class Exited(Exception):
        pass

    def fake_exit(code):
        runner.DB_WRITE_LOCK.release()
        raise Exited(code)

    worker._exit = fake_exit
    worker.startup()
    client.post("/api/crawl/slow")
    worker.tick()
    assert wait_until(lambda: (r := latest_run(factory)) is not None and r.status == "RUNNING")
    worker.request_stop()
    with pytest.raises(Exited):
        worker.shutdown(stop_timeout=0.3)
    assert latest_run(factory).error == "수집기 종료 대기 시간이 지나 중단됨"


def test_config_change_during_crawl_does_not_affect_running_crawl(env):
    """수집이 시작되면 검색 필터 파일을 바꿔도 진행 중인 수집은 시작 시점의 조건으로 끝난다."""
    client, factory, config, worker = env
    assert client.post("/api/crawl/slow").status_code == 202
    worker.process_requests()
    assert wait_until(lambda: (r := latest_run(factory)) is not None and r.status == "RUNNING")

    # 수집 도중 파일 변경 (조건 변경 + 검색 조건 추가)
    config.site_file("slow").write_text(
        "searches:\n- name: slow-dev\n  max_pages: 5\n  condition: {prefix: q}\n"
        "- name: slow-new\n  max_pages: 5\n", encoding="utf-8")
    release.set()
    assert wait_until(lambda: latest_run(factory).status == "SUCCESS")
    with factory() as s:
        assert set(s.scalars(select(JobPosting.external_id))) == {"p0", "p1", "p2"}
        assert s.scalar(select(func.count()).select_from(CrawlRun)) == 1  # 추가된 조건은 이번 수집에 없음

    # 다음 수집부터 바뀐 파일을 읽는다
    assert wait_until(lambda: not worker.launcher.running())
    assert client.post("/api/crawl/slow").status_code == 202
    worker.process_requests()
    assert wait_until(lambda: (r := latest_run(factory)) is not None and r.search_name == "slow-new"
                      and r.status == "SUCCESS")
    with factory() as s:
        assert {"q0", "q1", "q2"} <= set(s.scalars(select(JobPosting.external_id)))

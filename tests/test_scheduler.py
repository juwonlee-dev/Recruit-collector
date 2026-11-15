import pytest
from fastapi.testclient import TestClient

from recruit_collector import runner
from recruit_collector.models import make_session_factory
from recruit_collector.web import scheduler as sched_mod
from recruit_collector.web.app import create_app
from recruit_collector.web.launcher import AlreadyRunning
from tests.conftest import make_config

TZ = "Asia/Seoul"


def test_parse_cron_validates_format_and_min_interval():
    assert sched_mod.parse_cron("  0  *  * * * ", TZ)
    assert sched_mod.parse_cron("*/30 * * * *", TZ)
    assert sched_mod.parse_cron("0 9,18 * * 1-5", TZ)
    with pytest.raises(ValueError, match="다섯 칸"):
        sched_mod.parse_cron("0 * * *", TZ)
    with pytest.raises(ValueError, match="올바르지 않습니다"):
        sched_mod.parse_cron("0 25 * * *", TZ)
    for too_often in ("*/10 * * * *", "0,15 * * * *", "* * * * *"):
        with pytest.raises(ValueError, match="30분 이상"):
            sched_mod.parse_cron(too_often, TZ)
    times = sched_mod.next_times(sched_mod.parse_cron("0 */6 * * *", TZ), 3)
    assert [t.hour % 6 for t in times] == [0, 0, 0] and len(times) == 3
    assert sched_mod.describe("0 * * * *") == "1시간마다 (매시 정각)"
    assert sched_mod.describe("5 4 * * *") == "직접 입력 (5 4 * * *)"


class FakeLauncher:
    sites = ["saramin", "jobkorea", "wanted"]

    def __init__(self):
        self.started = []

    def start(self, site, trigger=runner.TRIGGER_MANUAL):
        if site == "jobkorea":
            raise AlreadyRunning(site)
        if site == "wanted":
            raise KeyError(site)
        self.started.append((site, trigger))


def test_scheduler_settings_persist_and_tick(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 's.db'}")
    launcher = FakeLauncher()
    sch = sched_mod.CrawlScheduler(launcher, factory, TZ, "0 * * * *")
    try:
        assert sch.settings() == (False, "0 * * * *")  # 처음: 꺼짐, config의 cron
        sch.sync()
        assert sch.next_run_at() is None

        assert sch.set_cron(" 0  */2 * * * ") == "0 */2 * * *"
        with pytest.raises(ValueError):
            sch.set_cron("*/5 * * * *")
        sch.set_enabled(True)
        nxt = sch.next_run_at()
        assert nxt is not None and nxt.minute == 0 and nxt.hour % 2 == 0

        # 다른 서버 인스턴스도 같은 설정으로 시작 (DB에 저장됨)
        other = sched_mod.CrawlScheduler(launcher, factory, TZ, "0 * * * *")
        assert other.settings() == (True, "0 */2 * * *")

        # 정해진 시각: 사이트별 시작, 이미 수집 중·조건 없는 사이트는 건너뜀
        sch._tick()
        assert launcher.started == [("saramin", runner.TRIGGER_SCHEDULE)]

        sch.set_enabled(False)
        assert sch.next_run_at() is None
    finally:
        sch.shutdown()


def test_scheduler_tab(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 's.db'}")
    config = make_config(tmp_path, {"saramin": [{"name": "s1", "condition": {}}]})
    with TestClient(create_app(factory, config)) as client:
        page = client.get("/settings", params={"tab": "scheduler"}).text
        assert "중지됨" in page and "자동 수집 시작" in page and 'value="0 * * * *" checked' in page
        assert "<h2>키워드</h2>" not in page and "<h2>로그인 사용</h2>" not in page  # 다른 탭 내용이 섞이지 않음
        tabs_end = page.index("</nav>", page.index('class="tabs"'))  # 설정 탭 메뉴의 끝
        assert page.index('href="/settings?tab=users"') < tabs_end < page.index("<h2>자동 수집</h2>")
        assert client.get("/api/status").json()["schedule_on"] is False

        res = client.post("/settings/scheduler/cron", data={"preset": "custom", "cron": "*/10 * * * *"}, follow_redirects=False)
        assert "err=1" in res.headers["location"]
        client.post("/settings/scheduler/cron", data={"preset": "0 */3 * * *"})
        client.post("/settings/scheduler/cron", data={"preset": "custom", "cron": "0 8-20/2 * * 1-5"})

        client.post("/settings/scheduler/toggle", data={"enabled": "1"})
        assert 'id="crawl-confirm"' in client.get("/").text  # 지금 수집 확인 모달 (자동 수집이 켜져 있을 때 status.js가 띄움)
        status = client.get("/api/status").json()
        assert status["schedule_on"] is True and status["next_run_at"]
        page = client.get("/settings", params={"tab": "scheduler"}).text
        assert "실행 중" in page and "직접 입력 (0 8-20/2 * * 1-5)" in page and "자동 수집 중지" in page

        client.post("/settings/scheduler/toggle", data={"enabled": "0"})
        assert client.get("/api/status").json()["schedule_on"] is False

    # 서버를 다시 띄우면 저장된 설정대로 (켜 두었으면 다시 시작)
    with factory() as s:
        from recruit_collector.web.auth import set_setting
        set_setting(s, sched_mod.ENABLED_KEY, "1")
        s.commit()
    with TestClient(create_app(factory, config)) as client:
        assert client.get("/api/status").json()["schedule_on"] is True


def test_worker_follows_schedule_changed_in_web(tmp_path):
    """조회 화면은 DB 설정만 바꾸고, 수집기(worker)가 다음 확인 때 스케줄러를 그 설정으로 맞춘다."""
    from recruit_collector.worker import Worker

    factory = make_session_factory(f"sqlite:///{tmp_path / 's.db'}")
    config = make_config(tmp_path, {"saramin": [{"name": "s1", "condition": {}}]})
    worker = Worker(config, factory)
    try:
        worker.startup()
        assert worker.scheduler.next_run_at() is None
        with TestClient(create_app(factory, config)) as client:
            client.post("/settings/scheduler/cron", data={"preset": "0 */3 * * *"})
            client.post("/settings/scheduler/toggle", data={"enabled": "1"})
            status = client.get("/api/status").json()
            assert status["schedule_on"] is True and status["worker_alive"] is True
            assert worker.scheduler.next_run_at() is None  # 아직 수집기가 확인하기 전

            assert worker.scheduler.sync_if_changed() is True
            nxt = worker.scheduler.next_run_at()
            assert nxt is not None and nxt.hour % 3 == 0 and nxt.minute == 0
            assert status["next_run_at"] == nxt.strftime("%Y-%m-%d %H:%M")  # 화면의 다음 수집 = 수집기의 다음 실행
            assert worker.scheduler.sync_if_changed() is False  # 바뀐 게 없으면 그대로

            client.post("/settings/scheduler/toggle", data={"enabled": "0"})
            worker.tick()
            assert worker.scheduler.next_run_at() is None
            page = client.get("/settings", params={"tab": "scheduler"}).text
            assert "<dt>수집기</dt>" in page and "마지막 신호" in page
    finally:
        worker.scheduler.shutdown()

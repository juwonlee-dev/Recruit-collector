import re
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from recruit_collector.config import AuthSettings
from recruit_collector.models import User, make_session_factory
from recruit_collector.web import auth
from recruit_collector.web.app import create_app
from tests.conftest import make_config

ADMIN = "boss@example.com"


@pytest.fixture
def env(tmp_path, monkeypatch):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'a.db'}")
    config = make_config(tmp_path, {"saramin": [{"name": "s1", "condition": {}}]}, keywords=["백엔드"])
    config.auth = AuthSettings("cid", "secret", (ADMIN,))
    users = {}  # code -> 구글 사용자 정보

    def fake_fetch(settings, code, redirect_uri):
        assert redirect_uri == "http://testserver/auth/callback"
        if code not in users:
            raise auth.LoginError("구글 토큰 발급에 실패했습니다. (HTTP 400)")
        return users[code]

    monkeypatch.setattr(auth, "fetch_google_user", fake_fetch)
    return factory, config, users


def login(client, users, email, sub=None, name="테스터"):
    """구글 로그인 흐름: /auth/login → (구글) → /auth/callback"""
    res = client.get("/auth/login", params={"next": "/settings?tab=users"}, follow_redirects=False)
    assert res.status_code == 303
    q = parse_qs(urlparse(res.headers["location"]).query)
    assert q["client_id"] == ["cid"] and q["redirect_uri"] == ["http://testserver/auth/callback"]
    users[email] = {"sub": sub or email, "email": email, "email_verified": True, "name": name}
    return client.get("/auth/callback", params={"code": email, "state": q["state"][0]}, follow_redirects=False)


def enable_login(factory):
    with factory() as s:
        auth.set_setting(s, auth.LOGIN_ENABLED_KEY, "1")
        s.commit()


def test_login_off_everything_open(env):
    factory, config, _ = env
    client = TestClient(create_app(factory, config))
    assert client.get("/settings").status_code == 200
    assert 'href="/settings"' in client.get("/").text
    assert client.get("/api/status").json()["searches"][0]["can_run"]
    assert ">로그인</a>" in client.get("/").text  # 구글 로그인이 설정돼 있으면 로그인 링크를 보여준다


def test_login_on_restricts_crawl_and_settings_to_admins(env):
    factory, config, users = env
    enable_login(factory)
    client = TestClient(create_app(factory, config))

    # 로그인 전: 설정 메뉴·지금 수집 버튼 없음, 직접 요청도 막힘
    assert 'href="/settings"' not in client.get("/").text
    assert not client.get("/api/status").json()["searches"][0]["can_run"]
    assert client.post("/api/crawl/saramin").status_code == 403
    res = client.get("/settings", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/auth/login?next=/settings"
    assert client.post("/settings/keywords", data={"keyword": "x"}, follow_redirects=False).status_code == 303
    assert client.post("/settings/login", data={"enabled": "0"}, follow_redirects=False).headers["location"].startswith("/auth/login")

    # 일반 사용자: 첫 로그인 때 자동 가입, 여전히 막힘
    res = login(client, users, "user@example.com")
    assert res.status_code == 303 and res.headers["location"] == "/settings?tab=users"
    assert client.get("/settings").status_code == 403
    assert client.post("/api/crawl/saramin").status_code == 403
    client.post("/auth/logout")

    # 설정 파일의 관리자: 설정 메뉴·지금 수집 버튼 보임
    login(client, users, ADMIN.upper())  # 이메일 대소문자 무관
    assert 'href="/settings"' in client.get("/").text
    assert client.get("/api/status").json()["searches"][0]["can_run"]
    assert client.get("/settings").status_code == 200

    with factory() as s:
        roles = {u.email: u.role for u in s.query(User)}
    assert roles == {"user@example.com": "user", ADMIN: "admin"}


def test_user_roles_managed_in_settings(env):
    factory, config, users = env
    enable_login(factory)
    client = TestClient(create_app(factory, config))
    other = TestClient(create_app(factory, config))
    login(other, users, "user@example.com")
    login(client, users, ADMIN)
    with factory() as s:
        ids = {u.email: u.id for u in s.query(User)}

    page = client.get("/settings", params={"tab": "users"}).text
    assert "user@example.com" in page and f'/settings/users/{ids["user@example.com"]}/role' in page
    assert f'/settings/users/{ids[ADMIN]}/role' not in page  # 설정 파일 관리자·나는 바꿀 수 없음

    client.post(f"/settings/users/{ids['user@example.com']}/role", data={"role": "admin"})
    assert other.get("/settings").status_code == 200  # 등급은 바로 반영
    res = client.post(f"/settings/users/{ids[ADMIN]}/role", data={"role": "user"}, follow_redirects=False)
    assert "err=1" in res.headers["location"]
    res = other.post(f"/settings/users/{ids['user@example.com']}/role", data={"role": "user"}, follow_redirects=False)
    assert "err=1" in res.headers["location"]  # 내 등급은 못 바꿈


def test_enable_login_requires_admin_login(env):
    factory, config, users = env
    client = TestClient(create_app(factory, config))
    res = client.post("/settings/login", data={"enabled": "1"}, follow_redirects=False)
    assert "err=1" in res.headers["location"]  # 로그인 안 함
    with factory() as s:
        assert not auth.login_enabled(s)

    page = client.get("/settings", params={"tab": "users"}).text
    assert 'id="login-modal"' in page and "http://testserver/auth/callback" in page

    login(client, users, ADMIN)
    client.post("/settings/login", data={"enabled": "1"})
    with factory() as s:
        assert auth.login_enabled(s)
    client.post("/settings/login", data={"enabled": "0"})
    with factory() as s:
        assert not auth.login_enabled(s)


def test_login_failures(env, tmp_path):
    factory, config, users = env
    client = TestClient(create_app(factory, config))
    # state가 맞지 않으면 거부
    client.get("/auth/login", follow_redirects=False)
    assert client.get("/auth/callback", params={"code": "x", "state": "wrong"}).status_code == 400
    # 구글 토큰 발급 실패
    res = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
    assert client.get("/auth/callback", params={"code": "nope", "state": state}).status_code == 400
    # 이메일 미인증 계정
    with factory() as s, pytest.raises(auth.LoginError):
        auth.sign_in(s, {"sub": "1", "email": "a@b.c", "email_verified": False}, config.auth)
    # 외부 주소로는 돌아가지 않음
    assert auth.safe_next("https://evil.example") == "/" and auth.safe_next("//evil") == "/"

    # 구글 로그인이 설정되지 않은 서버: 로그인 링크 없음, 로그인 안내
    config.auth = AuthSettings()
    client = TestClient(create_app(make_session_factory(f"sqlite:///{tmp_path / 'b.db'}"), config))
    assert ">로그인</a>" not in client.get("/").text
    assert client.get("/auth/login").status_code == 503


def test_salary_menu_requires_login_when_enabled(env):
    factory, config, users = env
    client = TestClient(create_app(factory, config))
    assert 'href="/salary"' in client.get("/").text  # 로그인 사용 꺼짐: 누구나

    enable_login(factory)
    assert 'href="/salary"' not in client.get("/").text
    res = client.get("/salary", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/auth/login?next=/salary"
    assert client.get("/salary/c/1", follow_redirects=False).status_code == 303
    res = client.get("/api/salary/search", params={"q": "넷마블"})
    assert res.status_code == 403 and res.json()["message"] == "로그인해야 사용할 수 있습니다."
    assert client.get("/api/salary/company/1").status_code == 403
    res = client.get("/salary/compare", params={"a": "1", "b": "2"}, follow_redirects=False)
    assert res.headers["location"] == "/auth/login?next=/salary/compare%3Fa%3D1%26b%3D2"

    login(client, users, "user@example.com")  # 일반 등급도 로그인하면 사용 가능
    assert 'href="/salary"' in client.get("/").text
    assert client.get("/salary").status_code == 200


def test_salary_modal_compare_button(env):
    """연봉 모달의 '비교하기'와 비교함 안내 모달: 연봉·인원 메뉴를 쓸 수 있을 때만 (로그인 사용 꺼짐, 또는 로그인함)."""
    factory, config, users = env
    client = TestClient(create_app(factory, config))
    marks = ('class="salary-compare-btn"', 'id="compare-go-modal"', 'id="compare-full-modal"', 'data-compare-max="4"')
    html = client.get("/").text
    assert all(m in html for m in marks)  # 로그인 사용 꺼짐: 누구나

    enable_login(factory)
    html = client.get("/").text
    assert 'id="salary-modal"' in html and not any(m in html for m in marks)  # 로그인 사용 중 비로그인: 없음

    login(client, users, "user@example.com")
    assert all(m in client.get("/").text for m in marks)


def test_salary_modal_login_prompt(env, monkeypatch):
    """로그인 사용 중 비로그인: 연봉 모달은 로그인 안내만 (조회 화면 없음), 공고 연봉 API는 올댓컴퍼니에 요청하지 않고 403."""
    from recruit_collector import salary
    from recruit_collector.crawlers.base import CrawlResult, Posting
    from recruit_collector.sync import sync_search

    factory, config, users = env
    with factory() as s:
        item = Posting(external_id="1", title="백엔드", company="회사", detail_url="https://e/1")
        sync_search(s, site="saramin", search_name="s1", result=CrawlResult([item], 1, True), missing_threshold=1)
        s.commit()
    calls = []

    def fake_lookup(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("조회하면 안 됨")

    monkeypatch.setattr(salary, "lookup", fake_lookup)
    client = TestClient(create_app(factory, config))

    enable_login(factory)
    html = client.get("/", params={"q": "백엔드"}).text
    assert "data-locked" in html and "로그인 후 이용할 수 있습니다" in html and "salary-locked-body\" aria-hidden=\"true\" inert" in html
    assert "비교하기" not in html
    assert "/auth/login?next=/%3Fq%3D%25EB%25B0" in html  # 로그인 뒤 보던 화면(/?q=백엔드)으로. 헤더의 로그인 링크와 같은 방식
    res = client.get("/api/postings/1/salary")
    assert res.status_code == 403 and res.json()["ok"] is False and calls == []

    login(client, users, "user@example.com")
    html = client.get("/").text
    assert "data-locked" not in html and 'class="salary-body"' in html


def test_company_name_links_to_salary_search(env):
    """공고 목록의 회사명: 연봉·인원 메뉴를 쓸 수 있으면 그 회사명으로 연봉·인원 검색(/salary?q=) 링크."""
    from recruit_collector.crawlers.base import CrawlResult, Posting
    from recruit_collector.sync import sync_search

    factory, config, users = env
    with factory() as s:
        item = Posting(external_id="1", title="백엔드", company="(주)카카오 & 친구들", detail_url="https://e/1")
        sync_search(s, site="saramin", search_name="s1", result=CrawlResult([item], 1, True), missing_threshold=1)
        s.commit()
    client = TestClient(create_app(factory, config))
    link = '<a class="company company-link" href="/salary?q=%28%EC%A3%BC%29%EC%B9%B4%EC%B9%B4%EC%98%A4%20%26%20%EC%B9%9C%EA%B5%AC%EB%93%A4"'
    assert link in client.get("/").text  # 로그인 사용 꺼짐: 누구나

    enable_login(factory)
    html = client.get("/").text
    assert link not in html and '<span class="company">(주)카카오 &amp; 친구들</span>' in html  # 비로그인: 글자만

    login(client, users, "user@example.com")
    assert link in client.get("/").text


# ------------------------------------------------------------ 로그인 토큰 (Access·Refresh JWT)

def logged_in(client) -> bool:
    return client.get("/settings", follow_redirects=False).status_code == 200


def admin_client(env):
    factory, config, users = env
    enable_login(factory)
    client = TestClient(create_app(factory, config))
    res = login(client, users, ADMIN)
    return client, res


def test_login_issues_httponly_access_and_refresh_cookies(env):
    client, res = admin_client(env)
    cookies = res.headers.get_list("set-cookie")
    for name in ("rc_access", "rc_refresh"):
        line = next(c for c in cookies if c.startswith(name + "="))
        assert "httponly" in line.lower() and "samesite=lax" in line.lower() and "path=/" in line.lower()
    assert any(c.startswith("rc_oauth=") and "max-age=0" in c.lower() for c in cookies)  # 확인값 쿠키는 지움
    assert logged_in(client)


def test_expired_access_is_refreshed_and_refresh_rotates(env):
    from recruit_collector.web import tokens
    client, _ = admin_client(env)
    old_refresh = client.cookies.get("rc_refresh")
    client.cookies.delete("rc_access")  # Access 만료
    assert logged_in(client)
    assert client.cookies.get("rc_access") and client.cookies.get("rc_refresh") != old_refresh
    # 조작된 Access는 무시하고 Refresh로 다시 발급
    client.cookies.set("rc_access", client.cookies.get("rc_access")[:-2] + "xx", domain="testserver.local")
    assert logged_in(client)
    assert tokens.decode("x", "not-a-jwt", "access") is None


def test_old_refresh_reuse_revokes_login(env, monkeypatch):
    from datetime import timedelta
    from recruit_collector.web import tokens
    client, _ = admin_client(env)
    old_refresh = client.cookies.get("rc_refresh")
    client.cookies.delete("rc_access")
    assert logged_in(client)
    new_refresh = client.cookies.get("rc_refresh")

    # 동시에 보낸 요청(잠깐 안): 옛 토큰에도 지금 토큰을 다시 준다
    client.cookies.delete("rc_access")
    client.cookies.set("rc_refresh", old_refresh, domain="testserver.local")
    assert logged_in(client) and client.cookies.get("rc_refresh") == new_refresh

    # 그 뒤에 옛 토큰이 오면 새어 나간 것으로 보고 로그인을 끊는다 (지금 토큰도 못 씀)
    monkeypatch.setattr(tokens, "REUSE_GRACE", timedelta(seconds=-1))
    client.cookies.delete("rc_access")
    client.cookies.set("rc_refresh", old_refresh, domain="testserver.local")
    assert not logged_in(client)
    client.cookies.set("rc_refresh", new_refresh, domain="testserver.local")
    assert not logged_in(client)


def test_logout_revokes_copied_tokens(env):
    client, _ = admin_client(env)
    copied = {k: client.cookies.get(k) for k in ("rc_access", "rc_refresh")}
    client.post("/auth/logout")
    assert not logged_in(client)
    for k, v in copied.items():
        client.cookies.set(k, v, domain="testserver.local")
    assert not logged_in(client)  # Access가 아직 만료 전이어도 바로 끊김
    client.cookies.delete("rc_access")
    assert not logged_in(client)


def test_refresh_expires_after_max_login_age(env):
    from datetime import timedelta
    from recruit_collector.models import LoginSession, utcnow
    factory = env[0]
    client, _ = admin_client(env)
    with factory() as s:
        row = s.query(LoginSession).one()
        row.expires_at = utcnow() - timedelta(seconds=1)
        s.commit()
    client.cookies.delete("rc_access")
    assert not logged_in(client)
    assert client.cookies.get("rc_refresh") is None  # 못 쓰는 쿠키는 지운다


def test_admin_logs_user_out_of_all_devices(env):
    factory, config, users = env
    client, _ = admin_client(env)
    phone = TestClient(create_app(factory, config))
    pc = TestClient(create_app(factory, config))
    login(phone, users, "user@example.com")
    login(pc, users, "user@example.com")
    assert phone.get("/salary", follow_redirects=False).status_code == 200
    with factory() as s:
        uid = s.query(User).filter_by(email="user@example.com").one().id
    page = client.get("/settings", params={"tab": "users"}).text
    assert f"/settings/users/{uid}/logout" in page and "2곳" in page
    res = client.post(f"/settings/users/{uid}/logout", follow_redirects=False)
    assert "err=1" not in res.headers["location"]
    for c in (phone, pc):
        assert c.get("/salary", follow_redirects=False).status_code == 303
    assert logged_in(client)


def test_devices_listed_and_logged_out_one_by_one(env):
    from recruit_collector.models import LoginSession
    from recruit_collector.web import tokens
    factory, config, users = env
    client, _ = admin_client(env)
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    windows = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
    phone = TestClient(create_app(factory, config), headers={"user-agent": iphone})
    pc = TestClient(create_app(factory, config), headers={"user-agent": windows})
    login(phone, users, "user@example.com")
    login(pc, users, "user@example.com")
    with factory() as s:
        rows = {r.user_agent: r for r in s.query(LoginSession)}
    assert rows[iphone].ip == "testclient"
    page = client.get("/settings", params={"tab": "users"}).text
    assert "iOS 18.0 · Safari" in page and "Windows PC" in page and "IP testclient" in page
    assert 'aria-label="휴대폰"' in page and 'aria-label="PC"' in page
    uid = rows[iphone].user_id
    assert "이 기기" in page and f'aria-controls="devices-{uid}"' in page and 'id="devices-%d"' % uid in page
    assert 'aria-label="로그인 기기 PC 1대, 휴대폰 1대 목록 보기"' in page  # '2곳' 대신 기기 종류별 대수
    assert page.count('class="devices-row"') == 2  # 기기가 있는 사용자(관리자·user)만 펼침 행

    res = client.post(f"/settings/devices/{rows[iphone].id}/logout", follow_redirects=False)
    assert "err=1" not in res.headers["location"] and res.headers["location"].endswith(f"#devices-{uid}")  # 돌아가면 펼쳐 둔다
    assert phone.get("/salary", follow_redirects=False).status_code == 303  # 그 기기만
    assert pc.get("/salary", follow_redirects=False).status_code == 200
    res = client.post(f"/settings/devices/{rows[iphone].id}/logout", follow_redirects=False)
    assert "err=1" in res.headers["location"]  # 이미 로그아웃됨



def test_mypage_lists_my_devices_and_logs_them_out(env):
    from recruit_collector.models import LoginSession
    factory, config, users = env
    enable_login(factory)
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    windows = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    pc = TestClient(create_app(factory, config), headers={"user-agent": windows})
    phone = TestClient(create_app(factory, config), headers={"user-agent": iphone})
    tablet = TestClient(create_app(factory, config))
    other = TestClient(create_app(factory, config))
    for c in (pc, phone, tablet):
        login(c, users, "user@example.com", name="홍길동")
    login(other, users, "other@example.com")
    with factory() as s:
        rows = {r.user_agent: r for r in s.query(LoginSession)}
        other_fam = s.query(LoginSession).join(User, User.id == LoginSession.user_id).filter(User.email == "other@example.com").one().id

    # 상단 사용자 메뉴(사진·이름) → 마이페이지·로그아웃 (일반 사용자도). 사진이 없으면 이름 첫 글자
    header = pc.get("/").text
    assert 'class="userbox usermenu"' in header and 'href="/me"' in header and 'action="/auth/logout"' in header
    assert '<span class="avatar" style="--size: 28px" aria-hidden="true">홍</span>' in header
    page = pc.get("/me").text
    assert "홍길동" in page and "user@example.com" in page and "일반" in page
    assert "3곳" in page and "이 기기" in page and "iOS 18.0 · Safari" in page
    assert other_fam not in page  # 다른 사람의 기기는 보이지 않는다

    # 다른 사람의 기기는 끊을 수 없다
    res = pc.post(f"/me/devices/{other_fam}/logout", follow_redirects=False)
    assert "err=1" in res.headers["location"]
    assert other.get("/salary", follow_redirects=False).status_code == 200

    # 기기 하나 로그아웃
    res = pc.post(f"/me/devices/{rows[iphone].id}/logout", follow_redirects=False)
    assert res.headers["location"].startswith("/me") and "err=1" not in res.headers["location"]
    assert phone.get("/salary", follow_redirects=False).status_code == 303
    assert pc.get("/salary", follow_redirects=False).status_code == 200

    # 다른 기기 모두 로그아웃: 지금 기기와 다른 사람은 그대로
    pc.post("/me/devices/logout-others")
    assert tablet.get("/salary", follow_redirects=False).status_code == 303
    assert pc.get("/salary", follow_redirects=False).status_code == 200
    assert other.get("/salary", follow_redirects=False).status_code == 200

    # 지금 기기를 로그아웃하면 이 브라우저도 로그아웃
    res = pc.post(f"/me/devices/{rows[windows].id}/logout", follow_redirects=False)
    assert res.headers["location"] == "/"
    assert pc.get("/salary", follow_redirects=False).status_code == 303


def test_mypage_requires_login(env):
    factory, config, users = env
    enable_login(factory)
    client = TestClient(create_app(factory, config))
    res = client.get("/me", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"].startswith("/auth/login?next=/me")
    res = client.post("/me/devices/logout-others", follow_redirects=False)
    assert res.headers["location"].startswith("/auth/login?next=/me")


@pytest.mark.parametrize("ua, kind, name, detail", [
    (None, "unknown", "알 수 없는 기기", ""),
    ("curl/8.5.0", "unknown", "알 수 없는 기기", ""),
    ("Mozilla/5.0 (Linux; Android 15; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/27.0 Chrome/125.0 Mobile Safari/537.36",
     "mobile", "갤럭시 SM-S928N", "Android 15 · 삼성 인터넷"),
    ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
     "mobile", "Android 휴대폰", "Android 10 · Chrome"),  # 크롬은 기종을 가림
    ("Mozilla/5.0 (Linux; Android 14; SM-X710N Build/UP1A.231005.007; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0 Safari/537.36 KAKAOTALK/25.9.0",
     "tablet", "갤럭시 SM-X710N", "Android 14 · 카카오톡"),
    ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
     "mobile", "Pixel 8", "Android 14 · Chrome"),
    ("Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
     "tablet", "iPad", "iPadOS 17.5 · Safari"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
     "pc", "Mac", "Chrome"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
     "pc", "Windows PC", "Edge"),
])
def test_device_info(ua, kind, name, detail):
    from recruit_collector.web import tokens
    d = tokens.device_info(ua)
    assert (d.kind, d.name, d.detail) == (kind, name, detail)

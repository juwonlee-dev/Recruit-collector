from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from recruit_collector import salary
from recruit_collector.crawlers.base import CrawlResult, Posting
from recruit_collector.models import FavoriteCompany, FavoritePosting, JobPosting
from recruit_collector.sync import sync_search
from recruit_collector.web import auth
from recruit_collector.web.app import create_app
from tests.test_auth import enable_login, env, login  # noqa: F401 (env는 fixture)


def add_postings(factory):
    with factory() as s:
        items = [Posting(external_id=str(i), title=f"공고{i}", company=f"회사{i}", detail_url=f"https://example.com/{i}")
                 for i in (1, 2)]
        sync_search(s, site="saramin", search_name="s1", result=CrawlResult(items, 1, True), missing_threshold=1)
        s.commit()
        return {p.title: p.id for p in s.scalars(select(JobPosting))}


def test_favorites_need_login_enabled_and_user(env):
    factory, config, users = env
    ids = add_postings(factory)
    client = TestClient(create_app(factory, config))
    pid = ids["공고1"]

    # 로그인 사용 꺼짐: 별 없음, API 막힘 (계정이 없으므로)
    assert "fav-btn" not in client.get("/").text
    assert client.put(f"/api/favorites/postings/{pid}").status_code == 403

    # 로그인 사용 켜짐, 비로그인: 별 없음, API 막힘
    enable_login(factory)
    assert "fav-btn" not in client.get("/").text and "favorite.js" not in client.get("/").text
    assert client.put(f"/api/favorites/postings/{pid}").status_code == 403
    assert client.put("/api/favorites/companies/7", json={"name": "넷마블"}).status_code == 403

    # 로그인: 별 보임
    login(client, users, "a@example.com")
    html = client.get("/").text
    assert html.count('class="fav-btn"') == 2 and "favorite.js" in html


def test_posting_favorites_are_per_account(env):
    factory, config, users = env
    ids = add_postings(factory)
    enable_login(factory)
    a, b = TestClient(create_app(factory, config)), TestClient(create_app(factory, config))
    login(a, users, "a@example.com")
    login(b, users, "b@example.com")
    pid = ids["공고1"]

    assert a.put(f"/api/favorites/postings/{pid}").json() == {"ok": True, "favorite": True}
    assert a.put(f"/api/favorites/postings/{pid}").status_code == 200  # 두 번 눌러도 한 건
    assert a.put("/api/favorites/postings/999999").status_code == 404
    with factory() as s:
        assert s.scalar(select(FavoritePosting.posting_id)) == pid and len(s.scalars(select(FavoritePosting)).all()) == 1

    assert f'data-fav-id="{pid}" data-fav-name="공고1"\n          aria-pressed="true"' in a.get("/").text
    assert "fav-btn is-on" not in b.get("/").text  # 다른 계정에는 없음
    assert a.get("/").text.count("fav-btn is-on") == 1

    assert a.delete(f"/api/favorites/postings/{pid}").json() == {"ok": True, "favorite": False}
    assert "fav-btn is-on" not in a.get("/").text


def test_company_favorites_in_salary_search(env, monkeypatch):
    factory, config, users = env
    enable_login(factory)
    monkeypatch.setattr(salary, "search_page", lambda http, q, page: (
        [salary.Candidate("11", "넷마블", "서울 구로구"), salary.Candidate("22", "넷마블네오", None)], False))
    a, b = TestClient(create_app(factory, config)), TestClient(create_app(factory, config))
    login(a, users, "a@example.com")
    login(b, users, "b@example.com")

    favs = lambda c: [x["favorite"] for x in c.get("/api/salary/search", params={"q": "넷마블"}).json()["candidates"]]
    assert favs(a) == [False, False]
    assert a.put("/api/favorites/companies/22", json={"name": "넷마블네오", "address": ""}).json()["favorite"]
    assert favs(a) == [False, True] and favs(b) == [False, False]
    with factory() as s:
        row = s.scalars(select(FavoriteCompany)).one()
        assert (row.company_id, row.name, row.address) == ("22", "넷마블네오", None)

    assert a.put("/api/favorites/companies/abc", json={"name": "x"}).status_code == 400
    assert a.put("/api/favorites/companies/33", json={}).status_code == 400
    assert a.delete("/api/favorites/companies/22").json() == {"ok": True, "favorite": False}
    assert favs(a) == [False, False]

    # 로그인 사용을 끄면 검색 결과에 별 정보가 없다 (null)
    with factory() as s:
        auth.set_setting(s, auth.LOGIN_ENABLED_KEY, "0")
        s.commit()
    assert favs(a) == [None, None]


def test_posting_detail_star(env):
    factory, config, users = env
    ids = add_postings(factory)
    client = TestClient(create_app(factory, config))
    pid = ids["공고1"]
    assert "fav-btn" not in client.get(f"/postings/{pid}").text  # 로그인 사용 꺼짐
    enable_login(factory)
    assert "fav-btn" not in client.get(f"/postings/{pid}").text  # 비로그인
    login(client, users, "a@example.com")
    html = client.get(f"/postings/{pid}").text
    assert 'class="fav-btn"' in html and "favorite.js" in html
    client.put(f"/api/favorites/postings/{pid}")
    assert 'class="fav-btn is-on"' in client.get(f"/postings/{pid}").text
    assert 'class="fav-btn"' in client.get(f"/postings/{ids['공고2']}").text  # 다른 공고는 그대로


def test_favorite_states_for_restored_pages(env):
    factory, config, users = env
    ids = add_postings(factory)
    enable_login(factory)
    client = TestClient(create_app(factory, config))
    p1, p2 = ids["공고1"], ids["공고2"]
    assert client.get("/api/favorites/postings", params={"ids": f"{p1},{p2}"}).status_code == 403  # 비로그인
    login(client, users, "a@example.com")
    client.put(f"/api/favorites/postings/{p2}")
    client.put("/api/favorites/companies/7", json={"name": "넷마블"})
    get = lambda kind, q: client.get(f"/api/favorites/{kind}", params={"ids": q}).json()
    assert get("postings", f"{p1},{p2},x") == {"ok": True, "favorites": [str(p2)]}
    assert get("companies", "7,8")["favorites"] == ["7"]
    assert client.get("/api/favorites/other", params={"ids": "1"}).status_code == 404


# ------------------------------------------------------------------ 활용: D-day · 변경 알림 · 관심 회사 새 공고

def sync_items(factory, items, name="s1"):
    with factory() as s:
        sync_search(s, site="saramin", search_name=name, result=CrawlResult(items, 1, True), missing_threshold=1)
        s.commit()


def item(i, company=None, **kw):
    return Posting(external_id=str(i), title=f"공고{i}", company=company or f"회사{i}", detail_url=f"https://example.com/{i}", **kw)


def test_dday_badge_levels():
    from datetime import date, timedelta

    from recruit_collector.web.favorites import dday_badge
    today = date(2026, 11, 12)
    p = lambda days, status="ACTIVE": JobPosting(status=status, deadline_at=today + timedelta(days=days) if days is not None else None)
    assert dday_badge(p(0), today) == ("오늘 마감", "urgent")
    assert dday_badge(p(3), today) == ("D-3", "urgent")
    assert dday_badge(p(7), today) == ("D-7", "soon")
    assert dday_badge(p(8), today) == ("D-8", "later")
    assert dday_badge(p(-1), today) == ("마감일 지남", "closed")
    assert dday_badge(p(None), today) is None  # 상시채용 등
    assert dday_badge(p(5, "DELETED"), today) == ("마감·삭제됨", "closed")


def test_favorite_tab_dday_changes_and_past(env):
    factory, config, users = env
    soon = date.today() + timedelta(days=10)
    sync_items(factory, [item(1, deadline="~ 12/31", deadline_at=soon), item(2), item(3)])
    enable_login(factory)
    client = TestClient(create_app(factory, config))
    assert 'class="view-tabs"' not in client.get("/").text  # 비로그인: 탭 없음
    assert "즐겨찾기한 공고" not in client.get("/", params={"view": "fav"}).text  # 탭 무시하고 전체 공고
    login(client, users, "a@example.com")
    with factory() as s:
        ids = {p.title: p.id for p in s.scalars(select(JobPosting))}
    assert "즐겨찾기한 공고가 없습니다" in client.get("/", params={"view": "fav"}).text
    for t in ("공고1", "공고2"):
        client.put(f"/api/favorites/postings/{ids[t]}")

    # 즐겨찾기한 뒤 공고1의 마감이 바뀌고, 공고2는 목록에서 사라짐(삭제)
    sync_items(factory, [item(1, deadline="~ 01/15", deadline_at=soon + timedelta(days=5)), item(3)])
    html = client.get("/", params={"view": "fav"}).text
    assert 'aria-current="page"><span class="tab-star"' in html and '<span class="tab-n" data-fav-total>2</span>' in html
    # 해제하면 목록에서 빼고 숫자를 줄이기 위한 표시 (favorite.js)
    assert '<ul class="list" data-view="fav">' in html and 'data-sum="changed"' in html and 'data-sum="past"' in html
    assert "공고3" not in html  # 즐겨찾기만
    assert 'title="최근 7일: 마감 변경">변경됨</a>' in html
    assert "badge badge-dday is-later" in html and "마감·삭제됨" in html
    assert html.index('data-fav-name="공고1"') < html.index("<span>지난 공고</span>") < html.index('data-fav-name="공고2"')  # 삭제된 공고는 아래 '지난 공고'
    assert "최근 7일 변경 <b>1</b>" in html and "지난 공고 <b>1</b>" in html
    assert 'class="nav-badge"' in html and "변경된 즐겨찾기 공고 1건" in html
    # 전체 공고 탭: 즐겨찾기 공고에만 배지, 삭제된 공고는 기본(활성)에서 빠짐
    html = client.get("/").text
    assert html.count("badge-dday") == 1 and "지난 공고</span>" not in html

    # 다른 계정에는 보이지 않음
    other = TestClient(create_app(factory, config))
    login(other, users, "b@example.com")
    assert "badge-changed" not in other.get("/").text and "nav-badge" not in other.get("/").text


def test_changes_before_favoriting_are_not_alerts(env):
    factory, config, users = env
    sync_items(factory, [item(1, deadline="~ 12/31")])
    sync_items(factory, [item(1, deadline="~ 01/15")])  # 즐겨찾기 전에 바뀜
    enable_login(factory)
    client = TestClient(create_app(factory, config))
    login(client, users, "a@example.com")
    with factory() as s:
        pid = s.scalar(select(JobPosting.id))
    client.put(f"/api/favorites/postings/{pid}")
    assert "badge-changed" not in client.get("/", params={"view": "fav"}).text


def test_company_feed_new_postings(env):
    factory, config, users = env
    sync_items(factory, [item(1, company="(주)넷마블"), item(2, company="카카오")])
    enable_login(factory)
    client = TestClient(create_app(factory, config))
    login(client, users, "a@example.com")
    assert "즐겨찾기한 회사가 없습니다" in client.get("/", params={"view": "company"}).text
    client.put("/api/favorites/companies/11", json={"name": "넷마블 주식회사", "address": "서울"})

    html = client.get("/", params={"view": "company"}).text
    assert "공고1" in html and "공고2" not in html  # 법인 표기·띄어쓰기를 빼고 같은 회사
    assert "badge-new" not in html  # 즐겨찾기 전에 이미 있던 공고는 새 공고가 아님
    # 관심 회사 칩의 ×와, 빼면 바로 목록에서 지울 공고 표시 (favorite.js가 data-key·data-company-key로 맞춘다)
    assert '<span class="fav-company" data-key="넷마블">' in html and 'class="fav-company-del" data-company-id="11"' in html
    assert 'data-company-key="넷마블"' in html

    # 즐겨찾기한 뒤 새로 수집된 공고 2건: 탭 숫자·상단 숫자·NEW
    sync_items(factory, [item(1, company="(주)넷마블"), item(2, company="카카오"), item(4, company="넷마블(주)"),
                         item(5, company="넷마블")])
    with factory() as s:
        ids = {p.title: p.id for p in s.scalars(select(JobPosting))}
    html = client.get("/").text
    assert 'data-new-count>2<' in html and "관심 회사 새 공고 2건" in html

    # 탭을 보기만 해서는 NEW가 떨어지지 않는다
    for _ in range(2):
        html = client.get("/", params={"view": "company"}).text
        assert html.count('class="badge badge-new"') == 2 and 'data-new-count>2<' in html

    # 상세를 열면 확인한 것
    assert client.get(f"/postings/{ids['공고4']}").status_code == 200
    html = client.get("/", params={"view": "company"}).text
    assert f'data-new-id="{ids["공고5"]}"' in html and f'data-new-id="{ids["공고4"]}"' not in html
    assert 'data-new-count>1<' in html
    # 원문 링크를 누르면(favorite.js가 알림) 확인한 것
    assert client.post(f"/api/postings/{ids['공고5']}/viewed").status_code == 204
    assert client.post("/api/postings/999999/viewed").status_code == 404
    html = client.get("/", params={"view": "company"}).text
    assert "badge-new" not in html and "data-new-count" not in html and "nav-badge" not in html
    # 캐시된 화면을 맞출 때 쓰는 조회
    res = client.get("/api/postings/new", params={"ids": f"{ids['공고4']},{ids['공고5']}"}).json()
    assert res == {"ok": True, "new": [], "count": 0, "alerts": 0}

    # 다른 계정은 따로 (그 계정은 회사를 즐겨찾기하지 않았음)
    other = TestClient(create_app(factory, config))
    login(other, users, "b@example.com")
    assert other.post(f"/api/postings/{ids['공고5']}/viewed").status_code == 204
    other.put("/api/favorites/companies/11", json={"name": "넷마블"})
    assert "badge-new" not in other.get("/", params={"view": "company"}).text  # 즐겨찾기 전에 수집된 공고
    # 비로그인은 기록하지 않음
    assert TestClient(create_app(factory, config)).post(f"/api/postings/{ids['공고5']}/viewed").status_code == 403


def test_company_key_backfill(tmp_path):
    from sqlalchemy import text

    from recruit_collector.models import make_session_factory
    url = f"sqlite:///{tmp_path / 'k.db'}"
    factory = make_session_factory(url)
    sync_items(factory, [item(1, company="(주)카카오 뱅크")])
    with factory() as s:
        s.execute(text("UPDATE job_postings SET company_key = NULL"))
        s.commit()
    factory = make_session_factory(url)  # 다시 열면 비어 있는 값을 채운다
    with factory() as s:
        assert s.scalar(select(JobPosting.company_key)) == "카카오뱅크"

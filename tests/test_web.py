import pytest
from fastapi.testclient import TestClient

from recruit_collector.crawlers.base import CrawlResult, Posting
from recruit_collector.models import CrawlRun, make_session_factory, utcnow
from recruit_collector.sync import sync_search
from recruit_collector.web.app import create_app


def posting(pid, title, **kw):
    return Posting(external_id=pid, title=title, company=f"회사{pid}", detail_url=f"https://example.com/{pid}", **kw)


@pytest.fixture
def client(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'web.db'}")
    with factory() as s:
        def sync(search, items):
            sync_search(s, site="saramin", search_name=search, result=CrawlResult(items, 1, True), missing_threshold=1)

        sync("saramin-java", [posting("1", "Java 백엔드", location="서울 강남구"), posting("2", "웹개발자", location="경기 성남시")])
        sync("saramin-java", [posting("1", "Java 백엔드", location="서울 강남구", deadline="~ 12/01")])  # 2 삭제, 1 변경
        s.add(CrawlRun(search_name="saramin-java", site="saramin", started_at=utcnow(), finished_at=utcnow(), status="SUCCESS", found=1))
        s.commit()
    return TestClient(create_app(factory))


def test_list_defaults_to_active(client):
    html = client.get("/").text
    assert "Java 백엔드" in html
    assert "웹개발자" not in html


def test_headhunting_filter_and_badge(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'hh.db'}")
    with factory() as s:
        sync_search(s, site="jobkorea", search_name="jk", missing_threshold=1, result=CrawlResult([
            posting("1", "서치펌 공고", headhunting=True), posting("2", "직접 채용", headhunting=False)], 1, True))
        sync_search(s, site="wanted", search_name="wt", missing_threshold=1,
                    result=CrawlResult([posting("3", "구분 없음")], 1, True))
        s.commit()
    client = TestClient(create_app(factory))
    html = client.get("/").text
    assert html.count('class="pill pill-hh"') == 1 and '<select name="hh">' in html
    assert '<option value="saramin_hh"' not in html  # 플랫폼 필터에는 크롤러가 아닌 플랫폼만
    html = client.get("/", params={"hh": "only"}).text
    assert "서치펌 공고" in html and "직접 채용" not in html and "구분 없음" not in html
    assert '<option value="only" selected>' in html
    html = client.get("/", params={"hh": "exclude"}).text
    assert "서치펌 공고" not in html and "직접 채용" in html and "구분 없음" in html  # 알 수 없는 공고는 남김
    assert "서치펌 공고" in client.get("/", params={"hh": "이상한값"}).text


def test_headhunting_filled_from_raw_for_existing_postings(tmp_path):
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path / 'fill.db'}"
    factory = make_session_factory(url)
    with factory() as s:
        sync_search(s, site="jobkorea", search_name="jk", missing_threshold=1, result=CrawlResult([
            posting("1", "a", raw={"userRefType": "S"}), posting("2", "b", raw={"userRefType": "C"})], 1, True))
        sync_search(s, site="remember", search_name="rm", missing_threshold=1, result=CrawlResult([
            posting("3", "c", raw={"job_posting_type": "internal_headhunter"})], 1, True))
        sync_search(s, site="saramin", search_name="sr", missing_threshold=1, result=CrawlResult([posting("4", "d")], 1, True))
        sync_search(s, site="wanted", search_name="wt", missing_threshold=1, result=CrawlResult([posting("5", "e")], 1, True))
        s.execute(text("UPDATE job_postings SET headhunting = NULL"))  # 컬럼 추가 전에 수집한 공고
        s.commit()
    with make_session_factory(url)() as s:  # 다시 열면 원본(raw)으로 채운다
        got = dict(s.execute(text("SELECT external_id, headhunting FROM job_postings")).all())
    assert got == {"1": True, "2": False, "3": True, "4": None, "5": False}  # 원티드는 일반


def test_list_filters(client):
    assert "웹개발자" in client.get("/", params={"status": "DELETED"}).text
    assert "웹개발자" in client.get("/", params={"status": "ALL", "location": "경기"}).text
    assert "Java 백엔드" not in client.get("/", params={"status": "ALL", "location": "경기"}).text
    assert "회사1" in client.get("/", params={"q": "회사1"}).text
    assert "조건에 맞는 공고가 없습니다" in client.get("/", params={"q": "없는공고"}).text


def test_detail_shows_history(client):
    html = client.get("/postings/1").text
    assert "Java 백엔드" in html
    assert "신규" in html and "변경" in html
    assert "~ 12/01" in html
    assert client.get("/postings/999").status_code == 404


def test_review_button_opens_jobplanet_modal(client):
    """회사명 옆 리뷰 버튼과 모달이 목록·상세에 있다. 리뷰 데이터는 서버가 가져오지 않는다 (링크만)."""
    for html in (client.get("/").text, client.get("/postings/1").text):
        assert 'class="review-btn" data-company="회사1"' in html
        assert 'id="review-modal"' in html and "/static/review.js" in html
    assert "jobplanet.co.kr/search?query=" in client.get("/static/review.js").text


def test_runs_page(client):
    html = client.get("/runs").text
    assert "saramin-java" in html and "SUCCESS" in html


@pytest.fixture
def sorted_client(tmp_path):
    from datetime import date, datetime

    factory = make_session_factory(f"sqlite:///{tmp_path / 'sort.db'}")
    t = lambda h: datetime(2026, 11, 5, h)  # noqa: E731
    items = [
        # 제목, 등록, 마감, 수집
        posting("a", "A공고", posted_at=t(1), deadline_at=date(2099, 1, 3), collected_at=t(9)),
        posting("b", "B공고", posted_at=t(3), deadline_at=date(2099, 1, 1), collected_at=t(7)),
        posting("c", "C공고", posted_at=None, deadline_at=None, collected_at=t(8)),
        posting("d", "D공고", posted_at=t(2), deadline_at=date(2099, 1, 2), collected_at=t(6)),
    ]
    with factory() as s:
        sync_search(s, site="jobkorea", search_name="jk", result=CrawlResult(items, 1, True), missing_threshold=1)
        # D만 내용이 바뀜 → 최근 변경 1순위
        changed = posting("d", "D공고", location="서울", posted_at=t(2), deadline_at=date(2099, 1, 2), collected_at=t(10))
        sync_search(s, site="jobkorea", search_name="jk", result=CrawlResult([changed], 1, False), missing_threshold=1)
        s.add(CrawlRun(search_name="jk", site="jobkorea", started_at=utcnow(), status="RUNNING", pages=3, found=60, total=200))
        s.commit()
    return TestClient(create_app(factory))


def order(html: str, titles=("A공고", "B공고", "C공고", "D공고")) -> str:
    return "".join(t[0] for t in sorted(titles, key=html.index))


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("posted", "BDAC"),     # 최근 등록순, 등록 시각 없는 C는 맨 뒤
        ("collected", "DACB"),  # 최근 수집순
        ("updated", "DACB"),    # 최근 변경순 (처음 수집 시에는 수집 시각 = 변경 시각)
        ("deadline", "BDAC"),   # 마감 임박순, 마감일 없는 C는 맨 뒤
    ],
)
def test_sorts(sorted_client, sort, expected):
    assert order(sorted_client.get("/", params={"sort": sort}).text) == expected


def test_default_sort_is_recent_posted(sorted_client):
    assert order(sorted_client.get("/").text) == "BDAC"


def test_status_api_reports_progress(sorted_client):
    d = sorted_client.get("/api/status").json()
    assert d["running"] is True
    assert d["summary"]["active"] == 4
    jk = next(x for x in d["searches"] if x["name"] == "jk")
    assert (jk["status"], jk["found"], jk["total"], jk["pages"]) == ("RUNNING", 60, 200, 3)


def test_filter_form_layout(client):
    """검색어 + 조회, 라벨 있는 상세 조건, 결과 줄의 정렬(폼 밖이지만 form 속성으로 함께 전송)."""
    html = client.get("/").text
    assert 'id="filters"' in html and 'class="f-submit"' in html
    assert '<select name="sort" form="filters" data-autosubmit>' in html
    assert 'data-active="0"' in html  # 고른 조건이 없으면 모바일에서 접힘
    html = client.get("/", params={"status": "ALL", "career": "0"}).text
    assert 'data-active="2"' in html and "전체 상태 · 신입" in html
    assert "초기화" not in html  # 초기화 버튼은 두지 않음


def test_career_multi_select(client):
    """경력은 여러 구간을 체크해 함께 찾는다 (합집합). 요약은 2개까지 이름, 더 많으면 '외 N개'."""
    html = client.get("/").text
    assert '<span class="ms-text">전체 경력</span>' in html and 'name="career" value="0"' in html
    html = client.get("/", params={"career": ["1-3", "0"]}).text
    assert '<span class="ms-text">신입, 1~3년차</span>' in html
    assert 'value="0" data-label="신입" checked' in html and 'value="1-3" data-label="1~3년차" checked' in html
    html = client.get("/", params={"career": ["0", "1-3", "any", "없음"]}).text
    assert '<span class="ms-text">신입 외 2개</span>' in html


def test_employment_multi_select(tmp_path):
    """고용형태는 여러 개를 체크해 함께 찾는다 (합집합). 표기의 항목 단위로 맞추고, 사이트별 표기 차이를 묶는다."""
    factory = make_session_factory(f"sqlite:///{tmp_path / 'emp.db'}")
    with factory() as s:
        items = [
            posting("1", "정규직공고", employment_type="정규직"),
            posting("2", "잡코리아계약", employment_type="정규직, 계약직"),
            posting("3", "사람인기간제", employment_type="정규직·기간제"),
            posting("4", "전환형파견", employment_type="파견직 (정규직 전환가능)·파견직"),
            posting("5", "인턴공고", employment_type="인턴직"),
            posting("6", "형태없음"),
        ]
        sync_search(s, site="saramin", search_name="x", result=CrawlResult(items, 1, True), missing_threshold=1)
        s.commit()
    client = TestClient(create_app(factory))

    def titles(**params):
        html = client.get("/", params=params).text
        return {t for t in ("정규직공고", "잡코리아계약", "사람인기간제", "전환형파견", "인턴공고", "형태없음") if f">{t}<" in html}

    html = client.get("/").text
    assert '<span class="ms-text">전체 고용형태</span>' in html and 'name="employment" value="regular"' in html
    assert 'name="search"' not in html and "크롤러" not in html  # 크롤러(검색 조건) 선택은 없음
    assert titles(employment="regular") == {"정규직공고", "잡코리아계약", "사람인기간제"}  # 파견직 (정규직 전환가능)은 제외
    assert titles(employment="contract") == {"잡코리아계약", "사람인기간제"}
    assert titles(employment=["intern", "dispatch"]) == {"전환형파견", "인턴공고"}
    assert len(titles()) == 6 and len(titles(employment="없음")) == 6
    html = client.get("/", params={"employment": ["intern", "regular"]}).text
    assert '<span class="ms-text">정규직, 인턴</span>' in html  # 선택지 순서대로 요약
    assert 'value="intern" data-label="인턴" checked' in html


def test_page_size(tmp_path):
    """한 페이지당 표시 수: 기본 10개, 10~50개 중 선택. 다른 값은 기본값으로. 페이지 이동 링크에 유지."""
    factory = make_session_factory(f"sqlite:///{tmp_path / 'size.db'}")
    with factory() as s:
        items = [posting(f"p{i:02d}", f"공고{i:02d}번") for i in range(25)]
        sync_search(s, site="saramin", search_name="x", result=CrawlResult(items, 1, True), missing_threshold=1)
        s.commit()
    client = TestClient(create_app(factory))

    def count(html):
        return html.count('class="title"')

    html = client.get("/").text
    assert count(html) == 10 and "1 / 3" in html
    assert '<option value="10" selected>10개씩</option>' in html
    assert 'href="/?status=ACTIVE&amp;sort=posted&amp;page=2"' not in html  # 기본값(10)은 주소에 넣지 않음
    html = client.get("/", params={"size": 20}).text
    assert count(html) == 20 and "1 / 2" in html and "size=20&amp;page=2" in html
    assert count(client.get("/", params={"size": 20, "page": 2}).text) == 5
    assert count(client.get("/", params={"size": 50}).text) == 25
    assert count(client.get("/", params={"size": 7}).text) == 10  # 선택지에 없는 값


def test_favicon(client):
    """파비콘: 모든 페이지 head에 연결하고, 브라우저가 찾는 루트 주소로도 응답한다."""
    html = client.get("/").text
    assert 'href="/static/favicon.svg?v=1"' in html and 'href="/favicon.ico?v=1"' in html and 'href="/apple-touch-icon.png?v=1"' in html
    res = client.get("/favicon.ico")
    assert res.status_code == 200 and res.headers["content-type"] == "image/x-icon" and res.content[:4] == bytes([0, 0, 1, 0])
    res = client.get("/apple-touch-icon.png")
    assert res.status_code == 200 and res.content[:4] == b"\x89PNG"
    res = client.get("/static/favicon.svg")
    assert res.status_code == 200 and res.headers["content-type"].startswith("image/svg+xml")


def test_blocks_search_engines(client):
    """robots.txt로 전체 수집 금지, 모든 응답에 X-Robots-Tag, 페이지에 robots 메타 태그."""
    res = client.get("/robots.txt")
    assert res.status_code == 200 and res.text == "User-agent: *\nDisallow: /\n"
    assert res.headers["content-type"].startswith("text/plain")
    for path in ("/", "/runs", "/static/style.css", "/api/status", "/없는주소"):
        assert client.get(path).headers["x-robots-tag"] == "noindex, nofollow, noarchive", path
    assert '<meta name="robots" content="noindex, nofollow, noarchive">' in client.get("/").text


def test_highlight_search_term(tmp_path):
    """검색하면 공고명·회사명에서 검색어와 일치하는 부분을 표시한다 (대소문자 무시, 나머지는 이스케이프)."""
    from recruit_collector.web.app import highlight

    assert str(highlight("Java 백엔드 java", "JAVA")) == '<mark class="hl">Java</mark> 백엔드 <mark class="hl">java</mark>'
    assert str(highlight("<b>a+b</b>", "a+b")) == '&lt;b&gt;<mark class="hl">a+b</mark>&lt;/b&gt;'
    assert str(highlight("<i>", "")) == "&lt;i&gt;" and str(highlight(None, "x")) == ""

    factory = make_session_factory(f"sqlite:///{tmp_path / 'hl.db'}")
    with factory() as s:
        items = [Posting(external_id="1", title="백엔드 개발자", company="백엔드랩", detail_url="https://e/1")]
        sync_search(s, site="saramin", search_name="x", result=CrawlResult(items, 1, True), missing_threshold=1)
        s.commit()
    client = TestClient(create_app(factory))
    html = client.get("/", params={"q": "백엔드"}).text
    assert '<mark class="hl">백엔드</mark> 개발자</a>' in html and '<mark class="hl">백엔드</mark>랩</a>' in html  # 회사명 (연봉·인원 검색 링크)
    assert '<mark class="hl">' not in client.get("/").text


def test_runs_paging_and_site_charts(tmp_path):
    import json
    import re
    from datetime import timedelta

    factory = make_session_factory(f"sqlite:///{tmp_path / 'runs.db'}")
    start = utcnow() - timedelta(days=10)
    with factory() as s:
        for i in range(60):  # 사람인 60회 (마지막 3회 중 1회 실패), 원티드 2회
            t = start + timedelta(hours=3 * i)
            s.add(CrawlRun(search_name="s-java", site="saramin", started_at=t, finished_at=t + timedelta(seconds=90),
                           status="FAILED" if i == 58 else "SUCCESS", trigger="schedule", found=1000 + i, created=i % 5, deleted=i % 3))
        for i in range(2):
            t = start + timedelta(hours=i)
            s.add(CrawlRun(search_name="w-be", site="wanted", started_at=t, finished_at=t + timedelta(seconds=30),
                           status="SUCCESS", trigger="manual", found=50))
        s.commit()
    client = TestClient(create_app(factory))

    html = client.get("/runs").text
    assert html.count("<tr>") - 1 == 50 and "1 / 2" in html and 'href="/runs?page=2"' in html
    assert "62건" in html
    data = json.loads(re.search(r'<script type="application/json" id="run-charts">(.*?)</script>', html, re.S).group(1))
    assert set(data) == {"saramin", "wanted"} and len(data["saramin"]) == 30 and len(data["wanted"]) == 2
    assert data["saramin"][-1]["found"] == 1059 and data["saramin"][0]["found"] == 1030  # 오래된 것부터
    assert "97%" in html and "1분 30초" in html and "1,059건" in html  # 성공률 29/30, 평균 소요, 최근 발견
    assert "실패 1" in html and "잡코리아" not in html.split('id="run-charts"')[0].split("run-cards")[1]  # 기록 없는 사이트는 카드 없음

    page2 = client.get("/runs", params={"page": 2}).text
    assert page2.count("<tr>") - 1 == 12 and "2 / 2" in page2
    assert client.get("/runs", params={"page": 99}).status_code == 200  # 넘치면 마지막 페이지
    filtered = client.get("/runs", params={"search": "w-be"}).text
    assert filtered.count("<tr>") - 1 == 2 and 'class="pager"' not in filtered



def test_privacy_and_terms_pages_are_public_and_linked(client):
    for path, title in (("/privacy", "개인정보처리방침"), ("/terms", "서비스 이용약관")):
        res = client.get(path)
        assert res.status_code == 200 and title in res.text
        assert "juwonlee.dev@gmail.com" in res.text and "2026-11-14" in res.text
    assert "Limited Use" in client.get("/privacy").text  # 구글 OAuth 심사 문구
    footer = client.get("/").text
    assert 'href="/privacy"' in footer and 'href="/terms"' in footer

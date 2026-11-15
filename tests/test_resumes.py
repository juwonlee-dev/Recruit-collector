import pytest
from fastapi.testclient import TestClient

from recruit_collector.models import Resume, ResumeTemplate, ResumeWidget
from recruit_collector.web import resumes as rs
from recruit_collector.web.app import create_app
from tests.test_auth import enable_login, env, login  # noqa: F401  (env는 pytest 픽스처)


def form_widget(**config):
    return {"kind": "form", "config": {"title": "경력", "fields": [{"key": "company", "label": "회사", "type": "text"}], **config}}


def layout_with(*cols):
    return {"rows": [{"id": "r1", "cols": [{"id": f"c{i}", "span": span, "items": items} for i, (span, items) in enumerate(cols)]}]}


# ---------------------------------------------------------------- 검증

def test_normalize_widget_rules():
    kind, c = rs.normalize_widget("form", {"title": "  학력 ", "repeatable": 1, "fields": [
        {"key": "a", "label": "학교", "type": "select", "options": ["학사", "", "학사", "석사"], "span": 40},
        {"key": "a", "label": "", "type": "nope", "placeholder": "x" * 999},
    ]})
    assert kind == "form" and c["title"] == "학력" and c["repeatable"] is True and c["show_title"] is True
    f1, f2 = c["fields"]
    assert f1["options"] == ["학사", "석사"] and f1["span"] == 12
    assert f2["key"] != "a" and f2["type"] == "text" and f2["label"] == "항목 2" and len(f2["placeholder"]) == 300
    with pytest.raises(rs.Invalid):
        rs.normalize_widget("form", {"fields": []})
    with pytest.raises(rs.Invalid):
        rs.normalize_widget("script", {})
    assert rs.normalize_widget("heading", {"text": "", "size": "xx", "align": "?"}) == (
        "heading", {"text": "제목", "size": "lg", "align": "left", "style": {}})
    assert rs.normalize_widget("note", {"text": "a"})[1]["align"] == "center"  # 고정 문구는 가운데가 기본
    assert rs.normalize_widget("divider", {"weight": "thick", "dash": "x", "space": "wide"})[1] == {
        "weight": "thick", "dash": "solid", "space": "wide", "style": {}}


def test_style_and_theme_are_normalized():
    assert rs.color("#ABC") == "#aabbcc" and rs.color("1a2b3c") == "#1a2b3c"
    for bad in ("red", "#12345", "#1a2b3c;background:url(x)", "#ggg", None, 7):  # style 속성에 들어가므로 색 코드만 통과
        assert rs.color(bad) == ""
    # 위젯 스타일: 종류마다 쓰는 값만, 기본값(100%)·잘못된 값은 버린다
    _, c = rs.normalize_widget("form", {"fields": [{"label": "a"}], "style": {
        "font": "nanummyeongjo", "size": "120", "color": "#F00", "title_color": "nope", "line_color": "#00ff00", "x": 1}})
    assert c["style"] == {"font": "nanummyeongjo", "size": 120, "color": "#ff0000", "line_color": "#00ff00"}
    assert rs.normalize_style("divider", {"font": "nanumgothic", "size": 150, "color": "#123456"}) == {"color": "#123456"}
    assert rs.normalize_style("note", {"size": 100, "font": "comic"}) == {}
    assert rs.normalize_style("heading", {"size": 999})["size"] == 200
    # 템플릿 스타일: 기본값과 같으면 남기지 않는다
    assert rs.normalize_theme({"font": "pretendard", "size": 8.7, "text": "#000000"}) == {}
    assert rs.normalize_theme({"font": "gowunbatang", "size": "30", "head": "#0a3d91", "accent": "zz"}) == {
        "font": "gowunbatang", "size": 12.0, "head": "#0a3d91"}
    assert rs.normalize_theme({"size": "nan"}) == {} and rs.normalize_theme("x") == {}
    assert rs.normalize_layout({"rows": [], "theme": {"line": "#ccc"}})["theme"] == {"line": "#cccccc"}


def test_document_style_variables():
    w = {"id": "w1", "kind": "form", "config": {"title": "경력", "fields": [{"key": "a", "label": "회사"}],
                                                "style": {"size": 150, "title_color": "#0a3d91", "font": "nanummyeongjo"}}}
    layout = rs.normalize_layout({**layout_with((12, [w])), "theme": {"size": 10.44, "accent": "#ff0000", "font": "nanumgothic"}})
    body = rs.theme_style(layout["theme"])
    assert "--k: 1.1954" in body and "--c-accent: #ff0000" in body and "NanumGothic" in body and "--c-text" not in body
    css = rs.document_rows(layout, {"w1": [{"a": "가나다"}]})[0]["cols"][0]["items"][0]["css"]
    assert "--k: 1.7931" in css and "--c-head: #0a3d91" in css and "--c-title: #0a3d91" in css and "NanumMyeongjo" in css
    files = [f["file"] for f in rs.font_faces(layout)]
    assert "Pretendard-Regular" in files and "NanumGothic-Bold" in files and "NanumMyeongjo-Regular" in files
    assert not any(f.startswith("GowunBatang") for f in files)  # 쓰지 않는 글꼴은 넣지 않는다
    assert rs.theme_style({}) == "" and rs.document_rows(rs.normalize_layout(layout_with((12, [w]))), {"w1": [{"a": "x"}]})[0]
    for key in rs.FONTS.values():  # 글꼴 파일이 실제로 있어야 한다
        for _, file in key["faces"]:
            assert (rs_fonts_dir() / f"{file}.woff2").is_file(), file


def rs_fonts_dir():
    from recruit_collector.web.resumes_web import FONTS_DIR
    return FONTS_DIR


def test_normalize_layout_rules():
    w = {"id": "same", **form_widget()}
    out = rs.normalize_layout(layout_with((6, [w]), (6, [dict(w)])))
    ids = [it["id"] for it in rs.iter_items(out)]
    assert len(set(ids)) == 2  # 겹치는 ID는 새로 붙인다
    with pytest.raises(rs.Invalid, match="12"):
        rs.normalize_layout(layout_with((8, []), (8, [])))
    with pytest.raises(rs.Invalid):
        rs.normalize_layout(layout_with(*[(2, [])] * 5))  # 한 행 최대 4칸
    with pytest.raises(rs.Invalid):
        rs.normalize_layout({"rows": "x"})


def test_data_defaults_and_normalize():
    layout = rs.normalize_layout(layout_with((12, [{"id": "w1", "kind": "form", "config": {"title": "t", "repeatable": True, "fields": [
        {"key": "name", "label": "이름", "default": "예시"}, {"key": "tags", "label": "기술", "type": "tags", "default": "Java, Spring"}]}}])))
    assert rs.default_data(layout) == {"w1": [{"name": "예시", "tags": ["Java", "Spring"]}]}
    data = rs.normalize_data(layout, {"w1": [{"name": " A ", "tags": "a, b, a", "x": 1}, "bad"], "ghost": [{}]})
    assert data == {"w1": [{"name": "A", "tags": ["a", "b"]}, {"name": "", "tags": []}]}


def test_display_options_are_normalized():
    _, c = rs.normalize_widget("form", {"display": "timeline", "title_style": "bar", "show_total": 1,
                                        "fields": [{"label": "a", "slot": "side"}, {"label": "b", "slot": "nope", "hide_label": 1}]})
    assert (c["display"], c["title_style"], c["show_total"], c["show_duration"]) == ("timeline", "bar", True, False)
    assert [f["slot"] for f in c["fields"]] == ["side", "info"] and c["fields"][1]["hide_label"] is True
    _, c = rs.normalize_widget("form", {"display": "x", "title_style": "y", "fields": [{"label": "a"}]})
    assert (c["display"], c["title_style"]) == ("grid", "line")


def test_birth_date_shows_year_and_man_age(monkeypatch):
    from datetime import date
    assert rs.man_age(date(1999, 5, 12), date(2026, 5, 11)) == 26  # 생일 전날
    assert rs.man_age(date(1999, 5, 12), date(2026, 5, 12)) == 27  # 생일 당일
    monkeypatch.setattr(rs, "_today", lambda: date(2026, 11, 14))
    _, c = rs.normalize_widget("form", {"fields": [{"key": "b", "label": "생년월일", "type": "date", "age": True},
                                                   {"key": "t", "label": "x", "type": "text", "age": True}]})
    birth, text = c["fields"]
    assert birth["age"] is True and text["age"] is False  # 나이 표시는 날짜 칸에만
    assert rs.display_text(birth, "1999-12-01") == "1999 (26세)"
    assert rs.display_text(birth, "엉뚱한 값") == "엉뚱한 값"
    assert rs.display_text({"type": "date"}, "2022-07-05") == "2022.07.05"
    profile = next(w for w in rs.BUILTIN_WIDGETS if w["key"] == "profile")["config"]["fields"]
    assert {f["key"]: f["type"] for f in profile}["birth"] == "date" and {f["key"]: f["type"] for f in profile}["address"] == "address"


def test_timeline_period_duration_and_total(monkeypatch):
    monkeypatch.setattr(rs, "_this_month", lambda: (2026, 11))
    layout = rs.normalize_layout(layout_with((12, [{"id": "w1", "kind": "form", "config": {
        "title": "경력", "display": "timeline", "show_duration": True, "show_total": True, "repeatable": True, "fields": [
            {"key": "s", "label": "입사", "type": "month", "slot": "side"},
            {"key": "e", "label": "퇴사", "type": "month", "slot": "side"},
            {"key": "c", "label": "회사", "slot": "title"},
            {"key": "d", "label": "부서", "slot": "meta"},
            {"key": "b", "label": "업무", "type": "textarea", "slot": "body"},
        ]}}])))
    data = {"w1": [{"s": "2021-08", "e": "2026-02", "c": "가회사", "d": "", "b": "개발"},
                   {"s": "2025-01", "e": "", "c": "나회사"},  # 겹치는 기간 + 재직 중
                   {"s": "", "e": "", "c": "", "b": ""}]}  # 빈 항목은 빠진다
    item = rs.document_rows(layout, rs.normalize_data(layout, data))[0]["cols"][0]["items"][0]
    first, second = item["entries"]
    assert (first["period"], first["duration"]) == ("2021.08 ~ 2026.02", "4년 7개월")  # 시작·끝 달 포함
    assert [c["field"]["key"] for c in first["title"]] == ["c"] and not first["meta"]
    assert (second["period"], second["duration"]) == ("2025.01 ~ 현재", "1년 11개월")
    assert item["total"] == "5년 4개월" and item["has_side"]  # 2021.08 ~ 2026.11, 겹친 기간은 한 번만
    assert rs.months_text(0) == "1개월 미만" and rs.merged_months([(0, 2), (10, 11)]) == 5


def test_saramin_template_renders_each_display(users):
    factory, a, _ = users
    with factory() as s:
        tid = s.query(ResumeTemplate).filter_by(builtin_key="basic").one().id
    doc = a.get(f"/resumes/templates/{tid}/preview").text
    assert "/static/fonts/Pretendard-Regular.woff2" in doc
    for css in ("is-profile", "is-cards", "is-timeline", "is-labeled", "w-title is-bar", "pf-badge"):
        assert css in doc


# ---------------------------------------------------------------- 화면

@pytest.fixture
def users(env):  # noqa: F811
    factory, config, google = env
    enable_login(factory)
    a = TestClient(create_app(factory, config))
    b = TestClient(create_app(factory, config))
    login(a, google, "a@example.com", name="가나다")
    login(b, google, "b@example.com")
    return factory, a, b


def test_resume_pages_require_login(env):  # noqa: F811
    factory, config, _ = env
    client = TestClient(create_app(factory, config))
    res = client.get("/resumes", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"].startswith("/auth/login?next=/resumes")
    assert client.put("/api/resumes/1", json={}).status_code == 403


def test_builtins_seeded_once(users):
    factory, a, _ = users
    TestClient(create_app(factory, None))  # 다시 띄워도 늘지 않는다
    with factory() as s:
        assert s.query(ResumeTemplate).filter(ResumeTemplate.builtin_key.is_not(None)).count() == len(rs.BUILTIN_TEMPLATES)
        assert s.query(ResumeWidget).filter(ResumeWidget.builtin_key.is_not(None)).count() == len(rs.BUILTIN_WIDGETS)
    page = a.get("/resumes?tab=templates").text
    assert "기본 이력서" in page
    assert '<details class="rs-guide" open>' in page  # 내 템플릿이 없으면 만드는 순서 안내를 펼쳐 둔다
    assert "위젯 만드는 순서" in a.get("/resumes?tab=widgets").text
    header = a.get("/").text  # 이력서 메뉴는 사용자 메뉴(마이페이지와 로그아웃 사이)에 있다
    nav = header[header.index("<nav>"):header.index("</nav>")]
    menu = header[header.index('class="usermenu-panel"'):header.index("</details>")]
    assert 'href="/resumes"' not in nav
    assert menu.index('href="/me"') < menu.index('href="/resumes"') < menu.index('action="/auth/logout"')


def test_obsolete_builtins_are_removed_but_copies_stay(users):
    from recruit_collector.models import utcnow
    factory, a, _ = users
    with factory() as s:  # 예전 정의에만 있던 기본 위젯·템플릿과 그 템플릿으로 만든 이력서
        now = utcnow()
        old_w = ResumeWidget(builtin_key="project", owner_id=None, visibility="public", name="프로젝트", kind="divider", config={},
                             created_at=now, updated_at=now)
        old_t = ResumeTemplate(builtin_key="saramin", owner_id=None, visibility="public", name="사람인형 이력서",
                               layout={"rows": []}, created_at=now, updated_at=now)
        s.add_all([old_w, old_t])
        s.flush()
        uid = s.query(Resume).count()  # noqa: F841
        from recruit_collector.models import User
        user = s.query(User).filter_by(email="a@example.com").one()
        s.add(Resume(owner_id=user.id, title="옛 이력서", template_id=old_t.id, template_name=old_t.name, layout={"rows": []}, data={},
                     created_at=now, updated_at=now))
        s.commit()
        rs.ensure_builtins(s)
        assert s.query(ResumeWidget).filter_by(builtin_key="project").count() == 0
        assert s.query(ResumeTemplate).filter_by(builtin_key="saramin").count() == 0
        r = s.query(Resume).filter_by(title="옛 이력서").one()
        assert r.template_id is None and r.template_name == "사람인형 이력서"


def test_write_resume_preview_and_pdf(users, monkeypatch):
    factory, a, b = users
    with factory() as s:
        tid = s.query(ResumeTemplate).filter_by(builtin_key="basic").one().id
    res = a.post("/resumes/new", data={"template_id": tid, "title": "백엔드 지원"}, follow_redirects=False)
    assert res.status_code == 303
    rid = int(res.headers["location"].split("/")[2])
    edit = a.get(f"/resumes/{rid}/edit").text
    assert "<title>이력서 작성 · 백엔드 지원" in edit and 'id="re-state"' in edit
    assert 'data-sticky-bar' in edit and 'id="re-save"' in edit  # 위쪽 고정 막대 + 수동 저장

    with factory() as s:
        r = s.get(Resume, rid)
        career = next(it for it in rs.iter_items(r.layout) if it["source_id"] and it["name"] == "경력")
        basic = next(it for it in rs.iter_items(r.layout) if it["name"] == "인적사항")
        essay = next(it for it in rs.iter_items(r.layout) if it["name"] == "자기소개서")
        assert r.data[essay["id"]][0]["title"] == "성장과정"  # 예시 텍스트가 미리 채워짐
    data = {basic["id"]: [{"name": "<b>홍길동</b>", "email": "hong@example.com"}],
            career["id"]: [{"company": "가회사", "duties": "개발"}, {"company": "나회사"}]}
    assert a.put(f"/api/resumes/{rid}", json={"title": "제목", "data": data}).json()["ok"]

    doc = a.get(f"/resumes/{rid}/preview").text
    assert "&lt;b&gt;홍길동&lt;/b&gt;" in doc and "<b>홍길동" not in doc  # 입력값은 이스케이프
    assert "가회사" in doc and "나회사" in doc and "PDF 다운로드" in doc

    monkeypatch.setattr(rs, "render_pdf", lambda html: b"%PDF-1.7 " + html.encode())
    pdf = a.get(f"/resumes/{rid}/pdf")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert "filename*=UTF-8''%EC%A0%9C%EB%AA%A9.pdf" in pdf.headers["content-disposition"]
    assert b"PDF \xeb\x8b\xa4\xec\x9a\xb4" not in pdf.content  # PDF에는 화면용 도구 막대가 없다

    # 다른 사용자는 볼 수도 고칠 수도 없다
    assert b.get(f"/resumes/{rid}/edit").status_code == 404
    assert b.get(f"/resumes/{rid}/pdf").status_code == 404
    assert b.put(f"/api/resumes/{rid}", json={"data": {}}).status_code == 404
    assert b.post(f"/resumes/{rid}/delete").status_code == 404

    a.post(f"/resumes/{rid}/copy")
    a.post(f"/resumes/{rid}/delete")
    with factory() as s:
        assert [r.title for r in s.query(Resume)] == ["제목 (복사본)"]


def test_template_build_share_and_snapshot(users):
    factory, a, b = users
    res = a.post("/resumes/templates/new", follow_redirects=False)
    tid = int(res.headers["location"].split("/")[3])
    page = a.get(f"/resumes/templates/{tid}/edit").text
    assert "rb-state" in page and "data-sticky-bar" in page and 'id="rb-save"' in page
    assert 'id="rb-tour"' in page and "resume_tour.js" in page  # 단계별 사용법 안내
    layout = layout_with((7, [{"id": "w1", "source_id": None, "name": "경력", **form_widget(repeatable=True)}]), (5, []))
    body = {"name": "내 템플릿", "description": "설명", "visibility": "private", "layout": layout}
    assert a.put(f"/api/resume-templates/{tid}", json=body).json()["ok"]
    bad = a.put(f"/api/resume-templates/{tid}", json={**body, "layout": layout_with((10, []), (10, []))})
    assert bad.status_code == 400 and "12" in bad.json()["message"]

    # 비공개: 다른 사용자는 못 본다 → 공개하면 미리보기·복사·이력서 작성 가능, 편집은 불가
    assert b.get(f"/resumes/templates/{tid}/preview").status_code == 404
    a.put(f"/api/resume-templates/{tid}", json={**body, "visibility": "public"})
    assert "내 템플릿" in b.get("/resumes?tab=templates").text
    assert b.get(f"/resumes/templates/{tid}/preview").status_code == 200
    assert b.get(f"/resumes/templates/{tid}/edit").status_code == 404
    assert b.put(f"/api/resume-templates/{tid}", json=body).status_code == 404
    res = b.post("/resumes/new", data={"template_id": tid}, follow_redirects=False)
    rid = int(res.headers["location"].split("/")[2])
    b.post("/resumes/templates/new", data={"from_id": tid})
    with factory() as s:
        assert s.query(ResumeTemplate).filter_by(owner_id=s.get(Resume, rid).owner_id, name="내 템플릿 (복사본)").count() == 1

    # 템플릿을 고치거나 지워도 이미 만든 이력서는 그대로
    a.put(f"/api/resume-templates/{tid}", json={**body, "layout": {"rows": []}})
    a.post(f"/resumes/templates/{tid}/delete")
    with factory() as s:
        r = s.get(Resume, rid)
        assert r.template_id is None and r.template_name == "내 템플릿" and len(list(rs.iter_items(r.layout))) == 1


def test_widgets_owned_and_builtin_readonly(users):
    factory, a, b = users
    res = a.post("/api/resume-widgets", json={"name": "수상", "visibility": "private", **form_widget()})
    w = res.json()["widget"]
    assert w["mine"] and w["config"]["fields"][0]["key"] == "company"
    assert a.put(f"/api/resume-widgets/{w['id']}", json={"name": "수상 내역", **form_widget(repeatable=True)}).json()["ok"]
    assert b.put(f"/api/resume-widgets/{w['id']}", json={"name": "x", **form_widget()}).status_code == 404
    assert "수상 내역" not in b.get("/resumes?tab=widgets").text  # 비공개
    with factory() as s:
        builtin = s.query(ResumeWidget).filter_by(builtin_key="career").one().id
    assert a.put(f"/api/resume-widgets/{builtin}", json={"name": "x", **form_widget()}).status_code == 404
    assert a.get(f"/resumes/widgets/{builtin}/edit").status_code == 404
    res = a.post("/resumes/widgets/new", data={"from_id": builtin}, follow_redirects=False)  # 복사해서 편집
    assert a.get(res.headers["location"]).status_code == 200
    res = a.post("/resumes/widgets/new", data={"kind": "note"}, follow_redirects=False)
    assert res.status_code == 303


def test_draft_preview_renders_unsaved_style(users):
    import json
    _, a, _ = users
    layout = {**layout_with((12, [{"id": "w1", "name": "경력", **form_widget(style={"color": "#ff0000"})}])),
              "theme": {"font": "gowunbatang", "size": 10}}
    page = a.post("/resumes/preview", data={"doc": json.dumps({"name": "편집 중", "layout": layout})})
    assert page.status_code == 200 and "<title>편집 중</title>" in page.text
    assert "GowunBatang-Regular.woff2" in page.text and "--c-text: #ff0000" in page.text  # 저장하지 않은 스타일
    widget = {"kind": "divider", "config": {"weight": "thick", "style": {"color": "#00ff00"}}}
    page = a.post("/resumes/preview", data={"doc": json.dumps({"name": "선", "widget": widget})}).text
    assert "wt-thick" in page and "--c-line: #00ff00" in page
    bad = a.post("/resumes/preview", data={"doc": json.dumps({"layout": {"rows": "x"}})})
    assert bad.status_code == 400
    assert a.post("/resumes/preview", data={"doc": "{"}).status_code == 400


def test_range_date_field_and_document(monkeypatch):
    monkeypatch.setattr(rs, "_this_month", lambda: (2026, 11))
    f = rs.normalize_field({"label": "기간", "type": "month", "range": 1, "open_end": 1, "age": 1}, 0, set())
    assert (f["range"], f["open_end"], f["allow_same"], f["age"]) == (True, True, True, False)  # 기간이면 나이 표시는 끈다
    assert rs.normalize_field({"label": "x", "type": "text", "range": 1, "open_end": 1}, 0, set())["range"] is False
    assert rs.normalize_field({"label": "x", "type": "date", "range": 1, "allow_same": False}, 0, set())["allow_same"] is False
    assert rs.normalize_value(f, "2021-08~2024-02") == "2021-08~2024-02"
    assert rs.normalize_value(f, " 2021-08 ~ ") == "2021-08~"
    assert rs.normalize_value(f, "2021-13~<b>") == "" and rs.normalize_value(f, "~") == ""  # 형식이 틀린 쪽은 버린다
    assert rs.display_text(f, "2021-08~") == "2021.08 ~ 현재" and rs.display_text(f, "2021-08~2024-02") == "2021.08 ~ 2024.02"
    closed = {**f, "open_end": False}
    assert rs.display_text(closed, "2021-08~") == "2021.08"
    layout = rs.normalize_layout(layout_with((12, [{"id": "w1", "kind": "form", "config": {
        "title": "경력", "display": "timeline", "show_duration": True, "show_total": True, "repeatable": True, "fields": [
            {"key": "p", "label": "재직기간", "type": "month", "slot": "side", "range": True, "open_end": True},
            {"key": "c", "label": "회사", "slot": "title"}]}}])))
    data = {"w1": [{"p": "2021-08~2026-02", "c": "가회사"}, {"p": "2025-01~", "c": "나회사"}]}
    item = rs.document_rows(layout, rs.normalize_data(layout, data))[0]["cols"][0]["items"][0]
    first, second = item["entries"]
    assert (first["period"], first["duration"], first["side"]) == ("2021.08 ~ 2026.02", "4년 7개월", [])
    assert (second["period"], second["duration"]) == ("2025.01 ~ 현재", "1년 11개월") and item["total"] == "5년 4개월"
    career = next(w for w in rs.BUILTIN_WIDGETS if w["key"] == "career")["config"]["fields"]
    assert career[0]["key"] == "period" and career[0]["range"] and career[0]["open_end"]


def test_tel_is_formatted_with_hyphens():
    cases = {"01012345678": "010-1234-5678", "010 1234 5678": "010-1234-5678", "0212345678": "02-1234-5678",
             "021234567": "02-123-4567", "0311234567": "031-123-4567", "050512345678": "0505-1234-5678",
             "15881234": "1588-1234", "+82 10-1234-5678": "+82 10-1234-5678", "내선 123": "내선 123", "": ""}
    for raw, want in cases.items():
        assert rs.format_tel(raw) == want, raw
    tel = rs.normalize_field({"label": "휴대폰", "type": "tel"}, 0, set())
    # 저장은 숫자만, 문서에는 하이픈을 붙여 보여 준다 (국제번호·글이 섞인 값은 적은 그대로)
    assert rs.normalize_value(tel, "010-9876-5432") == "01098765432" and rs.normalize_value(tel, " 02 123 4567 ") == "021234567"
    assert rs.normalize_value(tel, "+82 10-1234-5678") == "+82 10-1234-5678"
    assert rs.display_text(tel, "01098765432") == "010-9876-5432" and rs.display_text(tel, "021234567") == "02-123-4567"

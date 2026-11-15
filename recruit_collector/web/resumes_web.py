"""이력서 화면과 주소 (로그인한 사용자 누구나). 형식 정의·검증은 web/resumes.py.

  /resumes                         내 이력서·템플릿·위젯 목록 (tab=resumes|templates|widgets)
  /resumes/{id}/edit               이력서 작성 (static/resume_editor.js, 자동 저장)
  /resumes/{id}/preview, /pdf      문서 미리보기, PDF 다운로드 (templates/resume_doc.html)
  /resumes/templates/{id}/edit     템플릿 편집기: 위젯 배치 (static/resume_builder.js)
  /resumes/widgets/{id}/edit       위젯 편집 (static/resume_builder.js의 위젯 편집기)
화면이 저장하는 JSON 주소는 /api/resumes…, /api/resume-templates…, /api/resume-widgets… 이다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from html import escape as html_escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from recruit_collector.models import Resume, ResumeTemplate, ResumeWidget, User, utcnow
from recruit_collector.web import resumes as rs

# 이력서 문서 글꼴 (Pretendard). PDF는 파일 경로로 읽는다
FONTS_DIR = Path(__file__).parent / "static" / "fonts"

# 사용자 한 명이 만들 수 있는 개수
MAX_RESUMES, MAX_TEMPLATES, MAX_WIDGETS = 50, 50, 200
MAX_BODY = 1_000_000  # 저장 요청 본문 상한 (바이트)
TABS = {"resumes": "내 이력서", "templates": "템플릿", "widgets": "위젯"}


def install(app: FastAPI, *, session_factory: sessionmaker, templates: Jinja2Templates,
            require_user: Callable[[Request], User]) -> None:
    with session_factory() as s:
        rs.ensure_builtins(s)
    templates.env.globals.update(resume_field_types=rs.FIELD_TYPES, resume_widget_kinds=rs.WIDGET_KINDS,
                                 resume_visibility=rs.VISIBILITY, resume_fonts=rs.FONTS)

    # ------------------------------------------------------------ 공통

    def back(tab: str, message: str, error: bool = False) -> RedirectResponse:
        return RedirectResponse(f"/resumes?tab={tab}&msg={quote(message)}" + ("&err=1" if error else ""), status_code=303)

    def get_owned(s: Session, model, row_id: int, uid: int):
        row = s.get(model, row_id)
        if row is None or row.owner_id != uid:
            raise HTTPException(404, "찾을 수 없습니다.")
        return row

    def get_visible(s: Session, model, row_id: int, uid: int):
        row = s.get(model, row_id)
        if row is None or not rs.visible(row, uid):
            raise HTTPException(404, "찾을 수 없습니다.")
        return row

    def count(s: Session, model, uid: int) -> int:
        return s.scalar(select(func.count()).select_from(model).where(model.owner_id == uid)) or 0

    async def read_json(request: Request) -> dict:
        body = await request.body()
        if len(body) > MAX_BODY:
            raise rs.Invalid("내용이 너무 큽니다.")
        try:
            data = json.loads(body or b"{}")
        except ValueError as e:
            raise rs.Invalid("요청 형식이 올바르지 않습니다.") from e
        if not isinstance(data, dict):
            raise rs.Invalid("요청 형식이 올바르지 않습니다.")
        return data

    def pairs(d: dict) -> list:
        """선택지는 [키, 이름] 목록으로 보낸다: tojson은 사전 키를 가나다·알파벳순으로 정렬해 선택지 순서가 섞인다."""
        return list(d.items())

    def display_options() -> dict:
        """편집기의 입력 칸 종류·위젯 종류·문서 표시 형식·제목 모양·입력 칸 자리·스타일 선택지 (화면 JS가 순서대로 쓴다)."""
        return {"field_types": pairs(rs.FIELD_TYPES), "kinds": pairs(rs.WIDGET_KINDS), "grid": rs.GRID,
                "limits": {"cols": rs.MAX_COLS, "fields": rs.MAX_FIELDS},
                "displays": pairs(rs.DISPLAYS), "title_styles": pairs(rs.TITLE_STYLES), "slots": pairs(rs.SLOTS),
                "style": {"fonts": pairs({k: f["label"] for k, f in rs.FONTS.items()}), "default_font": rs.DEFAULT_FONT,
                          "font_css": {k: rs.font_family(k) for k in rs.FONTS},
                          "colors": pairs(rs.THEME_COLORS), "base_size": rs.BASE_SIZE, "size_min": rs.SIZE_MIN, "size_max": rs.SIZE_MAX,
                          "widget_sizes": rs.WIDGET_SIZES, "style_keys": rs.STYLE_KEYS, "aligns": pairs(rs.ALIGNS),
                          "heading_sizes": pairs(rs.HEADING_SIZES), "divider_weights": pairs(rs.DIVIDER_WEIGHTS),
                          "divider_dashes": pairs(rs.DIVIDER_DASHES), "divider_spaces": pairs(rs.DIVIDER_SPACES)}}

    def invalid(e: Exception) -> JSONResponse:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)

    def meta(data: dict, row) -> None:
        """이름·설명·공개 여부 (템플릿·위젯 공통)."""
        name = rs._str(data.get("name"), rs.LEN["name"])
        if not name:
            raise rs.Invalid("이름을 적어 주세요.")
        row.name = name
        row.description = rs._str(data.get("description"), rs.LEN["desc"]) or None
        row.visibility = data.get("visibility") if data.get("visibility") in rs.VISIBILITY else "private"

    async def form(request: Request) -> dict[str, str]:
        from urllib.parse import parse_qs

        return {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8")).items()}

    def doc_html(request: Request, title: str, layout: dict, data: dict, show_hints: bool, for_pdf: bool,
                 pdf_url: str | None = None) -> str:
        return templates.get_template("resume_doc.html").render(
            request=request, title=title, rows=rs.document_rows(layout, data, show_hints), for_pdf=for_pdf,
            show_hints=show_hints, pdf_url=pdf_url, font_base=FONTS_DIR.as_uri() if for_pdf else "/static/fonts",
            doc_style=rs.theme_style(layout.get("theme")), font_faces=rs.font_faces(layout))

    # ------------------------------------------------------------ 목록

    @app.get("/resumes", response_class=HTMLResponse)
    def resume_home(request: Request, tab: str = "resumes", msg: str = "", err: int = 0):
        me = require_user(request)
        tab = tab if tab in TABS else "resumes"
        with session_factory() as s:
            ctx: dict[str, Any] = {"tab": tab, "tabs": TABS, "message": msg, "is_error": bool(err), "uid": me.id}
            templates_all = list(s.scalars(select(ResumeTemplate).where(rs.visible_filter(ResumeTemplate, me.id))
                                           .order_by(ResumeTemplate.owner_id.is_not(None), ResumeTemplate.updated_at.desc())))
            ctx["usable_templates"] = templates_all
            if tab == "resumes":
                ctx["resumes"] = list(s.scalars(select(Resume).where(Resume.owner_id == me.id).order_by(Resume.updated_at.desc())))
            elif tab == "templates":
                ctx["mine"] = [t for t in templates_all if t.owner_id == me.id]
                ctx["others"] = [t for t in templates_all if t.owner_id != me.id]
            else:
                widgets = rs.widget_library(s, me.id)
                ctx["mine"] = [w for w in widgets if w.owner_id == me.id]
                ctx["others"] = [w for w in widgets if w.owner_id != me.id]
        return templates.TemplateResponse(request, "resumes.html", ctx)

    # ------------------------------------------------------------ 이력서

    @app.post("/resumes/new")
    async def resume_new(request: Request):
        me = require_user(request)
        f = await form(request)
        with session_factory() as s:
            if count(s, Resume, me.id) >= MAX_RESUMES:
                return back("resumes", f"이력서는 {MAX_RESUMES}개까지 만들 수 있습니다.", True)
            try:
                t = get_visible(s, ResumeTemplate, int(f.get("template_id") or 0), me.id)
            except (HTTPException, ValueError):
                return back("resumes", "템플릿을 골라 주세요.", True)
            now = utcnow()
            title = rs._str(f.get("title"), rs.LEN["title"]) or "새 이력서"
            r = Resume(owner_id=me.id, title=title, template_id=t.id, template_name=t.name, layout=t.layout,
                       data=rs.default_data(t.layout), created_at=now, updated_at=now)
            s.add(r)
            s.commit()
            return RedirectResponse(f"/resumes/{r.id}/edit", status_code=303)

    @app.get("/resumes/{resume_id}/edit", response_class=HTMLResponse)
    def resume_edit(request: Request, resume_id: int):
        me = require_user(request)
        with session_factory() as s:
            r = get_owned(s, Resume, resume_id, me.id)
            return templates.TemplateResponse(request, "resume_edit.html", {
                "resume": r, "state": {"id": r.id, "title": r.title, "layout": r.layout,
                                       "data": rs.normalize_data(r.layout, r.data)}})

    @app.put("/api/resumes/{resume_id}")
    async def resume_save(request: Request, resume_id: int):
        me = require_user(request)
        try:
            body = await read_json(request)
            with session_factory() as s:
                r = get_owned(s, Resume, resume_id, me.id)
                r.title = rs._str(body.get("title"), rs.LEN["title"]) or "이름 없는 이력서"
                r.data = rs.normalize_data(r.layout, body.get("data"))
                r.updated_at = utcnow()
                s.commit()
                return {"ok": True, "updated_at": r.updated_at.isoformat()}
        except rs.Invalid as e:
            return invalid(e)

    @app.get("/resumes/{resume_id}/preview", response_class=HTMLResponse)
    def resume_preview(request: Request, resume_id: int):
        me = require_user(request)
        with session_factory() as s:
            r = get_owned(s, Resume, resume_id, me.id)
            return HTMLResponse(doc_html(request, r.title, r.layout, rs.normalize_data(r.layout, r.data), False, False,
                                         pdf_url=f"/resumes/{r.id}/pdf"))

    @app.get("/resumes/{resume_id}/pdf")
    def resume_pdf(request: Request, resume_id: int):
        me = require_user(request)
        with session_factory() as s:
            r = get_owned(s, Resume, resume_id, me.id)
            html = doc_html(request, r.title, r.layout, rs.normalize_data(r.layout, r.data), False, True)
            title = r.title
        pdf = rs.render_pdf(html)
        name = quote(f"{title}.pdf")
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename=\"resume.pdf\"; filename*=UTF-8''{name}",
                                 "Cache-Control": "no-store"})

    @app.post("/resumes/{resume_id}/copy")
    def resume_copy(request: Request, resume_id: int):
        me = require_user(request)
        with session_factory() as s:
            if count(s, Resume, me.id) >= MAX_RESUMES:
                return back("resumes", f"이력서는 {MAX_RESUMES}개까지 만들 수 있습니다.", True)
            r = get_owned(s, Resume, resume_id, me.id)
            now = utcnow()
            s.add(Resume(owner_id=me.id, title=rs.copy_name(r.title)[:rs.LEN["title"]], template_id=r.template_id,
                         template_name=r.template_name, layout=r.layout, data=r.data, created_at=now, updated_at=now))
            s.commit()
        return back("resumes", "이력서를 복사했습니다.")

    @app.post("/resumes/{resume_id}/delete")
    def resume_delete(request: Request, resume_id: int):
        me = require_user(request)
        with session_factory() as s:
            r = get_owned(s, Resume, resume_id, me.id)
            title = r.title
            s.delete(r)
            s.commit()
        return back("resumes", f"'{title}' 이력서를 지웠습니다.")

    # ------------------------------------------------------------ 템플릿

    @app.post("/resumes/templates/new")
    async def template_new(request: Request):
        """새 템플릿. from_id가 있으면 그 템플릿(기본·공개 템플릿 포함)을 복사한다."""
        me = require_user(request)
        f = await form(request)
        with session_factory() as s:
            if count(s, ResumeTemplate, me.id) >= MAX_TEMPLATES:
                return back("templates", f"템플릿은 {MAX_TEMPLATES}개까지 만들 수 있습니다.", True)
            now = utcnow()
            if f.get("from_id"):
                try:
                    src = get_visible(s, ResumeTemplate, int(f["from_id"]), me.id)
                except (HTTPException, ValueError):
                    return back("templates", "템플릿을 찾을 수 없습니다.", True)
                t = ResumeTemplate(owner_id=me.id, name=rs.copy_name(src.name), description=src.description,
                                   visibility="private", layout=src.layout, created_at=now, updated_at=now)
            else:
                t = ResumeTemplate(owner_id=me.id, name="새 템플릿", visibility="private",
                                   layout={"rows": [{"id": "r1", "cols": [{"id": "c1", "span": 12, "items": []}]}]},
                                   created_at=now, updated_at=now)
            s.add(t)
            s.commit()
            return RedirectResponse(f"/resumes/templates/{t.id}/edit", status_code=303)

    @app.get("/resumes/templates/{template_id}/edit", response_class=HTMLResponse)
    def template_edit(request: Request, template_id: int):
        me = require_user(request)
        with session_factory() as s:
            t = get_owned(s, ResumeTemplate, template_id, me.id)
            library = [rs.widget_json(w, me.id) for w in rs.widget_library(s, me.id)]
            return templates.TemplateResponse(request, "resume_builder.html", {
                "template": t,
                "state": {"mode": "template", "id": t.id, "name": t.name, "description": t.description or "",
                          "visibility": t.visibility, "layout": t.layout, "library": library, **display_options()}})

    @app.put("/api/resume-templates/{template_id}")
    async def template_save(request: Request, template_id: int):
        me = require_user(request)
        try:
            body = await read_json(request)
            layout = rs.normalize_layout(body.get("layout"))
            with session_factory() as s:
                t = get_owned(s, ResumeTemplate, template_id, me.id)
                meta(body, t)
                t.layout = layout
                t.updated_at = utcnow()
                s.commit()
                return {"ok": True, "layout": layout}
        except rs.Invalid as e:
            return invalid(e)

    @app.get("/resumes/templates/{template_id}/preview", response_class=HTMLResponse)
    def template_preview(request: Request, template_id: int):
        """템플릿 미리보기: 예시 텍스트로 채우고, 빈 칸에는 힌트를 흐리게 보여 준다."""
        me = require_user(request)
        with session_factory() as s:
            t = get_visible(s, ResumeTemplate, template_id, me.id)
            return HTMLResponse(doc_html(request, t.name, t.layout, rs.default_data(t.layout), True, False))

    @app.post("/resumes/preview", response_class=HTMLResponse)
    async def draft_preview(request: Request):
        """편집 중인(저장하지 않은) 템플릿·위젯 미리보기. 편집기가 doc(JSON)을 새 창으로 보낸다.

        doc = {"name", "layout"} (템플릿) 또는 {"name", "widget": {"kind", "config"}} (위젯 하나). 저장하지 않고 그리기만 한다.
        """
        require_user(request)
        try:
            body = await request.body()
            if len(body) > 3 * MAX_BODY:  # 폼 인코딩으로 한글이 3배쯤 커진다
                raise rs.Invalid("내용이 너무 큽니다.")
            from urllib.parse import parse_qs

            doc = json.loads((parse_qs(body.decode("utf-8")).get("doc") or ["{}"])[0])
            if not isinstance(doc, dict):
                raise rs.Invalid("요청 형식이 올바르지 않습니다.")
            if isinstance(doc.get("widget"), dict):
                w = doc["widget"]
                layout = {"rows": [{"cols": [{"span": 12, "items": [{"kind": w.get("kind"), "config": w.get("config"),
                                                                     "name": doc.get("name")}]}]}]}
            else:
                layout = doc.get("layout")
            layout = rs.normalize_layout(layout)
        except (rs.Invalid, ValueError) as e:
            msg = str(e) if isinstance(e, rs.Invalid) else "요청 형식이 올바르지 않습니다."
            return HTMLResponse(f"<!doctype html><meta charset='utf-8'><p>{html_escape(msg)}</p>", status_code=400)
        name = rs._str(doc.get("name"), rs.LEN["name"]) or "미리보기"
        return HTMLResponse(doc_html(request, name, layout, rs.default_data(layout), True, False))

    @app.post("/resumes/templates/{template_id}/delete")
    def template_delete(request: Request, template_id: int):
        me = require_user(request)
        with session_factory() as s:
            t = get_owned(s, ResumeTemplate, template_id, me.id)
            name = t.name
            # 이 템플릿으로 만든 이력서(다른 사용자 것 포함)는 남기고 연결만 끊는다 (SQLite는 외래키 SET NULL을 따로 켜야 해서 직접)
            s.execute(update(Resume).where(Resume.template_id == t.id).values(template_id=None))
            s.delete(t)
            s.commit()
        return back("templates", f"'{name}' 템플릿을 지웠습니다. 이 템플릿으로 만든 이력서는 그대로 남습니다.")

    # ------------------------------------------------------------ 위젯

    def new_widget(s: Session, uid: int, data: dict) -> ResumeWidget:
        if count(s, ResumeWidget, uid) >= MAX_WIDGETS:
            raise rs.Invalid(f"위젯은 {MAX_WIDGETS}개까지 만들 수 있습니다.")
        kind, config = rs.normalize_widget(data.get("kind"), data.get("config"))
        now = utcnow()
        w = ResumeWidget(owner_id=uid, kind=kind, config=config, created_at=now, updated_at=now)
        meta(data, w)
        s.add(w)
        s.commit()
        return w

    @app.post("/resumes/widgets/new")
    async def widget_new(request: Request):
        """새 위젯 (from_id가 있으면 그 위젯을 복사)."""
        me = require_user(request)
        f = await form(request)
        with session_factory() as s:
            try:
                if f.get("from_id"):
                    try:
                        src = get_visible(s, ResumeWidget, int(f["from_id"]), me.id)
                    except (HTTPException, ValueError):
                        return back("widgets", "위젯을 찾을 수 없습니다.", True)
                    data = {"name": rs.copy_name(src.name), "description": src.description, "kind": src.kind, "config": src.config}
                else:
                    kind = f.get("kind") if f.get("kind") in rs.WIDGET_KINDS else "form"
                    config = {"form": {"title": "새 항목", "fields": [{"label": "내용", "type": "text"}]},
                              "heading": {"text": "제목"}, "note": {"text": "문구를 적어 주세요."}, "divider": {}}[kind]
                    data = {"name": "새 위젯", "kind": kind, "config": config}
                w = new_widget(s, me.id, data)
            except rs.Invalid as e:
                return back("widgets", str(e), True)
            return RedirectResponse(f"/resumes/widgets/{w.id}/edit", status_code=303)

    @app.get("/resumes/widgets/{widget_id}/edit", response_class=HTMLResponse)
    def widget_edit(request: Request, widget_id: int):
        me = require_user(request)
        with session_factory() as s:
            w = get_owned(s, ResumeWidget, widget_id, me.id)
            return templates.TemplateResponse(request, "resume_builder.html", {
                "widget": w,
                "state": {"mode": "widget", "widget": rs.widget_json(w, me.id), **display_options()}})

    @app.post("/api/resume-widgets")
    async def widget_create_api(request: Request):
        """템플릿 편집기에서 '내 위젯으로 저장'."""
        me = require_user(request)
        try:
            body = await read_json(request)
            with session_factory() as s:
                w = new_widget(s, me.id, body)
                return {"ok": True, "widget": rs.widget_json(w, me.id)}
        except rs.Invalid as e:
            return invalid(e)

    @app.put("/api/resume-widgets/{widget_id}")
    async def widget_save(request: Request, widget_id: int):
        me = require_user(request)
        try:
            body = await read_json(request)
            kind, config = rs.normalize_widget(body.get("kind"), body.get("config"))
            with session_factory() as s:
                w = get_owned(s, ResumeWidget, widget_id, me.id)
                meta(body, w)
                w.kind, w.config, w.updated_at = kind, config, utcnow()
                s.commit()
                return {"ok": True, "widget": rs.widget_json(w, me.id)}
        except rs.Invalid as e:
            return invalid(e)

    @app.post("/resumes/widgets/{widget_id}/delete")
    def widget_delete(request: Request, widget_id: int):
        me = require_user(request)
        with session_factory() as s:
            w = get_owned(s, ResumeWidget, widget_id, me.id)
            name = w.name
            s.delete(w)
            s.commit()
        return back("widgets", f"'{name}' 위젯을 지웠습니다. 이미 템플릿에 넣은 위젯은 그대로 남습니다.")

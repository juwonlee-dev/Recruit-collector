"""적재된 공고 조회, 수집 상태·수동 수집, 검색 필터 설정 웹 화면."""

from __future__ import annotations

import json
import re
import secrets
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from itsdangerous import BadSignature, URLSafeTimedSerializer

from recruit_collector import career as career_mod
from recruit_collector import filters, salary
from recruit_collector import region_names
from recruit_collector import regions as regions_mod
from recruit_collector.config import AppConfig, AuthSettings, read_yaml
from recruit_collector.crawlers import CRAWLERS, platform_of
from recruit_collector.salary_sources import SalarySources, SourceError
from recruit_collector.models import (ACTIVE, DELETED, CrawlRun, FavoriteCompany, FavoritePosting, JobPosting, PostingEvent,
                                      LoginSession, PostingSearch, User, company_key, utcnow)
from recruit_collector.web import auth
from recruit_collector.web import favorites as favs
from recruit_collector.web import resumes_web
from recruit_collector.web import scheduler as sched_mod
from recruit_collector.web import tokens
from recruit_collector import crawl_queue
from recruit_collector.crawl_queue import is_stale

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
PAGE_SIZES = (10, 20, 30, 40, 50)  # 공고 목록 한 페이지당 표시 수 (선택지)
DEFAULT_PAGE_SIZE = 10
MIN_COMPARE, MAX_COMPARE = 2, 4  # 연봉·인원 회사 비교: 한 번에 비교할 회사 수
KST = timedelta(hours=9)
# 크롤러 이름 → 표시 이름 (수집 상태·기록). 공고 목록의 플랫폼은 PLATFORM_LABELS
SITE_LABELS = {"saramin": "사람인", "saramin_hh": "사람인(헤드헌팅)", "jobkorea": "잡코리아", "wanted": "원티드", "remember": "리멤버", "jumpit": "점핏"}
PLATFORM_LABELS = {k: v for k, v in SITE_LABELS.items() if platform_of(k) == k}
# 한 플랫폼을 크롤러 여럿이 나눠 수집할 때, 플랫폼 안에서 크롤러를 부르는 이름 (수집 상태·기록)
CRAWLER_KIND_LABELS = {"saramin": "일반 공고", "saramin_hh": "헤드헌팅"}
DATE_ONLY_SITES = {"saramin"}  # 목록에 등록일이 날짜로만 표시되는 사이트
SORT_LABELS = {
    "posted": "최근 등록순",
    "collected": "최근 수집순",
    "updated": "최근 변경순",
    "deadline": "마감 임박순",
}
DEFAULT_SORT = "posted"


CAREER_OPTIONS = {k: label for k, (label, _, _) in career_mod.BUCKETS.items()} | {career_mod.ANY_KEY: "경력무관"}


# 고용형태: 사람인·잡코리아·원티드의 고용형태 선택지를 합친 목록. 키 → (이름, 사이트 표기들).
# 공고의 employment_type은 사이트 표기를 이어 쓴 값(잡코리아 '정규직, 계약직', 사람인 '정규직·계약직')이라
# 항목 단위로 정확히 맞춰 찾는다 ('파견직 (정규직 전환가능)'이 정규직에 걸리지 않도록). 리멤버는 고용형태가 없다
EMPLOYMENT_TYPES = {
    "regular": ("정규직", ["정규직"]),
    "contract": ("계약직", ["계약직", "기간제", "무기계약직"]),
    "intern": ("인턴", ["인턴", "인턴직"]),
    "dispatch": ("파견직", ["파견직", "파견직 (정규직 전환가능)"]),
    "subcontract": ("도급", ["도급"]),
    "freelance": ("프리랜서", ["프리랜서"]),
    "parttime": ("아르바이트", ["아르바이트", "파트"]),
    "trainee": ("연수생·교육생", ["연수생/교육생", "연수생", "교육생"]),
    "military": ("병역특례", ["병역특례", "전문연구요원", "산업기능요원", "보충역", "현역"]),
    "commission": ("위촉직·개인사업자", ["위촉직/개인사업자", "위촉직", "개인사업자"]),
}
EMPLOYMENT_OPTIONS = {k: label for k, (label, _) in EMPLOYMENT_TYPES.items()}
# 헤드헌팅 필터. 제외는 '헤드헌팅으로 확인된 공고'만 뺀다 (알 수 없는 예전 사람인 공고는 남김)
HEADHUNTING_OPTIONS = {"only": "헤드헌팅만", "exclude": "헤드헌팅 제외"}


def employment_condition(keys: list[str]):
    """고른 고용형태 중 하나라도 공고 표기에 있으면 참 (합집합)."""
    items = "·" + func.replace(JobPosting.employment_type, ", ", "·") + "·"
    return or_(*(items.like(f"%·{t}·%") for k in keys for t in EMPLOYMENT_TYPES[k][1]))


def career_condition(key: str):
    """연차 필터. 공고의 연차 범위가 선택 구간과 겹치면 포함하고, 경력무관 공고는 항상 포함한다.

    '경력무관'을 고르면 경력무관으로 명시된 공고만 보여준다.
    """
    if key == career_mod.ANY_KEY:
        return JobPosting.career_any.is_(True)
    if key not in career_mod.BUCKETS:
        return None
    _, lo, hi = career_mod.BUCKETS[key]
    overlap = [
        JobPosting.career_min.is_not(None),
        or_(JobPosting.career_max.is_(None), JobPosting.career_max >= lo),
    ]
    if hi is not None:
        overlap.append(JobPosting.career_min <= hi)
    return or_(JobPosting.career_any.is_(True), and_(*overlap))


def pick_regions(values: list[str], codes: dict[str, list[str]]) -> list[str]:
    """공고 검색의 지역(여러 개): '시도' 또는 '시도 시군구'. 공통 지역코드 순서로 정리한다.

    공통 지역코드에 없는 시도는 버리고, 그 시도에 없는 시군구는 시도 전체로 본다.
    시도 전체를 고르면 그 시도의 시군구는 이미 포함되므로 뺀다. 지역코드가 없으면(설정 없음) 받은 그대로 쓴다.
    """
    picked: list[str] = []
    for v in values:
        sido, _, city = v.strip().partition(" ")
        city = city.strip()
        if codes and sido not in codes:
            continue
        if codes and city not in codes[sido]:
            city = ""
        code = f"{sido} {city}" if city else sido
        if sido and code not in picked:
            picked.append(code)
    wholes = {c for c in picked if " " not in c}
    picked = [c for c in picked if " " not in c or c.split(" ", 1)[0] not in wholes]
    if codes:
        order = {code: i for i, code in enumerate(
            x for sido, cities in codes.items() for x in (sido, *(f"{sido} {c}" for c in cities)))}
        picked.sort(key=lambda c: order[c])
    return picked


def highlight(text: str | None, q: str = "") -> Markup:
    """공고 검색어(q)와 일치하는 부분을 <mark>로 감싼다. 검색과 같이 대소문자를 가리지 않고, 나머지 글자는 이스케이프한다."""
    text, q = text or "", (q or "").strip()
    if not q:
        return escape(text)
    parts = re.split(f"({re.escape(q)})", text, flags=re.IGNORECASE)
    return Markup("").join(Markup("<mark class=\"hl\">{}</mark>").format(p) if i % 2 else escape(p) for i, p in enumerate(parts))


def kst(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return (value + KST).strftime(fmt) if value else ""


# 개인정보처리방침(/privacy)·서비스 이용약관(/terms)에 쓰는 값. 구글 OAuth 동의 화면에 두 주소를 등록한다
LEGAL = {
    "operator": "Recruit Collector 운영자",
    "contact": "juwonlee.dev@gmail.com",
    "site": "https://recruit-collector.kro.kr",
    "effective": "2026-11-14",
}

RUNS_PAGE_SIZE = 50
CHART_RUNS = 30  # 수집 기록 상단 그래프: 사이트별 최근 실행 수


def run_charts(s: Session) -> list[dict]:
    """수집 기록 상단의 요약·그래프 자료. 채용 플랫폼별로 묶고, 그 안에 크롤러별 최근 CHART_RUNS회(오래된 것부터)."""
    groups: dict[str, dict] = {}
    for site, label in SITE_LABELS.items():
        runs = s.scalars(select(CrawlRun).where(CrawlRun.site == site)
                         .order_by(CrawlRun.id.desc()).limit(CHART_RUNS)).all()[::-1]
        if not runs:
            continue
        done = [r for r in runs if r.finished_at and r.status != "CANCELED"]
        secs = [(r.finished_at - r.started_at).total_seconds() for r in done]
        ok = sum(r.status == "SUCCESS" for r in done)
        platform = platform_of(site)
        group = groups.setdefault(platform, {"platform": platform, "label": PLATFORM_LABELS.get(platform, platform), "crawlers": []})
        group["crawlers"].append({
            "site": site, "label": label, "kind": CRAWLER_KIND_LABELS.get(site, ""), "last": runs[-1],
            "count": len(runs),
            "status_counts": {k: sum(r.status == k for r in runs) for k in ("SUCCESS", "PARTIAL", "FAILED", "CANCELED", "RUNNING")},
            "success_rate": round(ok * 100 / len(done)) if done else None,
            "avg_secs": int(sum(secs) / len(secs)) if secs else None,
            "points": [{
                "t": kst(r.started_at, "%m-%d %H:%M"), "status": r.status, "trigger": r.trigger,
                "found": r.found, "created": r.created, "updated": r.updated, "deleted": r.deleted,
                "secs": int((r.finished_at - r.started_at).total_seconds()) if r.finished_at else None,
            } for r in runs],
        })
    return list(groups.values())


def duration_label(secs: int | None) -> str:
    if secs is None:
        return "-"
    return f"{secs // 60}분 {secs % 60}초" if secs >= 60 else f"{secs}초"


def is_company_id(cid: str) -> bool:
    """연봉·인원 회사 ID: 올댓컴퍼니 회사 번호(숫자) 또는 국민연금 사업장('n' + 그 달 자료의 seq)."""
    return bool(re.fullmatch(r"n?\d{1,20}", cid or ""))


def today_kst():
    return (utcnow() + KST).date()


def sort_order(sort: str) -> list:
    if sort == "collected":
        return [JobPosting.last_seen_at.desc()]
    if sort == "updated":
        return [JobPosting.updated_at.desc()]
    if sort == "deadline":
        # 다가오는 마감 → 이미 지난 마감 → 마감일 없음(채용시·상시채용)
        bucket = case((JobPosting.deadline_at.is_(None), 2), (JobPosting.deadline_at < today_kst(), 1), else_=0)
        return [bucket, JobPosting.deadline_at.asc()]
    # 최근 등록순. 등록 시각을 모르는 공고는 뒤로
    return [JobPosting.posted_at.is_(None), JobPosting.posted_at.desc()]


def posted_label(p: JobPosting, short: bool = False) -> str:
    """등록 시각 표시. 날짜만 아는 사이트는 날짜만, 추정값이면 '추정'을 붙인다."""
    if p.posted_at is None:
        return ""
    if p.site in DATE_ONLY_SITES:
        text = kst(p.posted_at, "%m-%d" if short else "%Y-%m-%d")
    else:
        text = kst(p.posted_at, "%m-%d %H:%M" if short else "%Y-%m-%d %H:%M")
    return text + (" (추정)" if (p.raw or {}).get("posted_at_inferred") else "")


def deadline_label(p: JobPosting) -> str:
    if p.deadline_at is None:
        return (p.deadline or "").replace("~ ", "")
    weekday = "월화수목금토일"[p.deadline_at.weekday()]
    days = (p.deadline_at - today_kst()).days
    dday = "오늘 마감" if days == 0 else ("마감됨" if days < 0 else f"D-{days}")
    return f"{p.deadline_at:%m/%d}({weekday}) · {dday}"


def load_summary(s: Session) -> dict:
    since = utcnow() - timedelta(hours=24)

    def count(*where) -> int:
        return s.scalar(select(func.count()).select_from(JobPosting).where(*where))

    by_site = dict(
        s.execute(
            select(JobPosting.site, func.count()).where(JobPosting.status == ACTIVE).group_by(JobPosting.site)
        ).all()
    )
    return {
        "active": count(JobPosting.status == ACTIVE),
        "deleted": count(JobPosting.status == DELETED),
        "new_24h": count(JobPosting.first_seen_at >= since),
        "updated_24h": count(JobPosting.updated_at >= since, JobPosting.first_seen_at < since),
        "deleted_24h": count(JobPosting.deleted_at >= since),
        "active_by_site": {SITE_LABELS.get(k, k): v for k, v in sorted(by_site.items())},
        "active_by_platform": by_site,  # 수집 상태의 플랫폼 묶음에 표시
    }


def load_status(s: Session, config: AppConfig | None, can_crawl: bool = True) -> dict:
    """검색 조건별 최근 수집 상태 + 공고 수 요약 + 수집기(worker) 상태.

    RUNNING인데 진행 기록이 오래 없는 실행은 STALE(중단됨)로 보여준다.
    '지금 수집' 요청이 대기열에 있으면, 수집기가 살아 있으면 STARTING(시작 중), 꺼져 있으면 QUEUED(대기 중).
    can_crawl이 False면(로그인 사용 중 관리자가 아님) '지금 수집' 버튼을 내보내지 않는다.
    """
    latest_ids = select(func.max(CrawlRun.id)).group_by(CrawlRun.search_name)
    latest = {r.search_name: r for r in s.scalars(select(CrawlRun).where(CrawlRun.id.in_(latest_ids)))}
    last_success = dict(
        s.execute(
            select(CrawlRun.search_name, func.max(CrawlRun.finished_at))
            .where(CrawlRun.status == "SUCCESS")
            .group_by(CrawlRun.search_name)
        ).all()
    )

    # 설정의 검색 조건 순서대로, 설정에서 빠졌지만 기록이 남은 조건은 뒤에
    config_sites = {}
    if config:
        try:
            config_sites = {c.name: c.site for c in config.load_searches()}
        except Exception:  # 검색 필터 파일을 고치는 중이어도 상태 화면은 보여준다
            log.exception("검색 필터 파일을 읽을 수 없음")
    names = list(config_sites) + sorted(n for n in latest if n not in config_sites)
    now = utcnow()
    worker_alive = crawl_queue.worker_alive(s, now)
    queued = crawl_queue.pending_sites(s)
    searches = []
    for name in names:
        run = latest.get(name)
        site = config_sites.get(name) or (run.site if run else "")
        status = run.status if run else "NEVER"
        if run and is_stale(run, now):
            status = "STALE"
        if status != "RUNNING" and site in queued:
            status = "STARTING" if worker_alive else "QUEUED"
        searches.append({
            "name": name,
            "site": SITE_LABELS.get(site, site),
            "site_key": site,
            # 수집 상태는 채용 플랫폼별로 묶어 보여준다 (사람인 = 일반 공고 + 헤드헌팅 크롤러)
            "platform": platform_of(site),
            "platform_label": PLATFORM_LABELS.get(platform_of(site), SITE_LABELS.get(site, site)),
            "kind": CRAWLER_KIND_LABELS.get(site, ""),
            "can_run": can_crawl and config is not None and site in config_sites.values(),
            "status": status,
            "trigger": run.trigger if run else None,
            "started_at": kst(run.started_at) if run else None,
            "finished_at": kst(run.finished_at) if run and run.finished_at else None,
            "elapsed_sec": int(((run.finished_at or now) - run.started_at).total_seconds()) if run else None,
            "pages": run.pages if run else 0,
            "found": run.found if run else 0,
            "total": run.total if run else None,
            "created": run.created if run else 0,
            "updated": run.updated if run else 0,
            "deleted": run.deleted if run else 0,
            "error": run.error if run else None,
            "last_success_at": kst(last_success[name]) if last_success.get(name) else None,
        })

    # 다음 수집: 자동 수집(설정 > 스케줄러)이 켜져 있을 때 주기로 계산 (실행은 수집기가 한다)
    nxt = None
    if config:
        enabled, cron = _schedule_settings(s, config)
        nxt = sched_mod.planned_next_run(enabled, cron, config.timezone)
    next_run_at = nxt.strftime("%Y-%m-%d %H:%M") if nxt else None
    beat = crawl_queue.worker_heartbeat(s)

    return {
        "checked_at": kst(now, "%H:%M:%S"),
        "running": any(x["status"] in ("RUNNING", "STARTING") for x in searches),
        "next_run_at": next_run_at,
        "schedule_on": nxt is not None,
        "worker_alive": worker_alive,
        "worker_seen_at": kst(beat, "%m-%d %H:%M:%S") if beat else None,
        "summary": load_summary(s),
        "searches": searches,
    }


# 설정 화면에서 검색 조건 키를 보여줄 이름
FIELD_LABELS = {
    "keyword": "검색어", "keywords": "검색어",
    "locations": "지역", "addresses": "지역", "districts": "지역",
    "job_group_id": "직군", "job_categories": "직무", "job_keywords": "직무", "duties": "직무", "job_ids": "직무",
    "careers": "경력", "career_type": "경력", "years": "경력",
    "job_types": "고용형태", "company_types": "기업 형태", "sort": "정렬", "page_size": "페이지 크기", "params": "추가 파라미터",
}
VALUE_LABELS = {  # 직무 외에 자주 쓰는 코드 값
    "101000": "서울 전체", "102000": "경기 전체", "I000": "서울 전체", "B000": "경기 전체",
    "seoul.all": "서울 전체", "gyeonggi.all": "경기 전체",
}


def _value_text(value, names: dict[str, str]) -> str:
    if isinstance(value, list):  # 리멤버처럼 이름 자체가 값인 경우
        return " > ".join(map(str, value))
    name = names.get(json.dumps(value, ensure_ascii=False)) or VALUE_LABELS.get(str(value))
    return f"{name} ({value})" if name else str(value)


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd()).as_posix()
    except ValueError:
        return str(path)


def load_site_settings(config: AppConfig) -> list[dict]:
    """사이트별 검색 필터 파일 내용을 화면에 보여줄 형태로 읽는다."""
    sites = []
    for site in config.sites():
        path = config.site_file(site)
        crawler_cls = CRAWLERS.get(site)
        job_field = crawler_cls.job_filter_field if crawler_cls else None
        item = {
            "site": site, "label": SITE_LABELS.get(site, site), "job_field": job_field,
            "path": _display_path(path),
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(tzinfo=None),
            "searches": [], "generated": None, "catalog": None, "error": None,
        }
        try:
            data = read_yaml(path)
            config.load_searches(site)  # 수집에 쓸 수 있는 파일인지 확인
        except Exception as e:
            item["error"] = f"{type(e).__name__}: {e}"
            sites.append(item)
            continue
        names = {**filters.option_names(config, site), **regions_mod.option_names(config, site)}
        catalog = filters.load_catalog(config, site)
        if catalog:
            item["catalog"] = {"fetched_at": catalog["fetched_at"][:16].replace("T", " "), "count": len(catalog["options"])}
        for s in data.get("searches") or []:
            rows = []
            fixed = crawler_cls.fixed_condition if crawler_cls else {}
            for key, value in (s.get("condition") or {}).items():
                if key in fixed:
                    continue  # 고정 조건은 파일 값과 관계없이 크롤러 값으로 아래에 따로 보여준다
                values = value if isinstance(value, list) else [value]
                rows.append({
                    "label": FIELD_LABELS.get(key, key), "key": key, "is_job": key == job_field,
                    "is_region": crawler_cls is not None and key in crawler_cls.region_filter_fields,
                    "texts": [_value_text(v, names) for v in values] or ["(없음)"],
                })
            rows.sort(key=lambda r: (not r["is_job"], not r["is_region"]))  # 직무, 지역 필터를 위에
            for key in fixed:
                rows.append({"label": FIELD_LABELS.get(key, key), "key": key, "is_job": False, "is_region": False,
                             "is_fixed": True, "texts": [crawler_cls.fixed_condition_label or str(fixed[key])]})
            item["searches"].append({"name": s.get("name"), "max_pages": s.get("max_pages", 20), "rows": rows})
        if gen := data.get("generated"):
            item["generated"] = {
                "at": str(gen.get("at", ""))[:16].replace("T", " "), "keywords": gen.get("keywords") or [],
                "regions": [regions_mod.label(r) for r in gen.get("regions") or []],
            }
        sites.append(item)
    return sites


GENERATE_STATUS = {
    "updated": "반영됨", "unchanged": "변경 없음", "no_match": "매칭 없음",
    "unsupported": "미지원", "error": "실패",
}


def _schedule_settings(s: Session, config: AppConfig) -> tuple[bool, str]:
    """(자동 수집 켜짐 여부, cron) — 이미 열린 세션으로 읽는다."""
    enabled = auth.get_setting(s, sched_mod.ENABLED_KEY) == "1"
    cron = auth.get_setting(s, sched_mod.CRON_KEY) or sched_mod.normalize_cron(config.cron)
    return enabled, cron


def create_app(session_factory: sessionmaker, config: AppConfig | None = None) -> FastAPI:
    # 수집(자동·수동)은 별도 프로세스인 수집기(recruit_collector/worker.py, worker 컨테이너)가 한다.
    # 조회 화면은 DB로 설정을 바꾸고 '지금 수집' 요청을 대기열에 넣을 뿐이라, 다시 배포해도 수집에 영향이 없다
    app = FastAPI(title="Recruit Collector", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    auth_settings = config.auth if config else AuthSettings()
    with session_factory() as s:
        secret = auth.session_secret(s)
    # 구글 로그인 요청 확인값(state)·돌아갈 주소: 10분짜리 서명 쿠키 (로그인 토큰과 따로)
    oauth_signer = URLSafeTimedSerializer(secret, salt="oauth")
    OAUTH_COOKIE, OAUTH_AGE = "rc_oauth", 600

    def cookie_options(request: Request) -> dict:
        # HttpOnly: 화면 스크립트가 못 읽음. SameSite=Lax: 다른 사이트에서 보낸 폼(POST)에는 붙지 않음.
        # Secure: https 접속일 때만 (nginx가 X-Forwarded-Proto를 넘기고 FORWARDED_ALLOW_IPS로 믿게 해 둠)
        return {"httponly": True, "samesite": "lax", "secure": request.url.scheme == "https"}

    # 로그인 토큰(web/tokens.py): Access가 유효하면 그대로, 아니면 Refresh로 새로 발급해 응답 쿠키에 넣는다
    @app.middleware("http")
    async def login_tokens(request: Request, call_next):
        if request.url.path.startswith("/static/"):
            return await call_next(request)
        claims = tokens.decode(secret, request.cookies.get(tokens.ACCESS_COOKIE), "access")
        if claims:
            login = tokens.Login(uid=int(claims["sub"]), fam=claims["fam"])
        elif tokens.REFRESH_COOKIE in request.cookies:
            def do_refresh():
                with session_factory() as s:
                    return tokens.refresh(s, secret, request.cookies[tokens.REFRESH_COOKIE])
            login = await run_in_threadpool(do_refresh)
        else:
            login = tokens.Login()
        request.state.login = login
        response = await call_next(request)
        opts = cookie_options(request)
        if login.clear:
            response.delete_cookie(tokens.ACCESS_COOKIE, path="/", **opts)
            response.delete_cookie(tokens.REFRESH_COOKIE, path="/", **opts)
        else:
            if login.set_access:
                response.set_cookie(tokens.ACCESS_COOKIE, login.set_access, path="/",
                                    max_age=int(tokens.ACCESS_TTL.total_seconds()), **opts)
            if login.set_refresh:
                value, expires = login.set_refresh
                response.set_cookie(tokens.REFRESH_COOKIE, value, path="/",
                                    max_age=max(0, int((expires - utcnow()).total_seconds())), **opts)
        if "rc_session" in request.cookies:  # 예전 세션 쿠키 정리
            response.delete_cookie("rc_session", path="/")
        return response

    # 검색엔진·수집기 차단: robots.txt(전체 금지) + 모든 응답에 X-Robots-Tag + 페이지의 robots 메타 태그(base.html).
    # robots.txt를 따르지 않거나 다른 사이트의 링크로 들어온 경우에도 색인·링크 추적을 하지 않도록 헤더를 함께 둔다
    @app.middleware("http")
    async def no_robots(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    @app.get("/robots.txt", include_in_schema=False)
    def robots_txt():
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    # 파비콘: 브라우저·휴대폰이 페이지 링크 없이도 기본 주소로 찾는 경우가 있어 루트 주소로도 응답한다 (파일은 static/)
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(HERE / "static" / "favicon.ico", media_type="image/x-icon",
                            headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/apple-touch-icon.png", include_in_schema=False)
    def apple_touch_icon():
        return FileResponse(HERE / "static" / "apple-touch-icon.png", media_type="image/png",
                            headers={"Cache-Control": "public, max-age=604800"})

    def get_access(request: Request) -> auth.Access:
        """이 요청의 로그인 사용 여부·사용자·관리자 여부. 등급은 매번 DB에서 읽어 바로 반영한다."""
        cached = getattr(request.state, "access", None)
        if cached is not None:
            return cached
        login = getattr(request.state, "login", None) or tokens.Login()
        with session_factory() as s:
            enabled = auth.login_enabled(s)
            user = None
            if login.uid is not None:
                if tokens.is_active(s, login.fam, login.uid):  # 로그아웃·강제 로그아웃한 로그인이면 바로 끊는다
                    user = s.get(User, login.uid)
                else:
                    login.signed_out()
        access = auth.Access(enabled, auth_settings.google_ready, user, auth.is_admin(user, auth_settings))
        request.state.access = access
        return access

    class AdminRequired(Exception):
        message = "관리자로 로그인해야 사용할 수 있습니다."

    class LoginRequired(AdminRequired):
        message = "로그인해야 사용할 수 있습니다."

    def require_admin(request: Request) -> None:
        """로그인 사용 중이면 관리자만 통과. 꺼져 있으면 모두 통과."""
        if not get_access(request).can_manage:
            raise AdminRequired

    def require_login(request: Request) -> None:
        """로그인 사용 중이면 로그인한 사용자만 통과 (등급 무관). 꺼져 있으면 모두 통과."""
        if not get_access(request).can_view_salary:
            raise LoginRequired

    def get_fav_alerts(request: Request) -> dict | None:
        """상단 '공고' 메뉴 옆 숫자 (변경된 즐겨찾기 공고 + 관심 회사 새 공고). 즐겨찾기를 쓸 수 없으면 None."""
        access = get_access(request)
        if not access.can_favorite:
            return None
        with session_factory() as s:
            user = s.get(User, access.user.id)
            return favs.alerts(s, user) if user else None

    templates = Jinja2Templates(directory=HERE / "templates", context_processors=[
        lambda r: {"access": get_access(r), "fav_alerts": get_fav_alerts(r)}])

    def message_page(request: Request, title: str, message: str, status_code: int = 200) -> HTMLResponse:
        return templates.TemplateResponse(request, "auth_message.html", {"title": title, "message": message},
                                          status_code=status_code)

    @app.exception_handler(AdminRequired)
    async def admin_required(request: Request, exc: AdminRequired):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"ok": False, "message": exc.message}, status_code=403)
        if get_access(request).user is None:
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            if request.method != "GET":
                target = next((p for p in ("/me", "/resumes") if request.url.path.startswith(p)), "/settings")
            return RedirectResponse(f"/auth/login?next={quote(target)}", status_code=303)
        return message_page(request, "권한 없음", "관리자만 사용할 수 있는 메뉴입니다. 관리자에게 등급 변경을 요청하세요.", 403)
    templates.env.filters["kst"] = kst
    templates.env.filters["highlight"] = highlight
    templates.env.filters["region_label"] = regions_mod.label
    templates.env.globals.update(site_labels=SITE_LABELS, platform_labels=PLATFORM_LABELS, posted_label=posted_label, deadline_label=deadline_label,
                                 max_compare=MAX_COMPARE, dday_badge=favs.dday_badge, duration_label=duration_label)
    app.state.last_generation = None  # 마지막 필터 생성 결과 (설정 화면에 표시)
    # 필터 생성 중에는 다른 탭에서 온 생성·키워드 변경 요청도 막는다 (요청 처리는 이벤트 루프 한 곳에서만 바꿈)
    app.state.generating = False

    @app.get("/", response_class=HTMLResponse)
    def postings(
        request: Request,
        q: str = "",
        view: str = "",  # '' 전체 공고 / fav 즐겨찾기 / company 관심 회사 새 공고 (즐겨찾기를 쓸 수 있을 때만)
        site: str = "",
        status: str = "",
        region: list[str] = Query(default=[]),
        career: list[str] = Query(default=[]),
        employment: list[str] = Query(default=[]),
        hh: str = "",  # 헤드헌팅: '' 전체 / only / exclude (HEADHUNTING_OPTIONS)
        location: str = "",
        city: str = "",
        sort: str = "",
        page: int = 1,
        size: int = DEFAULT_PAGE_SIZE,
    ):
        page = max(page, 1)
        access = get_access(request)
        if view not in ("fav", "company") or not access.can_favorite:
            view = ""
        uid = access.user.id if access.can_favorite else None
        today = today_kst()
        # 즐겨찾기 탭은 마감·삭제된 공고도 아래 '지난 공고'로 보여주고, 마감 임박순이 기본
        default_status = "ALL" if view == "fav" else ACTIVE
        default_sort = "deadline" if view == "fav" else DEFAULT_SORT
        status = status or default_status
        if size not in PAGE_SIZES:
            size = DEFAULT_PAGE_SIZE
        region_codes = regions_mod.load_codes(config) if config else {}
        if location and not region:  # 예전 주소(?location=시도&city=시군구)도 연다
            region = [f"{location} {city}".strip()]
        region = pick_regions(region, region_codes)  # 여러 지역 (합집합)
        career = [k for k in CAREER_OPTIONS if k in career]  # 여러 경력 구간 (합집합)
        employment = [k for k in EMPLOYMENT_OPTIONS if k in employment]  # 여러 고용형태 (합집합)
        if sort not in SORT_LABELS:
            sort = default_sort
        if hh not in HEADHUNTING_OPTIONS:
            hh = ""
        conds = []
        if view == "fav":
            conds.append(JobPosting.id.in_(favs.favorite_ids(uid)))
        elif view == "company":
            conds.append(favs.company_condition(uid))
        if q:
            like = f"%{q.strip()}%"
            conds.append(or_(JobPosting.title.ilike(like), JobPosting.company.ilike(like)))
        if site:
            conds.append(JobPosting.site == site)
        if status in (ACTIVE, DELETED):
            conds.append(JobPosting.status == status)
        if region:  # 공고의 지역 표기에서 뽑아 둔 공통 지역코드로 찾는다 (region_names.location_codes)
            # 고른 지역 중 하나라도 맞으면 포함. 시군구로 찾으면 시도 전체(예: 서울전체)·전국 공고도 포함
            codes = dict.fromkeys(
                code for r in region for code in region_names.search_codes(*r.split(" ", 1)))
            conds.append(or_(*(JobPosting.regions.contains(f"|{code}|", autoescape=True) for code in codes)))
        if career:
            conds.append(or_(*(career_condition(k) for k in career)))
        if employment:
            conds.append(employment_condition(employment))
        if hh == "only":
            conds.append(JobPosting.headhunting.is_(True))
        elif hh == "exclude":
            conds.append(or_(JobPosting.headhunting.is_(None), JobPosting.headhunting.is_(False)))

        past_first = [case((favs.is_past(today), 1), else_=0)] if view == "fav" else []
        with session_factory() as s:
            total = s.scalar(select(func.count()).select_from(JobPosting).where(*conds))
            rows = s.scalars(
                select(JobPosting)
                .where(*conds)
                .order_by(*past_first, *sort_order(sort), JobPosting.id.desc())
                .limit(size)
                .offset((page - 1) * size)
            ).all()
            status_data = load_status(s, config, access.can_manage)
            favorites, changed, fav = set(), {}, None
            if uid is not None:
                favorites = set(s.scalars(select(FavoritePosting.posting_id).where(
                    FavoritePosting.user_id == uid, FavoritePosting.posting_id.in_([r.id for r in rows]))))
                changed = favs.changed_map(s, uid, [r.id for r in rows if r.id in favorites])
                fav = {
                    "summary": favs.summary(s, uid, today),
                    "new_count": favs.new_company_count(s, uid),
                    "companies": s.scalars(select(FavoriteCompany).where(FavoriteCompany.user_id == uid)
                                           .order_by(FavoriteCompany.created_at)).all(),
                    # NEW: 관심 회사 탭에서, 아직 상세·원문을 열지 않은 새 공고
                    "new_ids": favs.new_company_ids(s, uid, [r.id for r in rows]) if view == "company" else set(),
                    "past_ids": {r.id for r in rows if r.status == DELETED or (r.deadline_at and r.deadline_at < today)},
                }

        filters = {
            "view": view, "q": q, "site": site, "status": status, "region": region,
            "career": career, "employment": employment, "hh": hh, "sort": sort, "size": size,
        }
        pages = max(1, -(-total // size))

        def page_url(n: int) -> str:
            params = {k: v for k, v in {**filters, "page": n}.items() if v}
            if params.get("sort") == default_sort:
                params.pop("sort")
            if params.get("status") == default_status:
                params.pop("status")
            if params.get("size") == DEFAULT_PAGE_SIZE:
                params.pop("size")
            return "/?" + urlencode(params, doseq=True)

        return templates.TemplateResponse(
            request,
            "postings.html",
            {
                "rows": rows, "total": total, "page": page, "pages": pages, "page_url": page_url,
                "filters": filters, "sort_labels": SORT_LABELS, "page_sizes": PAGE_SIZES,
                "career_options": CAREER_OPTIONS, "employment_options": EMPLOYMENT_OPTIONS, "region_codes": region_codes,
                "headhunting_options": HEADHUNTING_OPTIONS,
                "status_data": status_data, "favorites": favorites, "changed": changed, "fav": fav, "today": today,
                "default_status": default_status,
                "queried_at": kst(utcnow(), "%H:%M:%S"),
            },
        )

    @app.get("/api/status")
    def api_status(request: Request):
        """화면 상단의 수집 상태·공고 수를 새로고침 없이 갱신하기 위한 API."""
        can_crawl = get_access(request).can_manage
        with session_factory() as s:
            return load_status(s, config, can_crawl)

    @app.post("/api/crawl/{site}")
    def start_crawl(request: Request, site: str):
        """사이트(크롤러 모듈)의 수집을 요청한다. 대기열에 넣으면 수집기가 가져가 자동 수집과 같은 과정으로 수집한다.

        수집기가 꺼져 있으면 대기열에 남았다가 수집기가 뜨면 실행된다.
        이미 대기 중이거나 수집 중인 사이트는 받지 않는다 (409).
        """
        require_admin(request)
        label = SITE_LABELS.get(site, site)
        if config is None:
            return JSONResponse({"ok": False, "message": "설정 없이 실행된 서버에서는 수집할 수 없습니다."}, status_code=503)
        if site not in config.sites():
            return JSONResponse({"ok": False, "message": f"설정에 없는 사이트입니다: {site}"}, status_code=404)
        user = get_access(request).user
        with session_factory() as s:
            try:
                crawl_queue.request_crawl(s, site, requested_by=user.email if user else None)
            except crawl_queue.AlreadyQueued:
                return JSONResponse({"ok": False, "message": f"{label}은(는) 이미 수집 대기 중입니다."}, status_code=409)
            except crawl_queue.AlreadyRunning:
                return JSONResponse({"ok": False, "message": f"{label}은(는) 이미 수집 중입니다."}, status_code=409)
            alive = crawl_queue.worker_alive(s)
        if alive:
            return JSONResponse({"ok": True, "message": f"{label} 수집을 요청했습니다. 곧 시작합니다."}, status_code=202)
        return JSONResponse({"ok": True, "queued": True,
                             "message": f"수집기가 꺼져 있어 {label} 수집을 대기열에 넣었습니다. 수집기가 켜지면 시작합니다."},
                            status_code=202)

    # ------------------------------------------------------------ 설정

    def settings_redirect(message: str, error: bool = False, tab: str = "") -> RedirectResponse:
        suffix = ("&err=1" if error else "") + (f"&tab={tab}" if tab else "")
        return RedirectResponse(f"/settings?msg={quote(message)}{suffix}", status_code=303)

    async def form_value(request: Request, key: str) -> str:
        """application/x-www-form-urlencoded 폼 값 하나 (폼 처리용 패키지 없이 읽는다)."""
        values = parse_qs((await request.body()).decode("utf-8")).get(key)
        return values[0] if values else ""

    def require_config() -> AppConfig:
        if config is None:
            raise HTTPException(503, "설정 없이 실행된 서버입니다.")
        return config

    def redirect_uri(request: Request) -> str:
        return str(request.url_for("auth_callback"))

    def login_problems(access: auth.Access) -> list[str]:
        """로그인 사용을 켤 수 없는 이유. 켠 뒤 아무도 관리할 수 없게 되는 일을 막는다."""
        problems = []
        if not access.google_ready:
            problems.append("config.yaml의 auth에 구글 클라이언트 ID·시크릿을 넣어야 합니다.")
        if access.user is None:
            problems.append("먼저 관리자 구글 계정으로 로그인해야 합니다 (로그인이 되는지 확인하기 위해).")
        elif not access.is_admin:
            problems.append("관리자 계정으로 로그인해야 켤 수 있습니다.")
        return problems

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, msg: str = "", err: int = 0, tab: str = ""):
        require_admin(request)
        cfg = require_config()
        common = {"tab": tab if tab in ("users", "scheduler") else "filters", "message": msg, "is_error": bool(err)}
        if common["tab"] == "scheduler":
            enabled, cron = sched_mod.load_settings(session_factory, cfg.cron)
            try:
                upcoming = sched_mod.next_times(sched_mod.parse_cron(cron, cfg.timezone), 5)
                cron_error = ""
            except ValueError as e:
                upcoming, cron_error = [], str(e)
            with session_factory() as s:
                last = s.scalars(
                    select(CrawlRun).where(CrawlRun.trigger == "schedule").order_by(CrawlRun.id.desc()).limit(1)
                ).first()
                beat = crawl_queue.worker_heartbeat(s)
                worker_alive = crawl_queue.worker_alive(s)
            nxt = sched_mod.planned_next_run(enabled, cron, cfg.timezone)
            return templates.TemplateResponse(request, "settings.html", {
                **common,
                "schedule_on": enabled,
                "cron": cron,
                "cron_label": sched_mod.describe(cron),
                "cron_error": cron_error,
                "presets": sched_mod.PRESETS,
                "is_preset": any(c == cron for c, _ in sched_mod.PRESETS),
                "upcoming": [t.strftime("%Y-%m-%d %H:%M") for t in upcoming],
                "next_run_at": nxt.strftime("%Y-%m-%d %H:%M") if nxt else None,
                "last_run": last,
                "worker_alive": worker_alive,
                "worker_seen_at": kst(beat, "%Y-%m-%d %H:%M:%S") if beat else None,
                "timezone": cfg.timezone,
                "sites": [SITE_LABELS.get(x, x) for x in cfg.sites()],
            })
        if common["tab"] == "users":
            access = get_access(request)
            with session_factory() as s:
                users = s.scalars(select(User).order_by(User.created_at)).all()
                devices = tokens.active_sessions(s)
            return templates.TemplateResponse(request, "settings.html", {
                **common,
                "users": users,
                "devices": devices,
                "current_fam": request.state.login.fam,
                "device_info": tokens.device_info,
                "login_on": access.login_enabled,
                "problems": login_problems(access),
                "redirect_uri": redirect_uri(request),
                "admin_emails": cfg.auth.admin_emails,
                "role_labels": auth.ROLE_LABELS,
            })
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                **common,
                "keywords": filters.load_keywords(cfg),
                "keywords_path": _display_path(cfg.keywords_file),
                "regions": regions_mod.load_regions(cfg),
                "regions_path": _display_path(cfg.regions_file),
                "region_codes": regions_mod.load_codes(cfg),
                "region_codes_path": _display_path(cfg.region_codes_file),
                "sites": load_site_settings(cfg),
                "generation": app.state.last_generation,
                "generate_status": GENERATE_STATUS,
                "max_keyword_length": filters.MAX_KEYWORD_LENGTH,
                "generating": app.state.generating,
            },
        )

    @app.post("/settings/keywords")
    async def add_keyword(request: Request):
        require_admin(request)
        keyword = await form_value(request, "keyword")
        if app.state.generating:
            return settings_redirect("필터를 생성하는 중입니다. 끝난 뒤에 다시 시도해 주세요.", error=True)
        try:
            filters.add_keyword(require_config(), keyword)
        except ValueError as e:
            return settings_redirect(str(e), error=True)
        return settings_redirect(f"키워드를 등록했습니다: {' '.join(keyword.split())}")

    @app.post("/settings/keywords/delete")
    async def delete_keyword(request: Request):
        require_admin(request)
        keyword = await form_value(request, "keyword")
        if app.state.generating:
            return settings_redirect("필터를 생성하는 중입니다. 끝난 뒤에 다시 시도해 주세요.", error=True)
        try:
            filters.remove_keyword(require_config(), keyword)
        except ValueError as e:
            return settings_redirect(str(e), error=True)
        return settings_redirect(f"키워드를 삭제했습니다: {keyword}")

    @app.post("/settings/regions")
    async def add_region(request: Request):
        require_admin(request)
        sido, city = await form_value(request, "sido"), await form_value(request, "city")
        if app.state.generating:
            return settings_redirect("필터를 생성하는 중입니다. 끝난 뒤에 다시 시도해 주세요.", error=True)
        try:
            code, removed = regions_mod.add_region(require_config(), sido, city)
        except ValueError as e:
            return settings_redirect(str(e), error=True)
        msg = f"지역을 등록했습니다: {regions_mod.label(code)}"
        if removed:
            msg += f" (포함되는 {', '.join(removed)}는 지웠습니다)"
        return settings_redirect(msg)

    @app.post("/settings/regions/delete")
    async def delete_region(request: Request):
        require_admin(request)
        code = await form_value(request, "region")
        if app.state.generating:
            return settings_redirect("필터를 생성하는 중입니다. 끝난 뒤에 다시 시도해 주세요.", error=True)
        try:
            regions_mod.remove_region(require_config(), code)
        except ValueError as e:
            return settings_redirect(str(e), error=True)
        return settings_redirect(f"지역을 삭제했습니다: {regions_mod.label(code)}")

    @app.post("/settings/generate")
    async def generate_filters(request: Request):
        """키워드·지역으로 사이트별 직무·지역 필터를 만들어 검색 필터 파일에 반영한다. 진행 중인 수집에는 영향이 없다."""
        require_admin(request)
        cfg = require_config()
        refresh = bool(await form_value(request, "refresh"))
        if app.state.generating:
            return settings_redirect("필터를 생성하는 중입니다. 끝난 뒤에 다시 시도해 주세요.", error=True)
        keywords = filters.load_keywords(cfg)
        regions = regions_mod.load_regions(cfg)
        if not keywords and not regions:
            return settings_redirect("등록된 키워드와 지역이 없습니다.", error=True)
        app.state.generating = True
        try:
            # 사이트에서 직무·지역 선택지를 받아올 수 있어 오래 걸릴 수 있으므로 별도 스레드에서
            results = await run_in_threadpool(filters.generate, cfg, keywords, refresh, SITE_LABELS, regions=regions)
        finally:
            app.state.generating = False
        app.state.last_generation = {
            "at": kst(utcnow(), "%Y-%m-%d %H:%M:%S"), "keywords": keywords,
            "regions": [regions_mod.label(r) for r in regions], "results": results,
        }
        failed = [SITE_LABELS.get(r.site, r.site) for r in results if r.status == "error"]
        if failed:
            return settings_redirect(f"일부 사이트는 필터를 만들지 못했습니다: {', '.join(failed)}", error=True)
        return settings_redirect("필터를 생성했습니다. 진행 중인 수집에는 영향이 없고 다음 수집부터 적용됩니다.")

    @app.post("/settings/login")
    async def toggle_login(request: Request):
        """로그인 사용 켜기·끄기 (화면에서는 확인 모달을 거친다)."""
        require_admin(request)
        enable = await form_value(request, "enabled") == "1"
        if enable and (problems := login_problems(get_access(request))):
            return settings_redirect(problems[0], error=True, tab="users")
        with session_factory() as s:
            auth.set_setting(s, auth.LOGIN_ENABLED_KEY, "1" if enable else "0")
            s.commit()
        if enable:
            return settings_redirect("로그인 사용을 켰습니다. 이제 '지금 수집'과 설정은 관리자만 쓸 수 있습니다.", tab="users")
        return settings_redirect("로그인 사용을 껐습니다. 모든 기능을 제한 없이 쓸 수 있습니다.", tab="users")

    @app.post("/settings/scheduler/toggle")
    async def toggle_schedule(request: Request):
        """자동 수집 시작·중지."""
        require_admin(request)
        require_config()
        enable = await form_value(request, "enabled") == "1"
        try:
            sched_mod.save_enabled(session_factory, enable, config.timezone, config.cron)
        except ValueError as e:
            return settings_redirect(str(e), error=True, tab="scheduler")
        if enable:
            return settings_redirect("자동 수집을 시작했습니다. 정해진 시각마다 사이트별 수집이 시작됩니다.", tab="scheduler")
        return settings_redirect("자동 수집을 중지했습니다. 진행 중인 수집은 끝까지 진행됩니다.", tab="scheduler")

    @app.post("/settings/scheduler/cron")
    async def set_schedule_cron(request: Request):
        """수집 주기 변경. 미리 정한 주기를 고르거나 cron을 직접 입력한다."""
        require_admin(request)
        require_config()
        choice = await form_value(request, "preset")
        expr = await form_value(request, "cron") if choice == "custom" else choice
        try:
            expr = sched_mod.save_cron(session_factory, expr, config.timezone)
        except ValueError as e:
            return settings_redirect(str(e), error=True, tab="scheduler")
        return settings_redirect(f"수집 주기를 바꿨습니다: {sched_mod.describe(expr)}", tab="scheduler")

    @app.post("/settings/users/{user_id}/role")
    async def set_user_role(request: Request, user_id: int):
        require_admin(request)
        role = await form_value(request, "role")
        if role not in auth.ROLE_LABELS:
            return settings_redirect("알 수 없는 등급입니다.", error=True, tab="users")
        me = get_access(request).user
        with session_factory() as s:
            user = s.get(User, user_id)
            if user is None:
                return settings_redirect("사용자를 찾을 수 없습니다.", error=True, tab="users")
            if user.email in auth_settings.admin_emails:
                return settings_redirect("설정 파일(config.yaml)에 지정된 관리자는 여기서 바꿀 수 없습니다.", error=True, tab="users")
            if me is not None and me.id == user.id:
                return settings_redirect("내 등급은 바꿀 수 없습니다.", error=True, tab="users")
            user.role = role
            s.commit()
            email = user.email
        return settings_redirect(f"{email}의 등급을 {auth.ROLE_LABELS[role]}(으)로 바꿨습니다.", tab="users")

    @app.post("/settings/users/{user_id}/logout")
    def logout_user(request: Request, user_id: int):
        """이 사용자의 모든 기기에서 로그아웃 (Refresh 토큰을 끊어 Access도 바로 못 씀)."""
        require_admin(request)
        with session_factory() as s:
            user = s.get(User, user_id)
            if user is None:
                return settings_redirect("사용자를 찾을 수 없습니다.", error=True, tab="users")
            n = tokens.revoke_user(s, user_id)
            email = user.email
        return settings_redirect(f"{email}의 로그인 {n}곳을 모두 로그아웃했습니다.", tab="users")

    @app.post("/settings/devices/{fam}/logout")
    def logout_device(request: Request, fam: str):
        """로그인한 기기 하나만 로그아웃."""
        require_admin(request)
        with session_factory() as s:
            row = s.get(LoginSession, fam)
            if row is None or not tokens.revoke(s, fam):
                return settings_redirect("이미 로그아웃된 기기입니다.", error=True, tab="users")
            user = s.get(User, row.user_id)
            label = tokens.device_label(row.user_agent)
        return settings_redirect(f"{user.email if user else ''}의 {label} 로그인을 로그아웃했습니다.", tab="users")

    # ------------------------------------------------------------ 마이페이지 (내 계정·내 로그인 기기)

    def require_user(request: Request) -> User:
        """로그인한 사용자 (로그인 사용 여부와 관계없이 로그인해야 한다)."""
        user = get_access(request).user
        if user is None:
            raise LoginRequired
        return user

    def me_redirect(message: str, error: bool = False) -> RedirectResponse:
        return RedirectResponse(f"/me?msg={quote(message)}" + ("&err=1" if error else ""), status_code=303)

    @app.get("/me", response_class=HTMLResponse)
    def mypage(request: Request, msg: str = "", err: int = 0):
        me = require_user(request)
        with session_factory() as s:
            user = s.get(User, me.id)
            devices = tokens.active_sessions(s).get(me.id, [])
        return templates.TemplateResponse(request, "mypage.html", {
            "user": user,
            "role_label": auth.ROLE_LABELS[auth.ADMIN] if get_access(request).is_admin else auth.ROLE_LABELS.get(user.role, user.role),
            "is_admin": get_access(request).is_admin,
            "devices": devices,
            "current_fam": request.state.login.fam,
            "device_info": tokens.device_info,
            "message": msg, "is_error": bool(err),
        })

    @app.post("/me/devices/logout-others")
    def logout_my_other_devices(request: Request):
        """지금 쓰는 기기를 뺀 내 모든 로그인을 끊는다."""
        me = require_user(request)
        with session_factory() as s:
            n = tokens.revoke_user(s, me.id, keep=request.state.login.fam)
        return me_redirect(f"다른 기기 {n}곳을 로그아웃했습니다." if n else "로그아웃할 다른 기기가 없습니다.")

    @app.post("/me/devices/{fam}/logout")
    def logout_my_device(request: Request, fam: str):
        """내 기기 하나만 로그아웃. 지금 쓰는 기기면 이 브라우저도 로그아웃된다."""
        me = require_user(request)
        login = request.state.login
        with session_factory() as s:
            row = s.get(LoginSession, fam)
            if row is None or row.user_id != me.id or not tokens.revoke(s, fam):  # 다른 사람의 기기는 끊을 수 없다
                return me_redirect("이미 로그아웃된 기기입니다.", error=True)
            label = tokens.device_label(row.user_agent)
        if fam == login.fam:
            login.signed_out()
            return RedirectResponse("/", status_code=303)
        return me_redirect(f"{label} 로그인을 로그아웃했습니다.")

    # 이력서 작성·관리 (web/resumes_web.py)
    resumes_web.install(app, session_factory=session_factory, templates=templates, require_user=require_user)

    @app.get("/auth/login")
    def login(request: Request, next: str = "/"):
        if not auth_settings.google_ready:
            return message_page(request, "로그인할 수 없음",
                                "구글 로그인이 설정되지 않았습니다. config.yaml의 auth에 구글 클라이언트 ID·시크릿을 넣어 주세요.", 503)
        state = auth.new_state()
        res = RedirectResponse(auth.authorize_url(auth_settings, redirect_uri(request), state), status_code=303)
        res.set_cookie(OAUTH_COOKIE, oauth_signer.dumps({"state": state, "next": auth.safe_next(next)}),
                       max_age=OAUTH_AGE, path="/auth", **cookie_options(request))
        return res

    @app.get("/auth/callback", name="auth_callback")
    async def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        res = await finish_login(request, code, state, error)
        res.delete_cookie(OAUTH_COOKIE, path="/auth", **cookie_options(request))  # 확인값은 한 번만 쓴다
        return res

    async def finish_login(request: Request, code: str, state: str, error: str):
        try:
            saved = oauth_signer.loads(request.cookies.get(OAUTH_COOKIE, ""), max_age=OAUTH_AGE)
        except BadSignature:  # 만료(SignatureExpired)도 여기
            saved = {}
        expected = saved.get("state")
        target = auth.safe_next(saved.get("next", "/"))
        if error:
            return message_page(request, "로그인 취소", "구글 로그인을 취소했거나 거부되었습니다.", 400)
        if not expected or not code or not secrets.compare_digest(state, expected):
            return message_page(request, "로그인 실패", "로그인 요청이 만료되었거나 올바르지 않습니다. 다시 시도해 주세요.", 400)
        try:
            info = await run_in_threadpool(auth.fetch_google_user, auth_settings, code, redirect_uri(request))
            with session_factory() as s:
                uid = auth.sign_in(s, info, auth_settings).id
                tokens.revoke(s, request.state.login.fam)  # 이 브라우저의 이전 로그인은 끊고 새로 시작
                tokens.sign_in(s, secret, uid, request.state.login,  # 새 Access·Refresh 발급 (응답 쿠키는 미들웨어가)
                               request.headers.get("user-agent"), request.client.host if request.client else None)
        except auth.LoginError as e:
            return message_page(request, "로그인 실패", str(e), 400)
        return RedirectResponse(target, status_code=303)

    @app.post("/auth/logout")
    def logout(request: Request):
        login = request.state.login
        with session_factory() as s:
            tokens.revoke(s, login.fam)  # 쿠키가 복사돼 있어도 더는 못 쓰게
        login.signed_out()
        return RedirectResponse("/", status_code=303)

    @app.get("/postings/{posting_id}", response_class=HTMLResponse)
    def posting_detail(request: Request, posting_id: int):
        with session_factory() as s:
            row = s.get(JobPosting, posting_id)
            if row is None:
                raise HTTPException(404)
            events = s.scalars(
                select(PostingEvent).where(PostingEvent.posting_id == posting_id).order_by(PostingEvent.id.desc())
            ).all()
            searches = s.scalars(
                select(PostingSearch).where(PostingSearch.posting_id == posting_id).order_by(PostingSearch.search_name)
            ).all()
            access = get_access(request)
            if access.can_favorite:  # 상세를 열면 확인한 공고 (관심 회사 새 공고의 NEW가 떨어짐)
                favs.mark_viewed(s, access.user.id, posting_id)
            favorite = access.can_favorite and s.get(FavoritePosting, (access.user.id, posting_id)) is not None
        return templates.TemplateResponse(
            request,
            "posting_detail.html",
            {
                "p": row, "events": events, "searches": searches, "favorite": favorite,
                "raw_json": json.dumps(row.raw, ensure_ascii=False, indent=2),
            },
        )

    # ------------------------------------------------------------ 연봉·인원 자료 (recruit_collector/salary_sources.py)
    # 올댓컴퍼니를 먼저 보고, 못 가져오면 국민연금 오픈 API. 공시 기준 평균연봉은 OpenDART (화면이 따로 불러옴)
    sources = SalarySources(config, session_factory) if config else None

    async def salary_call(what: str, fn) -> dict | JSONResponse:
        """자료 조회를 스레드에서 실행한다. 실패하면 화면에 보여줄 사유를 502로 돌려준다."""
        if sources is None:
            return JSONResponse({"ok": False, "message": "설정 없이 실행된 서버에서는 조회할 수 없습니다."}, status_code=503)
        try:
            data = await run_in_threadpool(fn)
        except SourceError as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=502)
        except Exception as e:  # 예상하지 못한 실패. 화면에 사유를 보여준다
            log.warning("연봉 정보 조회 실패 (%s): %s", what, e)
            return JSONResponse({"ok": False, "message": f"정보를 가져오지 못했습니다. ({type(e).__name__})"}, status_code=502)
        return {"ok": True, **data}

    @app.get("/api/postings/{posting_id}/salary")
    async def posting_salary(request: Request, posting_id: int, cid: str = ""):
        """공고 회사의 월별 인원·입사자·퇴사자·월평균급여 (조회할 때마다 가져옴. 국민연금 월별 자료는 DB에 둠).

        cid를 주면 검색 없이 그 회사를 가져온다 (모달에서 다른 후보를 고른 경우).
        로그인 사용 중이면 로그인한 사용자만 (비로그인이면 외부에 요청하지 않음).
        """
        require_login(request)
        if cid and not is_company_id(cid):
            raise HTTPException(400, "잘못된 회사 ID")
        with session_factory() as s:
            row = s.get(JobPosting, posting_id)
            if row is None:
                raise HTTPException(404)
            company, location, raw = row.company, row.location, row.raw
        return await salary_call(company, lambda: sources.posting(company, location, raw, cid or None))

    @app.get("/api/salary/disclosure")
    async def salary_disclosure(request: Request, name: str = "", address: str = ""):
        """공시 기준 평균연봉 (OpenDART 사업보고서 직원 현황). 공시 회사가 아니면 disclosure: null."""
        require_login(request)
        name = name.strip()[:200]
        if not name:
            raise HTTPException(400, "회사명이 필요합니다")
        if sources is None or not sources.dart.ready:
            return {"ok": True, "available": False, "disclosure": None}
        try:
            data = await run_in_threadpool(lambda: sources.disclosure(name, address.strip()[:500] or None))
        except Exception as e:
            log.warning("공시 평균연봉 조회 실패 (%s): %s", name, e)
            return JSONResponse({"ok": False, "message": f"전자공시에서 가져오지 못했습니다. ({type(e).__name__})"},
                                status_code=502)
        return {"ok": True, "available": True, "disclosure": data}

    @app.get("/salary", response_class=HTMLResponse)
    def salary_search_page(request: Request):
        require_login(request)
        return templates.TemplateResponse(request, "salary_search.html", {"max_compare": MAX_COMPARE})

    @app.get("/salary/c/{cid}", response_class=HTMLResponse)
    def salary_company_page(request: Request, cid: str):
        require_login(request)
        if not is_company_id(cid):
            raise HTTPException(404)
        return templates.TemplateResponse(request, "salary_company.html", {"cid": cid})

    @app.get("/salary/compare", response_class=HTMLResponse)
    def salary_compare_page(request: Request, ids: str = "", a: str = "", b: str = ""):
        """회사 2~4곳 비교 (?ids=1,2,3). 예전 두 회사 링크(?a=1&b=2)도 연다.
        자료는 화면이 회사별 API(/api/salary/company/{id})로 불러온다."""
        require_login(request)
        id_list = [x.strip() for x in ids.split(",")] if ids else [a, b]
        if not all(is_company_id(x) for x in id_list) or len(set(id_list)) != len(id_list) \
                or not MIN_COMPARE <= len(id_list) <= MAX_COMPARE:
            return RedirectResponse("/salary", status_code=303)
        return templates.TemplateResponse(request, "salary_compare.html", {"ids": id_list, "max_compare": MAX_COMPARE})

    @app.get("/api/salary/search")
    async def salary_search(request: Request, q: str = "", page: int = 1):
        """연봉·인원 메뉴의 회사 검색 (올댓컴퍼니 검색 결과 한 페이지)."""
        require_login(request)
        query = salary.search_query(q.strip())[:50]
        if not query or page < 1:
            raise HTTPException(400, "검색어를 입력하세요")

        access = get_access(request)

        def fn():
            data = sources.search(query, page)
            favorites = None  # 즐겨찾기를 쓸 수 없으면 null (화면에 별을 그리지 않음)
            if access.can_favorite:
                with session_factory() as s:
                    favorites = set(s.scalars(select(FavoriteCompany.company_id).where(
                        FavoriteCompany.user_id == access.user.id,
                        FavoriteCompany.company_id.in_([str(c["id"]) for c in data["candidates"]]))))
            for c in data["candidates"]:
                c["favorite"] = None if favorites is None else str(c["id"]) in favorites
            return data

        return await salary_call(query, fn)

    # ------------------------------------------------------------ 즐겨찾기 (계정별, 로그인 사용 중 로그인한 사용자만)
    # PUT = 지정, DELETE = 해제. 같은 요청을 여러 번 보내도 결과가 같다.
    # 다른 사이트에서는 세션 쿠키(SameSite=Lax)가 붙지 않고, JSON 본문이라 브라우저가 사전 확인(CORS)도 거친다
    def favorite_user(request: Request) -> int:
        access = get_access(request)
        if not access.can_favorite:
            raise HTTPException(403, "로그인해야 즐겨찾기를 쓸 수 있습니다.")
        return access.user.id

    @app.post("/api/postings/{posting_id}/viewed", status_code=204)
    def posting_viewed(request: Request, posting_id: int):
        """원문 링크를 누른 공고를 확인한 것으로 기록한다 (favorite.js가 sendBeacon으로 보냄)."""
        uid = favorite_user(request)
        with session_factory() as s:
            if s.get(JobPosting, posting_id) is None:
                raise HTTPException(404)
            favs.mark_viewed(s, uid, posting_id)

    @app.get("/api/postings/new")
    def postings_new(request: Request, ids: str = ""):
        """화면의 NEW 중 아직 확인하지 않은 것. 뒤로 가기로 캐시된 화면이 다시 보일 때 맞춘다 (favorite.js)."""
        uid = favorite_user(request)
        wanted = [int(x) for x in ids.split(",") if x.isdigit()][:200]
        with session_factory() as s:
            return {"ok": True, "new": sorted(favs.new_company_ids(s, uid, wanted)), "count": favs.new_company_count(s, uid),
                    "alerts": favs.alerts(s, s.get(User, uid))["total"]}

    @app.get("/api/favorites/{kind}")
    def favorite_states(request: Request, kind: str, ids: str = ""):
        """화면에 보이는 별들의 현재 상태. 뒤로 가기로 캐시된 화면이 다시 보일 때 다른 페이지에서 바꾼 것을 맞춘다 (favorite.js)."""
        uid = favorite_user(request)
        wanted = [x for x in ids.split(",") if is_company_id(x)][:200]
        with session_factory() as s:
            if kind == "postings":
                on = s.scalars(select(FavoritePosting.posting_id).where(
                    FavoritePosting.user_id == uid, FavoritePosting.posting_id.in_([int(x) for x in wanted])))
            elif kind == "companies":
                on = s.scalars(select(FavoriteCompany.company_id).where(
                    FavoriteCompany.user_id == uid, FavoriteCompany.company_id.in_(wanted)))
            else:
                raise HTTPException(404)
            return {"ok": True, "favorites": sorted(str(x) for x in on)}

    @app.put("/api/favorites/postings/{posting_id}")
    def favorite_posting_add(request: Request, posting_id: int):
        uid = favorite_user(request)
        with session_factory() as s:
            if s.get(JobPosting, posting_id) is None:
                raise HTTPException(404, "없는 공고입니다.")
            if s.get(FavoritePosting, (uid, posting_id)) is None:
                s.add(FavoritePosting(user_id=uid, posting_id=posting_id, created_at=utcnow()))
                s.commit()
        return {"ok": True, "favorite": True}

    @app.delete("/api/favorites/postings/{posting_id}")
    def favorite_posting_remove(request: Request, posting_id: int):
        uid = favorite_user(request)
        with session_factory() as s:
            s.query(FavoritePosting).filter_by(user_id=uid, posting_id=posting_id).delete()
            s.commit()
        return {"ok": True, "favorite": False}

    @app.put("/api/favorites/companies/{cid}")
    async def favorite_company_add(request: Request, cid: str):
        uid = favorite_user(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        name = body.get("name") if isinstance(body, dict) else None
        address = body.get("address") if isinstance(body, dict) else None
        if not is_company_id(cid) or len(cid) > 32 or not isinstance(name, str) or not name.strip()                 or not isinstance(address, (str, type(None))):
            raise HTTPException(400, "잘못된 회사 정보입니다.")
        with session_factory() as s:
            row = s.get(FavoriteCompany, (uid, cid))
            if row is None:
                s.add(FavoriteCompany(user_id=uid, company_id=cid, name=name.strip()[:200], name_key=company_key(name),
                                      address=(address or "").strip()[:500] or None, created_at=utcnow()))
                s.commit()
        return {"ok": True, "favorite": True}

    @app.delete("/api/favorites/companies/{cid}")
    def favorite_company_remove(request: Request, cid: str):
        uid = favorite_user(request)
        with session_factory() as s:
            s.query(FavoriteCompany).filter_by(user_id=uid, company_id=cid).delete()
            s.commit()
        return {"ok": True, "favorite": False}

    @app.get("/api/salary/company/{cid}")
    async def salary_company(request: Request, cid: str):
        """연봉·인원 메뉴에서 고른 회사의 월별 자료. 모달과 같은 모양으로 돌려준다."""
        require_login(request)
        if not is_company_id(cid):
            raise HTTPException(400, "잘못된 회사 ID")
        return await salary_call(cid, lambda: sources.company(cid))

    # 개인정보처리방침·이용약관: 로그인과 관계없이 누구나 볼 수 있다 (구글 OAuth 동의 화면에 등록하는 주소)
    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request):
        return templates.TemplateResponse(request, "privacy.html", {"legal": LEGAL})

    @app.get("/terms", response_class=HTMLResponse)
    def terms(request: Request):
        return templates.TemplateResponse(request, "terms.html", {"legal": LEGAL})

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request, search: str = "", page: int = Query(1, ge=1)):
        with session_factory() as s:
            cond = CrawlRun.search_name == search if search else True
            total = s.scalar(select(func.count()).select_from(CrawlRun).where(cond)) or 0
            pages = max(1, -(-total // RUNS_PAGE_SIZE))
            page = min(page, pages)
            rows = s.scalars(select(CrawlRun).where(cond).order_by(CrawlRun.id.desc())
                             .offset((page - 1) * RUNS_PAGE_SIZE).limit(RUNS_PAGE_SIZE)).all()
            charts = run_charts(s)

        def page_url(n: int) -> str:
            params = {k: v for k, v in {"search": search, "page": n if n > 1 else None}.items() if v}
            return "/runs" + (f"?{urlencode(params)}" if params else "")

        return templates.TemplateResponse(request, "runs.html", {
            "rows": rows, "search": search, "total": total, "page": page, "pages": pages, "page_url": page_url,
            "charts": charts, "chart_data": {c["site"]: c["points"] for g in charts for c in g["crawlers"]}})

    return app

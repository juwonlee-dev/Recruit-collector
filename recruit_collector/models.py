from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, UniqueConstraint, create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

log = logging.getLogger(__name__)

ACTIVE = "ACTIVE"
DELETED = "DELETED"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class JobPosting(Base):
    """공고 1건 = 1행. 사라진 공고는 행을 지우지 않고 status를 DELETED로 바꾼다."""

    __tablename__ = "job_postings"
    __table_args__ = (UniqueConstraint("site", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    site: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str]
    company: Mapped[str]
    # 회사명에서 법인 표기·괄호·띄어쓰기를 뺀 비교용 이름 (company_key). 관심 회사(즐겨찾기 회사)의 공고를 찾는 데 쓴다
    company_key: Mapped[str | None] = mapped_column(String(200), index=True)
    location: Mapped[str | None]
    # location 표기에서 뽑은 공통 지역코드 '|서울|서울 강남구|' (지역 검색용, region_names.location_codes)
    regions: Mapped[str | None]
    career: Mapped[str | None]
    education: Mapped[str | None]
    employment_type: Mapped[str | None]
    # 경력 정규화 (recruit_collector.career): 연차 0부터, 신입 = 0년차, max None = 상한 없음
    career_min: Mapped[int | None] = mapped_column(index=True)
    career_max: Mapped[int | None]
    career_any: Mapped[bool | None] = mapped_column(index=True)  # 경력무관으로 명시된 공고
    deadline: Mapped[str | None]  # 사이트 표기 그대로
    deadline_at: Mapped[date | None] = mapped_column(index=True)  # 마감일 (마감 임박순 정렬용)
    posted_at: Mapped[datetime | None] = mapped_column(index=True)  # 사이트에 등록된 시각 (최근 등록순 정렬용)
    # 헤드헌팅(서치펌) 공고인지. 원티드는 구분이 없어 모두 False. None = 알 수 없음 (이 컬럼 추가 전에 수집하고 다시 보이지 않은 사람인 공고)
    headhunting: Mapped[bool | None] = mapped_column(index=True)
    detail_url: Mapped[str]
    raw: Mapped[dict[str, Any]]
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=ACTIVE, index=True)
    first_seen_at: Mapped[datetime]  # 처음 수집한 시각
    last_seen_at: Mapped[datetime] = mapped_column(index=True)  # 가장 최근에 목록에서 수집한 시각 (최근 수집순)
    updated_at: Mapped[datetime] = mapped_column(index=True)  # 내용이 마지막으로 바뀐 시각. 처음엔 수집 시각 (최근 변경순)
    deleted_at: Mapped[datetime | None]


class PostingSearch(Base):
    """공고가 어떤 검색 조건(설정의 searches[].name)에서 발견되는지.

    삭제 판정은 검색 조건 단위로 한다. 한 공고가 여러 조건에 걸리면, 모든 조건에서
    빠졌을 때만 DELETED가 된다.
    """

    __tablename__ = "posting_searches"

    posting_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id"), primary_key=True)
    search_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    missed_runs: Mapped[int] = mapped_column(default=0)  # 연속으로 목록에서 보이지 않은 실행 횟수


class PostingEvent(Base):
    """공고 이력: CREATED / UPDATED / DELETED / REACTIVATED."""

    __tablename__ = "posting_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_runs.id"))
    event_type: Mapped[str] = mapped_column(String(16))
    changes: Mapped[dict[str, Any] | None]  # UPDATED: {필드: [이전값, 새값]}
    created_at: Mapped[datetime]


class CrawlRun(Base):
    """검색 조건 1개를 수집한 1회 실행 기록."""

    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    search_name: Mapped[str] = mapped_column(String(128), index=True)
    site: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    # 진행 중 페이지마다 갱신. RUNNING인데 오래 갱신이 없으면 프로세스가 중단된 것으로 본다
    heartbeat_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(16))  # RUNNING / SUCCESS / PARTIAL / FAILED
    trigger: Mapped[str | None] = mapped_column(String(16))  # schedule / cli / manual (조회 화면의 수동 수집)
    pages: Mapped[int] = mapped_column(default=0)  # 수집 중에도 페이지마다 갱신됨
    found: Mapped[int] = mapped_column(default=0)  # 수집 중에도 페이지마다 갱신됨
    total: Mapped[int | None]  # 사이트가 보고한 전체 결과 수 (진행률 표시용)
    created: Mapped[int] = mapped_column(default=0)
    updated: Mapped[int] = mapped_column(default=0)
    reactivated: Mapped[int] = mapped_column(default=0)
    deleted: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None]


class CrawlRequest(Base):
    """조회 화면의 '지금 수집' 요청 (수집 대기열). 수집기(worker 컨테이너)가 가져가 실행한다.

    PENDING(대기 중) → STARTED(수집 시작) 또는 SKIPPED(이미 수집 중 등으로 건너뜀).
    수집기가 꺼져 있으면 PENDING으로 남았다가 수집기가 뜨면 실행된다.
    한 사이트에 대기 중인 요청은 하나만 둘 수 있다 (부분 유니크 인덱스).
    """

    __tablename__ = "crawl_requests"
    __table_args__ = (
        Index("uq_crawl_requests_pending_site", "site", unique=True,
              sqlite_where=text("status = 'PENDING'"), postgresql_where=text("status = 'PENDING'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    site: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    requested_by: Mapped[str | None] = mapped_column(String(320))  # 요청한 사용자 이메일 (로그인 사용 중일 때)
    requested_at: Mapped[datetime]
    handled_at: Mapped[datetime | None]  # 수집기가 가져간 시각
    message: Mapped[str | None]  # 처리 결과 (건너뛴 이유 등)


class User(Base):
    """구글 로그인 사용자. 처음 로그인할 때 자동으로 가입된다."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(64), unique=True)  # 구글 계정 고유 ID
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str | None]
    picture: Mapped[str | None]
    role: Mapped[str] = mapped_column(String(16), default="user")  # admin / user
    created_at: Mapped[datetime]
    last_login_at: Mapped[datetime]


class FavoritePosting(Base):
    """계정별 즐겨찾기한 공고 (로그인 사용 중, 로그인한 사용자만)."""

    __tablename__ = "favorite_postings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime]


class PostingView(Base):
    """계정별로 확인한 공고: 공고 상세를 열었거나 원문 링크를 누른 공고. 관심 회사 새 공고의 NEW를 떼는 데 쓴다."""

    __tablename__ = "posting_views"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), primary_key=True)
    viewed_at: Mapped[datetime]


class FavoriteCompany(Base):
    """계정별 즐겨찾기한 회사. 연봉·인원 메뉴에서 찾은 올댓컴퍼니 회사 ID로 구분하고, 그때의 회사명·주소를 함께 둔다."""

    __tablename__ = "favorite_companies"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str | None] = mapped_column(String(200), index=True)  # company_key와 같은 방식 (공고와 맞추는 데 씀)
    address: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime]


# ---------------------------------------------------------------- 연봉·인원 자료 (recruit_collector/salary_sources.py)

class AtcCompany(Base):
    """올댓컴퍼니에서 본 회사(검색 결과·회사 페이지)의 이름·주소. 회사 페이지를 못 가져올 때 국민연금 자료를 찾는 데 쓴다."""

    __tablename__ = "atc_companies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(500))
    gone_at: Mapped[datetime | None]  # 회사 페이지가 없다고(404) 확인한 시각
    seen_at: Mapped[datetime]


class NpsMonth(Base):
    """국민연금 가입사업장 정보 오픈 API의 사업장 한 달 자료 (seq = 그 달 자료의 식별번호). 발표된 달은 바뀌지 않아 저장해 두고 쓴다."""

    __tablename__ = "nps_months"

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    workplace: Mapped[str] = mapped_column(String(300), index=True)  # 사업장 구분: 사업자번호 앞 6자리|사업장명(비교용)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(500))
    ym: Mapped[str] = mapped_column(String(7))  # 자료생성년월 YYYY-MM
    industry: Mapped[str | None] = mapped_column(String(200))
    headcount: Mapped[int]  # 가입자수
    notice_amount: Mapped[int]  # 당월고지금액 (원)
    hires: Mapped[int]  # 신규취득자수
    leaves: Mapped[int]  # 상실가입자수
    fetched_at: Mapped[datetime]


class DartCorp(Base):
    """OpenDART 공시 대상 회사 목록 (corpCode.xml). 회사명으로 고유번호를 찾는 데 쓴다."""

    __tablename__ = "dart_corps"

    corp_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str | None] = mapped_column(String(200), index=True)
    stock_code: Mapped[str | None] = mapped_column(String(6))
    modify_date: Mapped[str | None] = mapped_column(String(8))


class LoginSession(Base):
    """로그인 한 번(기기 하나)의 Refresh 토큰 기록 (web/tokens.py).

    Refresh 토큰은 쓸 때마다 새 것으로 바뀐다(current_jti). 이미 바뀐 옛 토큰(previous_jti)이 잠깐 뒤에 다시 오면
    동시에 보낸 요청으로 보고 받아 주지만, 그 뒤에 오면 토큰이 새어 나간 것으로 보고 이 로그인을 끊는다(revoked_at).
    """

    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(43), primary_key=True)  # 토큰의 fam 값
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime]  # 로그인한 때 (여기서 최대 60일)
    expires_at: Mapped[datetime]  # 지금 Refresh 토큰의 만료 시각
    current_jti: Mapped[str] = mapped_column(String(43))
    previous_jti: Mapped[str | None] = mapped_column(String(43))
    rotated_at: Mapped[datetime | None]  # 마지막으로 토큰을 바꾼 때 (= 최근 사용, 15분 단위)
    revoked_at: Mapped[datetime | None]
    user_agent: Mapped[str | None] = mapped_column(String(400))  # 로그인한 브라우저 (기기 표시용)
    ip: Mapped[str | None] = mapped_column(String(64))  # 로그인한 IP


class DartCache(Base):
    """OpenDART 응답 보관 (회사 개황·직원 현황). 자주 바뀌지 않아 일정 기간 다시 요청하지 않는다."""

    __tablename__ = "dart_cache"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)  # 예) company:00904672, emp:00904672:2025:11011
    payload: Mapped[dict[str, Any]]
    fetched_at: Mapped[datetime]


# ---------------------------------------------------------------- 이력서 (recruit_collector/web/resumes.py)
# 위젯 → 템플릿 → 이력서 순으로 '복사'해 쓴다. 템플릿에 위젯을 넣으면 그때의 위젯 설정을 템플릿에 복사하고,
# 이력서를 만들면 그때의 템플릿 배치를 이력서에 복사한다. 그래서 원본을 고치거나 지워도 이미 만든 것은 바뀌지 않는다.
# owner_id가 없는 행은 서비스가 기본으로 넣어 두는 위젯·템플릿(builtin_key)이며 누구나 보고 복사만 할 수 있다.

class ResumeWidget(Base):
    """이력서 위젯: 템플릿에 배치하는 항목 하나의 정의 (입력 칸 목록, 힌트, 예시 텍스트 등)."""

    __tablename__ = "resume_widgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    builtin_key: Mapped[str | None] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(300))
    visibility: Mapped[str] = mapped_column(String(10), default="private")  # private / public
    kind: Mapped[str] = mapped_column(String(20))  # form / heading / note / divider (resumes.WIDGET_KINDS)
    config: Mapped[dict[str, Any]]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ResumeTemplate(Base):
    """이력서 템플릿: 위젯을 행·칸에 배치한 문서 틀 (layout)."""

    __tablename__ = "resume_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    builtin_key: Mapped[str | None] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(300))
    visibility: Mapped[str] = mapped_column(String(10), default="private")
    layout: Mapped[dict[str, Any]]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class Resume(Base):
    """이용자가 작성한 이력서. 만든 때의 템플릿 배치(layout)를 복사해 두고, 입력한 내용은 data에 위젯별로 둔다.

    본인만 볼 수 있다. 사진 같은 첨부파일 위젯을 추가할 때는 파일을 별도 테이블에 두고 data에는 그 ID만 넣는다.
    """

    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    template_id: Mapped[int | None] = mapped_column(ForeignKey("resume_templates.id", ondelete="SET NULL"))
    template_name: Mapped[str | None] = mapped_column(String(100))  # 만든 때의 템플릿 이름 (템플릿이 지워져도 보이게)
    layout: Mapped[dict[str, Any]]
    data: Mapped[dict[str, Any]]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class AppSetting(Base):
    """화면에서 바꾸는 설정 값 (로그인 사용 여부, 토큰 서명 키 등)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str]


def make_session_factory(database_url: str) -> sessionmaker:
    # pool_pre_ping: DB 컨테이너가 다시 뜬 뒤 끊어진 연결을 쓰지 않도록 쓰기 전에 확인
    engine = create_engine(database_url, pool_pre_ping=True)
    if engine.url.get_backend_name() == "sqlite" and engine.url.database not in (None, "", ":memory:"):
        Path(engine.url.database).parent.mkdir(parents=True, exist_ok=True)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            # 수집기가 쓰는 동안 웹 화면이 읽을 수 있도록 WAL 모드 사용
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            # 사이트별 병렬 수집 시 두 스레드가 동시에 쓰기를 시도하므로 잠금 대기를 넉넉히 둔다
            cur.execute("PRAGMA busy_timeout=60000")
            cur.close()

    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    _fill_regions(engine)
    _fill_company_keys(engine)
    _fill_headhunting(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _add_missing_columns(engine: Engine) -> None:
    """모델에 새로 추가된 nullable 컬럼과 인덱스를 기존 테이블에 추가하는 최소한의 마이그레이션.

    컬럼 삭제·타입 변경 등 그 이상의 스키마 변경이 필요해지면 Alembic 도입을 권장.
    """
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                if not col.nullable:
                    raise RuntimeError(f"{table.name}.{col.name}: NOT NULL 컬럼은 자동 추가할 수 없음")
                col_type = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}'))
                log.info("컬럼 추가: %s.%s", table.name, col.name)
            for index in table.indexes:
                index.create(conn, checkfirst=True)


def company_key(name: str | None) -> str | None:
    """회사명 비교용 이름: (주)·주식회사 등 법인 표기, 괄호 안 표기, 띄어쓰기를 빼고 소문자로. 예) (주)카카오 → 카카오"""
    from recruit_collector.salary import normalize_name

    return normalize_name(name or "")[:200] or None


COMPANY_KEY_VERSION = "1"  # company_key 계산 방식을 바꾸면 올려서 모두 다시 계산한다


def _fill_company_keys(engine: Engine) -> None:
    """공고·즐겨찾기 회사의 비교용 회사명을 채운다 (컬럼 추가 전 행, 또는 계산 방식이 바뀐 경우 전부)."""
    key = "company_key_version"
    with engine.begin() as conn:
        version = conn.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": key}).scalar()
        every = version != COMPANY_KEY_VERSION
        for table, src, dst, pk in (("job_postings", "company", "company_key", "id"),
                                    ("favorite_companies", "name", "name_key", "user_id, company_id")):
            where = "1 = 1" if every else f"{dst} IS NULL"
            rows = conn.execute(text(f"SELECT {pk}, {src} AS src FROM {table} WHERE {where}")).mappings().all()
            if not rows:
                continue
            cond = " AND ".join(f"{c.strip()} = :{c.strip()}" for c in pk.split(","))
            conn.execute(text(f"UPDATE {table} SET {dst} = :v WHERE {cond}"),
                         [{**{c.strip(): r[c.strip()] for c in pk.split(",")}, "v": company_key(r["src"])} for r in rows])
            log.info("비교용 회사명 계산: %s %d건", table, len(rows))
        if every:
            conn.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": key})
            conn.execute(text("INSERT INTO app_settings (key, value) VALUES (:k, :v)"), {"k": key, "v": COMPANY_KEY_VERSION})


def _fill_headhunting(engine: Engine) -> None:
    """컬럼 추가 전에 수집한 잡코리아·리멤버 공고의 헤드헌팅 여부를 저장해 둔 원본(raw)으로 채우고, 원티드 공고는 일반으로 둔다.

    판별 기준은 각 크롤러와 같다. 사람인은 원본에 표시가 없어 다음 수집 때 채워진다.
    """
    import json

    from recruit_collector.crawlers import jobkorea, remember

    rules = {"jobkorea": ("userRefType", jobkorea.HEADHUNTING_REF_TYPE),
             "remember": ("job_posting_type", remember.HEADHUNTING_TYPE),
             "wanted": None}  # 구분 없음 → 일반
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT id, site, raw FROM job_postings WHERE headhunting IS NULL AND site IN ('jobkorea', 'remember', 'wanted')")).all()
        if not rows:
            return
        values = []
        for r in rows:
            if rules[r.site] is None:
                values.append({"id": r.id, "v": False})
                continue
            raw = json.loads(r.raw) if isinstance(r.raw, str) else (r.raw or {})
            key, value = rules[r.site]
            values.append({"id": r.id, "v": raw.get(key) == value})
        conn.execute(text("UPDATE job_postings SET headhunting = :v WHERE id = :id"), values)
        log.info("공고 헤드헌팅 여부 채움: %d건", len(values))


def _fill_regions(engine: Engine) -> None:
    """공고의 지역코드를 지역 표기로 채운다.

    비어 있는 공고(컬럼 추가 전에 수집한 공고)만 채우고, 계산 방식(CODES_VERSION)이 바뀌었으면 전부 다시 계산한다.
    """
    from recruit_collector.region_names import CODES_VERSION, encode, location_codes

    key = "regions_codes_version"
    with engine.begin() as conn:
        version = conn.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": key}).scalar()
        where = "regions IS NULL" if version == CODES_VERSION else "1 = 1"
        rows = conn.execute(text(f"SELECT id, location FROM job_postings WHERE {where}")).all()
        if rows:
            conn.execute(
                text("UPDATE job_postings SET regions = :regions WHERE id = :id"),
                [{"id": r.id, "regions": encode(location_codes(r.location))} for r in rows],
            )
            log.info("공고 지역코드 계산: %d건", len(rows))
        if version != CODES_VERSION:
            conn.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": key})
            conn.execute(text("INSERT INTO app_settings (key, value) VALUES (:k, :v)"), {"k": key, "v": CODES_VERSION})

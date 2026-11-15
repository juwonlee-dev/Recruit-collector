"""즐겨찾기 활용: 마감 D-day, 변경 알림, 관심 회사 새 공고 (계정별).

- D-day: 즐겨찾기한 공고의 제목 옆 배지. 마감일(deadline_at)을 아는 공고만. 마감·삭제된 공고는 '마감·삭제됨'.
- 변경 알림: 즐겨찾기한 공고가 최근 CHANGE_DAYS일 안에(즐겨찾기한 뒤에) 바뀌었거나(UPDATED) 다시 나타났으면(REACTIVATED) '변경됨'.
- 관심 회사 새 공고: 즐겨찾기한 회사(연봉·인원의 올댓컴퍼니 회사)와 비교용 회사명(company_key)이 같은 진행 중 공고.
  회사를 즐겨찾기한 뒤 처음 수집된 공고에 'NEW'를 붙이고, 그 공고의 상세를 열거나 원문 링크를 누르면(posting_views)
  확인한 것으로 보고 뗀다. 탭을 보기만 해서는 떼지 않는다.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from recruit_collector.models import (ACTIVE, DELETED, FavoriteCompany, FavoritePosting, JobPosting, PostingEvent, PostingView,
                                      User, utcnow)

CHANGE_DAYS = 7
URGENT_DAYS = 3  # 이하이면 빨간 배지
SOON_DAYS = 7  # 이하이면 주황 배지 (마감 임박으로 셈)

FIELD_LABELS = {"title": "제목", "company": "회사", "location": "지역", "career": "경력", "education": "학력",
                "employment_type": "고용형태", "deadline": "마감", "detail_url": "원문 주소"}


def dday_badge(p: JobPosting, today: date) -> tuple[str, str] | None:
    """(표시, 단계) 단계: urgent / soon / later / closed. 마감일을 모르면 None."""
    if p.status == DELETED:
        return "마감·삭제됨", "closed"
    if p.deadline_at is None:
        return None
    days = (p.deadline_at - today).days
    if days < 0:
        return "마감일 지남", "closed"
    if days == 0:
        return "오늘 마감", "urgent"
    return f"D-{days}", "urgent" if days <= URGENT_DAYS else "soon" if days <= SOON_DAYS else "later"


def is_past(today: date):
    """지난 공고(마감·삭제되었거나 마감일이 지남) 조건. 즐겨찾기 탭에서 아래 '지난 공고'로 모은다."""
    return or_(JobPosting.status == DELETED, JobPosting.deadline_at < today)


def favorite_ids(uid: int):
    return select(FavoritePosting.posting_id).where(FavoritePosting.user_id == uid)


def _changed_events(uid: int):
    """즐겨찾기한 공고의 최근 변경 이벤트 (즐겨찾기한 뒤, 최근 CHANGE_DAYS일)."""
    since = utcnow() - timedelta(days=CHANGE_DAYS)
    return (
        select(PostingEvent)
        .join(FavoritePosting, and_(FavoritePosting.posting_id == PostingEvent.posting_id, FavoritePosting.user_id == uid))
        .where(PostingEvent.event_type.in_(("UPDATED", "REACTIVATED")), PostingEvent.created_at >= since,
               PostingEvent.created_at >= FavoritePosting.created_at)
    )


def changed_map(s: Session, uid: int, posting_ids: list[int]) -> dict[int, str]:
    """{공고 ID: '마감·지역 변경' 같은 요약}. 배지 툴팁에 쓴다."""
    if not posting_ids:
        return {}
    fields: dict[int, list[str]] = {}
    for e in s.scalars(_changed_events(uid).where(PostingEvent.posting_id.in_(posting_ids)).order_by(PostingEvent.id)):
        names = fields.setdefault(e.posting_id, [])
        new = ["다시 올라옴"] if e.event_type == "REACTIVATED" else [f"{FIELD_LABELS.get(f, f)} 변경" for f in (e.changes or {})]
        names.extend(n for n in new if n not in names)
    return {pid: " · ".join(names) for pid, names in fields.items()}


def summary(s: Session, uid: int, today: date) -> dict:
    """즐겨찾기 탭 위 요약: 전체·마감 임박·변경됨·지난 공고 수."""
    fav = JobPosting.id.in_(favorite_ids(uid))
    soon = and_(JobPosting.status == ACTIVE, JobPosting.deadline_at >= today,
                JobPosting.deadline_at <= today + timedelta(days=SOON_DAYS))
    row = s.execute(select(
        func.count(),
        func.coalesce(func.sum(case((soon, 1), else_=0)), 0),
        func.coalesce(func.sum(case((is_past(today), 1), else_=0)), 0),
    ).where(fav)).one()
    return {"total": row[0], "soon": row[1], "past": row[2], "changed": changed_count(s, uid)}


def changed_count(s: Session, uid: int) -> int:
    sq = _changed_events(uid).subquery()
    return s.scalar(select(func.count(func.distinct(sq.c.posting_id)))) or 0


# ---------------------------------------------------------------- 관심 회사 새 공고

def company_condition(uid: int):
    keys = select(FavoriteCompany.name_key).where(FavoriteCompany.user_id == uid, FavoriteCompany.name_key.is_not(None))
    return JobPosting.company_key.in_(keys)


def _new_company_postings(uid: int):
    """관심 회사 새 공고: 회사를 즐겨찾기한 뒤 처음 수집된 진행 중 공고 중, 아직 확인하지 않은(상세·원문을 열지 않은) 것."""
    viewed = select(PostingView.posting_id).where(PostingView.user_id == uid)
    return (
        select(JobPosting.id)
        .join(FavoriteCompany, and_(FavoriteCompany.name_key == JobPosting.company_key, FavoriteCompany.user_id == uid))
        .where(JobPosting.status == ACTIVE, JobPosting.first_seen_at > FavoriteCompany.created_at,
               JobPosting.id.not_in(viewed))
        .distinct()
    )


def new_company_count(s: Session, uid: int) -> int:
    return s.scalar(select(func.count()).select_from(_new_company_postings(uid).subquery())) or 0


def new_company_ids(s: Session, uid: int, posting_ids: list[int]) -> set[int]:
    if not posting_ids:
        return set()
    return set(s.scalars(_new_company_postings(uid).where(JobPosting.id.in_(posting_ids))))


def mark_viewed(s: Session, uid: int, posting_id: int) -> None:
    """공고를 확인한 것으로 기록한다 (상세 열기·원문 누르기). 이미 있으면 시각만 갱신."""
    row = s.get(PostingView, (uid, posting_id))
    if row is None:
        s.add(PostingView(user_id=uid, posting_id=posting_id, viewed_at=utcnow()))
    else:
        row.viewed_at = utcnow()
    s.commit()


def alerts(s: Session, user: User) -> dict:
    """상단 '공고' 메뉴 옆 숫자: 변경된 즐겨찾기 공고 + 관심 회사 새 공고."""
    changed = changed_count(s, user.id)
    new = new_company_count(s, user.id)
    return {"changed": changed, "new": new, "total": changed + new}

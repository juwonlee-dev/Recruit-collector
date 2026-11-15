import pytest
from sqlalchemy import select

from recruit_collector.crawlers.base import CrawlResult, Posting
from recruit_collector.models import ACTIVE, DELETED, JobPosting, PostingEvent, make_session_factory
from recruit_collector.sync import sync_search


@pytest.fixture
def session():
    factory = make_session_factory("sqlite:///:memory:")
    with factory() as s:
        yield s


def posting(pid: str, title: str = "백엔드 개발자", **kw) -> Posting:
    return Posting(external_id=pid, title=title, company="회사", detail_url=f"https://example.com/{pid}", **kw)


def run(session, postings, *, search="s1", complete=True, threshold=1):
    stats = sync_search(
        session, site="saramin", search_name=search,
        result=CrawlResult(postings, pages=1, complete=complete), missing_threshold=threshold,
    )
    session.commit()
    return stats


def get(session, pid) -> JobPosting:
    return session.scalars(select(JobPosting).where(JobPosting.external_id == pid)).one()


def events(session, pid) -> list[str]:
    row = get(session, pid)
    return [e.event_type for e in session.scalars(select(PostingEvent).where(PostingEvent.posting_id == row.id).order_by(PostingEvent.id))]


def test_new_postings_are_created(session):
    stats = run(session, [posting("1"), posting("2")])
    assert (stats.created, stats.updated, stats.deleted) == (2, 0, 0)
    assert get(session, "1").status == ACTIVE
    assert events(session, "1") == ["CREATED"]


def test_changed_posting_is_updated_with_diff(session):
    run(session, [posting("1", deadline="~ 11/15")])
    stats = run(session, [posting("1", deadline="~ 11/30")])

    assert stats.updated == 1
    assert get(session, "1").deadline == "~ 11/30"
    ev = session.scalars(select(PostingEvent).where(PostingEvent.event_type == "UPDATED")).one()
    assert ev.changes == {"deadline": ["~ 11/15", "~ 11/30"]}


def test_unchanged_posting_only_refreshes_raw(session):
    run(session, [posting("1", raw={"readCount": 1})])
    stats = run(session, [posting("1", raw={"readCount": 2})])
    assert stats.updated == 0
    assert get(session, "1").raw == {"readCount": 2}


def test_missing_posting_is_soft_deleted(session):
    run(session, [posting("1"), posting("2")])
    stats = run(session, [posting("1")])

    assert stats.deleted == 1
    row = get(session, "2")
    assert row.status == DELETED and row.deleted_at is not None
    assert events(session, "2") == ["CREATED", "DELETED"]


def test_deletion_waits_for_threshold(session):
    run(session, [posting("1"), posting("2")], threshold=2)
    assert run(session, [posting("1")], threshold=2).deleted == 0
    assert get(session, "2").status == ACTIVE
    assert run(session, [posting("1")], threshold=2).deleted == 1


def test_seen_again_resets_missed_count(session):
    run(session, [posting("1"), posting("2")], threshold=2)
    run(session, [posting("1")], threshold=2)  # 1회 누락
    run(session, [posting("1"), posting("2")], threshold=2)  # 다시 보임 → 카운트 초기화
    assert run(session, [posting("1")], threshold=2).deleted == 0


def test_incomplete_crawl_never_deletes(session):
    run(session, [posting("1"), posting("2")])
    stats = run(session, [], complete=False)
    assert stats.deleted == 0
    assert get(session, "2").status == ACTIVE


def test_deleted_posting_reappears(session):
    run(session, [posting("1")])
    run(session, [])
    stats = run(session, [posting("1")])

    assert stats.reactivated == 1
    assert get(session, "1").status == ACTIVE
    assert events(session, "1") == ["CREATED", "DELETED", "REACTIVATED"]


def test_posting_tracked_by_another_search_is_not_deleted(session):
    run(session, [posting("1")], search="python")
    run(session, [posting("1")], search="backend")

    assert run(session, [], search="python").deleted == 0
    assert get(session, "1").status == ACTIVE
    assert run(session, [], search="backend").deleted == 1
    assert get(session, "1").status == DELETED


def test_times_come_from_collection_time(session):
    from datetime import datetime

    t1, t2 = datetime(2026, 11, 5, 1), datetime(2026, 11, 5, 2)
    run(session, [posting("1", collected_at=t1)])
    row = get(session, "1")
    assert (row.first_seen_at, row.last_seen_at, row.updated_at) == (t1, t1, t1)

    run(session, [posting("1", collected_at=t2)])  # 내용 변화 없음
    assert (row.last_seen_at, row.updated_at) == (t2, t1)


def test_posted_at_is_kept_but_inferred_value_is_replaced_by_exact(session):
    from datetime import datetime

    inferred, exact, other = datetime(2026, 11, 1), datetime(2026, 11, 3), datetime(2026, 11, 4)
    run(session, [posting("1", posted_at=inferred, raw={"posted_at_inferred": True})])
    run(session, [posting("1", posted_at=exact)])
    row = get(session, "1")
    assert row.posted_at == exact and "posted_at_inferred" not in row.raw

    run(session, [posting("1", posted_at=other, raw={"posted_at_inferred": True})])  # 정확한 값은 유지
    assert row.posted_at == exact and "posted_at_inferred" not in row.raw

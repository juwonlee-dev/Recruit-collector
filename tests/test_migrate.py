import pytest
from sqlalchemy import func, select

from recruit_collector.crawlers.base import CrawlResult, Posting
from recruit_collector.migrate import copy_all, sqlite_readonly, verify
from recruit_collector.models import JobPosting, PostingEvent, User, make_session_factory, utcnow
from recruit_collector.sync import sync_search


def test_copy_all_keeps_every_row(tmp_path):
    """SQLite → 다른 DB로 모든 표를 id 그대로 옮기고, 검증이 같다고 보고한다. 원본은 바뀌지 않는다."""
    src_path = tmp_path / "src.db"
    src = make_session_factory(f"sqlite:///{src_path}")
    with src() as s:
        items = [Posting(external_id=str(i), title=f"공고{i}", company="회사", detail_url="https://example.com",
                         employment_type="정규직", raw={"키": [i, "값"]}) for i in range(5)]
        sync_search(s, site="saramin", search_name="x", result=CrawlResult(items, 1, True), missing_threshold=1)
        sync_search(s, site="saramin", search_name="x", result=CrawlResult(items[:3], 1, True), missing_threshold=1)
        s.add(User(google_sub="g1", email="a@example.com", created_at=utcnow(), last_login_at=utcnow()))
        s.commit()

    dst = make_session_factory(f"sqlite:///{tmp_path / 'dst.db'}")
    engine = dst.kw["bind"]
    counts = copy_all(sqlite_readonly(src_path), engine)
    assert counts["job_postings"] == 5 and counts["users"] == 1 and counts["posting_events"] == 7
    assert verify(sqlite_readonly(src_path), engine) == []
    with dst() as s:
        assert s.scalar(select(JobPosting).where(JobPosting.external_id == "4")).status == "DELETED"
        assert s.scalar(select(func.count()).select_from(PostingEvent)) == 7

    with pytest.raises(RuntimeError):  # 대상에 공고가 있으면 --replace 없이는 멈춤
        copy_all(sqlite_readonly(src_path), engine)
    copy_all(sqlite_readonly(src_path), engine, replace=True)
    assert verify(sqlite_readonly(src_path), engine) == []

    with dst() as s:  # 대상만 바꾸면 검증이 차이를 찾는다
        s.scalar(select(JobPosting).where(JobPosting.external_id == "0")).title = "바뀜"
        s.commit()
    assert verify(sqlite_readonly(src_path), engine) == ["job_postings: 1행의 값이 다름"]

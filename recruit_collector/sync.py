"""수집 결과를 DB에 반영: 신규 추가 / 변경 갱신 / 사라진 공고 삭제 처리."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from recruit_collector.crawlers.base import CrawlResult, Posting
from recruit_collector.region_names import encode, location_codes
from recruit_collector.models import (
    ACTIVE,
    DELETED,
    JobPosting,
    PostingEvent,
    PostingSearch,
    company_key,
    utcnow,
)

# 변경 감지 대상 필드. raw는 조회수 등 매번 바뀌는 값이 섞여 있어 제외한다.
TRACKED_FIELDS = ("title", "company", "location", "career", "education", "employment_type", "deadline", "detail_url")
# 위 필드(표기)에서 파생된 정규화 값과 헤드헌팅 여부. 변경 감지는 원래 표기로 하고, 이 값들은 매번 덮어쓴다.
DERIVED_FIELDS = ("career_min", "career_max", "career_any", "deadline_at", "headhunting")


@dataclass
class SyncStats:
    found: int = 0
    created: int = 0
    updated: int = 0
    reactivated: int = 0
    deleted: int = 0


def content_hash(p: Posting) -> str:
    data = {f: getattr(p, f) for f in TRACKED_FIELDS}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def sync_search(
    session: Session,
    *,
    site: str,
    search_name: str,
    result: CrawlResult,
    missing_threshold: int,
    run_id: int | None = None,
) -> SyncStats:
    """검색 조건 1개의 수집 결과를 반영한다.

    result.complete가 False(오류, max_pages 도달)이면 수집된 공고의 추가/갱신만 하고
    삭제 판정은 하지 않는다. 일부만 본 상태에서 "안 보였다"는 판단을 할 수 없기 때문.

    수집 도중 공고가 추가/마감되면 페이지가 밀려 일부 공고를 놓칠 수 있으므로,
    missing_threshold 회 연속으로 보이지 않았을 때만 삭제로 처리한다.
    """
    now = utcnow()
    stats = SyncStats(found=len(result.postings))

    def event(posting: JobPosting, event_type: str, changes: dict | None = None) -> None:
        session.add(PostingEvent(posting_id=posting.id, run_id=run_id, event_type=event_type, changes=changes, created_at=now))

    ids = [p.external_id for p in result.postings]
    existing = {
        row.external_id: row
        for row in session.scalars(select(JobPosting).where(JobPosting.site == site, JobPosting.external_id.in_(ids)))
    }
    links = {
        link.posting_id: link
        for link in session.scalars(select(PostingSearch).where(PostingSearch.search_name == search_name))
    }

    seen_posting_ids: set[int] = set()
    for item in result.postings:
        h = content_hash(item)
        collected = item.collected_at or now  # 이 공고가 담긴 목록 페이지를 가져온 시각
        row = existing.get(item.external_id)
        if row is None:
            row = JobPosting(
                site=site, **{f: getattr(item, f) for f in TRACKED_FIELDS + DERIVED_FIELDS},
                external_id=item.external_id, posted_at=item.posted_at,
                raw=item.raw, content_hash=h, status=ACTIVE,
                # 처음 수집 시에는 수집 시각을 변경 시각으로도 기록
                first_seen_at=collected, last_seen_at=collected, updated_at=collected, deleted_at=None,
            )
            session.add(row)
            session.flush()
            event(row, "CREATED")
            stats.created += 1
        else:
            if row.status == DELETED:
                row.status, row.deleted_at = ACTIVE, None
                event(row, "REACTIVATED")
                stats.reactivated += 1
            if row.content_hash != h:
                changes = {
                    f: [getattr(row, f), getattr(item, f)] for f in TRACKED_FIELDS if getattr(row, f) != getattr(item, f)
                }
                for f in changes:
                    setattr(row, f, getattr(item, f))
                row.content_hash, row.updated_at = h, collected
                event(row, "UPDATED", changes)
                stats.updated += 1
            for f in DERIVED_FIELDS:
                setattr(row, f, getattr(item, f))
            _merge_posted_at(row, item)
            row.last_seen_at = collected
        row.regions = encode(location_codes(row.location))  # 지역 표기에서 파생 (매번 덮어씀)
        row.company_key = company_key(row.company)  # 관심 회사 공고 찾기용 (매번 덮어씀)

        seen_posting_ids.add(row.id)
        link = links.get(row.id)
        if link is None:
            session.add(PostingSearch(posting_id=row.id, search_name=search_name, missed_runs=0))
        else:
            link.missed_runs = 0

    if result.complete:
        unlinked: list[int] = []
        for posting_id, link in links.items():
            if posting_id in seen_posting_ids:
                continue
            link.missed_runs += 1
            if link.missed_runs >= missing_threshold:
                session.delete(link)
                unlinked.append(posting_id)
        session.flush()

        # 다른 검색 조건에서도 더 이상 추적되지 않는 공고만 삭제 처리
        still_linked = set(session.scalars(select(PostingSearch.posting_id).where(PostingSearch.posting_id.in_(unlinked))))
        for row in session.scalars(select(JobPosting).where(JobPosting.id.in_(unlinked), JobPosting.status == ACTIVE)):
            if row.id in still_linked:
                continue
            row.status, row.deleted_at = DELETED, now
            event(row, "DELETED")
            stats.deleted += 1

    return stats


def _merge_posted_at(row: JobPosting, item: Posting) -> None:
    """등록 시각은 한 번 정해지면 바뀌지 않는다. 비어 있거나 추정값일 때만 더 나은 값으로 채운다.

    (사람인은 목록에 '수정일'만 보이는 공고의 등록일을 추정하므로, 이후 실제 등록일이 보이면 교체)
    """
    prev_inferred = bool(row.raw.get("posted_at_inferred"))
    new_inferred = bool(item.raw.get("posted_at_inferred"))
    if item.posted_at and (row.posted_at is None or (prev_inferred and not new_inferred)):
        row.posted_at, inferred = item.posted_at, new_inferred
    else:
        inferred = prev_inferred and row.posted_at is not None
    raw = {k: v for k, v in item.raw.items() if k != "posted_at_inferred"}
    if inferred:
        raw["posted_at_inferred"] = True
    row.raw = raw

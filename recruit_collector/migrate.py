"""SQLite DB의 모든 데이터를 설정의 DB(PostgreSQL 등)로 옮긴다.

- 원본 SQLite는 읽기 전용으로 열어 바꾸지 않는다.
- 대상 DB의 표를 비우고(대상에 공고가 있으면 --replace 없이는 멈춤) id를 그대로 유지해 복사한 뒤,
  PostgreSQL이면 id 자동 증가(시퀀스)를 이어지게 맞춘다.
- 복사는 한 트랜잭션이라 중간에 실패하면 대상에 아무것도 남지 않는다.
- 끝나면 표마다 모든 행을 원본과 비교해 하나라도 다르면 실패로 알린다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine

from recruit_collector.models import Base, JobPosting

log = logging.getLogger(__name__)

BATCH = 1000


def sqlite_readonly(path: str | Path) -> Engine:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    return create_engine(f"sqlite:///file:{path.as_posix()}?mode=ro&uri=true")


def _rows(conn, table, cols):
    pk = list(table.primary_key.columns)
    return conn.execute(select(*(table.c[c] for c in cols)).order_by(*pk))


def copy_all(src: Engine, dst: Engine, replace: bool = False) -> dict[str, int]:
    """src의 모든 표를 dst로 복사하고 표별 행 수를 돌려준다. dst에는 표가 이미 만들어져 있어야 한다."""
    src_cols = {t: {c["name"] for c in inspect(src).get_columns(t)} for t in inspect(src).get_table_names()}
    tables = Base.metadata.sorted_tables  # 외래키 순서 (공고 → 검색 조건·이력)
    counts = {}
    with dst.begin() as d:
        if not replace and d.scalar(select(func.count()).select_from(JobPosting)):
            raise RuntimeError("대상 DB에 이미 공고가 있습니다. 덮어쓰려면 --replace를 붙이세요")
        for table in reversed(tables):
            d.execute(table.delete())
        with src.connect() as s:
            for table in tables:
                if table.name not in src_cols:
                    counts[table.name] = 0
                    continue
                cols = [c.name for c in table.columns if c.name in src_cols[table.name]]
                n = 0
                result = _rows(s, table, cols)
                while batch := result.fetchmany(BATCH):
                    d.execute(table.insert(), [dict(zip(cols, r)) for r in batch])
                    n += len(batch)
                counts[table.name] = n
                log.info("%s: %d행 복사", table.name, n)
        if dst.dialect.name == "postgresql":
            for table in tables:
                if "id" in table.c and table.c.id.autoincrement is not False and table.c.id.primary_key:
                    d.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table.name}), 1), "
                        f"(SELECT MAX(id) FROM {table.name}) IS NOT NULL)"
                    ))
    return counts


def verify(src: Engine, dst: Engine) -> list[str]:
    """표마다 모든 행(원본에 있는 열)을 비교해 다른 점을 돌려준다. 비어 있으면 같다."""
    src_cols = {t: {c["name"] for c in inspect(src).get_columns(t)} for t in inspect(src).get_table_names()}
    problems = []
    with src.connect() as s, dst.connect() as d:
        for table in Base.metadata.sorted_tables:
            if table.name not in src_cols:
                continue
            cols = [c.name for c in table.columns if c.name in src_cols[table.name]]
            a, b = _rows(s, table, cols).all(), _rows(d, table, cols).all()
            if len(a) != len(b):
                problems.append(f"{table.name}: 행 수가 다름 (원본 {len(a)}, 대상 {len(b)})")
                continue
            diff = sum(1 for x, y in zip(a, b) if tuple(x) != tuple(y))
            if diff:
                problems.append(f"{table.name}: {diff}행의 값이 다름")
    return problems

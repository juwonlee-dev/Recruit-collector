"""설정 파일 읽기.

- config.yaml: DB·스케줄·HTTP 등 공통 설정과 아래 파일들의 위치
- 검색 필터 파일(config/searches/<사이트>.yaml): 사이트(크롤러 모듈)별 검색 조건
- 키워드 파일(config/keywords.yaml): 필터 생성에 쓰는 키워드 (recruit_collector.filters)

검색 필터 파일은 수집을 시작할 때마다 새로 읽어 메모리에 올린다. 수집이 시작된 뒤에
파일을 바꿔도 진행 중인 수집은 이미 읽어 둔 조건으로 끝까지 진행된다.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from recruit_collector.crawlers import CRAWLERS
from recruit_collector.crawlers.base import Condition
from recruit_collector.http import HttpSettings


@dataclass
class SearchConfig:
    name: str  # 검색 조건 식별자. 삭제 판정 단위이므로 바꾸면 기존 이력과 연결이 끊긴다.
    site: str
    condition: Condition
    max_pages: int


@dataclass
class AuthSettings:
    """구글 로그인. 로그인 사용 여부는 설정 화면에서 켜고 끈다 (DB에 저장)."""

    google_client_id: str = ""
    google_client_secret: str = ""
    admin_emails: tuple[str, ...] = ()  # 항상 관리자인 구글 이메일. 다른 사용자의 등급은 설정 화면에서 정한다

    @property
    def google_ready(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


@dataclass
class AppConfig:
    database_url: str
    cron: str
    timezone: str
    missing_threshold: int
    http: HttpSettings
    searches_dir: Path  # 사이트별 검색 필터 파일 폴더 (<사이트>.yaml)
    keywords_file: Path
    catalog_dir: Path  # 필터 생성용 매핑 데이터 (사이트별 직무·지역 선택지)
    regions_file: Path  # 필터 생성에 쓰는 지역 (설정 화면에서 등록)
    region_codes_file: Path  # 공통 지역코드
    auth: AuthSettings = field(default_factory=AuthSettings)

    def site_file(self, site: str) -> Path:
        return self.searches_dir / f"{site}.yaml"

    def sites(self) -> list[str]:
        """검색 필터 파일이 있는 사이트 (크롤러 등록 순서, 등록되지 않은 사이트는 뒤에)."""
        stems = {p.stem for p in self.searches_dir.glob("*.yaml")}
        order = list(CRAWLERS)
        return sorted(stems, key=lambda s: (order.index(s) if s in order else len(order), s))

    def load_searches(self, site: str | None = None) -> list[SearchConfig]:
        """검색 필터 파일을 지금 읽어 검색 조건 목록을 만든다. site를 주면 그 사이트 파일만 읽는다."""
        sites = [site] if site else self.sites()
        searches = [s for name in sites for s in load_site_file(self.site_file(name), name)]
        names = [s.name for s in searches]
        if len(names) != len(set(names)):
            raise ValueError("검색 조건 이름(name)이 중복됨")
        return searches


def read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class _PlainDumper(yaml.SafeDumper):
    """같은 객체가 여러 번 나와도 &id001 같은 참조 대신 값을 그대로 쓴다 (사람이 읽고 고치는 파일이므로)."""

    def ignore_aliases(self, data) -> bool:
        return True


def write_yaml(path: Path, data: dict, header: str = "") -> None:
    """같은 폴더의 임시 파일에 쓴 뒤 바꿔치기한다. 읽는 쪽이 반쯤 쓰인 파일을 보지 않도록."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = header + yaml.dump(data, Dumper=_PlainDumper, allow_unicode=True, sort_keys=False, width=200)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_site_file(path: Path, site: str) -> list[SearchConfig]:
    """사이트 검색 필터 파일 하나를 읽는다. 사이트는 파일 이름으로 정해진다."""
    crawler_cls = CRAWLERS.get(site)
    if crawler_cls is None:
        raise ValueError(f"지원하지 않는 사이트: {site} (지원: {sorted(CRAWLERS)})")
    if not path.exists():
        raise KeyError(site)
    searches = []
    for s in read_yaml(path).get("searches") or []:
        searches.append(
            SearchConfig(
                name=s["name"],
                site=site,
                condition=crawler_cls.condition_class.from_dict(s.get("condition") or {}),
                max_pages=s.get("max_pages", 20),
            )
        )
    return searches


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    data = read_yaml(path)
    if "searches" in data:
        raise ValueError(
            "config.yaml의 searches는 사이트별 파일(config/searches/<사이트>.yaml)로 옮겨졌습니다. "
            "config.example.yaml을 참고해 searches 항목을 지워 주세요."
        )

    base = path.parent  # 파일 위치는 config.yaml 기준 상대 경로
    paths = data.get("paths") or {}
    http = data.get("http") or {}
    auth = data.get("auth") or {}
    schedule = data.get("schedule") or {}
    return AppConfig(
        # 환경 변수 DATABASE_URL이 있으면 그것을 쓴다 (도커: docker-compose.yml에서 PostgreSQL 주소를 넘김)
        database_url=os.environ.get("DATABASE_URL") or data.get("database_url", "sqlite:///data/recruit.db"),
        cron=schedule.get("cron", "0 * * * *"),
        timezone=schedule.get("timezone", "Asia/Seoul"),
        missing_threshold=(data.get("sync") or {}).get("missing_threshold", 2),
        http=HttpSettings(
            user_agent=http["user_agent"],
            delay_seconds=tuple(http.get("delay_seconds", (2.0, 4.0))),
            timeout=http.get("timeout", 20.0),
            max_retries=http.get("max_retries", 3),
            respect_robots=http.get("respect_robots", True),
        ),
        searches_dir=base / paths.get("searches_dir", "config/searches"),
        keywords_file=base / paths.get("keywords_file", "config/keywords.yaml"),
        catalog_dir=base / paths.get("catalog_dir", "config/catalog"),
        regions_file=base / paths.get("regions_file", "config/regions.yaml"),
        region_codes_file=base / paths.get("region_codes_file", "config/region_codes.yaml"),
        auth=AuthSettings(
            google_client_id=str(auth.get("google_client_id") or "").strip(),
            google_client_secret=str(auth.get("google_client_secret") or "").strip(),
            admin_emails=tuple(str(e).strip().lower() for e in auth.get("admin_emails") or [] if str(e).strip()),
        ),
    )

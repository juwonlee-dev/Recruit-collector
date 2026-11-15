from pathlib import Path

REGION_CODES = Path(__file__).resolve().parent.parent / "config" / "region_codes.yaml"

import yaml

from recruit_collector.config import AppConfig
from recruit_collector.http import HttpSettings


def make_config(
    root: Path, sites: dict[str, list[dict]], keywords: list[str] | None = None, regions: list[str] | None = None
) -> AppConfig:
    """사이트별 검색 필터 파일과 키워드 파일을 root 아래에 만들고 그 위치를 가리키는 설정을 돌려준다."""
    searches_dir = root / "searches"
    searches_dir.mkdir(parents=True, exist_ok=True)
    for site, searches in sites.items():
        (searches_dir / f"{site}.yaml").write_text(
            yaml.safe_dump({"searches": searches}, allow_unicode=True), encoding="utf-8"
        )
    keywords_file = root / "keywords.yaml"
    if keywords is not None:
        keywords_file.write_text(yaml.safe_dump({"keywords": keywords}, allow_unicode=True), encoding="utf-8")
    regions_file = root / "regions.yaml"
    if regions is not None:
        regions_file.write_text(yaml.safe_dump({"regions": regions}, allow_unicode=True), encoding="utf-8")
    return AppConfig(
        database_url="", cron="0 * * * *", timezone="Asia/Seoul", missing_threshold=1,
        http=HttpSettings(user_agent="test", delay_seconds=(0, 0), respect_robots=False),
        searches_dir=searches_dir, keywords_file=keywords_file, catalog_dir=root / "catalog",
        regions_file=regions_file, region_codes_file=REGION_CODES,
    )

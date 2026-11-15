from recruit_collector.crawlers.base import Crawler
from recruit_collector.crawlers.jobkorea import JobkoreaCrawler
from recruit_collector.crawlers.jumpit import JumpitCrawler
from recruit_collector.crawlers.remember import RememberCrawler
from recruit_collector.crawlers.saramin import SaraminCrawler, SaraminHeadhuntingCrawler
from recruit_collector.crawlers.wanted import WantedCrawler

# 사이트 이름 → 크롤러 클래스. 새 사이트는 여기에 등록한다.
CRAWLERS: dict[str, type[Crawler]] = {
    SaraminCrawler.site: SaraminCrawler,
    SaraminHeadhuntingCrawler.site: SaraminHeadhuntingCrawler,
    JobkoreaCrawler.site: JobkoreaCrawler,
    WantedCrawler.site: WantedCrawler,
    RememberCrawler.site: RememberCrawler,
    JumpitCrawler.site: JumpitCrawler,
}


def platform_of(site: str) -> str:
    """크롤러 이름 → 공고를 저장하는 플랫폼 이름 (예: saramin_hh → saramin). 모르는 이름은 그대로."""
    cls = CRAWLERS.get(site)
    return (cls.platform or cls.site) if cls else site

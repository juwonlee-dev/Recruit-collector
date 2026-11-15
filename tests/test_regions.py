import json
from dataclasses import asdict, dataclass, field

import pytest
import yaml
from fastapi.testclient import TestClient

from recruit_collector import filters, region_names, regions
from recruit_collector.crawlers import jobkorea, remember, saramin, wanted
from recruit_collector.crawlers.base import Condition, Crawler, JobOption, Page, ParseError, RegionOption
from recruit_collector.models import make_session_factory
from recruit_collector.web.app import create_app
from tests.conftest import make_config


# ---------------------------------------------------------------- 사이트별 지역 선택지 파싱

def test_parse_saramin_region_options():
    area = {
        "101000": [
            {"mcode": "101000", "code": "101000", "name": "서울전체", "id": "loc_mcd", "type": "all"},
            {"mcode": "101000", "code": "101010", "name": "강남구", "id": "loc_cd"},
        ],
        "102000": [
            {"mcode": "102000", "code": "102000", "name": "경기전체", "id": "loc_mcd", "type": "all"},
            {"mcode": "102000", "code": "102320", "name": "안양시", "id": "loc_cd"},
            {"mcode": "102000", "code": "102330", "name": "안양시 동안구", "id": "loc_cd"},
            {"mcode": "102000", "code": "102320", "name": "안양시", "id": "loc_cd"},  # 중복은 한 번만
        ],
        "211200": [{"code": "211210", "name": "x", "id": "loc_bcd"}],  # 시도 전체가 없는 묶음은 건너뜀
    }
    html = '<script>var p = {"count":{"1":2},"area":' + json.dumps(area, separators=(",", ":")) + ',"x":1};</script>'  # 실제 페이지처럼 \\u 이스케이프
    opts = saramin.parse_region_options(html)
    assert [(o.value, o.sido, o.name, o.field) for o in opts] == [
        ("101000", "서울", "", "locations"), ("101010", "서울", "강남구", "districts"),
        ("102000", "경기", "", "locations"), ("102320", "경기", "안양시", "districts"),
        ("102330", "경기", "안양시 동안구", "districts"),
    ]
    with pytest.raises(ParseError):
        saramin.parse_region_options("<html></html>")


def test_saramin_districts_go_to_loc_cd():
    c = saramin.SaraminCondition.from_dict({"locations": ["101000"], "districts": ["102320", "102330"]})
    params = c.to_params(1)
    assert params["loc_mcd"] == "101000" and params["loc_cd"] == "102320,102330"


def test_parse_jobkorea_region_options():
    area = [
        {"code": "I000", "parentCode": "", "displayName": "서울"},
        {"code": "I010", "parentCode": "I000", "displayName": "강남구"},
        {"code": "I000_", "parentCode": "I000", "displayName": "전지역"},
        {"code": "B000", "parentCode": "", "displayName": "경기"},
        {"code": "B240", "parentCode": "B000", "displayName": "안양시 동안구"},
    ]
    state = {"mutations": [], "queries": [{"queryKey": ["CODES"], "state": {"data": {"area": area}}}]}
    chunk = json.dumps(json.dumps(state, ensure_ascii=False, separators=(",", ":")), ensure_ascii=False)[1:-1]  # push 안의 문자열 이스케이프
    html = f'<script>self.__next_f.push([1,"{chunk}"])</script>'
    opts = jobkorea.parse_region_options(html)
    assert [(o.value, o.sido, o.name) for o in opts] == [
        ("I000", "서울", ""), ("B000", "경기", ""), ("I010", "서울", "강남구"), ("B240", "경기", "안양시 동안구"),
    ]


def test_parse_wanted_region_options():
    data = {"props": {"pageProps": {"filterData": {"countries": [
        {"key": "jp", "locations": [{"key": "tokyo", "display": "도쿄", "districts": [{"key": "tokyo.all", "display": "전체"}]}]},
        {"key": "kr", "locations": [
            {"key": "all", "display": "전국", "districts": []},
            {"key": "gyeonggi", "display": "경기", "districts": [
                {"key": "gyeonggi.all", "display": "전체"}, {"key": "gyeonggi.anyang-si", "display": "안양시"},
            ]},
        ]},
    ]}}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script>'
    opts = wanted.parse_region_options(html)
    assert [(o.value, o.sido, o.name) for o in opts] == [("gyeonggi.all", "경기", ""), ("gyeonggi.anyang-si", "경기", "안양시")]


def test_parse_remember_region_options():
    data = {"data": {"addresses": [
        {"name": "경기도", "subs": [{"name": "안양시 동안구"}]},
        {"name": "세종특별자치시", "subs": [{"name": "세종특별자치시"}]},
    ]}}
    opts = remember.parse_region_options(data)
    assert [o.value for o in opts] == [["경기도"], ["경기도", "안양시 동안구"], ["세종특별자치시"]]


# ---------------------------------------------------------------- 매핑

def opts(*items):
    return [asdict(RegionOption(v, sido, name, f)) for v, sido, name, f in items]


SARAMIN = opts(
    ("101000", "서울", "", "locations"), ("101010", "서울", "강남구", "districts"),
    ("102000", "경기", "", "locations"), ("102320", "경기", "안양시", "districts"),
    ("102330", "경기", "안양시 동안구", "districts"), ("102340", "경기", "안양시 만안구", "districts"),
    ("112000", "전남광주", "", "locations"), ("117000", "전국", "", "locations"),
)
REMEMBER = opts(
    (["서울특별시"], "서울특별시", "", "addresses"), (["경기도"], "경기도", "", "addresses"),
    (["경기도", "안양시 동안구"], "경기도", "안양시 동안구", "addresses"),
    (["경기도", "안양시 만안구"], "경기도", "안양시 만안구", "addresses"),
    (["광주광역시"], "광주광역시", "", "addresses"), (["전라남도"], "전라남도", "", "addresses"),
    (["전라남도", "순천시"], "전라남도", "순천시", "addresses"),
)


def test_match_regions_by_sido_and_city():
    matches, unmatched = regions.match_regions(["서울", "경기 안양시", "경기 과천시"], SARAMIN)
    # 시 이름과 같은 선택지(안양시)가 있으면 그것만, 시도 전체는 시도 필드로
    assert [(m.value, m.field, m.name) for m in matches] == [
        ("101000", "locations", "서울 전체"), ("102320", "districts", "경기 안양시"),
    ]
    assert unmatched == ["경기 과천시"]

    # 시 단위 선택지가 없으면 그 시의 구를 모두. 시도 이름이 달라도(경기도) 같은 시도
    matches, _ = regions.match_regions(["경기 안양시", "경기"], REMEMBER)
    assert [m.value for m in matches] == [["경기도", "안양시 동안구"], ["경기도", "안양시 만안구"], ["경기도"]]


def test_match_regions_merged_sido():
    # 전남광주: 사람인은 한 코드, 리멤버는 광주·전남 두 곳
    assert [m.value for m in regions.match_regions(["전남광주"], SARAMIN)[0]] == ["112000"]
    assert [m.value for m in regions.match_regions(["전남광주"], REMEMBER)[0]] == [["광주광역시"], ["전라남도"]]
    assert [m.value for m in regions.match_regions(["전남광주 순천시"], REMEMBER)[0]] == [["전라남도", "순천시"]]


def test_match_regions_old_names():
    old = opts((["인천광역시", "중구"], "인천광역시", "중구", "addresses"), (["인천광역시", "동구"], "인천광역시", "동구", "addresses"),
               (["경상북도", "군위군"], "경상북도", "군위군", "addresses"))
    new = opts(("I230", "인천", "제물포구", "locations"), ("F100", "대구", "군위군", "locations"))
    # 새 이름이 있으면 그것을, 옛 이름만 있으면 옛 구역으로
    assert [m.value for m in regions.match_regions(["인천 제물포구", "대구 군위군"], new)[0]] == ["I230", "F100"]
    assert [m.value for m in regions.match_regions(["인천 제물포구", "대구 군위군"], old)[0]] == [
        ["인천광역시", "중구"], ["인천광역시", "동구"], ["경상북도", "군위군"],
    ]


def test_region_codes_file_is_valid(tmp_path):
    config = make_config(tmp_path, {})
    codes = regions.load_codes(config)
    assert list(codes)[:2] == ["서울", "경기"] and "안양시" in codes["경기"] and codes["세종"] == []
    assert all(sido in region_names.SIDO_KEYS.values() for sido in codes)  # 모든 시도가 사이트 이름과 매핑됨


def test_regions_add_remove(tmp_path):
    config = make_config(tmp_path, {})
    assert regions.add_region(config, "경기", "안양시") == ("경기 안양시", [])
    assert regions.add_region(config, "서울") == ("서울", [])
    with pytest.raises(ValueError, match="이미 등록"):
        regions.add_region(config, "서울", "")
    with pytest.raises(ValueError, match="없는 시도"):
        regions.add_region(config, "서울특별시")
    with pytest.raises(ValueError, match="없는 시군구"):
        regions.add_region(config, "서울", "안양시")
    with pytest.raises(ValueError, match="포함"):
        regions.add_region(config, "서울", "강남구")  # 서울 전체에 포함
    # 시도 전체를 등록하면 그 시도의 시군구는 지운다
    assert regions.add_region(config, "경기") == ("경기", ["경기 안양시"])
    assert regions.load_regions(config) == ["서울", "경기"]
    assert regions.remove_region(config, "서울") == ["경기"]
    with pytest.raises(ValueError):
        regions.remove_region(config, "서울")


# ---------------------------------------------------------------- 필터 생성

@dataclass
class FakeCond(Condition):
    jobs: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    districts: list[str] = field(default_factory=list)


class FakeSite(Crawler):
    """사람인처럼 시도·시군구가 다른 필드인 사이트"""

    site = "site_r"
    condition_class = FakeCond
    job_filter_field = "jobs"
    region_filter_fields = ("locations", "districts")
    region_fetched = 0

    def fetch_page(self, condition, page):
        return Page([], is_last=True)

    def fetch_job_options(self):
        return [JobOption("84", "백엔드/서버개발")]

    def fetch_region_options(self):
        type(self).region_fetched += 1
        return [RegionOption(**o) for o in SARAMIN]


@pytest.fixture
def region_site(tmp_path, monkeypatch):
    FakeSite.region_fetched = 0
    monkeypatch.setitem(filters.CRAWLERS, "site_r", FakeSite)
    monkeypatch.setitem(regions.CRAWLERS, "site_r", FakeSite)
    config = make_config(tmp_path, {
        "site_r": [{"name": "r-dev", "condition": {"jobs": ["keep"], "locations": ["101000", "102000"]}}],
    }, keywords=[], regions=["서울", "경기 안양시"])
    return config


def test_generate_writes_region_fields(region_site):
    config = region_site
    [r] = filters.generate(config, [], regions=regions.load_regions(config))
    assert r.status == "updated" and r.matches == [] and r.region_unmatched == []
    data = yaml.safe_load(config.site_file("site_r").read_text(encoding="utf-8"))
    # 키워드가 없으면 직무는 그대로, 지역은 시도·시군구 필드로 나눠 반영
    assert data["searches"][0]["condition"] == {"jobs": ["keep"], "locations": ["101000"], "districts": ["102320"]}
    assert data["generated"]["regions"] == ["서울", "경기 안양시"]
    assert data["generated"]["region_matches"][1] == {"value": "102320", "field": "districts", "name": "경기 안양시", "regions": ["경기 안양시"]}

    # 같은 지역이면 변경 없음, 지역 선택지는 저장해 두고 다시 씀
    assert filters.generate(config, [], regions=["경기 안양시", "서울"])[0].status == "unchanged"
    assert FakeSite.region_fetched == 1

    # 시군구만 남기면 시도 필드는 비운다. 맞는 지역이 없으면 지역 필드를 바꾸지 않는다
    filters.generate(config, ["서버 개발"], regions=["경기 안양시"])
    cond = yaml.safe_load(config.site_file("site_r").read_text(encoding="utf-8"))["searches"][0]["condition"]
    assert cond == {"jobs": ["84"], "districts": ["102320"]}
    [r] = filters.generate(config, [], regions=["경기 과천시"])
    assert r.status == "no_match" and r.region_unmatched == ["경기 과천시"]
    assert config.load_searches("site_r")[0].condition.districts == ["102320"]


def test_settings_region_forms_and_generate(region_site, tmp_path):
    config = region_site
    client = TestClient(create_app(make_session_factory(f"sqlite:///{tmp_path / 's.db'}"), config))

    page = client.get("/settings").text
    assert "<h2>지역</h2>" in page and "서울 전체" in page and "경기 안양시" in page
    assert page.index("<h2>키워드</h2>") < page.index("<h2>지역</h2>") < page.index("<h2>검색 필터</h2>")
    assert 'id="region-codes"' in page and '<option value="안양시">' not in page  # 처음 시도(서울)의 시군구만

    res = client.post("/settings/regions", data={"sido": "경기", "city": "과천시"}, follow_redirects=False)
    assert res.status_code == 303 and "err" not in res.headers["location"]
    res = client.post("/settings/regions", data={"sido": "경기", "city": "없는시"}, follow_redirects=False)
    assert "err=1" in res.headers["location"]
    client.post("/settings/regions/delete", data={"region": "경기 과천시"})
    assert regions.load_regions(config) == ["서울", "경기 안양시"]

    # 키워드가 없어도 지역만으로 필터 생성
    page = client.post("/settings/generate", data={}).text
    assert "필터를 생성했습니다" in page and "반영됨" in page
    assert "<b>경기 안양시</b>" in page  # 생성 결과의 지역 매핑
    assert "서울 전체 (101000)" in page and "경기 안양시 (102320)" in page  # 검색 필터 카드의 지역 이름


# ---------------------------------------------------------------- 공고 지역 → 공통 지역코드 (공고 검색)

@pytest.mark.parametrize("location, codes", [
    ("경기 안양시 동안구", ["경기", "경기 안양시"]),
    ("서울", ["서울", "서울 전체"]),  # 시도만 있으면 시도 전체
    ("서울 전체 외 1", ["서울", "서울 전체"]),  # 리멤버
    ("경기 성남시 외 2", ["경기", "경기 성남시"]),
    # 사람인: 시도 없이 이어지는 시군구는 앞의 시도
    ("서울전체 , 강남구 , 경기 광명시 , 광주시 , 안양시 동안구",
     ["서울", "서울 전체", "서울 강남구", "경기", "경기 광명시", "경기 광주시", "경기 안양시"]),
    ("서울전체 , 경기 경기전체 , 안양시", ["서울", "서울 전체", "경기", "경기 전체", "경기 안양시"]),
    ("강남구 , 강동구 , 경기 경기전체", ["경기", "경기 전체"]),  # 시도를 알 수 없는 앞부분은 버림
    # 잡코리아: 쉼표로 나뉜 '시도 시군구'
    ("서울 성동구, 대전, 경기 안양시 만안구", ["서울", "서울 성동구", "대전", "대전 전체", "경기", "경기 안양시"]),
    ("전국", ["전국"]),
    ("광주 광산구", ["전남광주", "전남광주 광산구"]),  # 원티드·리멤버의 광주
    ("인천 중구", ["인천", "인천 중구", "인천 제물포구", "인천 영종구"]),  # 옛 이름은 새 코드로도
    ("", []), (None, []), ("해외 미국", []),
])
def test_location_codes(location, codes):
    assert region_names.location_codes(location) == codes


def test_postings_search_by_sido_and_city(tmp_path):
    from recruit_collector.crawlers.base import CrawlResult, Posting
    from recruit_collector.sync import sync_search

    factory = make_session_factory(f"sqlite:///{tmp_path / 'p.db'}")
    items = [
        Posting(external_id=str(i), title=t, company="c", detail_url=f"https://e/{i}", location=loc)
        for i, (t, loc) in enumerate([
            ("안양공고", "경기 안양시 동안구"),
            ("사람인여러곳", "서울전체 , 경기 광명시 , 안양시"),
            ("성남공고", "경기 성남시 분당구"),
            ("강남공고", "서울 강남구"),
            ("경기전체공고", "경기 경기전체"),
            ("전국공고", "전국"),
        ])
    ]
    with factory() as s:
        sync_search(s, site="saramin", search_name="x", result=CrawlResult(items, 1, True), missing_threshold=1)
        s.commit()
    config = make_config(tmp_path, {})
    client = TestClient(create_app(factory, config))

    every = {"안양공고", "사람인여러곳", "성남공고", "강남공고", "경기전체공고", "전국공고"}

    def titles(**params):
        html = client.get("/", params=params).text
        return {t for t in every if t in html}

    assert titles(location="경기") == {"안양공고", "사람인여러곳", "성남공고", "경기전체공고", "전국공고"}
    # 시군구로 찾으면 그 시군구 공고와 시도 전체·전국 공고
    assert titles(location="경기", city="안양시") == {"안양공고", "사람인여러곳", "경기전체공고", "전국공고"}
    assert titles(location="서울", city="강남구") == {"사람인여러곳", "강남공고", "전국공고"}  # 사람인여러곳 = 서울전체
    assert titles(location="서울", city="마포구") == {"사람인여러곳", "전국공고"}
    assert titles(location="서울") == {"사람인여러곳", "강남공고", "전국공고"}
    assert titles(location="서울", city="안양시") == titles(location="서울")  # 그 시도에 없는 시군구는 무시
    assert titles(location="없는곳") == every

    # 여러 지역: 고른 지역 중 하나라도 맞으면 (합집합)
    assert titles(region=["서울 강남구", "경기 성남시"]) == {"사람인여러곳", "강남공고", "성남공고", "경기전체공고", "전국공고"}
    assert titles(region=["서울", "경기"]) == every
    assert titles(region=["경기 안양시", "경기 성남시"]) == {"안양공고", "사람인여러곳", "성남공고", "경기전체공고", "전국공고"}
    assert titles(region=["서울", "없는곳"]) == titles(region="서울")
    assert titles(region="서울 안양시") == titles(region="서울")  # 그 시도에 없는 시군구는 시도 전체로
    assert titles(region=["없는곳"]) == every

    page = client.get("/", params={"location": "경기", "city": "안양시"}).text  # 예전 주소
    assert 'value="경기 안양시" data-label="경기 안양시" checked' in page
    assert '<span class="ms-text">경기 안양시</span>' in page
    assert 'href="/?page=2' not in page
    # 시도 전체를 고르면 그 시도의 시군구는 빠지고(체크 해제·비활성), 3곳 이상은 '첫 지역 외 N곳'
    page = client.get("/", params={"region": ["경기 안양시", "서울 강남구", "경기", "부산"]}).text
    assert 'value="경기" data-label="경기 전체" data-whole checked' in page
    assert 'value="경기 안양시" data-label="경기 안양시"  disabled' in page
    assert '<span class="ms-text">서울 강남구 외 2곳</span>' in page  # 공통 지역코드 순서: 서울 강남구, 경기 전체, 부산 전체
    assert 'aria-pressed="true">서울<' in page  # 처음 펼치면 고른 첫 지역의 시도


def test_pick_regions():
    from recruit_collector.web.app import pick_regions

    codes = {"서울": ["강남구", "강서구"], "경기": ["안양시"]}
    assert pick_regions(["경기 안양시", "서울 강서구", "서울 강남구", "서울 강남구"], codes) == ["서울 강남구", "서울 강서구", "경기 안양시"]
    assert pick_regions(["서울 강남구", "서울", "경기"], codes) == ["서울", "경기"]
    assert pick_regions(["부산", "서울 없는구", ""], codes) == ["서울"]
    assert pick_regions(["부산 해운대구"], {}) == ["부산 해운대구"]  # 지역코드가 없으면 그대로


def test_existing_postings_get_region_codes(tmp_path):
    """지역코드 컬럼이 생기기 전에 수집한 공고는 DB를 열 때 채운다."""
    from sqlalchemy import text

    from recruit_collector.crawlers.base import CrawlResult, Posting
    from recruit_collector.models import JobPosting
    from recruit_collector.sync import sync_search

    url = f"sqlite:///{tmp_path / 'old.db'}"
    factory = make_session_factory(url)
    with factory() as s:
        p = Posting(external_id="1", title="t", company="c", detail_url="https://e/1", location="경기 안양시 만안구")
        sync_search(s, site="saramin", search_name="x", result=CrawlResult([p], 1, True), missing_threshold=1)
        s.commit()
        assert s.scalar(text("SELECT regions FROM job_postings")) == "|경기|경기 안양시|"
        s.execute(text("UPDATE job_postings SET regions = NULL"))
        s.commit()
    with make_session_factory(url)() as s:
        assert s.query(JobPosting).one().regions == "|경기|경기 안양시|"
        # 계산 방식이 바뀌면(버전이 다르면) 채워진 공고도 다시 계산한다
        s.execute(text("UPDATE job_postings SET regions = '|옛값|'"))
        s.commit()
    with make_session_factory(url)() as s:
        assert s.query(JobPosting).one().regions == "|옛값|"  # 같은 버전이면 그대로
        s.execute(text("UPDATE app_settings SET value = '0' WHERE key = 'regions_codes_version'"))
        s.commit()
    with make_session_factory(url)() as s:
        assert s.query(JobPosting).one().regions == "|경기|경기 안양시|"

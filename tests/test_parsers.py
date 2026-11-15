from pathlib import Path

import pytest

from recruit_collector import career as career_mod
from recruit_collector.crawlers import jobkorea, saramin
from recruit_collector.crawlers.base import ParseError

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_saramin_list():
    page = saramin.parse_list_page(read("saramin_list.html"), page_size=100)

    assert len(page.postings) == 5
    assert page.is_last  # 5건 < page_size
    p = page.postings[1]
    assert p.external_id == "55455428"
    assert p.title == "Backend (Python) 개발자"
    assert p.company == "씨메스로보틱스(주)"
    assert p.detail_url == "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=55455428"
    assert (p.location, p.career, p.education, p.employment_type) == ("서울 강남구", "경력 3~10년", "대졸↑", "정규직·계약직")
    assert p.deadline == "~ 12/01(화)"


def test_saramin_full_page_is_not_last():
    page = saramin.parse_list_page(read("saramin_list.html"), page_size=5)
    assert not page.is_last


def test_saramin_empty_result():
    page = saramin.parse_list_page(read("saramin_empty.html"), page_size=100)
    assert page.postings == [] and page.is_last


def test_saramin_unknown_structure_raises():
    with pytest.raises(ParseError):
        saramin.parse_list_page("<html><body>점검 중</body></html>", page_size=100)


def test_jobkorea_list():
    page = jobkorea.parse_list_page(read("jobkorea_list.html"))

    assert [p.external_id for p in page.postings] == ["50309869", "50242499", "49970060"]
    assert not page.is_last  # pageNumber 0 / totalPages 123
    p = page.postings[0]
    assert p.title == "2026년 하반기 고영테크놀러지 신입사원 채용"
    assert p.company == "㈜고영테크놀러지"
    assert p.detail_url == "https://www.jobkorea.co.kr/Recruit/GI_Read/50309869"
    assert p.location == "경기 용인시 수지구, 경기 수원시 영통구, 경기 여주시"
    assert p.career == "신입"
    assert p.employment_type == "정규직"
    assert p.deadline == "2026-11-18"
    assert p.raw["readCount"] == 1109
    assert not any(k.startswith("_internal") for k in p.raw)
    assert page.postings[1].career == "신입·경력"
    assert p.headhunting is False  # userRefType C


def test_jobkorea_search_firm_is_headhunting():
    item = {"id": "1", "title": "t", "postingCompanyName": "서치펌", "userRefType": "S"}
    assert jobkorea._to_posting(item, {}, {}).headhunting is True
    assert jobkorea._to_posting({**item, "userRefType": "C"}, {}, {}).headhunting is False


def test_jobkorea_unknown_structure_raises():
    with pytest.raises(ParseError):
        jobkorea.parse_list_page("<html><body>점검 중</body></html>")


def test_condition_rejects_unknown_keys():
    with pytest.raises(ValueError, match="keywrod"):
        saramin.SaraminCondition.from_dict({"keywrod": "python"})


def test_saramin_condition_params():
    cond = saramin.SaraminCondition.from_dict({"keyword": "python", "locations": ["101000", "102000"], "careers": ["1"]})
    params = cond.to_params(3)
    assert params["searchword"] == "python"
    assert params["loc_mcd"] == "101000,102000"
    assert params["exp_cd"] == "1"
    assert params["recruitPage"] == "3"
    assert "job_type" not in params


def test_saramin_job_keywords_are_joined_for_or_search():
    cond = saramin.SaraminCondition.from_dict({"locations": ["101000"], "job_keywords": ["84", "87", "235"]})
    params = cond.to_params(1)
    assert params["cat_kewd"] == "84,87,235"
    assert params["searchword"] == ""


def test_jobkorea_locations_are_sent_as_cookie():
    cond = jobkorea.JobkoreaCondition.from_dict({"locations": ["I000", "B000"], "duties": ["1000229", "1000231"]})
    assert cond.to_headers() == {"Cookie": "searchBarLocal=I000,B000"}
    assert cond.to_params(1)["duty"] == "1000229,1000231"


def test_saramin_dates():
    from datetime import date, datetime

    today = date(2026, 11, 5)
    assert saramin.parse_deadline("~ 11/15(일)", today) == date(2026, 11, 15)
    assert saramin.parse_deadline("~ 01/10(토)", today) == date(2027, 1, 10)  # 연말에 내년 마감
    assert saramin.parse_deadline("오늘마감", today) == today
    assert saramin.parse_deadline("23시마감", today) == today
    assert saramin.parse_deadline("진행예정", today) is None
    assert saramin.parse_deadline("내일마감", today) == date(2026, 11, 6)
    assert saramin.parse_deadline("채용시", today) is None
    assert saramin.parse_deadline("상시채용", today) is None
    assert saramin.parse_registered("등록일 26/11/04") == datetime(2026, 11, 3, 15)  # KST 0시 = UTC 전날 15시
    assert saramin.parse_registered("수정일 26/11/05") is None


def test_saramin_infers_posted_at_from_lower_id():
    from datetime import datetime

    d1, d2 = datetime(2026, 11, 1), datetime(2026, 11, 3)
    ps = [
        saramin.Posting("300", "t", "c", "u"),                 # 수정일만 → 바로 아래 ID(250)의 등록일
        saramin.Posting("250", "t", "c", "u", posted_at=d2),
        saramin.Posting("200", "t", "c", "u"),                 # → 100의 등록일
        saramin.Posting("100", "t", "c", "u", posted_at=d1),
        saramin.Posting("50", "t", "c", "u"),                  # 더 낮은 ID가 없음 → 추정 불가
    ]
    saramin.infer_posted_at(ps)
    assert [p.posted_at for p in ps] == [d2, d2, d1, d1, None]
    assert ps[0].raw == {"posted_at_inferred": True} and ps[1].raw == {}


def test_jobkorea_dates():
    from datetime import date, datetime

    p = jobkorea.parse_list_page(read("jobkorea_list.html")).postings[0]
    assert p.posted_at == datetime(2026, 11, 5, 2, 31, 43, 870000)  # 11:31:43.87 KST
    assert p.deadline_at == date(2026, 11, 18)


def test_jobkorea_far_future_deadline_means_always_open():
    item = {"id": "1", "title": "t", "applicationPeriod": {"start": "2026-09-01T00:00:00+09:00", "end": "2070-01-01T00:00:00+09:00"}}
    p = jobkorea._to_posting(item, {}, {})
    assert (p.deadline, p.deadline_at) == ("상시채용", None)


def test_wanted_list():
    import json

    from recruit_collector.crawlers import wanted

    page = wanted.parse_list(json.loads(read("wanted_list.json")))
    assert [p.external_id for p in page.postings] == ["396871", "373691", "396855"]
    assert not page.is_last  # links.next 있음
    p = page.postings[0]
    assert p.title == "Document AI Engineer (Backend)"
    assert p.company == "크라우드웍스"
    assert p.location == "서울 강남구"
    assert p.detail_url == "https://www.wanted.co.kr/wd/396871"
    assert (p.career, p.career_min, p.career_max, p.career_any) == ("경력1년↑", 1, None, False)  # annual_to=100 → 상한 없음
    assert page.postings[1].career == "경력 8~15년"
    assert p.employment_type == "정규직"


def test_wanted_postings_are_not_headhunting():
    import json

    from recruit_collector.crawlers import wanted

    page = wanted.parse_list(json.loads(read("wanted_list.json")))
    assert page.postings and all(p.headhunting is False for p in page.postings)  # 구분이 없어 모두 일반


def test_wanted_condition_sends_repeated_keys():
    from recruit_collector.crawlers import wanted

    params = wanted.WantedCondition(job_ids=[872, 873], locations=["seoul.all", "gyeonggi.all"]).to_params(3)
    assert params["job_ids"] == [872, 873] and params["locations"] == ["seoul.all", "gyeonggi.all"]
    assert (params["limit"], params["offset"]) == (20, 40)


def test_wanted_newbie_and_missing_career():
    from recruit_collector.crawlers import wanted

    assert wanted._career(0, 0, True) == career_mod.Career(0, 0)
    assert wanted._career(2, 5, True) == career_mod.Career(0, 5)  # 신입 지원 가능
    assert wanted._career(None, None, False) == career_mod.Career()


def test_remember_list():
    import json
    from datetime import date, datetime

    from recruit_collector.crawlers import remember

    page = remember.parse_list(json.loads(read("remember_list.json")))
    assert len(page.postings) == 3 and page.total and not page.is_last
    p = page.postings[0]
    assert p.external_id == "318037"
    assert p.detail_url == "https://career.rememberapp.co.kr/job/posting/318037"
    assert (p.career, p.career_min, p.career_max) == ("신입·경력 ~3년", 0, 3)  # min=None, max=3
    assert p.deadline_at == date(2026, 11, 30)
    assert p.posted_at == datetime(2026, 11, 4, 15, 0)  # 2026-11-05 00:00 KST
    assert "job_description" not in p.raw
    any_ = page.postings[1]
    assert (any_.career, any_.career_any) == ("경력무관", True)
    assert p.headhunting is False  # job_posting_type general
    hh = remember._to_posting({**json.loads(read("remember_list.json"))["data"][0], "job_posting_type": "internal_headhunter"})
    assert hh.headhunting is True


def test_remember_body_reuses_seed_and_snake_case():
    from recruit_collector.crawlers import remember

    cond = remember.RememberCondition(job_categories=[["SW개발", "백엔드"]], addresses=[["서울특별시"]])
    body = cond.to_body(2, seed=123)
    assert body["search"]["job_category_names"] == [{"level1": "SW개발", "level2": "백엔드"}]
    assert body["search"]["addresses"] == [["서울특별시"]]
    assert (body["page"], body["per"], body["seed"]) == (2, 30, 123)


def test_jumpit_list():
    import json
    from datetime import date

    from recruit_collector.crawlers import jumpit

    page = jumpit.parse_list(json.loads(read("jumpit_list.json")), page=1)
    assert [p.external_id for p in page.postings] == ["55566554", "55566568", "55563430"]
    assert page.total == 143 and page.is_last  # 3건 < 페이지당 16건
    p = page.postings[0]
    assert (p.title, p.company, p.location) == ("ADC 사이트 대응 개발 인력 채용", "파이오링크", "서울 금천구")
    assert p.detail_url == "https://jumpit.saramin.co.kr/position/55566554"
    assert (p.career, p.career_min, p.career_max) == ("경력 2~3년", 2, 3)
    assert (p.deadline, p.deadline_at) == ("2026-12-13", date(2026, 12, 13))
    assert page.postings[1].career == "신입"
    assert (page.postings[2].deadline, page.postings[2].deadline_at) == ("상시채용", None)
    assert all(p.headhunting is False for p in page.postings)


def test_jumpit_paging_uses_total_count():
    from recruit_collector.crawlers import jumpit

    full = {"result": {"totalCount": 40, "positions": [{"id": i, "title": "t"} for i in range(16)]}}
    assert not jumpit.parse_list(full, page=2).is_last  # 32 < 40
    assert jumpit.parse_list(full, page=3).is_last  # 48 >= 40
    # 전체 건수를 넘는 페이지는 totalCount=0, 빈 목록
    assert jumpit.parse_list({"result": {"totalCount": 0, "positions": []}}, page=4).is_last
    with pytest.raises(ParseError):
        jumpit.parse_list({"message": "error"}, page=1)


def test_jumpit_condition_sends_repeated_keys():
    from recruit_collector.crawlers import jumpit

    params = jumpit.JumpitCondition(job_categories=[1, 3], location_tags=[101000, 102000]).to_params(2)
    assert params["jobCategory"] == [1, 3] and params["locationTag"] == [101000, 102000]
    assert (params["sort"], params["page"]) == ("reg_dt", 2)


def test_jumpit_job_and_region_options():
    from recruit_collector.crawlers import jumpit
    from recruit_collector.crawlers.base import JobOption, RegionOption

    codes = {"result": {
        "jobCategory": [{"id": 1, "name": "서버/백엔드 개발자"}],
        "locationTag": [
            {"code": 117000, "name": "전국", "children": []},
            {"code": 101000, "name": "서울", "children": [{"code": 101000, "name": "서울전체"}, {"code": 101010, "name": "강남구"}]},
            {"code": 118000, "name": "세종", "children": [{"code": 118000, "name": "세종특별자치시"}]},
        ],
    }}
    assert jumpit.parse_job_options(codes) == [JobOption(1, "서버/백엔드 개발자", "개발")]
    assert jumpit.parse_region_options(codes) == [
        RegionOption(101000, "서울", "", "location_tags"),
        RegionOption(101010, "서울", "강남구", "location_tags"),
        RegionOption(118000, "세종", "", "location_tags"),
    ]

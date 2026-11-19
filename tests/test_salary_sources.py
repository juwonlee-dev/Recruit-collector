"""연봉·인원 자료: 올댓컴퍼니 → 국민연금 대체, 공시 기준 평균연봉 (외부 요청 없이 가짜 응답으로)."""

from datetime import date

import httpx
import pytest
from sqlalchemy import select

from recruit_collector import dart, nps, salary
from recruit_collector.models import AtcCompany, DartCorp, NpsMonth, make_session_factory
from recruit_collector.salary_sources import SalarySources, SourceError
from tests.conftest import make_config


# ------------------------------------------------------------------ 국민연금

def nps_row(ym, seq, name="크디랩 주식회사", bizno="710870****", addr="서울특별시 영등포구 의사당대로", sggu="560", status="1"):
    return {"bzowrRgstNo": bizno, "dataCrtYm": ym, "ldongAddrMgplDgCd": "11", "ldongAddrMgplSgguCd": sggu,
            "ldongAddrMgplSgguEmdCd": "110", "seq": seq, "wkplJnngStcd": status, "wkplNm": name, "wkplRoadNmDtlAddr": addr}


class FakeNps(nps.NpsClient):
    """_get만 바꿔 끼운 국민연금 클라이언트. calls에 요청을 남긴다."""

    def __init__(self, search_rows):
        super().__init__(key="test")
        self.search_rows = search_rows
        self.calls = []

    def _get(self, op, **params):
        self.calls.append((op, params))
        if op == "getBassInfoSearchV2":
            return self.search_rows, len(self.search_rows)
        seq = params["seq"]
        if op == "getDetailInfoSearchV2":
            return [{"jnngpCnt": 10, "crrmmNtcAmt": str(3_800_000 + seq % 100), "vldtVlKrnNm": "응용 소프트웨어 개발 및 공급업",
                     "wkplNm": "크디랩 주식회사"}], 1
        return [{"nwAcqzrCnt": 1, "lssJnngpCnt": 0}], 1


def test_contribution_rate_and_estimate():
    assert nps.contribution_rate("2025-12") == 0.09
    assert nps.contribution_rate("2026-01") == 0.095
    assert nps.contribution_rate("2040-01") == 0.13  # 2033년부터 13%로 고정
    assert nps.estimate_salary(3_815_060, 10, "2026-09") == 4_015_853
    assert nps.estimate_salary(0, 10, "2026-09") is None and nps.estimate_salary(100, 0, "2026-09") is None


def test_search_groups_months_across_moves(tmp_path):
    """이전해 주소(법정동)가 바뀌어도 같은 사업자번호·이름이면 한 사업장. 받은 달은 저장하고 다시 요청하지 않는다."""
    rows = [nps_row("202510", 1, sggu="110", addr="서울특별시 종로구 사직로"), nps_row("202511", 2, sggu="110"),
            nps_row("202609", 12), nps_row("202609", 99, name="크디랩스", bizno="123456****")]
    client = FakeNps(rows)
    places = client.search("크디랩(주)")
    assert [len(w.months) for w in places] == [3, 1]
    wp, match = nps.choose(places, "크디랩 주식회사", [("서울", "영등포구")])
    assert match == "name_address" and wp.latest_seq == 12 and wp.address == "서울특별시 영등포구 의사당대로"

    sf = make_session_factory(f"sqlite:///{tmp_path / 'n.db'}")
    with sf() as s:
        months, industry = client.months(s, wp)
    assert [m.ym for m in months] == ["2025-10", "2025-11", "2026-09"] and industry == "응용 소프트웨어 개발 및 공급업"
    assert months[-1].salary == nps.estimate_salary(3_800_012, 10, "2026-09") and months[0].hires == 1
    n_calls = len(client.calls)
    with sf() as s:
        client.months(s, wp)
        assert s.scalar(select(NpsMonth.workplace).where(NpsMonth.seq == 12)) == wp.key
    assert len(client.calls) == n_calls  # 저장해 둔 달은 다시 요청하지 않음
    client.search("크디랩")
    assert len(client.calls) == n_calls  # 같은 검색어는 잠시 기억


# ------------------------------------------------------------------ 올댓컴퍼니 → 국민연금

def gone_error(url="https://allthatcompany.com/c/1302598"):
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))


@pytest.fixture
def sources(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)
    sf = make_session_factory(f"sqlite:///{tmp_path / 's.db'}")
    src = SalarySources(make_config(tmp_path, {}), sf)
    src.nps = FakeNps([nps_row("202608", 11), nps_row("202609", 12)])
    monkeypatch.setattr("recruit_collector.salary_sources.HttpClient", lambda cfg: type("H", (), {"close": lambda self: None})())
    return src, sf


def test_posting_falls_back_when_company_page_is_gone(sources, monkeypatch):
    src, sf = sources
    cand = salary.Candidate("1302598", "크디랩 주식회사", "서울특별시 영등포구 의사당대로", True, True)
    monkeypatch.setattr(salary, "search", lambda http, company, regions: ([cand], cand, "name_address"))
    fetched = []

    def fetch(http, cid):
        fetched.append(cid)
        raise gone_error()

    monkeypatch.setattr(salary, "fetch_company", fetch)
    data = src.posting("크디랩(주)", "서울 영등포구", None)
    c = data["company"]
    # 대체 자료여도 ID는 올댓컴퍼니 ID: 연봉·인원 메뉴에서 담은 같은 회사와 비교함에 두 번 담기지 않는다
    assert c["id"] == "1302598" and c["source"]["key"] == "nps" and "삭제된 회사 페이지라" in c["fallback"]
    assert data["match"] == "name_address" and data["candidates"][0]["id"] == "1302598"  # 다른 후보도 고를 수 있다
    assert [m["ym"] for m in c["months"]] == ["2026-08", "2026-09"]

    # 같은 회사를 회사 페이지·비교에서 열면: 삭제 확인은 하루 기억해 올댓컴퍼니에 다시 요청하지 않고, 남겨 둔 이름으로 찾는다
    data = src.company("1302598")
    assert data["company"]["id"] == "1302598" and fetched == ["1302598"]
    with sf() as s:
        assert s.get(AtcCompany, "1302598").gone_at is not None

    # 국민연금 ID로 다시 열기 (비교·즐겨찾기)
    assert src.company("n12")["company"]["name"] == "크디랩 주식회사"


def test_company_without_known_name_explains(sources, monkeypatch):
    src, _ = sources
    monkeypatch.setattr(salary, "fetch_company", lambda http, cid: (_ for _ in ()).throw(gone_error()))
    with pytest.raises(SourceError, match="삭제된 회사 페이지라.*다시 검색"):
        src.company("777")


def test_posting_uses_allthatcompany_when_it_works(sources, monkeypatch):
    src, sf = sources
    cand = salary.Candidate("9", "넷마블(주)", "서울특별시 구로구", True, True)
    monkeypatch.setattr(salary, "search", lambda http, company, regions: ([cand], cand, "name_address"))
    monkeypatch.setattr(salary, "fetch_company", lambda http, cid: salary.CompanyStats(
        cid, "넷마블(주)", "서울특별시 구로구", "게임", salary.COMPANY_URL.format(cid), [salary.Month("2026-09", 5_000_000, 1, 2, 800)]))
    c = src.posting("넷마블(주)", "서울 구로구", None)["company"]
    assert c["source"]["key"] == "allthatcompany" and "fallback" not in c and src.nps.calls == []
    with sf() as s:
        assert s.get(AtcCompany, "9").name == "넷마블(주)"


def test_search_falls_back_to_nps(sources, monkeypatch):
    src, _ = sources

    def down(http, q, page):
        raise RuntimeError("요청 실패 (HTTP 503)")

    monkeypatch.setattr(salary, "search_page", down)
    data = src.search("크디랩", 1)
    assert data["source"]["key"] == "nps" and data["candidates"] == [
        {"id": "n12", "name": "크디랩 주식회사", "address": "서울특별시 영등포구 의사당대로"}]
    with pytest.raises(SourceError):
        src.search("크디랩", 2)


# ------------------------------------------------------------------ 공시 기준 평균연봉 (OpenDART)

def test_summarize_employee_rows():
    rows = [{"fo_bbm": "게임사업", "sexdstn": "남", "sm": "508", "jan_salary_am": "95,000,000", "fyer_salary_totamt": "46,327,000,000"},
            {"fo_bbm": "게임사업", "sexdstn": "여", "sm": "287", "jan_salary_am": "74,000,000", "fyer_salary_totamt": "20,859,000,000"}]
    out = dart.summarize(rows)
    assert out["headcount"] == 795 and out["avg"] == round((508 * 95_000_000 + 287 * 74_000_000) / 795)
    # 합계 행이 있으면 그 값
    total = {"fo_bbm": "합계", "sexdstn": "합계", "sm": "795", "jan_salary_am": "88,000,000", "fyer_salary_totamt": "67,186,000,000"}
    assert dart.summarize(rows + [total]) == {"avg": 88_000_000, "headcount": 795, "total": 67_186_000_000}
    assert dart.summarize([{"sm": "-", "jan_salary_am": "-"}]) is None


class FakeDart(dart.DartClient):
    def __init__(self, responses):
        super().__init__(key="test")
        self.responses = responses
        self.calls = []

    def ensure_corps(self, s):
        if not s.scalar(select(DartCorp.corp_code)):
            s.add_all([DartCorp(corp_code="00904672", name="넷마블", name_key="넷마블", stock_code="251270"),
                       DartCorp(corp_code="00441854", name="넷마블", name_key="넷마블", stock_code=None)])
            s.commit()

    def _request(self, path, **params):
        self.calls.append((path, params))
        key = (path, params.get("corp_code"), params.get("bsns_year"))
        return type("R", (), {"json": lambda _self: self.responses.get(key, {"status": "013", "message": "없음"})})()


def test_disclosure_picks_company_by_address_and_caches(tmp_path):
    sf = make_session_factory(f"sqlite:///{tmp_path / 'd.db'}")
    emp = {"status": "000", "list": [{"rcept_no": "20260318001170", "fo_bbm": "게임", "sexdstn": "남", "sm": "10",
                                      "jan_salary_am": "80,000,000", "fyer_salary_totamt": "800,000,000"}]}
    client = FakeDart({
        ("company.json", "00904672", None): {"status": "000", "adres": "서울특별시 구로구 디지털로26길 38"},
        ("company.json", "00441854", None): {"status": "000", "adres": "경기도 성남시 분당구"},
        ("empSttus.json", "00904672", "2025"): emp,
    })
    with sf() as s:
        corp = client.find_corp(s, "넷마블(주)", [("서울", "구로구")])
        assert corp.corp_code == "00904672"
        years = client.salary_years(s, corp, today=date(2026, 11, 12))
    assert years == [{"year": 2025, "avg": 80_000_000, "headcount": 10, "total": 800_000_000,
                      "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260318001170"}]
    n = len(client.calls)
    with sf() as s:
        client.salary_years(s, corp, today=date(2026, 11, 12))
    assert len(client.calls) == n  # 자료 있음·없음 모두 저장해 둔다
    with sf() as s:  # 주소를 모르면: 같은 이름 중 상장사가 하나뿐이면 그곳
        assert client.find_corp(s, "넷마블", []).corp_code == "00904672"
        assert client.find_corp(s, "없는회사", []) is None

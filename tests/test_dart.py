"""DART provider 테스트 — list.json + fnlttSinglAcnt 응답을 주입해 정규화 검증."""
from datetime import datetime, timezone

from app.core.errors import ProviderError
from app.data.schema import Kind
from app.providers.dart import DARTProvider, _amt, _yoy
from app.providers.kis import HttpTransport

NOW = datetime(2026, 6, 19, 8, 0, tzinfo=timezone.utc)

LIST_OK = {"status": "000", "list": [
    {"report_nm": "[기재정정]사업보고서 (2023.12)", "rcept_dt": "20240315", "corp_code": "00126380"},
    {"report_nm": "사업보고서 (2025.12)", "rcept_dt": "20260311", "corp_code": "00126380"},
    {"report_nm": "분기보고서 (2026.03)", "rcept_dt": "20260515", "corp_code": "00126380"},
]}

FIN_OK = {"status": "000", "list": [
    # 연결(CFS) 손익계산서
    {"fs_div": "CFS", "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "300,000", "frmtrm_amount": "250,000"},
    {"fs_div": "CFS", "sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": "50,000", "frmtrm_amount": "40,000"},
    {"fs_div": "CFS", "sj_div": "IS", "account_nm": "당기순이익", "thstrm_amount": "40,000", "frmtrm_amount": "30,000"},
    # 연결 재무상태표
    {"fs_div": "CFS", "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "1,000,000"},
    {"fs_div": "CFS", "sj_div": "BS", "account_nm": "부채총계", "thstrm_amount": "400,000"},
    {"fs_div": "CFS", "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "600,000"},
    # 별도(OFS) — CFS 가 있으면 무시되어야 함
    {"fs_div": "OFS", "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "111,111", "frmtrm_amount": "1"},
]}


class FakeTransport(HttpTransport):
    def __init__(self, list_resp=LIST_OK, fin_resp=FIN_OK):
        self.list_resp = list_resp; self.fin_resp = fin_resp; self.calls = []

    def get(self, url, headers, params):
        self.calls.append(url)
        if url.endswith("/list.json"):
            return 200, self.list_resp
        if url.endswith("/fnlttSinglAcnt.json"):
            return 200, self.fin_resp
        return 404, {}

    def post(self, url, headers, body):
        return 404, {}


def _provider(t=None):
    return DARTProvider("KEY", {"005930": "00126380"}, transport=t or FakeTransport())


def test_amt_parsing():
    assert _amt("1,234") == 1234.0
    assert _amt("(500)") == -500.0    # 괄호 = 음수
    assert _amt("-") is None
    assert _amt("") is None


def test_yoy():
    assert _yoy(300, 250) == (300 - 250) / 250
    assert _yoy(100, 0) is None       # 0 나눗셈 방지
    assert _yoy(None, 50) is None


def test_requires_corp_code():
    p = DARTProvider("KEY", {}, transport=FakeTransport())
    try:
        p.fetch("999999", "financials", now=NOW)
        assert False
    except ProviderError as e:
        assert "corp_code" in str(e)


def test_financials_normalized():
    p = _provider()
    dp = p.fetch("005930", "financials", now=NOW)
    f = dp.payload
    assert dp.kind == Kind.FINANCIALS.value
    assert f["revenue"] == 300000.0
    assert f["op_income"] == 50000.0
    assert f["net_income"] == 40000.0
    assert f["revenue_yoy"] == round((300000 - 250000) / 250000, 4)   # 0.2
    assert f["op_yoy"] == round((50000 - 40000) / 40000, 4)           # 0.25
    assert f["debt_ratio"] == round(400000 / 600000 * 100, 1)          # 66.7
    assert f["fs_div"] == "CFS"      # 연결 우선


def test_as_of_is_disclosure_date():
    # 가장 최근 '사업보고서'(2026-03-11 접수)의 접수일이 as_of
    p = _provider()
    dp = p.fetch("005930", "financials", now=NOW)
    assert dp.as_of.year == 2026 and dp.as_of.month == 3 and dp.as_of.day == 11
    assert dp.as_of <= NOW           # lookahead 차단
    assert dp.payload["bsns_year"] == "2025"


def test_no_annual_report_raises():
    no_annual = {"status": "000", "list": [
        {"report_nm": "분기보고서 (2026.03)", "rcept_dt": "20260515", "corp_code": "00126380"}]}
    p = _provider(FakeTransport(list_resp=no_annual))
    try:
        p.fetch("005930", "financials", now=NOW)
        assert False
    except ProviderError as e:
        assert "사업보고서" in str(e)


def test_dart_error_status_raises():
    err = {"status": "013", "message": "조회된 데이타가 없습니다."}
    p = _provider(FakeTransport(list_resp=err))
    try:
        p.fetch("005930", "financials", now=NOW)
        assert False
    except ProviderError as e:
        assert "013" in str(e)

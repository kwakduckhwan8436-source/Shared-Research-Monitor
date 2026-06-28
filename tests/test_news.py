"""News provider 테스트 — DART 공시(list.json) 응답을 주입해 분류·감성 검증."""
from datetime import datetime, timezone

from app.core.errors import ProviderError
from app.data.schema import Kind
from app.providers.news import NewsProvider
from app.providers.kis import HttpTransport

NOW = datetime(2026, 6, 19, 8, 0, tzinfo=timezone.utc)

DISC_OK = {"status": "000", "list": [
    {"report_nm": "유상증자결정", "rcept_dt": "20260610", "corp_code": "00126380"},
    {"report_nm": "단일판매ㆍ공급계약체결", "rcept_dt": "20260605", "corp_code": "00126380"},
    {"report_nm": "영업(잠정)실적(공정공시)", "rcept_dt": "20260601", "corp_code": "00126380"},
]}

NO_DATA = {"status": "013", "message": "조회된 데이타가 없습니다."}


class FakeTransport(HttpTransport):
    def __init__(self, resp=DISC_OK):
        self.resp = resp

    def get(self, url, headers, params):
        if url.endswith("/list.json"):
            return 200, self.resp
        return 404, {}

    def post(self, url, headers, body):
        return 404, {}


def _provider(resp=DISC_OK):
    return NewsProvider("KEY", {"005930": "00126380"}, llm_client=None,
                        transport=FakeTransport(resp))


def test_requires_corp_code():
    p = NewsProvider("KEY", {}, transport=FakeTransport())
    try:
        p.fetch("999999", "news", now=NOW)
        assert False
    except ProviderError as e:
        assert "corp_code" in str(e)


def test_disclosures_classified():
    p = _provider()
    dp = p.fetch("005930", "news", now=NOW)
    items = dp.payload["items"]
    assert dp.kind == Kind.NEWS.value
    assert len(items) == 3
    # 유상증자 -> 리스크 플래그 '유증' + 부정 감성
    유증 = next(i for i in items if "유상증자" in i["title"])
    assert "유증" in 유증["risk_flags"]
    assert 유증["sentiment"] < 0
    # 공급계약 -> 이벤트 태그
    계약 = next(i for i in items if "공급계약" in i["title"])
    assert "공급계약" in 계약["events"]
    # 실적 공시 -> 이벤트 '실적'
    실적 = next(i for i in items if "실적" in i["title"])
    assert "실적" in 실적["events"]


def test_as_of_is_now():
    # 뉴스 피드의 as_of 는 fetch 시점(지금의 최근 공시 스냅샷)
    p = _provider()
    dp = p.fetch("005930", "news", now=NOW)
    assert dp.as_of == NOW
    assert dp.source == "dart-news"


def test_no_disclosures_is_empty_not_error():
    # status 013 = 공시 없음 -> 빈 items (RiskFlags 가 '리스크 없음'으로 발화 가능)
    p = _provider(NO_DATA)
    dp = p.fetch("005930", "news", now=NOW)
    assert dp.payload["items"] == []
    assert dp.as_of == NOW


def test_future_disclosure_filtered():
    future = {"status": "000", "list": [
        {"report_nm": "유상증자결정", "rcept_dt": "20260620", "corp_code": "00126380"}]}  # now=06-19
    p = _provider(future)
    dp = p.fetch("005930", "news", now=NOW)
    assert dp.payload["items"] == []   # 미래 공시는 제외


def test_dart_error_status_raises():
    err = {"status": "020", "message": "사용한도 초과"}
    p = _provider(err)
    try:
        p.fetch("005930", "news", now=NOW)
        assert False
    except ProviderError as e:
        assert "020" in str(e)

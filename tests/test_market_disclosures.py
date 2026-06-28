"""시장 전체 공시(DART, corp_code 없이) 파싱 회귀 — 대형주 편중 해소 검증."""
from datetime import datetime, timezone
from app.providers.dart import DARTProvider


class _FakeT:
    def __init__(self, lst, total_page=1):
        self.lst = lst
        self.total_page = total_page
        self.calls = 0

    def get(self, url, headers, params):
        self.calls += 1
        return 200, {"status": "000", "total_page": self.total_page, "list": self.lst}


NOW = datetime(2026, 6, 21, 5, 0, tzinfo=timezone.utc)

_ROWS = [
    {"corp_name": "대형전자", "stock_code": "005930", "corp_cls": "Y",
     "report_nm": "자기주식취득결정", "rcept_no": "20260621000900", "rcept_dt": "20260621"},
    {"corp_name": "중소바이오", "stock_code": "099999", "corp_cls": "K",
     "report_nm": "단일판매ㆍ공급계약체결", "rcept_no": "20260621000999", "rcept_dt": "20260621"},
    {"corp_name": "비상장", "stock_code": "", "corp_cls": "E",
     "report_nm": "감사보고서제출", "rcept_no": "20260621000500", "rcept_dt": "20260621"},
]


def test_market_wide_includes_small_caps():
    d = DARTProvider("k", {}, transport=_FakeT(_ROWS))
    items = d.recent_disclosures(NOW, only_listed=True)
    syms = {it["symbol"] for it in items}
    assert "099999" in syms                       # 중소형 포함
    assert "" not in syms                          # 비상장(종목코드 없음) 제외


def test_sorted_newest_first():
    d = DARTProvider("k", {}, transport=_FakeT(_ROWS))
    items = d.recent_disclosures(NOW)
    nos = [it["rcept_no"] for it in items]
    assert nos == sorted(nos, reverse=True)        # 접수번호 역순(최신 우선)


def test_disclosure_fields_and_url():
    d = DARTProvider("k", {}, transport=_FakeT(_ROWS))
    it = d.recent_disclosures(NOW)[0]
    assert it["url"].startswith("https://dart.fss.or.kr/")
    assert it["rcept_no"] in it["url"]
    assert it["source"] == "공시" and it["market"] in ("코스피", "코스닥", "코넥스", "기타")
    assert it["published_at"].endswith("+09:00")   # KST


def test_no_key_returns_empty_gracefully():
    d = DARTProvider("", {}, transport=_FakeT(_ROWS))
    # 키 없으면 _get 이 ProviderError → recent_disclosures 는 빈 리스트
    assert d.recent_disclosures(NOW) == []


def test_status_013_is_not_error():
    """status=013(데이터 없음)은 오류가 아니라 빈 결과."""
    class T013:
        def get(self, url, headers, params):
            return 200, {"status": "013", "message": "조회된 데이타가 없습니다."}
    d = DARTProvider("key", {}, transport=T013())
    items = d.recent_disclosures(NOW, days=2)
    assert items == [] and d.last_disclosure_error is None


def test_inactive_key_surfaces_error():
    """status=011(미활성 키)은 사람이 읽는 오류로 노출."""
    class T011:
        def get(self, url, headers, params):
            return 200, {"status": "011", "message": "사용할 수 없는 키입니다."}
    d = DARTProvider("badkey", {}, transport=T011())
    items = d.recent_disclosures(NOW, days=2)
    assert items == [] and d.last_disclosure_error and "활성화" in d.last_disclosure_error


def test_missing_key_message():
    d = DARTProvider("", {}, transport=_FakeT(_ROWS))
    items = d.recent_disclosures(NOW)
    assert items == [] and "DART 키" in (d.last_disclosure_error or "")


def test_weekend_lookback_finds_friday():
    """주말이면 조회 기간을 늘려 직전 거래일(금요일) 공시를 잡는다."""
    class WeekendT:
        def get(self, url, headers, params):
            if params["bgn_de"] <= "20260619":   # 금요일 이하로 내려가면 공시 있음
                return 200, {"status": "000", "total_page": 1, "list": [
                    {"corp_name": "금요일사", "stock_code": "123450", "corp_cls": "K",
                     "report_nm": "공급계약", "rcept_no": "20260619000111", "rcept_dt": "20260619"}]}
            return 200, {"status": "013"}          # 토~일 없음
    d = DARTProvider("key", {}, transport=WeekendT())
    from datetime import datetime as _dt, timezone as _tz
    sunday = _dt(2026, 6, 21, 5, 0, tzinfo=_tz.utc)
    items = []
    for days in (2, 5, 10):
        items = d.recent_disclosures(sunday, days=days)
        if items or d.last_disclosure_error:
            break
    assert items and items[0]["corp"] == "금요일사"

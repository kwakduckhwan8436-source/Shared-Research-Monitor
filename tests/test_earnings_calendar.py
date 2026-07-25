"""실적발표 캘린더 — 공시 제목 분류 · 조회 구간 · 하루 상한 테스트."""
from datetime import date, datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 5, 20, 10, 0, tzinfo=KST)


def test_classify_earnings_catches_periodic_reports():
    """예전 버그: 제목에 '실적'이 들어간 공시만 잡아서
    실적의 대부분인 정기보고서가 하나도 안 걸렸다."""
    from app.providers.dart import classify_earnings
    assert classify_earnings("분기보고서 (2026.03)")["label"] == "분기실적"
    assert classify_earnings("반기보고서 (2026.06)")["label"] == "반기실적"
    assert classify_earnings("사업보고서 (2025.12)")["label"] == "연간실적"


def test_classify_earnings_specific_rule_wins():
    """예전 버그: '실적' 규칙이 '영업(잠정)실적'보다 위에 있어
    뒤 규칙이 죽은 코드였다. 구체적인 규칙이 먼저 이겨야 한다."""
    from app.providers.dart import classify_earnings
    assert classify_earnings("영업(잠정)실적(공정공시)")["label"] == "잠정실적"
    assert classify_earnings(
        "연결재무제표기준영업(잠정)실적(공정공시)")["label"] == "잠정실적(연결)"


def test_classify_earnings_krx_types():
    from app.providers.dart import classify_earnings
    assert classify_earnings(
        "매출액또는손익구조30%(대규모법인은15%)이상변경")["label"] == "손익구조 변경"
    assert classify_earnings("결산실적공시예고")["kind"] == "forecast"
    assert classify_earnings("영업(잠정)실적(공정공시)")["kind"] == "actual"


def test_classify_earnings_ignores_non_earnings():
    from app.providers.dart import classify_earnings
    assert classify_earnings("유상증자 결정") is None
    assert classify_earnings("주주총회소집공고") is None
    assert classify_earnings("단일판매ㆍ공급계약체결") is None
    assert classify_earnings("") is None
    assert classify_earnings(None) is None


def test_classify_earnings_marks_amendments():
    from app.providers.dart import classify_earnings
    r = classify_earnings("[기재정정]영업(잠정)실적(공정공시)")
    assert r["amended"] is True
    assert classify_earnings("영업(잠정)실적(공정공시)")["amended"] is False


class _FakeTransport:
    """DART list.json 흉내 — 요청 파라미터를 기록한다."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get(self, url, headers, params):
        self.calls.append(params)
        ty = params.get("pblntf_ty", "")
        page = int(params.get("page_no", 1))
        rows = [r for r in self.rows if r.get("_ty", ty) == ty] if ty else self.rows
        if page > 1:
            return 200, {"status": "000", "list": [], "total_page": 1}
        return 200, {"status": "000", "list": rows, "total_page": 1}


def _row(name, title, dt, code, ty):
    return {"corp_name": name, "report_nm": title, "rcept_dt": dt,
            "stock_code": code, "corp_cls": "Y",
            "rcept_no": dt + code + title[:2], "_ty": ty}


def test_recent_disclosures_uses_explicit_range():
    """예전 버그: 조회 구간을 now-days 로 잡아 과거월이 어긋났다."""
    from app.providers.dart import DARTProvider
    tr = _FakeTransport([_row("삼성전자", "분기보고서", "20260410", "005930", "A")])
    d = DARTProvider("KEY", transport=tr)
    d.recent_disclosures(NOW, bgn_date=date(2026, 4, 1), end_date=date(2026, 4, 30))
    assert tr.calls[0]["bgn_de"] == "20260401"
    assert tr.calls[0]["end_de"] == "20260430"


def test_recent_disclosures_passes_type_filter():
    """전체 공시는 하루 수천 건이라 유형을 좁혀야 한 달이 덮인다."""
    from app.providers.dart import DARTProvider
    tr = _FakeTransport([_row("삼성전자", "분기보고서", "20260515", "005930", "A")])
    d = DARTProvider("KEY", transport=tr)
    d.recent_disclosures(NOW, days=2, pblntf_ty="A")
    assert tr.calls[0]["pblntf_ty"] == "A"
    # 유형을 안 주면 파라미터 자체가 없어야(기존 동작 유지)
    tr2 = _FakeTransport([])
    DARTProvider("KEY", transport=tr2).recent_disclosures(NOW, days=2)
    assert "pblntf_ty" not in tr2.calls[0]


def test_earnings_disclosures_merges_types_and_filters():
    from app.providers.dart import DARTProvider
    rows = [
        _row("삼성전자", "분기보고서", "20260515", "005930", "A"),
        _row("현대차", "사업보고서", "20260331", "005380", "A"),
        _row("기아", "유상증자 결정", "20260410", "000270", "A"),      # 실적 아님
        _row("NAVER", "영업(잠정)실적(공정공시)", "20260420", "035420", "I"),
    ]
    d = DARTProvider("KEY", transport=_FakeTransport(rows))
    out = d.earnings_disclosures(NOW, date(2026, 3, 1), date(2026, 5, 31))
    labels = {o["corp"]: o["earnings"]["label"] for o in out}
    assert labels == {"삼성전자": "분기실적", "현대차": "연간실적",
                      "NAVER": "잠정실적"}

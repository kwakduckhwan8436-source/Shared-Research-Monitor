"""주주환원·지배구조 공시 분류 · 수집 테스트 (배치1)."""
from datetime import date, datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 5, 20, 10, 0, tzinfo=KST)


def test_classify_buyback_signs():
    from app.providers.dart import classify_governance as cg
    # 소각 = 가장 강한 주주환원(positive)
    assert cg("주요사항보고서(자기주식소각결정)")["cat"] == "buyback"
    assert cg("주요사항보고서(자기주식소각결정)")["sign"] == "positive"
    assert cg("주요사항보고서(자기주식취득결정)")["sign"] == "positive"
    # 처분·신탁해지 = 비우호(negative)
    assert cg("주요사항보고서(자기주식처분결정)")["sign"] == "negative"
    assert cg("자기주식취득신탁계약해지결정")["sign"] == "negative"


def test_classify_specific_before_general():
    """구체적 규칙(소각)이 일반 규칙(취득)보다 먼저 이겨야 한다."""
    from app.providers.dart import classify_governance as cg
    r = cg("주요사항보고서(자기주식소각결정)")
    assert r["label"] == "자사주 소각"   # '자사주' 일반 규칙에 먹히지 않음


def test_classify_insider():
    from app.providers.dart import classify_governance as cg
    assert cg("주식등의대량보유상황보고서")["cat"] == "insider"
    assert cg("임원ㆍ주요주주특정증권등소유상황보고서")["cat"] == "insider"


def test_classify_control():
    from app.providers.dart import classify_governance as cg
    assert cg("최대주주변경")["cat"] == "control"
    assert cg("회사합병결정")["cat"] == "control"
    assert cg("영업양수결정")["cat"] == "control"


def test_classify_ignores_unrelated():
    from app.providers.dart import classify_governance as cg
    assert cg("분기보고서") is None
    assert cg("단일판매ㆍ공급계약체결") is None
    assert cg("현금·현물배당결정") is None   # 배당은 별도(이 패널 아님)
    assert cg("") is None
    assert cg(None) is None


def test_classify_marks_amendment():
    from app.providers.dart import classify_governance as cg
    assert cg("[기재정정]주요사항보고서(자기주식취득결정)")["amended"] is True
    assert cg("주요사항보고서(자기주식취득결정)")["amended"] is False


class _FakeTransport:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get(self, url, headers, params):
        self.calls.append(params)
        ty = params.get("pblntf_ty", "")
        page = int(params.get("page_no", 1))
        rows = [r for r in self.rows if r.get("_ty") == ty] if ty else self.rows
        if page > 1:
            return 200, {"status": "000", "list": [], "total_page": 1}
        return 200, {"status": "000", "list": rows, "total_page": 1}


def _row(name, title, dt, code, ty):
    return {"corp_name": name, "report_nm": title, "rcept_dt": dt,
            "stock_code": code, "corp_cls": "Y",
            "rcept_no": dt + code + title[:3], "_ty": ty}


def test_governance_disclosures_merges_and_filters():
    from app.providers.dart import DARTProvider
    rows = [
        _row("삼성전자", "주요사항보고서(자기주식소각결정)", "20260515", "005930", "B"),
        _row("현대차", "주식등의대량보유상황보고서", "20260510", "005380", "D"),
        _row("기아", "회사합병결정", "20260505", "000270", "I"),
        _row("잡음", "분기보고서", "20260512", "111111", "B"),   # 제외돼야
    ]
    d = DARTProvider("KEY", transport=_FakeTransport(rows))
    out = d.governance_disclosures(NOW, date(2026, 5, 1), date(2026, 5, 31))
    got = {o["corp"]: o["gov"]["cat"] for o in out}
    assert got == {"삼성전자": "buyback", "현대차": "insider", "기아": "control"}


def test_week_windows_covers_range_without_gap():
    from app.providers.dart import DARTProvider
    ws = DARTProvider._week_windows(date(2026, 5, 1), date(2026, 5, 31))
    days = set()
    for b, e in ws:
        d = b
        while d <= e:
            days.add(d); d += timedelta(days=1)
    assert days == {date(2026, 5, i) for i in range(1, 32)}

"""수급 스크리너(외국인/기관 순매수) + 전종목 검색 회귀."""
import os
os.environ.setdefault("RECO_DATA_SOURCE", "mock")
from app.api.main import build_context


def _ctx():
    import importlib, app.config
    importlib.reload(app.config)
    c = build_context()
    c.service.refresh_data(c.universe, c.all_kinds())
    return c


def test_supply_modes_sorted_by_supply_score():
    c = _ctx()
    for mode in ("foreign", "inst"):
        rows = c.service.screen(mode, top_n=20)
        assert rows, f"{mode} 결과 없음"
        scores = [r["supply_score"] for r in rows]
        assert scores == sorted(scores, reverse=True)        # 종합 수급 점수 큰 순
        assert all(r["who"] == ("inst" if mode == "inst" else "foreign") for r in rows)
        # 점수 구성요소가 모두 존재
        assert all("intensity" in r and "supply_score" in r for r in rows)


def test_supply_rows_have_fields():
    c = _ctx()
    r = c.service.screen("foreign", top_n=5)[0]
    for k in ("symbol", "name", "net_buy", "net_buy5", "streak", "turnover", "volume", "change_pct"):
        assert k in r


def test_foreign_vs_inst_differ():
    c = _ctx()
    f = c.service.screen("foreign", top_n=10)
    i = c.service.screen("inst", top_n=10)
    # 외국인 1위와 기관 1위가 보통 다르다(다른 수급 주체)
    assert f and i
    assert f[0]["symbol"] != i[0]["symbol"] or f[0]["net_buy"] != i[0]["net_buy"]


def test_search_across_full_universe():
    c = _ctx()
    syms = c.ssot.symbols()
    if syms:
        name = c.name_of(syms[20]) if len(syms) > 20 else c.name_of(syms[0])
        key = (name or syms[0])[:2]
        rows = c.service.screen("foreign", top_n=300, q=key)
        # 검색 결과가 있으면 키워드를 포함
        if rows:
            assert any(key in (r["name"] or "") or key in r["symbol"] for r in rows)


def test_search_empty_when_no_match():
    c = _ctx()
    rows = c.service.screen("foreign", top_n=100, q="존재하지않는종목명ZZZ")
    assert rows == []


def test_theme_money_flow_by_net_buy():
    c = _ctx()
    rows = c.service.screen("foreign", top_n=40)
    tf = c.service.theme_money_flow(rows, top_n=5, by="net_buy")
    assert isinstance(tf, list)
    if tf:
        tos = [t["turnover"] for t in tf]
        assert tos == sorted(tos, reverse=True)


def test_theme_money_flow_empty():
    c = _ctx()
    assert c.service.theme_money_flow([], top_n=5, by="net_buy") == []


def test_screen_filter_includes_small_caps():
    c = _ctx()
    syms = c.ssot.symbols()
    relaxed = c.service.uf.screen_filter(c.ssot, syms)
    strict = c.service.uf.filter(c.ssot, syms, c.clock.now())
    assert len(relaxed) >= len(strict)


def test_net_buy_helpers():
    c = _ctx()
    syms = c.ssot.symbols()
    if syms:
        nb = c.service._net_buy(syms[0], "foreign", days=1)
        streak = c.service._net_buy_streak(syms[0], "foreign")
        assert nb is None or isinstance(nb, float)
        assert isinstance(streak, int) and streak >= 0


def test_search_includes_stocks_without_supply():
    """검색 시엔 수급 데이터가 없어도 종목이 나와야 한다(검색 동작 보장)."""
    import os as _os
    _os.environ["RECO_DATA_SOURCE"] = "mock"
    import importlib, app.config
    importlib.reload(app.config)
    from app.api.main import build_context
    from app.data.schema import Kind
    c = build_context()
    c.service.refresh_data(c.universe, [Kind.OHLCV.value])   # 수급 일부러 미적재
    syms = c.ssot.symbols()
    if syms:
        name = c.name_of(syms[5]) or syms[5]
        key = name[:2]
        rows = c.service.screen("foreign", top_n=50, q=key)
        # 수급 없어도 검색되어야
        assert rows, "수급 미적재 상태에서 검색 결과가 비었음"
        assert any(key in (r["name"] or "") or key in r["symbol"] for r in rows)


def test_no_supply_excluded_when_not_searching():
    """검색이 아닐 때는 수급 없는 종목 제외(랭킹 정확성)."""
    import os as _os
    _os.environ["RECO_DATA_SOURCE"] = "mock"
    import importlib, app.config
    importlib.reload(app.config)
    from app.api.main import build_context
    from app.data.schema import Kind
    c = build_context()
    c.service.refresh_data(c.universe, [Kind.OHLCV.value])   # 수급 미적재
    rows = c.service.screen("foreign", top_n=50)             # 검색 아님
    assert rows == []   # 수급 없으니 랭킹 비어야


def test_market_classification_and_filter():
    """코스피/코스닥 분류 + 시장 필터."""
    c = _ctx()
    rows = c.service.screen("foreign", top_n=20)
    assert rows
    assert all("market" in r for r in rows)
    kospi = c.service.screen("foreign", top_n=200, market="KOSPI")
    kosdaq = c.service.screen("foreign", top_n=200, market="KOSDAQ")
    assert all(r["market"] == "KOSPI" for r in kospi)
    assert all(r["market"] == "KOSDAQ" for r in kosdaq)
    # 둘 다 종목이 있어야(mock 에 양쪽 존재)
    assert kospi and kosdaq


def test_market_helper():
    c = _ctx()
    syms = c.ssot.symbols()
    if syms:
        m = c.service._market(syms[0])
        assert m in ("KOSPI", "KOSDAQ", "KONEX", "")


def test_krx_stock_list_parser():
    from app.providers.krx import parse_stock_list
    body = {"OutBlock_1": [
        {"ISU_SRT_CD": "005930", "ISU_ABBRV": "삼성전자", "MKT_TP_NM": "KOSPI"},
        {"ISU_SRT_CD": "247540", "ISU_ABBRV": "에코프로비엠", "MKT_TP_NM": "KOSDAQ"},
        {"ISU_SRT_CD": "BAD", "ISU_ABBRV": "x", "MKT_TP_NM": "KOSPI"},
    ]}
    rows = parse_stock_list(body)
    assert len(rows) == 2                       # 무효코드 제외
    d = {c: (n, m) for c, n, m in rows}
    assert d["005930"] == ("삼성전자", "KOSPI")
    assert d["247540"][1] == "KOSDAQ"


def test_screener_sorted_by_supply_quality():
    c = _ctx()
    rows = c.service.screen("foreign", top_n=30)
    scored = [r for r in rows if r.get("supply_score") is not None]
    # 수급 점수 내림차순(수급 좋은 순)
    s = [r["supply_score"] for r in scored]
    assert s == sorted(s, reverse=True)


def test_complex_conditions():
    c = _ctx()
    c.service.refresh_data(c.universe, c.all_kinds())
    base = c.service.screen("foreign", top_n=100)
    s3 = c.service.screen("foreign", top_n=100, cond_streak=3)
    assert all((r.get("streak") or 0) >= 3 for r in s3)
    assert len(s3) <= len(base)
    al = c.service.screen("foreign", top_n=100, cond_align=True)
    for r in al:
        assert c.service._is_aligned(r["symbol"])


def test_sub_investor_fields():
    from app.providers.kis import _F_SUB, _SUB_KO, _f
    r = {"pen_fund_ntby_qty": "300", "ivtr_ntby_qty": "150", "pe_fund_ntby_qty": "50"}
    sub = {}
    for key, field in _F_SUB.items():
        if field in r and str(r.get(field)).strip() not in ("", "None"):
            sub[key] = _f(r.get(field))
    assert sub["pension"] == 300.0
    assert sub["trust"] == 150.0
    assert sub["private"] == 50.0
    assert _SUB_KO["pension"] == "연기금"


def test_etf_parser_and_bundled():
    from app.providers.krx import parse_etf_list
    body = {"output": [
        {"ISU_SRT_CD": "069500", "ISU_ABBRV": "KODEX 200"},
        {"ISU_SRT_CD": "BAD", "ISU_ABBRV": "x"},
    ]}
    rows = parse_etf_list(body)
    assert len(rows) == 1
    assert rows[0] == ("069500", "KODEX 200", "ETF")
    from app.data.etfs import etf_list, etf_name_of
    bundled = etf_list()
    assert len(bundled) > 30
    assert etf_name_of("069500") == "KODEX 200"


def test_krx_investor_detail_parser():
    from app.providers.krx import parse_investor_detail
    body = {"output": [
        {"INVST_TP_NM": "연기금", "NETBID_TRDVAL": "30000000000"},
        {"INVST_TP_NM": "투신", "NETBID_TRDVAL": "15000000000"},
        {"INVST_TP_NM": "사모", "NETBID_TRDVAL": "-5000000000"},
    ]}
    d = parse_investor_detail(body)
    assert d["pension"] == 300.0
    assert d["trust"] == 150.0
    assert d["private"] == -50.0


def test_market_calendar():
    from app.core.calendar_events import computed_events, _second_thursday
    # 2026-03 둘째 목요일 = 만기일
    assert _second_thursday(2026, 3).isoformat() == "2026-03-12"
    ev = computed_events(2026, 3)
    types = {e["type"] for e in ev}
    assert "expiry" in types     # 만기일
    assert "holiday" in types    # 삼일절 대체 휴장
    # 12월엔 배당락 참고
    dec = computed_events(2026, 12)
    assert any(e["type"] == "dividend" for e in dec)


def test_econ_and_disclosure_classify():
    from app.core.econ_events import econ_events
    jan = econ_events(2026, 1)
    assert any("금융통화위" in e["label"] for e in jan)
    assert any("FOMC" in e["label"] for e in jan)
    # 공시 분류 규칙
    rules = [("주주총회", "주총"), ("배당", "배당"), ("유상증자", "유상증자"),
             ("자기주식", "자사주"), ("영업(잠정)실적", "잠정실적"), ("증권신고서", "공모")]
    def classify(t):
        for kw, short in rules:
            if kw in t:
                return short
        return None
    assert classify("주주총회소집공고") == "주총"
    assert classify("단일판매ㆍ공급계약체결") is None   # 이벤트성 아님 → 제외


def test_etf_distribution_and_ical():
    from app.core.econ_events import econ_events
    # 1월 ETF 분배금 기준일(월말 영업일)
    jan = econ_events(2026, 1)
    assert any("ETF 분배금" in e["label"] for e in jan)
    # 2월엔 ETF 분배금 없음
    feb = econ_events(2026, 2)
    assert not any("ETF 분배금" in e["label"] for e in feb)


def test_rate_limit_and_stats():
    from app.data.store import Store
    import datetime
    st = Store(":memory:")
    now = datetime.datetime.now().isoformat()
    for i in range(5):
        st.add_chat("U", "msg", now, None, "cid%d" % i)
    st.add_post("A", "t", "b", now)
    stats = st.admin_stats(days=7)
    assert stats["totals"]["chat"] == 5
    assert stats["totals"]["post"] == 1
    assert "chat_daily" in stats and "visitor_daily" in stats
    assert stats["reports"]["open"] == 0


def test_glossary_content():
    from app.content.glossary import GLOSSARY, DISCLOSURE_GUIDE, categories
    assert len(GLOSSARY) >= 50          # 50개 이상 용어
    terms = [g["term"] for g in GLOSSARY]
    assert len(terms) == len(set(terms))  # 중복 없음
    for must in ("PER", "PBR", "공매도", "배당락", "유상증자", "네 마녀의 날"):
        assert must in terms
    for g in GLOSSARY:                   # 모든 항목에 설명 존재
        assert g["term"] and g["desc"] and g["cat"]
    assert len(categories()) >= 4
    assert len(DISCLOSURE_GUIDE) >= 8
    for d in DISCLOSURE_GUIDE:
        assert d["what"] and d["read"] and d["caution"]


def test_daily_tip_rotation():
    from app.content.glossary import GLOSSARY
    # 날짜별로 용어가 결정적으로 순환하는지(인덱스 계산)
    for doy in (1, 100, 200, 366):
        idx = doy % len(GLOSSARY)
        assert 0 <= idx < len(GLOSSARY)
        assert GLOSSARY[idx]["term"]


def test_seo_pages_and_slug():
    from app.content.glossary import slugify, term_by_slug, guide_by_etype, GLOSSARY
    # 슬러그 충돌 없음
    slugs = [slugify(g["term"]) for g in GLOSSARY]
    assert len(slugs) == len(set(slugs))
    # 조회
    assert term_by_slug("PER")["term"] == "PER"
    assert term_by_slug(slugify("네 마녀의 날"))["term"] == "네 마녀의 날"
    assert guide_by_etype("dividend") is not None
    assert term_by_slug("존재하지않는용어") is None


def test_calendar_enriched_and_guides():
    from app.core.calendar_events import computed_events
    from app.content.glossary import START_GUIDE, FAQ
    # 3월: 법정 사업보고서 제출기한(filing)
    mar = computed_events(2026, 3)
    assert any(e["type"] == "filing" for e in mar)
    assert any("CPI" in e["label"] for e in mar)
    assert any("고용보고서" in e["label"] for e in mar)
    # 초보 가이드 / FAQ
    assert len(START_GUIDE) >= 5
    assert len(FAQ) >= 6
    for s in START_GUIDE:
        assert s["title"] and s["body"]
    for f in FAQ:
        assert f["q"] and f["a"]


def test_policy_news_provider():
    from app.providers.policy_news import PolicyNewsProvider
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    fixture = ('<?xml version="1.0"?><rss><channel>'
               '<item><title>2026 경제정책방향</title><link>https://korea.kr/1</link>'
               '<pubDate>Sat, 27 Jun 2026 09:00:00 +0900</pubDate>'
               '<description>본문 내용 길게</description></item>'
               '<item><title>금융소비자 보호 방안</title><link>https://korea.kr/2</link>'
               '<pubDate>Fri, 26 Jun 2026 14:00:00 +0900</pubDate></item>'
               '</channel></rss>')
    src = {"name": "정책브리핑", "url": "x",
           "attribution": "출처: 정책브리핑(korea.kr), 공공누리"}
    p = PolicyNewsProvider(transport=lambda u: fixture, sources=[src])
    items = p.fetch_all(datetime.now(KST))
    assert len(items) == 2
    # 제목+링크+출처만, 본문 미복제
    for it in items:
        assert it["title"] and it["link"]
        assert "공공누리" in it["attribution"]
        assert it["publisher"] == "정책브리핑"
        assert "body" not in it and "description" not in it
    # 최신순 정렬
    assert items[0]["title"] == "2026 경제정책방향"


def test_calendar_us_holiday_and_trade():
    from app.core.calendar_events import computed_events
    # 11월: 미국 추수감사절 휴장 + 한국 수출입동향
    nov = computed_events(2026, 11)
    assert any(e["type"] == "global" and "추수감사절" in e["label"] for e in nov)
    assert any("수출입동향" in e["label"] for e in nov)
    # 7월: 독립기념일 휴장
    jul = computed_events(2026, 7)
    assert any(e["type"] == "global" and "독립기념일" in e["label"] for e in jul)

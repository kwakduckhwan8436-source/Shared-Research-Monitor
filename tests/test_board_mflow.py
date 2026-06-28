"""버그 게시판 + 수급 연결/오실레이터(RSI) 회귀."""
import os
os.environ.setdefault("RECO_DATA_SOURCE", "mock")
from app.api.main import build_context
from app.api.routes import _rsi_series


def _ctx():
    import importlib, app.config
    importlib.reload(app.config)
    c = build_context()
    c.service.refresh_data(c.universe, c.all_kinds())
    return c


def test_board_add_and_list():
    c = _ctx()
    pid = c.store.add_post("덕환사마", "버그있어요", "검색이 느립니다", "2026-06-22T10:00:00+09:00", "버그")
    assert pid >= 1
    posts = c.store.get_posts()
    assert posts and posts[0]["title"] == "버그있어요"
    assert posts[0]["author"] == "덕환사마" and posts[0]["category"] == "버그"
    assert posts[0]["status"] == "open"


def test_board_newest_first():
    c = _ctx()
    c.store.add_post("a", "첫째", "x", "2026-06-22T10:00:00+09:00")
    c.store.add_post("b", "둘째", "y", "2026-06-22T10:01:00+09:00")
    posts = c.store.get_posts()
    assert posts[0]["title"] == "둘째"   # 최신 먼저


def test_rsi_series_basic():
    # 단조 증가 → RSI 100 수렴
    up = list(range(1, 30))
    osc = _rsi_series(up, period=14)
    assert osc[-1] == 100.0
    assert osc[0] is None
    # 단조 감소 → RSI 0
    down = list(range(30, 1, -1))
    od = _rsi_series(down, period=14)
    assert od[-1] == 0.0


def test_rsi_handles_short_series():
    assert _rsi_series([], 14) == []
    assert _rsi_series([5.0], 14) == [None]
    out = _rsi_series([1.0, 2.0, 3.0], period=2)
    assert len(out) == 3 and out[-1] is not None


def test_market_flow_aggregation():
    """시장 종목 수급을 날짜별 합산 → 누적 + 오실레이터 산출 가능."""
    c = _ctx()
    per_date = {}
    for sym in c.ssot.symbols():
        if c.service._market(sym) != "KOSPI":
            continue
        dp = c.ssot.get(sym, "supply")
        if dp is None:
            continue
        for d in dp.payload.get("daily", []):
            acc = per_date.setdefault(d["date"], 0.0)
            per_date[d["date"]] = acc + (d.get("foreign_net") or 0.0)
    dates = sorted(per_date)
    assert dates, "코스피 수급 집계가 비었음"
    cum, run = [], 0.0
    for dt in dates:
        run += per_date[dt]
        cum.append(run)
    osc = _rsi_series(cum, period=min(14, max(2, len(cum) - 1)))
    assert len(osc) == len(cum)


def test_stock_oscillator_rsi_stochastic():
    """종목별 오실레이터 — RSI + 스토캐스틱 산출."""
    c = _ctx()
    from app.data.schema import Kind
    sym = c.ssot.symbols()[0]
    o = c.ssot.get(sym, Kind.OHLCV.value)
    assert o and o.payload.get("bars")
    bars = o.payload["bars"][-60:]
    closes = [b["c"] for b in bars]
    highs = [b.get("h", b["c"]) for b in bars]
    lows = [b.get("l", b["c"]) for b in bars]
    rsi = _rsi_series(closes, 14)
    assert rsi[-1] is None or 0 <= rsi[-1] <= 100
    # 스토캐스틱 %K
    kp = 14
    raw_k = [None] * len(closes)
    for i in range(len(closes)):
        if i < kp - 1:
            continue
        wh = max(highs[i - kp + 1:i + 1]); wl = min(lows[i - kp + 1:i + 1])
        raw_k[i] = (closes[i] - wl) / (wh - wl) * 100 if wh > wl else 50.0
    assert raw_k[-1] is None or 0 <= raw_k[-1] <= 100


def test_stock_supply_oscillator():
    """수급 있는 종목은 외국인 누적 RSI 오실레이터 산출 가능."""
    c = _ctx()
    from app.data.schema import Kind
    sym = None
    for s in c.ssot.symbols():
        if c.ssot.get(s, Kind.SUPPLY.value):
            sym = s; break
    if sym:
        daily = c.ssot.get(sym, Kind.SUPPLY.value).payload.get("daily", [])
        cum, run = [], 0.0
        for d in daily:
            run += d.get("foreign_net") or 0.0
            cum.append(run)
        if len(cum) >= 3:
            osc = _rsi_series(cum, period=min(14, len(cum) - 1))
            assert len(osc) == len(cum)

"""테마 매핑 무결성 + 테마 자금흐름 집계 회귀."""
from datetime import datetime, timezone, timedelta
import statistics as st
from app.data.themes import THEMES, all_theme_symbols, themes_for
from app.providers.mock import _REAL_STOCKS
from app.core.ssot import SSOT
from app.data.schema import Kind, DataPoint

NOW = datetime(2026, 6, 19, 8, 0, tzinfo=timezone.utc)


def test_all_theme_codes_in_universe():
    valid = {c for c, _, _ in _REAL_STOCKS}
    unknown = [s for s in all_theme_symbols() if s not in valid]
    assert unknown == [], f"유니버스 외 코드: {unknown}"


def test_themes_nonempty_and_reverse_map():
    assert len(THEMES) >= 10
    for t, syms in THEMES.items():
        assert syms, f"{t} 비어있음"
    assert "반도체" in themes_for("005930")
    assert "2차전지" in themes_for("247540")


def _put(ss, sym, week_ret, window=5):
    bars = []
    p = 10000.0
    for d in range(30):
        bars.append({"t": (NOW - timedelta(days=30 - d)).isoformat(),
                     "o": p, "h": p * 1.01, "l": p * 0.99, "c": p, "v": 1_000_000})
    for k in range(window):
        bars[-window + k]["c"] = p * (1 + week_ret * (k + 1) / window)
    ss.put(DataPoint(sym, Kind.OHLCV.value, {"bars": bars}, NOW, NOW, "t"))


def _theme_ret(ss, theme, window=5):
    rets = []
    for s in THEMES[theme]:
        dp = ss.get(s, Kind.OHLCV.value)
        bars = dp.payload.get("bars") if dp else None
        if bars and len(bars) >= window + 1:
            rets.append((bars[-1]["c"] - bars[-1 - window]["c"]) / bars[-1 - window]["c"] * 100)
    return st.median(rets) if rets else None


def test_theme_flow_aggregation():
    ss = SSOT()
    for s in THEMES["반도체"][:6]:
        _put(ss, s, 0.08)       # +8% 주간
    for s in THEMES["금융"][:6]:
        _put(ss, s, -0.04)      # -4% 주간
    assert _theme_ret(ss, "반도체") > 5     # 강세 테마 양수
    assert _theme_ret(ss, "금융") < 0       # 약세 테마 음수

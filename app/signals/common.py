"""공통 시그널 — 모든 호라이즌에서 쓰이는 뉴스 감성·리스크.

리스크 플래그는 호의도를 떨어뜨리고, 동시에 evidence 의 risk_flags 로 노출되어
스코어러가 별도로 표면화한다(상세 화면 경고용).
"""
from __future__ import annotations

from app.data.schema import Kind
from app.signals.base import Signal, SignalContext, SignalResult, clamp01


class NewsSentiment(Signal):
    """최근 뉴스 감성 평균을 호의도로."""
    name = "news_sentiment"
    horizon = "common"
    required_kinds = (Kind.NEWS.value,)
    def __init__(self, horizon: str = "common"):
        self.horizon = horizon

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.NEWS.value)
        items = dp.payload.get("items", [])
        if not items:
            return self.abstain("no news items")
        sentiments = [it.get("sentiment", 0.0) for it in items]
        avg = sum(sentiments) / len(sentiments)   # [-1,1]
        value = clamp01(0.5 + avg / 2.0)
        return SignalResult(self.name, self.horizon, value=value, confidence=0.6,
                            evidence={"avg_sentiment": round(avg, 3),
                                      "n_items": len(items),
                                      "titles": [it.get("title") for it in items]})


class RiskFlags(Signal):
    """공시 리스크 + 공매도 비중. 위험할수록 호의도 낮음."""
    name = "risk_flags"
    horizon = "common"
    required_kinds = (Kind.NEWS.value,)   # short 는 optional

    def __init__(self, horizon: str = "common"):
        self.horizon = horizon

    def _compute(self, ctx: SignalContext) -> SignalResult:
        news = ctx.require(Kind.NEWS.value)
        flags: list[str] = []
        for it in news.payload.get("items", []):
            flags.extend(it.get("risk_flags", []))
        # 공매도는 optional — 있으면 반영, 없으면 무시(추정 안 함)
        short_ratio = None
        short_dp = ctx.ssot.get(ctx.symbol, Kind.SHORT.value)
        if short_dp is not None:
            short_ratio = short_dp.payload.get("short_balance_ratio")
            if short_ratio is not None and short_ratio > 5.0:
                flags.append(f"공매도비중 {short_ratio}%")
        flags = sorted(set(flags))
        risk_level = clamp01(len(flags) * 0.3)
        value = clamp01(1.0 - risk_level)
        return SignalResult(self.name, self.horizon, value=value, confidence=0.7,
                            evidence={"risk_flags": flags, "short_ratio": short_ratio})


class PriceMomentum(Signal):
    """기간 수익률 모멘텀. 호라이즌별 lookback(단타3·스윙20·중장기120일).
    일봉(OHLCV)만 있으면 발화하므로, 호가/재무가 없어도 모든 호라이즌에서 종목이 스코어된다."""
    name = "price_momentum"
    horizon = "common"
    required_kinds = (Kind.OHLCV.value,)
    _LOOKBACK = {"daytrade": 3, "swing": 20, "midlong": 120}

    def __init__(self, horizon: str = "common"):
        self.horizon = horizon

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.OHLCV.value)
        bars = dp.payload.get("bars", [])
        if len(bars) < 2:
            return self.abstain("bars<2")
        lb = self._LOOKBACK.get(self.horizon, 20)
        n = min(lb, len(bars) - 1)
        c_now = bars[-1]["c"]
        c_then = bars[-1 - n]["c"]
        if c_then <= 0:
            return self.abstain("bad price")
        ret = (c_now - c_then) / c_then              # 기간 수익률
        value = clamp01(0.5 + ret / 0.40)            # ±20%를 양 끝으로 [0,1] 매핑(상승=높음)
        return SignalResult(self.name, self.horizon, value=value, confidence=0.7,
                            evidence={"lookback_days": n, "return_pct": round(ret * 100, 2),
                                      "c_now": round(c_now, 1), "c_prev": round(c_then, 1)})


def _rsi(closes: list[float], n: int = 14) -> "float | None":
    """Wilder RSI(n). 데이터 부족 시 None."""
    if len(closes) < n + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-n, 0):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_gain = gains / n
    avg_loss = losses / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


class OverheatGuard(Signal):
    """과열 가드 — RSI 가 과매수(>~65)일수록 호의도를 낮춰 '이미 많이 오른' 종목 추격을 억제.
    price_momentum(추세추종)의 쏠림을 상쇄한다. 일봉만 있으면 발화."""
    name = "overheat_guard"
    horizon = "common"
    required_kinds = (Kind.OHLCV.value,)

    def __init__(self, horizon: str = "common"):
        self.horizon = horizon

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.OHLCV.value)
        bars = dp.payload.get("bars", [])
        closes = [b["c"] for b in bars]
        rsi = _rsi(closes, 14)
        if rsi is None:
            return self.abstain("bars<15")
        # RSI<=60 이면 1.0, 90 이상이면 0.0 (과매수 페널티). 과매도(저RSI)는 페널티 없음.
        value = clamp01(1.0 - max(0.0, rsi - 60.0) / 30.0)
        return SignalResult(self.name, self.horizon, value=value, confidence=0.7,
                            evidence={"rsi14": round(rsi, 1),
                                      "state": "과매수" if rsi > 70 else ("과매도" if rsi < 30 else "중립")})


COMMON_SIGNALS = [NewsSentiment, RiskFlags, PriceMomentum, OverheatGuard]

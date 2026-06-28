"""스윙(수일~수주) 시그널 — 일봉·수급 기반."""
from __future__ import annotations

import statistics
from app.data.schema import Kind
from app.signals.base import Signal, SignalContext, SignalResult, clamp01


def _closes(bars: list[dict]) -> list[float]:
    return [b["c"] for b in bars]


def _sma(xs: list[float], n: int) -> float:
    if len(xs) < n:
        raise ValueError("not enough bars")
    return sum(xs[-n:]) / n


class MovingAverageAlignment(Signal):
    """MA5 > MA20 > MA60 정배열 정도."""
    name = "ma_alignment"
    horizon = "swing"
    required_kinds = (Kind.OHLCV.value,)

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.OHLCV.value)
        bars = dp.payload["bars"]
        if len(bars) < 60:
            return self.abstain("bars<60")
        closes = _closes(bars)
        ma5, ma20, ma60 = _sma(closes, 5), _sma(closes, 20), _sma(closes, 60)
        satisfied = int(ma5 > ma20) + int(ma20 > ma60)
        value = satisfied / 2.0
        return SignalResult(self.name, self.horizon, value=value, confidence=0.9,
                            evidence={"ma5": round(ma5, 1), "ma20": round(ma20, 1),
                                      "ma60": round(ma60, 1), "satisfied_pairs": satisfied})


class ForeignInstNetBuyStreak(Signal):
    """외인+기관 N일 연속 순매수 정도."""
    name = "foreign_inst_streak"
    horizon = "swing"
    required_kinds = (Kind.SUPPLY.value,)

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.SUPPLY.value)
        daily = dp.payload["daily"]
        if len(daily) < 5:
            return self.abstain("supply<5")
        nets = [d["foreign_net"] + d["inst_net"] for d in daily]
        streak = 0
        for net in reversed(nets):
            if net > 0:
                streak += 1
            else:
                break
        recent_sum = sum(nets[-5:])
        # 호의도: 연속일수(최대 5) 기반 +- 방향
        value = clamp01(0.5 + 0.1 * min(streak, 5) * (1 if recent_sum > 0 else -1))
        return SignalResult(self.name, self.horizon, value=value, confidence=0.85,
                            evidence={"streak_days": streak,
                                      "recent5_net_sum": round(recent_sum, 1),
                                      "last5": [round(x, 1) for x in nets[-5:]]})


class VolumeBreakout(Signal):
    """거래량 동반 돌파: 당일 거래량 vs 20일 평균 + 20일 고가 근접."""
    name = "volume_breakout"
    horizon = "swing"
    required_kinds = (Kind.OHLCV.value,)

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.OHLCV.value)
        bars = dp.payload["bars"]
        if len(bars) < 20:
            return self.abstain("bars<20")
        vols = [b["v"] for b in bars]
        avg20 = statistics.mean(vols[-20:])
        vol_ratio = bars[-1]["v"] / avg20 if avg20 else 0.0
        high20 = max(b["h"] for b in bars[-20:])
        proximity = clamp01(bars[-1]["c"] / high20)  # 1에 가까울수록 신고가 근접
        vol_score = clamp01((vol_ratio - 1.0) / 1.5)
        value = clamp01(0.5 * vol_score + 0.5 * (proximity - 0.9) / 0.1) if proximity > 0.9 else vol_score * 0.5
        return SignalResult(self.name, self.horizon, value=clamp01(value), confidence=0.8,
                            evidence={"vol_ratio": round(vol_ratio, 2),
                                      "high20": round(high20, 1),
                                      "close": bars[-1]["c"],
                                      "proximity": round(proximity, 3)})


SWING_SIGNALS = [MovingAverageAlignment, ForeignInstNetBuyStreak, VolumeBreakout]

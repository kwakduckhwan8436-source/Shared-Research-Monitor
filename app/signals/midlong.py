"""중장기(수개월+) 시그널 — 재무·밸류에이션 기반.

재무 데이터의 as_of 는 *공시 시점*이다. 신선도 게이트가 너무 오래된 재무를 막는다.
"""
from __future__ import annotations

from app.data.schema import Kind
from app.signals.base import Signal, SignalContext, SignalResult, clamp01


def _percentile_rank(hist: list[float], value: float) -> float:
    """value 가 hist 안에서 차지하는 분위(0~1). 낮을수록 0."""
    if not hist:
        raise ValueError("empty hist")
    below = sum(1 for x in hist if x < value)
    return below / len(hist)


class ValuationPercentile(Signal):
    """PER/PBR 가 자기 과거 분위에서 '싼' 쪽이면 호의도 높음."""
    name = "valuation_percentile"
    horizon = "midlong"
    required_kinds = (Kind.FINANCIALS.value,)

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.FINANCIALS.value)
        f = dp.payload
        per, pbr = f.get("per"), f.get("pbr")
        per_hist, pbr_hist = f.get("per_hist") or [], f.get("pbr_hist") or []
        if per is None or pbr is None or not per_hist or not pbr_hist:
            return self.abstain("missing per/pbr history")
        per_rank = _percentile_rank(per_hist, per)   # 0=역사적 최저(쌈)
        pbr_rank = _percentile_rank(pbr_hist, pbr)
        cheapness = 1.0 - (per_rank + pbr_rank) / 2.0  # 쌀수록 1
        return SignalResult(self.name, self.horizon, value=clamp01(cheapness), confidence=0.8,
                            evidence={"per": per, "pbr": pbr,
                                      "per_pctile": round(per_rank, 2),
                                      "pbr_pctile": round(pbr_rank, 2)})


class EarningsGrowth(Signal):
    """매출·영업이익 YoY 성장."""
    name = "earnings_growth"
    horizon = "midlong"
    required_kinds = (Kind.FINANCIALS.value,)

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.FINANCIALS.value)
        f = dp.payload
        rev_yoy, op_yoy = f.get("revenue_yoy"), f.get("op_yoy")
        if rev_yoy is None or op_yoy is None:
            return self.abstain("missing yoy")
        # 0% -> 0.5, +50% -> ~1.0, -50% -> ~0.0
        score = 0.5 + (0.5 * rev_yoy + 0.5 * op_yoy)
        return SignalResult(self.name, self.horizon, value=clamp01(score), confidence=0.8,
                            evidence={"revenue_yoy": rev_yoy, "op_yoy": op_yoy,
                                      "debt_ratio": f.get("debt_ratio")})


class LongTermTrend(Signal):
    """장기 추세 — 장기이평(120일) 위/아래 + 기울기. 재무(DART)가 없어도 중장기 점수를 준다.
    추세추종이지만 장기 관점이라 가치/성장 신호와 함께 균형을 이룬다."""
    name = "long_term_trend"
    horizon = "midlong"
    required_kinds = (Kind.OHLCV.value,)

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.OHLCV.value)
        bars = dp.payload["bars"]
        closes = [b["c"] for b in bars]
        if len(closes) < 60:
            return self.abstain("bars<60")
        n = min(120, len(closes))
        ma_long = sum(closes[-n:]) / n
        c = closes[-1]
        if ma_long <= 0:
            return self.abstain("bad ma")
        above = (c - ma_long) / ma_long                       # 이평 대비 위치
        # 기울기: 장기이평의 최근 변화(20일 전 이평과 비교)
        slope = 0.0
        if len(closes) >= n + 20:
            ma_prev = sum(closes[-n - 20:-20]) / n
            if ma_prev > 0:
                slope = (ma_long - ma_prev) / ma_prev
        # 이평 위 + 우상향이면 높음. ±15% 이격, ±10% 기울기를 양 끝으로.
        pos_score = 0.5 + above / 0.30
        slope_score = 0.5 + slope / 0.20
        value = clamp01(0.6 * pos_score + 0.4 * slope_score)
        return SignalResult(self.name, self.horizon, value=value, confidence=0.7,
                            evidence={"vs_ma120_pct": round(above * 100, 2),
                                      "ma120_slope_pct": round(slope * 100, 2),
                                      "above_ma": c > ma_long})


MIDLONG_SIGNALS = [ValuationPercentile, EarningsGrowth, LongTermTrend]

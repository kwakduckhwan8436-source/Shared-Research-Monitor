"""단타(당일~며칠) 시그널 — 실시간 틱·호가·분봉 기반.

주의: tick/orderbook 은 신선도 예산이 매우 짧다(초 단위). 데이터가 stale 하면 abstain.
"""
from __future__ import annotations

import statistics
from app.data.schema import Kind
from app.signals.base import Signal, SignalContext, SignalResult, clamp01


class VolumeSurge(Signal):
    """당일 거래량 급증 — 최근 봉 거래량 vs 직전 평균."""
    name = "volume_surge"
    horizon = "daytrade"
    required_kinds = (Kind.OHLCV.value,)

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.OHLCV.value)
        bars = dp.payload["bars"]
        if len(bars) < 20:
            return self.abstain("bars<20")
        vols = [b["v"] for b in bars]
        avg = statistics.mean(vols[-20:-1])
        ratio = vols[-1] / avg if avg else 0.0
        value = clamp01((ratio - 1.0) / 2.0)
        return SignalResult(self.name, self.horizon, value=value, confidence=0.75,
                            evidence={"vol_ratio": round(ratio, 2),
                                      "today_vol": vols[-1], "avg19": round(avg, 0)})


class OrderBookImbalance(Signal):
    """호가 잔량 불균형 — (매수잔량 - 매도잔량)/총잔량."""
    name = "orderbook_imbalance"
    horizon = "daytrade"
    required_kinds = (Kind.ORDERBOOK.value,)

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.ORDERBOOK.value)
        ob = dp.payload
        bid_qty = sum(q for _, q in ob["bids"])
        ask_qty = sum(q for _, q in ob["asks"])
        total = bid_qty + ask_qty
        if total == 0:
            return self.abstain("empty orderbook")
        imbalance = (bid_qty - ask_qty) / total  # [-1,1]
        value = clamp01(0.5 + imbalance / 2.0)
        return SignalResult(self.name, self.horizon, value=value, confidence=0.7,
                            evidence={"bid_qty": bid_qty, "ask_qty": ask_qty,
                                      "imbalance": round(imbalance, 3)})


class IntradayStrength(Signal):
    """당일 종가 강도 — (종가-저가)/(고가-저가). 고가 부근 마감일수록 강함.
    호가(WS)가 없어도 일봉만으로 단타 강도를 근사한다."""
    name = "intraday_strength"
    horizon = "daytrade"
    required_kinds = (Kind.OHLCV.value,)

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.OHLCV.value)
        bars = dp.payload["bars"]
        if not bars:
            return self.abstain("no bars")
        b = bars[-1]
        rng = b["h"] - b["l"]
        if rng <= 0:
            return self.abstain("zero range")
        pos = (b["c"] - b["l"]) / rng              # 0=저가마감, 1=고가마감
        return SignalResult(self.name, self.horizon, value=clamp01(pos), confidence=0.65,
                            evidence={"close_pos": round(pos, 2),
                                      "high": b["h"], "low": b["l"], "close": b["c"]})


class OpeningGap(Signal):
    """시가갭 — 전일 종가 대비 당일 시가. 적당한 갭상승(+1~4%)을 선호, 과도한 갭은 중립화."""
    name = "opening_gap"
    horizon = "daytrade"
    required_kinds = (Kind.OHLCV.value,)

    def _compute(self, ctx: SignalContext) -> SignalResult:
        dp = ctx.require(Kind.OHLCV.value)
        bars = dp.payload["bars"]
        if len(bars) < 2:
            return self.abstain("bars<2")
        prev_c = bars[-2]["c"]
        today_o = bars[-1]["o"]
        if prev_c <= 0:
            return self.abstain("bad prev close")
        gap = (today_o - prev_c) / prev_c          # 갭 비율
        # +2% 부근을 정점으로, 갭다운(-)과 과도한 갭(+8%↑)은 낮게
        value = clamp01(0.5 + gap / 0.08)
        if gap > 0.10:                              # 과열 갭은 오히려 감점
            value = clamp01(0.5 - (gap - 0.10) / 0.10)
        return SignalResult(self.name, self.horizon, value=value, confidence=0.6,
                            evidence={"gap_pct": round(gap * 100, 2),
                                      "prev_close": prev_c, "today_open": today_o})


DAYTRADE_SIGNALS = [VolumeSurge, OrderBookImbalance, IntradayStrength, OpeningGap]

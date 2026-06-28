"""유니버스 필터.

추천 후보를 거른다: 관리종목/거래정지 제외, 최소 유동성(거래대금), 가격 하한(동전주 노이즈).
데이터(OHLCV)가 없으면 제외하되 *추정으로 통과시키지 않는다*.
"""
from __future__ import annotations

import statistics
from datetime import datetime
from dataclasses import dataclass

from app.core.ssot import SSOT
from app.data.schema import Kind


@dataclass(frozen=True)
class UniverseFilter:
    min_price: float = 2_000.0          # 동전주 제외
    min_avg_turnover: float = 1.0e8     # 20일 평균 거래대금 하한(원). 데모값.
    excluded_status: tuple[str, ...] = ("관리", "정지", "상장폐지")

    def passes(self, ssot: SSOT, symbol: str, now: datetime) -> tuple[bool, str]:
        dp = ssot.get(symbol, Kind.OHLCV.value)
        if dp is None:
            return False, "no ohlcv"
        bars = dp.payload.get("bars", [])
        status = dp.payload.get("status", "normal")
        if status in self.excluded_status:
            return False, f"status={status}"
        if len(bars) < 20:
            return False, "bars<20"
        last_close = bars[-1]["c"]
        if last_close < self.min_price:
            return False, f"price<{self.min_price:.0f}"
        turnover = statistics.mean(b["c"] * b["v"] for b in bars[-20:])
        if turnover < self.min_avg_turnover:
            return False, "low turnover"
        return True, "ok"

    def filter(self, ssot: SSOT, symbols: list[str], now: datetime) -> list[str]:
        return [s for s in symbols if self.passes(ssot, s, now)[0]]

    def screen_passes(self, ssot: SSOT, symbol: str, *, min_turnover: float = 0.0) -> bool:
        """스크리너(주도주/핫)용 완화 기준 — 중소형주를 전부 포함한다.
        제외는 (1)데이터 없음 (2)관리/정지/폐지 (3)등락률 계산 불가(2봉 미만)
        (4)min_turnover 미만(기본 0=무제한). 동전주·저거래대금 컷 없음."""
        dp = ssot.get(symbol, Kind.OHLCV.value)
        if dp is None:
            return False
        if dp.payload.get("status", "normal") in self.excluded_status:
            return False
        bars = dp.payload.get("bars", [])
        if len(bars) < 2:
            return False
        if min_turnover > 0:
            last = bars[-1]
            if last["c"] * last["v"] < min_turnover:
                return False
        return True

    def screen_filter(self, ssot: SSOT, symbols: list[str], *,
                      min_turnover: float = 0.0) -> list[str]:
        return [s for s in symbols if self.screen_passes(ssot, s, min_turnover=min_turnover)]

"""Verdict — 추천을 사후 검증한다. 이게 '정직성'의 증거다.

추천 시점 ref_price 와 평가 시점 현재가를 비교해 전방 수익률을 구하고,
신뢰도 구간별 실측 적중률을 집계한다(신뢰도 0.8이 진짜 0.8에 가까운가?).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from app.core.clock import Clock
from app.core.ssot import SSOT
from app.data.schema import Kind
from app.data.store import Store


def _current_price(ssot: SSOT, symbol: str) -> Optional[float]:
    dp = ssot.get(symbol, Kind.OHLCV.value)
    if dp is None:
        return None
    bars = dp.payload.get("bars", [])
    return bars[-1]["c"] if bars else None


class VerdictEvaluator:
    def __init__(self, ssot: SSOT, store: Store, clock: Clock, *, hit_threshold: float = 0.0):
        self.ssot = ssot
        self.store = store
        self.clock = clock
        self.hit_threshold = hit_threshold  # 전방수익률 > threshold 면 hit

    def evaluate_open(self, horizon: Optional[str] = None) -> int:
        """미검증 추천들을 현재가로 평가. 평가 건수 반환."""
        now = self.clock.now()
        rows = self.store.open_recommendations(horizon)
        evaluated = 0
        for row in rows:
            ref = row["ref_price"]
            if ref is None:
                continue
            cur = _current_price(self.ssot, row["symbol"])
            if cur is None:
                continue
            fwd = (cur - ref) / ref
            self.store.save_verdict({
                "recommendation_id": row["id"], "symbol": row["symbol"],
                "horizon": row["horizon"], "confidence": row["confidence"],
                "ref_price": ref, "eval_price": cur, "forward_return": fwd,
                "hit": fwd > self.hit_threshold, "evaluated_at": now.isoformat(),
            })
            evaluated += 1
        return evaluated

    def calibration_report(self, bins: int = 5) -> list[dict]:
        """신뢰도 구간별 실측 적중률·평균 전방수익률."""
        rows = self.store.calibration_rows()
        buckets: dict[int, list[tuple[float, int]]] = defaultdict(list)
        for r in rows:
            b = min(bins - 1, int(r["confidence"] * bins))
            buckets[b].append((r["forward_return"], r["hit"]))
        report = []
        for b in range(bins):
            lo, hi = b / bins, (b + 1) / bins
            data = buckets.get(b, [])
            if data:
                hit_rate = sum(h for _, h in data) / len(data)
                avg_ret = sum(fr for fr, _ in data) / len(data)
            else:
                hit_rate = avg_ret = None
            report.append({
                "confidence_range": f"{lo:.1f}-{hi:.1f}", "n": len(data),
                "hit_rate": None if hit_rate is None else round(hit_rate, 3),
                "avg_forward_return": None if avg_ret is None else round(avg_ret, 4),
            })
        return report

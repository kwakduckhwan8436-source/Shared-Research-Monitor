"""통합 데모 — 전체 파이프라인을 mock 데이터로 끝까지 돌린다.

실행: python run_pipeline.py
검증 항목: 데이터 적재 -> 호라이즌별 추천 -> 멱등성 -> 사후검증/캘리브레이션.
의존성: 표준 라이브러리만 (FastAPI/LLM 불필요).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from app.core.clock import FrozenClock
from app.core.eventbus import EventBus
from app.core.ssot import SSOT
from app.data.store import Store
from app.providers.mock import MockProvider, universe_symbols, name_of
from app.providers.base import ProviderRouter
from app.reco.service import RecommendationService
from app.reco.verdict import VerdictEvaluator
from app.signals.registry import HORIZONS, required_kinds_for


def banner(t: str) -> None:
    print("\n" + "=" * 64 + f"\n  {t}\n" + "=" * 64)


def main() -> int:
    clock = FrozenClock(datetime(2026, 6, 19, 7, 0, tzinfo=timezone.utc))
    bus = EventBus()
    events: list[str] = []
    bus.subscribe("reco.generated", lambda t, p: events.append(f"{t}:{p['horizon']}={p['count']}"))

    ssot = SSOT()
    store = Store(path=":memory:")
    provider = ProviderRouter([MockProvider()])

    svc = RecommendationService(
        ssot, provider, store, clock,
        name_resolver=name_of, bus=bus, allow_uncalibrated=True,
    )

    # 1) 데이터 적재 (모든 호라이즌에 필요한 kind 합집합)
    banner("1) 데이터 적재 (mock)")
    all_kinds = sorted({k for h in HORIZONS for k in required_kinds_for(h)})
    loaded = svc.refresh_data(universe_symbols(), all_kinds)
    print(f"적재 DataPoint: {loaded}, SSOT size: {ssot.size()}, symbols: {len(ssot.symbols())}")
    print(f"snapshot_id: {ssot.snapshot_id()}")

    # 2) 호라이즌별 추천
    for horizon in HORIZONS:
        banner(f"2) 추천 — {horizon}")
        recs = svc.recommend(horizon, top_n=5, persist=True)
        for i, r in enumerate(recs, 1):
            cal = "CALIB" if r.weights_calibrated else "규칙기반(미캘리브)"
            flags = (" ⚠ " + ", ".join(r.risk_flags)) if r.risk_flags else ""
            print(f"  {i}. {r.symbol} {r.name:14s} score={r.score:5.1f} "
                  f"conf={r.confidence:.2f} cov={r.coverage:.2f} [{cal}]{flags}")
            fired = ", ".join(f"{f['name']}={f['value']:.2f}" for f in r.fired)
            print(f"      발화: {fired}")
            if r.abstained:
                ab = ", ".join(f"{a['name']}" for a in r.abstained)
                print(f"      보류: {ab}")

    # 3) 멱등성 검증: 같은 스냅샷 -> 같은 추천
    banner("3) 멱등성 검증")
    a = svc.recommend("swing", top_n=5)
    b = svc.recommend("swing", top_n=5)
    same = [(x.symbol, x.score, x.confidence) for x in a] == \
           [(x.symbol, x.score, x.confidence) for x in b]
    print(f"동일 스냅샷 2회 추천 일치: {same}")
    if not same:
        print("  ✗ 멱등성 위반!")
        return 1

    # 4) 사후 검증 시나리오: 시간을 진행시켜 현재가 갱신 후 verdict
    banner("4) 사후 검증(verdict) — 5일 경과 가정")
    clock.advance(days=5)
    svc.refresh_data(universe_symbols(), ["ohlcv"])  # 갱신된 가격
    ev = VerdictEvaluator(ssot, store, clock)
    n = ev.evaluate_open()
    print(f"평가된 추천 수: {n}")
    report = ev.calibration_report(bins=5)
    print("  신뢰도 구간 | 표본 | 적중률 | 평균전방수익률")
    for row in report:
        hr = "-" if row["hit_rate"] is None else f"{row['hit_rate']:.2f}"
        ar = "-" if row["avg_forward_return"] is None else f"{row['avg_forward_return']*100:+.2f}%"
        print(f"   {row['confidence_range']:>9s} | {row['n']:>4d} | {hr:>6s} | {ar:>8s}")

    banner("이벤트 로그")
    print(events)

    print("\n✓ 전체 파이프라인 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

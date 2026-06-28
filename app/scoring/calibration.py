"""캘리브레이션 — verdict(사후 검증) 데이터로 가중치를 산출한다.

베이스라인 방법: 각 시그널 값과 전방 수익률의 정보계수(IC, 상관)를 구하고,
양의 IC 에 비례해 가중치를 둔다. (단순·해석가능. 과최적화 위험 낮음)

⚠ 충분한 표본(MIN_SAMPLES)이 없으면 NotCalibrated 를 던진다 —
   적은 데이터로 가중치를 '추정'하지 않는다.

운영 권장: walk-forward (구간을 굴리며 학습/검증 분리)로 IC 안정성을 확인할 것.
본 스켈레톤은 단일 구간 IC 만 계산하는 출발점이다.
"""
from __future__ import annotations

import statistics
from typing import Optional

from app.core.errors import NotCalibrated, DataUnavailable
from app.scoring.weights import WeightSet

MIN_SAMPLES = 30  # 시그널당 최소 표본


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx = statistics.pstdev(xs)
    sy = statistics.pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    return cov / (sx * sy)


def calibrate_from_samples(
    horizon: str,
    samples: dict[str, list[tuple[float, float]]],   # signal_name -> [(value, forward_return)]
) -> WeightSet:
    """양의 IC 비례 가중치. 표본 부족 시 NotCalibrated."""
    ics: dict[str, float] = {}
    for name, pairs in samples.items():
        if len(pairs) < MIN_SAMPLES:
            raise NotCalibrated(
                f"{horizon}/{name}: 표본 {len(pairs)} < {MIN_SAMPLES}. 캘리브레이션 불가."
            )
        vals = [v for v, _ in pairs]
        rets = [r for _, r in pairs]
        ic = _pearson(vals, rets)
        if ic is None:
            raise NotCalibrated(f"{horizon}/{name}: IC 계산 불가(분산 0).")
        ics[name] = ic

    positive = {n: ic for n, ic in ics.items() if ic > 0}
    if not positive:
        raise NotCalibrated(f"{horizon}: 양의 IC 시그널 없음. 가중치 산출 불가.")
    total = sum(positive.values())
    weights = {n: round(ic / total, 4) for n, ic in positive.items()}
    return WeightSet(horizon, weights, calibrated=True,
                     source=f"IC-calibrated (n>={MIN_SAMPLES}, ic={ {k: round(v,3) for k,v in ics.items()} })")

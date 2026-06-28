"""신선도 게이트 — 호라이즌마다 허용 staleness 가 다르다.

단타는 초 단위 신선도가 필요하고, 중장기 재무는 며칠 묵어도 된다.
예산을 초과하면 stale 값을 쓰지 않고 DataUnavailable 로 처리한다(추정금지).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.core.errors import DataUnavailable
from app.data.schema import DataPoint

# (horizon, kind) -> 최대 허용 나이(초). ("*", kind) 는 모든 호라이즌 공통 fallback.
STALENESS_BUDGET: dict[tuple[str, str], int] = {
    ("daytrade", "tick"): 5,
    ("daytrade", "orderbook"): 5,
    ("daytrade", "ohlcv"): 300,
    ("daytrade", "supply"): 60,
    ("swing", "ohlcv"): 4 * 86_400,    # 일봉=전일 종가, 주말·공휴일 끼면 2~3일 시차 정상
    ("swing", "supply"): 4 * 86_400,   # KRX 투자자별은 T+1 공개 + 주말 -> 여유 필요
    ("swing", "short"): 86_400,
    ("midlong", "ohlcv"): 7 * 86_400,
    ("midlong", "financials"): 450 * 86_400,   # 사업보고서는 연 1회 -> 다음 보고서까지 유효
    ("*", "news"): 3 * 86_400,
    ("*", "financials"): 450 * 86_400,         # 분기보고서 fetch 추가 시 좁힐 것
    ("*", "short"): 3 * 86_400,
}


def budget_for(horizon: str, kind: str) -> Optional[int]:
    if (horizon, kind) in STALENESS_BUDGET:
        return STALENESS_BUDGET[(horizon, kind)]
    return STALENESS_BUDGET.get(("*", kind))


def age_seconds(dp: DataPoint, now: datetime) -> float:
    return (now - dp.as_of).total_seconds()


def is_fresh(dp: DataPoint, horizon: str, now: datetime) -> bool:
    budget = budget_for(horizon, dp.kind)
    if budget is None:
        # 예산이 정의 안 된 (horizon, kind) 조합 -> 보수적으로 신선하지 않다고 본다.
        return False
    age = age_seconds(dp, now)
    return 0 <= age <= budget


def require_fresh(dp: Optional[DataPoint], symbol: str, kind: str,
                  horizon: str, now: datetime) -> DataPoint:
    """없거나 미래 데이터거나 예산 초과면 DataUnavailable. 통과하면 DataPoint 반환."""
    if dp is None:
        raise DataUnavailable(symbol, kind, "no data")
    age = age_seconds(dp, now)
    if age < 0:
        raise DataUnavailable(symbol, kind, f"future data (as_of>{now.isoformat()})")
    budget = budget_for(horizon, kind)
    if budget is None:
        raise DataUnavailable(symbol, kind, f"no staleness budget for ({horizon},{kind})")
    if age > budget:
        raise DataUnavailable(symbol, kind, f"stale: age={age:.0f}s > budget={budget}s")
    return dp

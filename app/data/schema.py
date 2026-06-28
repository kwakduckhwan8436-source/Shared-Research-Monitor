"""표준 데이터 스키마.

모든 외부 데이터는 DataPoint 로 정규화된다. 두 타임스탬프 분리가 핵심:
- as_of:      데이터가 '유효한' 시점 (종가 -> 장마감, 공시 -> 공시일, 수급 -> 공개 기준일)
- fetched_at: 우리가 '가져온' 시점

시그널은 as_of <= clock.now() 인 데이터만 사용한다 (lookahead 차단).
payload 모양은 kind 별로 아래 docstring 에 고정한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class Kind(str, Enum):
    OHLCV = "ohlcv"          # payload: {"bars":[{date,o,h,l,c,v}, ...], "status": "normal|관리|정지"}
    TICK = "tick"            # payload: {"price":float,"qty":int,"strength":float,"ts":iso}
    ORDERBOOK = "orderbook"  # payload: {"bids":[[price,qty]*5], "asks":[[price,qty]*5]}
    SUPPLY = "supply"        # payload: {"daily":[{date,foreign_net,inst_net,retail_net(억)}]}
    FINANCIALS = "financials"  # payload: {revenue,op_income,net_income,per,pbr,debt_ratio,
                               #           revenue_yoy,op_yoy, per_hist:[...], pbr_hist:[...]}
    SHORT = "short"          # payload: {"short_balance_ratio":float(%), "trend":"up|down|flat"}
    NEWS = "news"            # payload: {"items":[{title,body,published_at,sentiment,events,risk_flags}]}


@dataclass(frozen=True)
class DataPoint:
    symbol: str
    kind: str
    payload: Any
    as_of: datetime
    fetched_at: datetime
    source: str

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.fetched_at.tzinfo is None:
            raise ValueError("as_of/fetched_at must be timezone-aware")

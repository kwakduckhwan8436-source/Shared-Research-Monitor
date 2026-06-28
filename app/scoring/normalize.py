"""cross-sectional 정규화.

이건 랭킹 도구다. 절대 점수보다 '유니버스 내 상대 위치'가 중요하다.
raw_score 들을 유니버스 안에서 백분위(0~100)로 환산해 종목 간 비교 가능하게 만든다.
"""
from __future__ import annotations


def percentile_scores(raw_by_symbol: dict[str, float]) -> dict[str, float]:
    """각 심볼 raw_score -> 0~100 백분위. 동점은 평균 순위."""
    if not raw_by_symbol:
        return {}
    items = sorted(raw_by_symbol.items(), key=lambda kv: kv[1])
    n = len(items)
    if n == 1:
        return {items[0][0]: 50.0}
    out: dict[str, float] = {}
    for rank, (sym, _) in enumerate(items):
        out[sym] = round(100.0 * rank / (n - 1), 2)
    return out

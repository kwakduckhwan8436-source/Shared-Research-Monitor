"""신선도 게이트 테스트 — 없음/미래/stale -> DataUnavailable."""
from datetime import datetime, timezone, timedelta

from app.core.errors import DataUnavailable
from app.data.freshness import require_fresh, is_fresh, budget_for
from app.data.schema import DataPoint, Kind

NOW = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)


def _dp(kind, age_seconds):
    as_of = NOW - timedelta(seconds=age_seconds)
    return DataPoint("005930", kind, {}, as_of=as_of, fetched_at=NOW, source="t")


def test_missing_raises():
    try:
        require_fresh(None, "005930", Kind.OHLCV.value, "swing", NOW)
        assert False, "should raise"
    except DataUnavailable as e:
        assert "no data" in str(e)


def test_future_raises():
    future = DataPoint("005930", Kind.TICK.value, {}, as_of=NOW + timedelta(seconds=10),
                       fetched_at=NOW, source="t")
    try:
        require_fresh(future, "005930", Kind.TICK.value, "daytrade", NOW)
        assert False
    except DataUnavailable as e:
        assert "future" in str(e)


def test_stale_tick_raises():
    # 단타 tick 예산 5초. 60초 지난 데이터는 stale.
    try:
        require_fresh(_dp(Kind.TICK.value, 60), "005930", Kind.TICK.value, "daytrade", NOW)
        assert False
    except DataUnavailable as e:
        assert "stale" in str(e)


def test_fresh_financials_passes_for_midlong():
    # 중장기 재무 예산은 100일. 30일 된 재무는 신선.
    dp = _dp(Kind.FINANCIALS.value, 30 * 86400)
    assert is_fresh(dp, "midlong", NOW)
    require_fresh(dp, "005930", Kind.FINANCIALS.value, "midlong", NOW)  # no raise


def test_supply_tplus1_allowed_for_swing():
    # 스윙 supply 예산 -> T+1(약 1일) 공개 시차 허용.
    dp = _dp(Kind.SUPPLY.value, 20 * 3600)
    assert is_fresh(dp, "swing", NOW)


def test_swing_ohlcv_tolerates_weekend_gap():
    # 일봉은 전일 종가이므로 금요일 종가를 월요일에 보면 약 3일 시차.
    # 스윙 OHLCV 예산이 주말을 견뎌야 모든 종목이 보류되지 않는다(라이브 회귀 방지).
    fri_close = _dp(Kind.OHLCV.value, 3 * 86400 + 3600)   # 3일 1시간 전
    assert is_fresh(fri_close, "swing", NOW)
    require_fresh(fri_close, "005930", Kind.OHLCV.value, "swing", NOW)  # no raise


def test_swing_supply_tolerates_weekend_gap():
    # 수급도 T+1 + 주말이면 2~3일 시차가 정상.
    dp = _dp(Kind.SUPPLY.value, 3 * 86400)
    assert is_fresh(dp, "swing", NOW)

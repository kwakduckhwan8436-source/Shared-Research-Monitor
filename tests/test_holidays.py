"""KRX 휴장일 + 52주 고저 회귀 테스트."""
from datetime import date
from app.core.holidays import is_market_holiday, all_holiday_dates, holidays_for


def test_weekend_is_holiday():
    assert is_market_holiday(date(2026, 6, 20))   # 토
    assert is_market_holiday(date(2026, 6, 21))   # 일
    assert not is_market_holiday(date(2026, 6, 19))  # 금(평일)


def test_known_2026_holidays():
    for d in ["2026-01-01", "2026-02-17", "2026-05-05", "2026-09-25",
              "2026-10-09", "2026-12-25", "2026-12-31"]:
        y, m, dd = map(int, d.split("-"))
        assert is_market_holiday(date(y, m, dd)), d


def test_substitute_holidays():
    # 대체공휴일도 휴장
    assert is_market_holiday(date(2026, 3, 2))    # 삼일절 대체
    assert is_market_holiday(date(2026, 9, 28))   # 추석 대체


def test_all_dates_sorted_unique():
    dates = all_holiday_dates()
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))
    assert len(holidays_for(2026)) == 15

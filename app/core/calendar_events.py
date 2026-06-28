"""증시 캘린더 — 법적으로 안전한 공개 일정만 제공.

포함 일정(모두 공개·확정 정보, 시세 데이터 아님):
  · 휴장일(KRX 공휴일) — holidays.py
  · 선물·옵션 동시만기일(네 마녀의 날) — 3·6·9·12월 둘째 목요일(계산)
  · 옵션 만기일(월) — 매월 둘째 목요일(계산)
  · 배당락 예상일 — 연말 마지막 거래일 직전(참고)
  · 운영자 등록 일정 — market_calendar.txt 또는 settings(실적시즌·공모주 등)

투자 추천·시세·목표가 등은 일절 포함하지 않는다.
"""
from __future__ import annotations

from datetime import date, timedelta
from app.core.holidays import holidays_for, is_market_holiday


def _second_thursday(year: int, month: int) -> date:
    """해당 월의 둘째 목요일(옵션 만기 기준일)."""
    d = date(year, month, 1)
    # 첫 목요일
    offset = (3 - d.weekday()) % 7   # 목요일=3
    first_thu = d + timedelta(days=offset)
    return first_thu + timedelta(days=7)


def _adjust_for_holiday(d: date) -> date:
    """만기일이 휴장일이면 직전 영업일로 당김."""
    while is_market_holiday(d):
        d = d - timedelta(days=1)
    return d


def _last_trading_day(year: int) -> date:
    """연말 마지막 거래일(12/31부터 역산, 휴장일·주말 제외)."""
    d = date(year, 12, 31)
    while is_market_holiday(d):
        d = d - timedelta(days=1)
    return d


def computed_events(year: int, month: int) -> list[dict]:
    """해당 연·월의 계산 가능한 공개 일정."""
    out: list[dict] = []
    # 휴장일
    for ds, name in holidays_for(year).items():
        if ds.startswith(f"{year:04d}-{month:02d}"):
            out.append({"date": ds, "type": "holiday", "label": f"휴장 · {name}"})
    # 만기일
    exp = _adjust_for_holiday(_second_thursday(year, month))
    if exp.month == month:
        if month in (3, 6, 9, 12):
            out.append({"date": exp.isoformat(), "type": "expiry",
                        "label": "선물·옵션 동시만기일(네 마녀의 날)"})
        else:
            out.append({"date": exp.isoformat(), "type": "expiry",
                        "label": "옵션 만기일"})
    # 배당락 참고(12월) — 마지막 거래일 직전(통상 배당락)
    if month == 12:
        ltd = _last_trading_day(year)
        # 마지막 거래일은 배당 권리락 이후 — 권리부 마지막일은 그 직전 거래일
        prev = ltd - timedelta(days=1)
        while is_market_holiday(prev):
            prev = prev - timedelta(days=1)
        out.append({"date": ltd.isoformat(), "type": "dividend",
                    "label": "연말 배당락일(참고)"})
    # 경제지표·통화정책 발표 일정(내장 공개 일정)
    try:
        from app.core.econ_events import econ_events
        out.extend(econ_events(year, month))
    except Exception:
        pass
    return out

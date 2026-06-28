"""한국거래소(KRX) 휴장일 — 장 상태(장중/마감) 판정에 사용.

주의:
- 공휴일·대체공휴일은 매년 바뀐다(특히 음력 기반 설날·추석, 대체공휴일). **매년 갱신 필요.**
- 토요일·일요일은 코드에서 별도로 '주말 휴장' 처리하므로 여기엔 평일 휴장일만 넣는다.
- 출처: 한국거래소 휴장일 공지 / 관공서 공휴일. 평일 공휴일 + 12/31 연말 휴장.

갱신법: 연말에 다음 해 KRX 휴장일 공지를 확인해 해당 연도 set 을 추가하면 된다.
"""
from __future__ import annotations

from datetime import date

# 연도별 평일 휴장일 (YYYY-MM-DD)
KRX_HOLIDAYS: dict[int, dict[str, str]] = {
    2026: {
        "2026-01-01": "신정",
        "2026-02-16": "설날 연휴",
        "2026-02-17": "설날",
        "2026-02-18": "설날 연휴",
        "2026-03-02": "삼일절 대체",
        "2026-05-05": "어린이날",
        "2026-05-25": "부처님오신날 대체",
        "2026-08-17": "광복절 대체",
        "2026-09-24": "추석 연휴",
        "2026-09-25": "추석",
        "2026-09-28": "추석 대체",
        "2026-10-05": "개천절 대체",
        "2026-10-09": "한글날",
        "2026-12-25": "성탄절",
        "2026-12-31": "연말 휴장",
    },
}


def holidays_for(year: int) -> dict[str, str]:
    return KRX_HOLIDAYS.get(year, {})


def all_holiday_dates() -> list[str]:
    out: list[str] = []
    for y in KRX_HOLIDAYS.values():
        out.extend(y.keys())
    return sorted(out)


def is_market_holiday(d: date) -> bool:
    """주말 또는 등록된 휴장일이면 True."""
    if d.weekday() >= 5:           # 토(5)·일(6)
        return True
    return d.isoformat() in KRX_HOLIDAYS.get(d.year, {})

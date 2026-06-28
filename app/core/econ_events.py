"""경제지표·통화정책·법정공시 일정 — 공개·확정·규칙기반 일정만(사실 정보).

모두 각 기관이 사전 공개하거나 법으로 정해진 일정이며 시세·전망이 아니다.
정확한 날짜는 매년 기관 발표로 갱신해야 하므로, 확정 일정만 내장하고
미확정은 비워둔다(추측 금지). 운영자가 보강 가능.

출처(공개): 한국은행 금융통화위원회, 미 연준 FOMC, 미 노동부(BLS) CPI/고용,
한국 통계청·관세청 발표 일정, 자본시장법상 정기보고서 법정 제출기한 등.
"""
from __future__ import annotations

from datetime import date, timedelta
import calendar as _cal

# ── 통화정책 ──────────────────────────────────────────────
# 2026년 한국은행 금융통화위원회(통화정책방향 결정) — 공개 일정
_BOK_MPC_2026 = [
    "2026-01-15", "2026-02-26", "2026-04-16", "2026-05-28",
    "2026-07-09", "2026-08-27", "2026-10-15", "2026-11-26",
]
# 2026년 미 연준 FOMC(둘째 날=결정일 기준)
_FOMC_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]
# 2026년 미 노동부(BLS) 소비자물가(CPI) 발표(공개 일정, 현지 발표일 기준)
_US_CPI_2026 = [
    "2026-01-13", "2026-02-11", "2026-03-11", "2026-04-10",
    "2026-05-13", "2026-06-10", "2026-07-14", "2026-08-12",
    "2026-09-11", "2026-10-13", "2026-11-13", "2026-12-10",
]
# 2026년 미국 증시(뉴욕증권거래소) 휴장일 — 공개 일정. 한국 투자자의 미장 참고용.
_US_MARKET_HOLIDAYS_2026 = {
    "2026-01-01": "신정", "2026-01-19": "마틴 루터 킹 데이",
    "2026-02-16": "대통령의 날", "2026-04-03": "성금요일",
    "2026-05-25": "메모리얼 데이", "2026-06-19": "준틴스",
    "2026-07-03": "독립기념일(대체)", "2026-09-07": "노동절",
    "2026-11-26": "추수감사절", "2026-12-25": "크리스마스",
}


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def _us_jobs_report(year: int, month: int) -> date:
    """미국 고용상황 보고서 — 통상 매월 첫째 금요일."""
    d = date(year, month, 1)
    offset = (4 - d.weekday()) % 7   # 금요일=4
    return d + timedelta(days=offset)


def _last_business_day(year: int, month: int) -> date:
    last = date(year, month, _cal.monthrange(year, month)[1])
    while _is_weekend(last):
        last = last - timedelta(days=1)
    return last


# ── 자본시장법상 정기보고서 법정 제출기한(12월 결산법인 기준) ──
# 사업보고서: 사업연도 경과 후 90일 이내 → 3/31
# 1분기보고서: 분기 경과 후 45일 → 5/15
# 반기보고서: 반기 경과 후 45일 → 8/14
# 3분기보고서: 분기 경과 후 45일 → 11/14
_FILING_DEADLINES = {
    3: ("2026-03-31", "사업보고서 제출기한(12월 결산법인)"),
    5: ("2026-05-15", "1분기보고서 제출기한(12월 결산법인)"),
    8: ("2026-08-14", "반기보고서 제출기한(12월 결산법인)"),
    11: ("2026-11-16", "3분기보고서 제출기한(12월 결산법인)"),  # 11/14 토 → 익영업일
}


def econ_events(year: int, month: int) -> list[dict]:
    """해당 연·월의 경제지표·통화정책·법정공시 일정(내장 공개 일정)."""
    pref = f"{year:04d}-{month:02d}"
    out: list[dict] = []

    # 통화정책
    for ds in _BOK_MPC_2026:
        if ds.startswith(pref):
            out.append({"date": ds, "type": "econ",
                        "label": "한국은행 금융통화위원회(기준금리 결정)"})
    for ds in _FOMC_2026:
        if ds.startswith(pref):
            out.append({"date": ds, "type": "econ",
                        "label": "미 연준 FOMC(기준금리 결정)"})

    # 미국 지표
    for ds in _US_CPI_2026:
        if ds.startswith(pref):
            out.append({"date": ds, "type": "econ",
                        "label": "미국 소비자물가지수(CPI) 발표"})
    # 미국 고용보고서(첫째 금요일)
    jobs = _us_jobs_report(year, month)
    if jobs.month == month:
        out.append({"date": jobs.isoformat(), "type": "econ",
                    "label": "미국 고용보고서(비농업 고용) 발표"})
    # 미국 증시 휴장일(미장 참고용)
    for ds, nm in _US_MARKET_HOLIDAYS_2026.items():
        if ds.startswith(pref):
            out.append({"date": ds, "type": "global",
                        "label": f"미국 증시 휴장 · {nm}"})

    # 한국 지표 — 통계청 소비자물가(매월 초), 관세청 수출입동향(매월 1일)
    # 관세청 수출입동향: 매월 1일 발표(전월 실적). 1일이 휴일이면 익영업일.
    imp = date(year, month, 1)
    while _is_weekend(imp):
        imp = imp + timedelta(days=1)
    out.append({"date": imp.isoformat(), "type": "econ",
                "label": "한국 수출입동향(관세청) 발표"})

    # 한국 지표 — 통계청 소비자물가(매월 초), 관세청 수출입동향(매월 1일)
    # 통계청 소비자물가는 통상 매월 2일경 발표(주말이면 익영업일). 보수적으로 둘째 영업일.
    d2 = date(year, month, 1)
    biz = 0
    cur = d2
    while biz < 2:
        if not _is_weekend(cur):
            biz += 1
            if biz == 2:
                break
        cur = cur + timedelta(days=1)
    out.append({"date": cur.isoformat(), "type": "econ",
                "label": "한국 소비자물가동향(통계청) 발표(참고)"})

    # ETF 분배금 지급 기준일 — 통상 1·4·7·10·12월 마지막 영업일(분배락)
    if month in (1, 4, 7, 10, 12):
        last = _last_business_day(year, month)
        out.append({"date": last.isoformat(), "type": "econ",
                    "label": "다수 ETF 분배금 지급 기준일(분배락, 참고)"})

    # 법정 공시 제출기한(12월 결산법인)
    if month in _FILING_DEADLINES:
        ds, label = _FILING_DEADLINES[month]
        if ds.startswith(pref):
            out.append({"date": ds, "type": "filing", "label": label})

    return out

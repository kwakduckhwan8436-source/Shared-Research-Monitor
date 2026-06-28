"""이상징후(anomaly) 감지 — 종목의 '자기 과거' 대비 비정상 움직임을 통계로 포착한다.

아이디어: 거래량·수급(외인/기관)이 그 종목의 평소 범위(평균±표준편차)를 크게 벗어나면
무언가 일어나고 있다는 신호. 외인/기관의 비정상 매집은 종종 뉴스·급등 *전에* 나타난다.

판정(전부 보유 데이터로 가능):
- 거래량 z-score (오늘 거래량 vs 직전 N일)
- 외국인/기관 순매수 z-score (오늘 vs 직전 N일)
- 조용한 매집: 외인+기관이 며칠 연속 순매수인데 주가는 잠잠 → 매집 의심
- 거래량만 터지고 가격 무반응 → 손바뀜/분산 의심

⚠ 통계적 이상일 뿐 호재/악재 방향을 단정하지 않는다. 조사의 출발점이지 매매 신호가 아니다.
   z-score 는 분포 가정에 민감하고, 거래량은 한쪽으로 치우쳐 거짓 신호가 날 수 있다.
"""
from __future__ import annotations

import statistics as st


def _zscore(history: list[float], value: float) -> float | None:
    """직전 값들(history, 현재 제외) 대비 value 의 z-score."""
    if len(history) < 5:
        return None
    m = st.mean(history)
    sd = st.pstdev(history)
    if sd == 0:
        return 0.0
    return (value - m) / sd


def _sev(z: float, lo: float = 2.0, hi: float = 5.0) -> float:
    """|z| 를 0~1 심각도로. lo 이하=0, hi 이상=1."""
    a = abs(z)
    if a <= lo:
        return 0.0
    return min(1.0, (a - lo) / (hi - lo))


def detect_anomalies(bars: list[dict] | None,
                     supply_daily: list[dict] | None,
                     window: int = 20) -> dict:
    """단일 종목 이상징후. {score, flags:[...], metrics:{...}}.
    flags: [{type, label, z, severity, detail}]"""
    flags: list[dict] = []
    metrics: dict = {}

    # ---- 거래량 이상 ----
    vol_z = None
    price_chg = None
    if bars and len(bars) >= window + 1:
        vols = [b["v"] for b in bars]
        vol_z = _zscore(vols[-1 - window:-1], vols[-1])
        c, c1 = bars[-1]["c"], bars[-2]["c"]
        price_chg = (c - c1) / c1 * 100 if c1 else 0.0
        metrics["vol_z"] = round(vol_z, 2) if vol_z is not None else None
        metrics["price_chg"] = round(price_chg, 2)
        if vol_z is not None and vol_z >= 2.0:
            if abs(price_chg) < 1.0:
                flags.append({"type": "거래량급증_무반응", "label": "거래량 급증·가격 무반응",
                              "z": round(vol_z, 2), "severity": _sev(vol_z),
                              "detail": f"거래량 평소 대비 z={vol_z:.1f}인데 등락 {price_chg:+.1f}% — 손바뀜/매집 가능"})
            else:
                flags.append({"type": "거래량급증", "label": "거래량 급증",
                              "z": round(vol_z, 2), "severity": _sev(vol_z),
                              "detail": f"거래량 평소 대비 z={vol_z:.1f} (등락 {price_chg:+.1f}%)"})

    # ---- 수급 이상(외국인/기관) ----
    if supply_daily and len(supply_daily) >= window + 1:
        f_hist = [d["foreign_net"] for d in supply_daily[-1 - window:-1]]
        i_hist = [d["inst_net"] for d in supply_daily[-1 - window:-1]]
        f_now = supply_daily[-1]["foreign_net"]
        i_now = supply_daily[-1]["inst_net"]
        fz = _zscore(f_hist, f_now)
        iz = _zscore(i_hist, i_now)
        metrics["foreign_z"] = round(fz, 2) if fz is not None else None
        metrics["inst_z"] = round(iz, 2) if iz is not None else None
        if fz is not None and abs(fz) >= 2.0:
            buy = f_now > 0
            flags.append({"type": "외국인_" + ("매수" if buy else "매도") + "급증",
                          "label": "외국인 " + ("순매수" if buy else "순매도") + " 급증",
                          "z": round(fz, 2), "severity": _sev(fz),
                          "detail": f"외국인 순매수 z={fz:.1f} (평소 범위 크게 이탈)"})
        if iz is not None and abs(iz) >= 2.0:
            buy = i_now > 0
            flags.append({"type": "기관_" + ("매수" if buy else "매도") + "급증",
                          "label": "기관 " + ("순매수" if buy else "순매도") + " 급증",
                          "z": round(iz, 2), "severity": _sev(iz),
                          "detail": f"기관 순매수 z={iz:.1f} (평소 범위 크게 이탈)"})

        # ---- 조용한 매집: 외인+기관 N일 연속 순매수 + 주가 잠잠 ----
        streak = 0
        for d in reversed(supply_daily):
            if d["foreign_net"] + d["inst_net"] > 0:
                streak += 1
            else:
                break
        metrics["accum_streak"] = streak
        if streak >= 4 and bars and len(bars) >= 6:
            back = min(streak, len(bars) - 1)
            c_now = bars[-1]["c"]
            c_then = bars[-1 - back]["c"]
            move = (c_now - c_then) / c_then * 100 if c_then else 0.0
            if abs(move) < 4.0:
                flags.append({"type": "조용한_매집", "label": "조용한 매집(외인+기관)",
                              "z": float(streak), "severity": min(1.0, streak / 8.0),
                              "detail": f"외인+기관 {streak}일 연속 순매수인데 주가 {move:+.1f}% — 매집 의심"})

    score = round(max([f["severity"] for f in flags], default=0.0), 3)
    flags.sort(key=lambda x: x["severity"], reverse=True)
    return {"score": score, "flags": flags, "metrics": metrics}

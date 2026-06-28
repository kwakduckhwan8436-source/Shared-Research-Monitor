"""시장 국면(regime) 감지 — 코스피 추세 + VIX(변동성)로 강세/약세/횡보를 판정한다.

왜 중요한가: 같은 신호도 국면에 따라 의미가 다르다. 강세장에선 모멘텀 추격이 통하지만
약세장에선 위험하다. 국면을 알면 어떤 호라이즌·신호를 더/덜 신뢰할지 판단할 수 있다.

판정 입력(둘 다 외부 데이터, 야후):
- 코스피 종가 시계열 → 추세(장기이평 대비 위치, 60일 수익률, 추세 기울기)
- VIX 수준 → 변동성/공포 (낮음=안정, 높음=불안)
  * VIX 가 없으면 코스피 자체 실현변동성으로 대체.

⚠ 국면 판정은 참고용 신호다. 자동으로 점수를 바꾸지 않는다(그건 백테스트 검증이 선행돼야 함).
   대신 국면과 그 함의를 보여줘 사용자가 해석을 조정하게 한다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Regime:
    regime: str            # bull | bear | range
    label: str             # 한글 라벨
    vix: float | None
    vix_state: str         # 안정 | 보통 | 불안 | 공포
    vs_ma_pct: float | None    # 코스피 vs 장기이평(%)
    ret_60d: float | None      # 코스피 60거래일 수익률(%)
    trend_slope: float | None  # 장기이평 기울기(%)
    realized_vol: float | None # 코스피 연율 실현변동성(%)
    confidence: float          # 판정 신뢰도(0~1)
    guidance: dict             # 호라이즌별 함의


def _sma(xs: list[float], n: int) -> float | None:
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def _annualized_vol(closes: list[float], n: int = 20) -> float | None:
    if len(closes) < n + 1:
        return None
    rets = []
    for i in range(-n, 0):
        if closes[i - 1] > 0:
            rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
    if len(rets) < 2:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return (var ** 0.5) * (252 ** 0.5) * 100.0


def _vix_state(vix: float | None) -> str:
    if vix is None:
        return "—"
    if vix < 15:
        return "안정"
    if vix < 20:
        return "보통"
    if vix < 28:
        return "불안"
    return "공포"


def _guidance(regime: str) -> dict:
    if regime == "bull":
        return {
            "summary": "상승 추세. 모멘텀·돌파 신호가 통하기 쉬운 국면.",
            "daytrade": "거래량·돌파 추종 유효. 단, 과열 종목은 되돌림 주의.",
            "swing": "추세 순응(이평 정배열·모멘텀) 신뢰도 상대적으로 높음.",
            "midlong": "보유 지속에 우호적. 신규는 밸류·실적과 함께 분할 접근.",
            "caution": "과열(고RSI) 추격은 늘 위험. 상위 점수가 이미 많이 오른 것은 아닌지 확인.",
        }
    if regime == "bear":
        return {
            "summary": "하락 추세/고변동. 모멘텀 추격이 위험한 국면.",
            "daytrade": "변동성 큼 → 손절 짧게. 반등 추격보다 분할·관망.",
            "swing": "모멘텀 신호 신뢰도 낮아짐. 역추세·저점매수는 칼날 잡기 위험.",
            "midlong": "우량주 분할매수 구간일 수 있으나 섣부른 바닥 단정 금지.",
            "caution": "‘싸 보인다’는 더 싸질 수 있음. 현금 비중·리스크 관리 우선.",
        }
    return {
        "summary": "방향성 불명확(횡보). 신호 엇갈림이 잦은 국면.",
        "daytrade": "박스권 매매 우위. 돌파는 가짜 돌파(휩쏘) 주의.",
        "swing": "추세 신호 신뢰도 중간. 확인 후 진입, 무리한 추격 자제.",
        "midlong": "관망·분할. 국면 전환 신호(추세·거래량) 확인 후 비중 조절.",
        "caution": "방향 베팅보다 종목 선별이 중요. 손익비 관리.",
    }


def detect_regime(kospi_closes: list[float] | None, vix: float | None = None) -> Regime:
    vs_ma = ret60 = slope = rvol = None
    score = 0.0          # +상승 / -하락
    parts = 0

    if kospi_closes and len(kospi_closes) >= 60:
        c = kospi_closes[-1]
        ma_long = _sma(kospi_closes, min(200, len(kospi_closes)))
        if ma_long and ma_long > 0:
            vs_ma = (c - ma_long) / ma_long * 100
            score += max(-1.0, min(1.0, vs_ma / 8.0)); parts += 1
        c60 = kospi_closes[-61] if len(kospi_closes) >= 61 else kospi_closes[0]
        if c60 > 0:
            ret60 = (c - c60) / c60 * 100
            score += max(-1.0, min(1.0, ret60 / 10.0)); parts += 1
        if len(kospi_closes) >= 220:
            ma_prev = _sma(kospi_closes[:-20], 200)
            if ma_prev and ma_prev > 0 and ma_long:
                slope = (ma_long - ma_prev) / ma_prev * 100
                score += max(-1.0, min(1.0, slope / 4.0)); parts += 1
        rvol = _annualized_vol(kospi_closes, 20)

    trend = (score / parts) if parts else 0.0     # -1..+1

    # VIX(또는 실현변동성)로 위험 가중
    risk = None
    if vix is not None:
        risk = vix
    elif rvol is not None:
        risk = rvol
    high_vol = (risk is not None and risk >= 25)

    # 분류
    if parts == 0:
        regime, label = "range", "판정 불가(데이터 부족)"
        conf = 0.0
    elif trend >= 0.25 and not high_vol:
        regime, label = "bull", "강세장"
        conf = min(1.0, 0.5 + trend / 2 + parts * 0.1)
    elif trend <= -0.2 or high_vol:
        regime, label = "bear", "약세장" if trend <= -0.2 else "약세·고변동"
        conf = min(1.0, 0.5 + abs(trend) / 2 + parts * 0.1)
    else:
        regime, label = "range", "횡보장"
        conf = min(1.0, 0.4 + parts * 0.1)

    return Regime(
        regime=regime, label=label, vix=vix, vix_state=_vix_state(vix),
        vs_ma_pct=round(vs_ma, 2) if vs_ma is not None else None,
        ret_60d=round(ret60, 2) if ret60 is not None else None,
        trend_slope=round(slope, 2) if slope is not None else None,
        realized_vol=round(rvol, 1) if rvol is not None else None,
        confidence=round(conf, 2), guidance=_guidance(regime),
    )

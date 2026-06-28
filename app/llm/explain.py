"""근거 설명 레이어.

LLM 은 종목을 고르거나 점수를 만들지 않는다. 이미 계산된 추천 증거(JSON)를 받아
사람이 읽을 한국어 근거 요약 + 리스크 고지를 쓴다.

두 경로:
1) Anthropic API (운영) — 아키텍처의 '설명 프롬프트'를 system 으로.
2) 오프라인 템플릿 — 네트워크/키 없이 evidence 를 그대로 포매팅(추정 없음).
"""
from __future__ import annotations

import json
from typing import Any, Optional

SYSTEM_PROMPT_EXPLAIN = """[역할]
너는 한국 주식 종목 추천 시스템의 "설명 레이어"다. 너는 종목을 고르거나 점수를 만들지 않는다.
이미 계산된 증거(JSON)를 받아, 사람이 읽을 수 있는 한국어 근거 요약과 리스크 고지를 작성한다.

[절대 규칙 - 추정금지]
1. evidence에 없는 수치/사실을 만들거나 추정하지 마라. 모르면 "데이터 없음"이라고 쓴다.
2. 인과를 단정하지 마라. 사실만 기술한다(예: "외인 N일 연속 순매수(+X억)").
3. abstain된 시그널은 "판단 보류(사유)"로 명시한다. 빠뜨리거나 긍정으로 메우지 마라.
4. confidence가 낮으면(<0.5) 그 사실을 먼저 분명히 알린다. 점수만 강조하지 마라.
5. 미래 가격/수익률을 예측하거나 매수·매도를 권유하지 마라. 증거 요약이지 투자 자문이 아니다.

[출력 형식 (한국어, 간결)]
## {symbol} · {horizon} · 점수 {score}/100 (신뢰도 {confidence})
- 핵심 근거: (발화 시그널 3~5개를 evidence 수치와 함께)
- 보류·미확인: (abstain된 시그널과 사유)
- 리스크: (risk_flags 풀어서)
- 한 줄 요약: (과장 없이)

[금지] "강력 추천","급등 임박" 같은 단정·선동 표현. evidence 밖 인용.
"""


def build_payload(rec: dict[str, Any]) -> dict[str, Any]:
    """Recommendation(dict) -> LLM 입력용 증거 페이로드."""
    return {
        "symbol": rec["symbol"], "name": rec.get("name"), "horizon": rec["horizon"],
        "score": rec["score"], "confidence": rec["confidence"],
        "coverage": rec.get("coverage"),
        "weights_calibrated": rec["weights_calibrated"],
        "risk_flags": rec["risk_flags"],
        "signals": rec["fired"], "abstained": rec["abstained"],
    }


def _offline_explain(p: dict[str, Any]) -> str:
    lines = [f"## {p['symbol']} {p.get('name') or ''} · {p['horizon']} · "
             f"점수 {p['score']}/100 (신뢰도 {p['confidence']:.2f})"]
    if p["confidence"] < 0.5:
        lines.append(f"> ⚠ 신뢰도 낮음({p['confidence']:.2f}) — 발화 시그널이 적거나 합의가 약합니다.")
    if not p["weights_calibrated"]:
        lines.append("> 가중치: 규칙기반 기본값(통계 캘리브레이션 아님).")
    lines.append("- 핵심 근거:")
    for s in p["signals"]:
        ev = ", ".join(f"{k}={v}" for k, v in s["evidence"].items()
                       if k != "risk_flags" and not isinstance(v, list))
        lines.append(f"    · {s['name']}={s['value']:.2f} (conf {s['confidence']:.2f}) {ev}")
    if p["abstained"]:
        lines.append("- 보류·미확인:")
        for a in p["abstained"]:
            lines.append(f"    · {a['name']}: {a['abstain_reason']}")
    lines.append("- 리스크: " + (", ".join(p["risk_flags"]) if p["risk_flags"] else "특이사항 없음(데이터 기준)"))
    lines.append("- 한 줄 요약: 위 근거는 데이터 기반 사실이며, 투자 판단·자문이 아닙니다.")
    return "\n".join(lines)


def explain(rec: dict[str, Any], *, client: Optional[Any] = None,
            model: str = "claude-sonnet-4-6") -> str:
    payload = build_payload(rec)
    if client is None:
        return _offline_explain(payload)
    try:
        resp = client.messages.create(
            model=model, max_tokens=800,
            system=SYSTEM_PROMPT_EXPLAIN,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        return _offline_explain(payload) + f"\n\n_(LLM 오류 폴백: {e})_"

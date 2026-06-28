"""뉴스·공시 감성 분석.

두 경로:
1) Anthropic API (운영) — 아키텍처 문서의 '뉴스 감성 프롬프트'를 system 으로 사용, JSON only.
2) 오프라인 폴백 — 키워드 규칙 기반. 네트워크/키 없이도 파이프라인이 돈다.

규칙(추정금지): 본문에 명시된 사실만. 미래 가격 예측 금지. 감성은 '사건의 성격'에 한정.
"""
from __future__ import annotations

import json
from typing import Any, Optional

SYSTEM_PROMPT_SENTIMENT = """[역할] 너는 한국 주식 뉴스·공시 감성 분석기다. 원문에 '명시된 사실'만 추출한다.

[규칙 - 추정금지]
- 본문에 없는 내용을 추론하지 마라. 기자의 전망·"~할 것으로 보인다"류 의견은 fact가 아니다.
- 미래 가격 영향을 예측하지 마라. 감성은 '텍스트가 서술하는 사건의 성격'에 한정한다.
- 본문이 종목과 무관하면 relevant=false.

[출력 - JSON only, 다른 텍스트·마크다운·코드펜스 금지]
{"relevant":true,"sentiment":-1.0,"events":[],"risk_flags":[],"key_facts":[],"rationale":""}
"""

# 오프라인 폴백용 키워드 사전 (운영에선 LLM 이 대체).
_POS = ["흑자", "수주", "신고가", "최대 실적", "어닝 서프라이즈", "증가", "성장", "호조", "계약 체결", "공급 계약"]
_NEG = ["적자", "감소", "하락", "부진", "쇼크", "리콜", "소송", "횡령", "배임", "유상증자", "감자", "거래정지", "관리종목", "상장폐지"]
_RISK = {
    "유상증자": "유증", "전환사채": "CB발행", "횡령": "횡령·배임", "배임": "횡령·배임",
    "감자": "감자", "관리종목": "관리종목", "상장폐지": "상장폐지", "거래정지": "거래정지",
    "소송": "소송",
}


def _offline_analyze(symbol: str, title: str, body: str) -> dict[str, Any]:
    text = f"{title} {body}"
    pos = sum(1 for k in _POS if k in text)
    neg = sum(1 for k in _NEG if k in text)
    total = pos + neg
    sentiment = 0.0 if total == 0 else round((pos - neg) / total, 3)
    risk_flags = sorted({flag for kw, flag in _RISK.items() if kw in text})
    events = sorted({k for k in (_POS + _NEG) if k in text})
    return {
        "relevant": True,
        "sentiment": sentiment,
        "events": events,
        "risk_flags": risk_flags,
        "key_facts": [],            # 오프라인 폴백은 수치 추출을 하지 않는다(추정 방지).
        "rationale": f"offline keyword scan: pos={pos}, neg={neg}",
        "engine": "offline",
    }


def analyze(item: dict[str, Any], *, client: Optional[Any] = None,
            model: str = "claude-sonnet-4-6") -> dict[str, Any]:
    """item: {symbol,title,body,published_at,source}. 반환: 감성 dict."""
    symbol = item.get("symbol", "")
    title = item.get("title", "")
    body = item.get("body", "")
    if client is None:
        return _offline_analyze(symbol, title, body)

    user = json.dumps(
        {"symbol": symbol, "title": title, "body": body,
         "published_at": item.get("published_at"), "source": item.get("source")},
        ensure_ascii=False,
    )
    try:
        resp = client.messages.create(
            model=model, max_tokens=600,
            system=SYSTEM_PROMPT_SENTIMENT,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(text)
        data["engine"] = "anthropic"
        return data
    except Exception as e:  # 실패 시 폴백 (조용히 깨지지 않게).
        out = _offline_analyze(symbol, title, body)
        out["rationale"] += f" | llm_error: {e}"
        return out

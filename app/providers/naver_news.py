"""네이버 뉴스 검색 API provider — 실시간 언론 기사.

DART 공시(공식 공시)와 별개로, 종목명으로 실시간 뉴스 기사를 가져온다.
네이버 검색 오픈API(공식)를 쓰므로 크롤링/약관 문제가 없다.

엔드포인트: https://openapi.naver.com/v1/search/news.json?query=&sort=date
헤더: X-Naver-Client-Id, X-Naver-Client-Secret (developers.naver.com 발급)

산출: [{title, published_at, sentiment, events, risk_flags, source:"news", link}] (최신순).
키 미설정 시 빈 리스트(공시만 표시).

⚠ 네트워크/키 필요. 파싱(HTML 제거·pubDate)은 fixture 로 검증되어 있습니다.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Optional

from app.providers.kis import UrllibTransport, HttpTransport
from app.llm import sentiment as sentiment_mod

NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", s or "")).strip()


def _parse_pubdate(s: str) -> Optional[datetime]:
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None


class NaverNewsProvider:
    name = "naver-news"

    def __init__(self, client_id: str, client_secret: str, *,
                 transport: Optional[HttpTransport] = None, display: int = 20):
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport or UrllibTransport()
        self.display = display

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def fetch_news(self, query: str, now: datetime) -> list[dict]:
        if not self.enabled:
            return []
        status, body = self.transport.get(NAVER_URL, {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }, {"query": query, "display": str(self.display), "sort": "date"})
        if status != 200:
            return []
        out = []
        for it in body.get("items", []):
            title = _strip_html(it.get("title", ""))
            if not title:
                continue
            pub = _parse_pubdate(it.get("pubDate", ""))
            pub_utc = pub.astimezone().astimezone(tz=None) if pub else None
            if pub is not None and pub.timestamp() > now.timestamp():
                continue  # 미래 기사 제외
            desc = _strip_html(it.get("description", ""))
            analysis = sentiment_mod.analyze(
                {"symbol": query, "title": title, "body": desc,
                 "published_at": pub.isoformat() if pub else "", "source": "naver"},
                client=None,
            )
            out.append({
                "title": title,
                "published_at": pub.isoformat() if pub else "",
                "sentiment": analysis["sentiment"],
                "events": analysis["events"], "risk_flags": analysis["risk_flags"],
                "source": "news", "link": it.get("link", ""),
            })
        return out

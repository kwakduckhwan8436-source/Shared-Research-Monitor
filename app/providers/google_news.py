"""구글 뉴스 RSS provider — 키워드로 다수 언론사 기사를 모은다(네이버 보완).

왜: 한 포털(네이버)만 의존하면 그 인덱싱에 갇힌다. 구글 뉴스 RSS 는
- 키(인증) 불필요, 키워드 검색 지원
- 수많은 언론사를 집계(특정 포털이 안 잡는 매체도 포함)
- 한국/해외 뉴스 모두 커버
- 원문 링크로 연결(우리는 제목·링크만 보여주고 본문은 미수집 → 저작권 안전)

엔드포인트: https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko
응답: RSS(XML). item 의 title 은 "제목 - 언론사" 형식, link 는 구글뉴스 리디렉션 URL.

⚠ 비공식 RSS 라 구조가 바뀔 수 있다. 실패 시 graceful(빈 리스트). 네트워크 필요.
"""
from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from app.llm import sentiment as sentiment_mod

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def _strip_tags(s: str) -> str:
    out = []
    depth = 0
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return " ".join("".join(out).split())


def _parse_rfc822(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


class GoogleNewsProvider:
    """구글 뉴스 RSS 기반. 키 불필요라 항상 enabled(네트워크만 되면)."""

    def __init__(self, *, transport=None, timeout: float = 8.0, hl: str = "ko",
                 gl: str = "KR", max_items: int = 20):
        self.transport = transport      # 테스트용 주입(없으면 urllib)
        self.timeout = timeout
        self.hl = hl
        self.gl = gl
        self.max_items = max_items
        self.enabled = True

    def _fetch_text(self, query: str) -> Optional[str]:
        if self.transport is not None:
            return self.transport(query)
        q = urllib.parse.quote(query)
        url = f"{GOOGLE_NEWS_RSS}?q={q}&hl={self.hl}&gl={self.gl}&ceid={self.gl}:{self.hl}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (stock_reco)"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return None
                return resp.read().decode("utf-8", "replace")
        except Exception:
            return None

    def parse_rss(self, text: str, query: str, now: datetime) -> list[dict]:
        """RSS XML → 기사 dict 리스트(네이버 provider 와 동일 형식)."""
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []
        out: list[dict] = []
        for item in root.iter("item"):
            raw_title = (item.findtext("title") or "").strip()
            if not raw_title:
                continue
            # "제목 - 언론사" → 제목/언론사 분리
            source_el = item.find("source")
            publisher = (source_el.text or "").strip() if source_el is not None else ""
            title = raw_title
            if publisher and raw_title.endswith(" - " + publisher):
                title = raw_title[: -(len(publisher) + 3)].strip()
            elif " - " in raw_title:
                title, _, publisher = raw_title.rpartition(" - ")
                title = title.strip(); publisher = publisher.strip()
            link = (item.findtext("link") or "").strip()
            pub = _parse_rfc822(item.findtext("pubDate") or "")
            if pub is not None and pub.timestamp() > now.timestamp():
                continue  # 미래 기사 제외
            desc = _strip_tags(item.findtext("description") or "")
            analysis = sentiment_mod.analyze(
                {"symbol": query, "title": title, "body": desc,
                 "published_at": pub.isoformat() if pub else "", "source": "google"},
                client=None,
            )
            out.append({
                "title": title,
                "published_at": pub.isoformat() if pub else "",
                "sentiment": analysis["sentiment"],
                "events": analysis["events"], "risk_flags": analysis["risk_flags"],
                "source": "news", "provider": "google", "publisher": publisher,
                "link": link,
            })
            if len(out) >= self.max_items:
                break
        return out

    def fetch_news(self, query: str, now: datetime) -> list[dict]:
        text = self._fetch_text(query)
        if not text:
            return []
        return self.parse_rss(text, query, now)

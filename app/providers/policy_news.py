"""정부정책 RSS provider — 정책브리핑(korea.kr) 및 부처 보도자료.

법적 안전: 모두 공공기관이 공개하는 RSS이며, 공공누리(출처표시) 기준으로
제목+링크+출처만 표시한다. 본문은 복제하지 않는다.

수집원(공개 RSS):
  · 정책브리핑(korea.kr) — 정부 공식 정책 포털(문화체육관광부 운영)
  · 금융위원회, 기획재정부 등 부처 보도자료 RSS

⚠ 각 기관의 RSS 주소·구조는 바뀔 수 있다. 실패 시 graceful(빈 리스트).
  네트워크 필요. 운영자는 SOURCES 의 url 을 실제 RSS 주소로 갱신/추가 가능.
"""
from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional

KST = timezone(timedelta(hours=9))

# 공개 정책 RSS 소스 — (이름, RSS URL, 출처표기)
# 모두 정책브리핑(korea.kr)이 제공하는 부처별 RSS로, 공공누리 제1유형(출처표시)
# 기준이라 출처만 밝히면 상업적 이용도 가능. 실제 주소는 기관 사정에 따라
# 달라질 수 있어 운영자가 검증/갱신 권장(누리집 'RSS' 메뉴에서 확인).
SOURCES = [
    {"name": "정책브리핑", "url": "https://www.korea.kr/rss/policy.xml",
     "attribution": "출처: 정책브리핑(korea.kr), 공공누리"},
    {"name": "기획재정부", "url": "https://www.korea.kr/rss/dept_moef.xml",
     "attribution": "출처: 기획재정부, 공공누리"},
    {"name": "금융위원회", "url": "https://www.korea.kr/rss/dept_fsc.xml",
     "attribution": "출처: 금융위원회, 공공누리"},
    {"name": "산업통상자원부", "url": "https://www.korea.kr/rss/dept_motie.xml",
     "attribution": "출처: 산업통상자원부, 공공누리"},
    {"name": "중소벤처기업부", "url": "https://www.korea.kr/rss/dept_mss.xml",
     "attribution": "출처: 중소벤처기업부, 공공누리"},
    {"name": "국토교통부", "url": "https://www.korea.kr/rss/dept_molit.xml",
     "attribution": "출처: 국토교통부, 공공누리"},
    {"name": "과학기술정보통신부", "url": "https://www.korea.kr/rss/dept_msit.xml",
     "attribution": "출처: 과학기술정보통신부, 공공누리"},
    {"name": "고용노동부", "url": "https://www.korea.kr/rss/dept_moel.xml",
     "attribution": "출처: 고용노동부, 공공누리"},
    {"name": "공정거래위원회", "url": "https://www.korea.kr/rss/dept_ftc.xml",
     "attribution": "출처: 공정거래위원회, 공공누리"},
    {"name": "농림축산식품부", "url": "https://www.korea.kr/rss/dept_mafra.xml",
     "attribution": "출처: 농림축산식품부, 공공누리"},
]

# 금융·증시와 직접 관련도가 높은 핵심 부처(기본 활성). 나머지는 운영자가 켤 수 있음.
CORE_SOURCE_NAMES = {"정책브리핑", "기획재정부", "금융위원회",
                     "산업통상자원부", "중소벤처기업부", "공정거래위원회"}


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _parse_date(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    if not s:
        return None
    # RFC822(RSS 표준)
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    # ISO/기타 형식 폴백
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 2].strip(), fmt).replace(tzinfo=KST)
        except Exception:
            continue
    return None


class PolicyNewsProvider:
    """정부정책 RSS 수집. 키 불필요(네트워크만 되면 enabled).
    제목+링크+출처만 반환(본문 미복제)."""

    def __init__(self, *, transport=None, timeout: float = 8.0,
                 max_items_per_source: int = 8, sources: Optional[list] = None,
                 all_sources: bool = False) -> None:
        self.transport = transport          # 테스트 주입(없으면 urllib)
        self.timeout = timeout
        self.max_items = max_items_per_source
        if sources is not None:
            self.sources = sources
        elif all_sources:
            self.sources = SOURCES
        else:
            # 기본: 금융·증시 관련도 높은 핵심 부처만(부하·노이즈 감소)
            self.sources = [s for s in SOURCES if s["name"] in CORE_SOURCE_NAMES]
        self.enabled = True
        self.last_error: Optional[str] = None

    def _fetch_text(self, url: str) -> Optional[str]:
        if self.transport is not None:
            return self.transport(url)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (stock_reco)"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return None
                return resp.read().decode("utf-8", "replace")
        except Exception as e:
            self.last_error = str(e)
            return None

    def parse_rss(self, text: str, source: dict, now: datetime) -> list[dict]:
        """RSS XML → 정책 항목(제목+링크+출처만). 본문(description)은 저장하지 않는다."""
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []
        out: list[dict] = []
        for item in root.iter("item"):
            title = _strip_tags(item.findtext("title") or "")
            link = (item.findtext("link") or "").strip()
            if not title or not link:
                continue
            pub = _parse_date(item.findtext("pubDate") or item.findtext("date") or "")
            if pub is not None and pub.timestamp() > now.timestamp() + 3600:
                continue  # 미래 항목 제외
            out.append({
                "title": title,                       # 제목만(본문 복제 안 함)
                "link": link,                         # 원문 링크
                "published_at": pub.isoformat() if pub else "",
                "source": "정책",                      # 피드 종류 태그
                "publisher": source["name"],          # 기관명
                "attribution": source["attribution"], # 공공누리 출처표기
            })
            if len(out) >= self.max_items:
                break
        return out

    def fetch_all(self, now: datetime) -> list[dict]:
        """모든 소스에서 수집해 합치고 최신순 정렬."""
        items: list[dict] = []
        for src in self.sources:
            text = self._fetch_text(src["url"])
            if not text:
                continue
            try:
                items.extend(self.parse_rss(text, src, now))
            except Exception:
                continue
        items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        return items

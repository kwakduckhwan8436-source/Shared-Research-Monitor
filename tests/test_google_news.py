"""구글 뉴스 RSS 파싱 회귀 — 제목/언론사 분리, 미래기사 제외, 형식 일치."""
from datetime import datetime, timezone
from app.providers.google_news import GoogleNewsProvider, _strip_tags, _parse_rfc822

NOW = datetime(2026, 6, 21, 5, 0, tzinfo=timezone.utc)

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>삼성전자 자사주 매입 결정 - 한국경제</title><link>https://news.google.com/rss/articles/ABC</link>
<pubDate>Fri, 20 Jun 2025 05:30:00 GMT</pubDate><description>&lt;a href="x"&gt;본문&lt;/a&gt;</description>
<source url="https://hankyung.com">한국경제</source></item>
<item><title>코스닥 중소형주 급등 - 머니투데이</title><link>https://news.google.com/rss/articles/DEF</link>
<pubDate>Fri, 20 Jun 2025 04:00:00 GMT</pubDate><source url="https://mt.co.kr">머니투데이</source></item>
<item><title>미래기사</title><link>x</link><pubDate>Mon, 01 Jan 2035 00:00:00 GMT</pubDate><source url="x">X</source></item>
</channel></rss>"""


def test_parse_basic():
    p = GoogleNewsProvider()
    items = p.parse_rss(RSS, "증시", NOW)
    assert len(items) == 2                       # 미래기사 제외
    assert items[0]["title"] == "삼성전자 자사주 매입 결정"
    assert items[0]["publisher"] == "한국경제"
    assert items[0]["provider"] == "google" and items[0]["source"] == "news"
    assert items[0]["link"].startswith("https://news.google.com/")


def test_fields_match_naver_shape():
    p = GoogleNewsProvider()
    it = p.parse_rss(RSS, "증시", NOW)[0]
    for k in ("title", "published_at", "sentiment", "events", "risk_flags", "source", "link"):
        assert k in it


def test_transport_injection_and_fetch():
    p = GoogleNewsProvider(transport=lambda q: RSS)
    items = p.fetch_news("증시", NOW)
    assert len(items) == 2


def test_bad_xml_returns_empty():
    p = GoogleNewsProvider()
    assert p.parse_rss("<not xml", "q", NOW) == []


def test_helpers():
    assert _strip_tags("<a href='x'>hi</a> there") == "hi there"
    assert _parse_rfc822("") is None
    d = _parse_rfc822("Fri, 20 Jun 2025 05:30:00 GMT")
    assert d is not None and d.year == 2025


def test_no_key_needed():
    p = GoogleNewsProvider()
    assert p.enabled is True            # 키 불필요

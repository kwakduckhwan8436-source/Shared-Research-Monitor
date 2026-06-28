"""네이버 뉴스 검색 API provider 테스트 — HTML 제거·pubDate 파싱·감성."""
from datetime import datetime, timezone

from app.providers.naver_news import NaverNewsProvider, _strip_html, _parse_pubdate
from app.providers.kis import HttpTransport

NOW = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)

NEWS_OK = {"items": [
    {"title": "<b>삼성전자</b>, 신고가 경신&quot;실적 개선&quot;",
     "description": "메모리 수요 회복으로 영업이익 증가",
     "link": "https://n.news/1", "pubDate": "Thu, 18 Jun 2026 09:30:00 +0900"},
    {"title": "삼성전자 유상증자 우려",
     "description": "자금 조달 관련", "link": "https://n.news/2",
     "pubDate": "Wed, 17 Jun 2026 15:00:00 +0900"},
    {"title": "미래 기사(제외돼야 함)", "description": "",
     "link": "x", "pubDate": "Sat, 20 Jun 2026 09:00:00 +0900"},  # now=06-19 이후
]}


class FakeT(HttpTransport):
    def __init__(self, resp=NEWS_OK, status=200):
        self.resp = resp; self.status = status; self.last_params = None
    def get(self, url, headers, params):
        self.last_params = params
        return self.status, self.resp
    def post(self, url, headers, body): return 404, {}
    def post_form(self, url, headers, data): return 404, {}


def test_strip_html():
    assert _strip_html("<b>삼성</b>전자&quot;A&quot;") == '삼성전자"A"'
    assert _strip_html("&amp;&lt;tag&gt;") == "&<tag>"


def test_parse_pubdate():
    d = _parse_pubdate("Thu, 18 Jun 2026 09:30:00 +0900")
    assert d is not None and d.year == 2026 and d.month == 6 and d.day == 18
    assert _parse_pubdate("garbage") is None


def test_disabled_without_keys():
    p = NaverNewsProvider("", "", transport=FakeT())
    assert p.enabled is False
    assert p.fetch_news("삼성전자", NOW) == []


def test_news_parsed_and_sorted():
    p = NaverNewsProvider("id", "secret", transport=FakeT())
    items = p.fetch_news("삼성전자", NOW)
    assert len(items) == 2                       # 미래 기사 1건 제외
    assert items[0]["title"] == '삼성전자, 신고가 경신"실적 개선"'   # HTML 제거
    assert items[0]["source"] == "news"
    assert all(it.get("link") for it in items)
    # 유상증자 기사 -> 리스크 플래그
    유증 = next(i for i in items if "유상증자" in i["title"])
    assert "유증" in 유증["risk_flags"]


def test_query_uses_sort_date():
    t = FakeT()
    NaverNewsProvider("id", "secret", transport=t).fetch_news("삼성전자", NOW)
    assert t.last_params["sort"] == "date"       # 최신순(실시간)
    assert t.last_params["query"] == "삼성전자"


def test_http_error_returns_empty():
    p = NaverNewsProvider("id", "secret", transport=FakeT(status=429))
    assert p.fetch_news("삼성전자", NOW) == []

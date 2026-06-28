"""News provider — DART 공시(공식 API) 기반 뉴스/이벤트 감성·리스크.

소스 선택 이유: 유증·횡령·감자·계약·실적 등 시장 리스크/이벤트의 1차 출처는 공시다.
DART Open API(list.json)는 공식·공개라 크롤링/약관 이슈가 없고 신호가 정확하다.
(언론 기사 수집은 선택적 후속 소스로 확장 가능 — 같은 DataProvider 계약.)

담당 kind: news. 흐름: 최근 공시 수집 -> 제목 분류(이벤트/리스크) + 감성 분석 -> DataPoint.

핵심:
- 각 항목 published_at 은 공시 접수일. published_at <= now 만 사용(lookahead 차단).
- NEWS DataPoint 의 as_of = now: '지금 시점의 최근 공시 스냅샷'(피드는 매 갱신마다 최신).
  -> 최근 공시가 없으면 items=[] (리스크 없음으로 RiskFlags 발화, NewsSentiment 는 abstain).
- DART status '013'(데이터 없음)은 에러가 아니라 '공시 없음'으로 처리.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.clock import KST
from app.core.errors import ProviderError
from app.data.schema import DataPoint, Kind
from app.providers.base import DataProvider
from app.providers.kis import UrllibTransport, HttpTransport
from app.llm import sentiment as sentiment_mod

DART_BASE = "https://opendart.fss.or.kr/api"

# 공시 제목 -> 리스크 플래그 (공식 보고서명 부분일치, 고정밀)
_RISK_PATTERNS = {
    "유상증자": "유증", "전환사채": "CB발행", "신주인수권부사채": "BW발행",
    "횡령": "횡령·배임", "배임": "횡령·배임", "감자": "감자",
    "상장폐지": "상장폐지위험", "상장적격성": "상장폐지위험",
    "불성실공시": "불성실공시", "영업정지": "영업정지", "소송": "소송",
    "관리종목": "관리종목", "회생절차": "회생절차",
}
# 공시 제목 -> 이벤트 태그
_EVENT_PATTERNS = {
    "공급계약": "공급계약", "단일판매": "공급계약", "수주": "수주",
    "잠정실적": "실적", "영업(잠정)실적": "실적", "결산실적": "실적", "영업실적": "실적",
    "자기주식취득": "자사주매입", "배당": "배당", "무상증자": "무상증자",
}


def _match(name: str, table: dict[str, str]) -> set[str]:
    return {v for k, v in table.items() if k in name}


class NewsProvider(DataProvider):
    name = "news"
    supported_kinds = (Kind.NEWS.value,)

    def __init__(self, api_key: str, corp_code_map: Optional[dict[str, str]] = None, *,
                 llm_client: Optional[Any] = None,
                 transport: Optional[HttpTransport] = None, lookback_days: int = 30):
        self.api_key = api_key
        self.corp_code_map = corp_code_map or {}
        self.llm = llm_client
        self.transport = transport or UrllibTransport()
        self.lookback_days = lookback_days

    def _corp_code(self, symbol: str) -> str:
        code = self.corp_code_map.get(symbol)
        if not code:
            raise ProviderError(f"DART corp_code 미등록: {symbol} (corpCode.xml 매핑 필요)")
        return code

    def _disclosures(self, corp_code: str, now: datetime) -> list[dict]:
        if not self.api_key:
            raise ProviderError("DART api_key 누락 (.env 의 DART_API_KEY)")
        end = now.astimezone(KST).date()
        start = end - timedelta(days=self.lookback_days)
        status, body = self.transport.get(f"{DART_BASE}/list.json", {}, {
            "crtfc_key": self.api_key, "corp_code": corp_code,
            "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
            "page_count": "100",
        })
        if status != 200:
            raise ProviderError(f"DART HTTP {status} (news)")
        st = str(body.get("status", "000"))
        if st == "013":          # 조회된 데이터 없음 -> 공시 없음(에러 아님)
            return []
        if st != "000":
            raise ProviderError(f"DART status={st} {body.get('message','')} (news)")
        return body.get("list", [])

    @staticmethod
    def _pub_dt(rcept_dt: str) -> Optional[datetime]:
        if not rcept_dt or len(rcept_dt) != 8:
            return None
        y, m, d = int(rcept_dt[:4]), int(rcept_dt[4:6]), int(rcept_dt[6:8])
        return datetime(y, m, d, 16, 0, tzinfo=KST).astimezone(timezone.utc)

    def fetch(self, symbol: str, kind: str, *, now: datetime) -> Optional[DataPoint]:
        if kind != Kind.NEWS.value:
            return None
        corp = self._corp_code(symbol)
        rows = self._disclosures(corp, now)
        items = []
        for it in rows:
            pub = self._pub_dt(it.get("rcept_dt", ""))
            if pub is None or pub > now:        # lookahead 차단
                continue
            title = it.get("report_nm", "").strip()
            if not title:
                continue
            analysis = sentiment_mod.analyze(
                {"symbol": symbol, "title": title, "body": title,
                 "published_at": pub.isoformat(), "source": "dart"},
                client=self.llm,
            )
            risk = sorted(set(analysis["risk_flags"]) | _match(title, _RISK_PATTERNS))
            events = sorted(set(analysis["events"]) | _match(title, _EVENT_PATTERNS))
            sentiment = analysis["sentiment"]
            if risk and sentiment > -0.3:        # 리스크 공시는 부정으로 본다(공식 이벤트 기준)
                sentiment = -0.5
            rcept_no = it.get("rcept_no", "")
            link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""
            items.append({
                "title": title, "published_at": pub.isoformat(),
                "sentiment": sentiment, "events": events, "risk_flags": risk,
                "source": "공시", "link": link,
            })
        # as_of = now: 지금 시점의 최근 공시 스냅샷(피드)
        return DataPoint(symbol, Kind.NEWS.value, {"items": items},
                         as_of=now, fetched_at=now, source="dart-news")

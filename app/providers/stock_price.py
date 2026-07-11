"""금융위원회 주식시세정보 API provider.

공공데이터포털(data.go.kr)의 getStockPriceInfo 로 전일 종가·시가총액을 가져온다.
목적: PER/PBR 계산(시세가 아니라 '지표'로 활용). 이용허락범위 제한 없음(상업 가능), 무료.

주의:
- 실시간 아님. 기준일 하루 뒤 오후 1시 이후 갱신(전일 종가).
- basDt(기준일)에 오늘 날짜를 넣으면 데이터가 비어 있을 수 있음 → 최근 영업일을 역으로 탐색.
- 응답 필드: srtnCd(종목코드6자리), itmsNm(종목명), clpr(종가),
  mrktTotAmt(시가총액), lstgStCnt(상장주식수), mrktCtg(시장구분).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

KST = timezone(timedelta(hours=9))
BASE_URL = ("https://apis.data.go.kr/1160100/service/"
            "GetStockSecuritiesInfoService/getStockPriceInfo")


def _to_int(s) -> Optional[int]:
    try:
        return int(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


class StockPriceProvider:
    """전일 종가·시가총액 조회. 전종목 일괄 수집 지원."""

    def __init__(self, service_key: str, *, transport=None, timeout: float = 15.0) -> None:
        # 키가 인코딩된 형태(%2B 등)일 수 있으니 그대로 쓰되, 요청 시 안전 처리
        self.service_key = (service_key or "").strip()
        self.transport = transport      # 테스트 주입
        self.timeout = timeout
        self.last_error: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.service_key)

    def _get(self, params: dict) -> Optional[dict]:
        # serviceKey는 이미 인코딩됐을 수 있으므로 수동으로 붙이고 나머지만 인코딩
        qs = urllib.parse.urlencode({k: v for k, v in params.items()
                                     if k != "serviceKey"})
        url = f"{BASE_URL}?serviceKey={self.service_key}&{qs}"
        if self.transport is not None:
            raw = self.transport(url)
            if raw is None:
                return None
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        req = urllib.request.Request(url, headers={"User-Agent": "stock_reco/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    self.last_error = f"HTTP {resp.status}"
                    return None
                body = resp.read().decode("utf-8", "replace")
                return json.loads(body)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return None

    @staticmethod
    def _recent_basdt(now: datetime, back: int) -> str:
        """기준일 문자열(YYYYMMDD). now 로부터 back 일 전(주말/휴일 감안해 여유)."""
        d = now.astimezone(KST).date() - timedelta(days=back)
        return d.strftime("%Y%m%d")

    def fetch_all_prices(self, now: Optional[datetime] = None,
                         *, max_rows: int = 3000) -> dict[str, dict]:
        """전종목 종가·시가총액을 한 번에. 반환 {종목코드6자리: {...}}.
        가장 최근에 데이터가 있는 영업일을 자동 탐색(최대 7일 역순)."""
        self.last_error = None
        if not self.enabled:
            self.last_error = "주식시세 API 키가 없습니다."
            return {}
        now = now or datetime.now(KST)
        # 최근 영업일 탐색: 1일 전부터 최대 7일 전까지 시도
        for back in range(1, 8):
            bas = self._recent_basdt(now, back)
            body = self._get({
                "serviceKey": self.service_key,
                "resultType": "json",
                "numOfRows": str(max_rows),
                "pageNo": "1",
                "basDt": bas,
            })
            rows = self._extract_items(body)
            if rows:
                out: dict[str, dict] = {}
                for it in rows:
                    code = (it.get("srtnCd") or "").strip()
                    # srtnCd 는 앞에 'A' 붙거나 6자리일 수 있음 → 끝 6자리 숫자만
                    code6 = "".join(ch for ch in code if ch.isdigit())[-6:]
                    if len(code6) != 6:
                        continue
                    clpr = _to_int(it.get("clpr"))
                    mcap = _to_int(it.get("mrktTotAmt"))
                    shares = _to_int(it.get("lstgStCnt"))
                    out[code6] = {
                        "name": (it.get("itmsNm") or "").strip(),
                        "close": clpr,
                        "market_cap": mcap,
                        "shares": shares,
                        "market": (it.get("mrktCtg") or "").strip(),
                        "bas_dt": bas,
                    }
                if out:
                    return out
        self.last_error = "최근 7일 내 시세 데이터를 찾지 못했습니다."
        return {}

    @staticmethod
    def _extract_items(body: Optional[dict]) -> list:
        if not body:
            return []
        try:
            resp = body.get("response", {})
            header = resp.get("header", {})
            code = header.get("resultCode")
            if code not in (None, "00", "0"):
                return []
            items = resp.get("body", {}).get("items", {})
            if not items:
                return []
            item = items.get("item")
            if item is None:
                return []
            if isinstance(item, dict):
                return [item]
            return list(item)
        except (AttributeError, TypeError):
            return []

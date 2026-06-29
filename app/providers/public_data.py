"""공공데이터포털(data.go.kr) 금융 공공데이터 provider.

수집 대상(모두 공적 데이터):
  · 공시정보: 배당·유상/무상증자·주주총회·자기주식·합병·분할 등 (캘린더/동향용)
  · 주식발행정보: 의무보호예수 반환(해제) 정보
  · 기업기본정보: 대표자·업종·계열사 등

법적 안전:
  · 모두 공공기관(금융위원회/한국예탁결제원)이 개방한 공공데이터.
  · 단, 데이터별 공공누리 유형이 다름(일부 2유형=상업적 이용 시 별도 계약).
    상업적(광고) 운영 시 각 데이터 약관을 반드시 확인해 사용할 것.
  · 실시간 아님: 기준일자로부터 영업일 +1일 오후 갱신(전일 기준 데이터).
  · 사실 정보만 표시하고 매매를 추천하지 않는다.

⚠ 네트워크 필요(컨테이너 차단 → 사용자 서버에서 동작). 실패 시 graceful(빈 리스트).
  엔드포인트 경로/파라미터는 포털 명세 기준이며, 기관 사정으로 바뀔 수 있어
  운영자가 ENDPOINTS 를 조정할 수 있다.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Optional

KST = timezone(timedelta(hours=9))

# 공공데이터포털 금융 API 베이스(서비스별 경로). 운영자가 실제 명세에 맞게 조정 가능.
BASE = "https://apis.data.go.kr/1160100/service"
ENDPOINTS = {
    # 공시정보 서비스(배당/증자/자기주식 등) — getDisclosure* 오퍼레이션
    "dividend":   BASE + "/GetDisclInfoService/getDividendInfo",
    "rights":     BASE + "/GetDisclInfoService/getCapitalIncreaseInfo",   # 유/무상증자
    "treasury":   BASE + "/GetDisclInfoService/getTreasuryStockInfo",     # 자기주식
    # 주식발행정보 서비스 — 보호예수 반환(해제)
    "lockup":     BASE + "/GetStocIssuInfoService/getMandatoryDepositReturnInfo",
    # 기업기본정보
    "corp":       BASE + "/CorpBasicInfoService/getCorpOutline",
    # 기업재무정보(요약재무제표) — 매출/영업이익/순이익/자산/부채 등
    "finance":    BASE + "/GetFinaStatInfoService/getSummFinaStat",
    # 주식배당정보(상세) — 배당기준일/지급일/배당률/주식종류 등
    "dividend_detail": BASE + "/GetStocActInfoService/getStockDividend",
    # 주식권리일정(배당·증자·교환·감자 등 권리행사 일정)
    "rights_schedule": BASE + "/GetStocActInfoService/getRightSchedule",
}

# 운영자가 발급 후 Swagger 명세에서 확인한 정확한 URL로 덮어쓸 수 있다.
# 예: RECO_CORP_EP_DIVIDEND=https://apis.data.go.kr/1160100/service/GetDisclInfoService/getDividendInfo
_ENV_KEYS = {
    "dividend": "RECO_CORP_EP_DIVIDEND", "rights": "RECO_CORP_EP_RIGHTS",
    "treasury": "RECO_CORP_EP_TREASURY", "lockup": "RECO_CORP_EP_LOCKUP",
    "corp": "RECO_CORP_EP_CORP",
    "finance": "RECO_CORP_EP_FINANCE",
    "dividend_detail": "RECO_CORP_EP_DIVDETAIL",
    "rights_schedule": "RECO_CORP_EP_RIGHTSCHED",
}


def _resolve_endpoint(kind: str) -> Optional[str]:
    """환경변수 오버라이드가 있으면 우선, 없으면 기본 ENDPOINTS."""
    env = _ENV_KEYS.get(kind)
    if env:
        val = os.getenv(env, "").strip()
        if val:
            return val
    return ENDPOINTS.get(kind)


def _attribution() -> str:
    return "출처: 금융위원회·한국예탁결제원 (공공데이터포털 data.go.kr)"


class PublicDataProvider:
    """공공데이터포털 금융 데이터 수집. 서비스키 1개로 여러 오퍼레이션 호출."""

    def __init__(self, service_key: str, *, transport=None, timeout: float = 8.0,
                 max_rows: int = 50) -> None:
        self.key = service_key
        self.transport = transport      # 테스트 주입: fn(endpoint, params)->text
        self.timeout = timeout
        self.max_rows = max_rows
        self.enabled = bool(service_key)
        self.last_error: Optional[str] = None

    def _fetch(self, endpoint: str, params: dict) -> Optional[str]:
        if self.transport is not None:
            return self.transport(endpoint, params)
        q = dict(params)
        q.setdefault("serviceKey", self.key)
        q.setdefault("numOfRows", self.max_rows)
        q.setdefault("pageNo", 1)
        q.setdefault("resultType", "json")
        url = endpoint + "?" + urllib.parse.urlencode(q, safe="%")
        req = urllib.request.Request(url, headers={"User-Agent": "stock_reco/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return None
                return resp.read().decode("utf-8", "replace")
        except Exception as e:
            self.last_error = str(e)
            return None

    @staticmethod
    def parse_items(text: str) -> list[dict]:
        """공공데이터포털 표준 응답(JSON 또는 XML)에서 item 리스트를 뽑는다."""
        if not text:
            return []
        text = text.strip()
        # JSON 우선
        if text.startswith("{"):
            try:
                data = json.loads(text)
                body = (((data or {}).get("response") or {}).get("body") or {})
                items = (body.get("items") or {})
                row = items.get("item") if isinstance(items, dict) else items
                if row is None:
                    return []
                return row if isinstance(row, list) else [row]
            except Exception:
                return []
        # XML 폴백
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []
        out = []
        for it in root.iter("item"):
            d = {child.tag: (child.text or "").strip() for child in it}
            if d:
                out.append(d)
        return out

    # ---- 개별 수집(원시 dict 리스트 반환; 호출부에서 표준화) ----
    def fetch(self, kind: str, params: Optional[dict] = None) -> list[dict]:
        ep = _resolve_endpoint(kind)
        if not ep or not self.enabled:
            return []
        text = self._fetch(ep, params or {})
        return self.parse_items(text)

    def diagnose(self, kind: str, params: Optional[dict] = None) -> dict:
        """진단: 해당 종류의 엔드포인트를 실제 호출해 성공/실패와 응답 앞부분을 돌려준다.
        키 값은 노출하지 않는다(URL에서 serviceKey 제거)."""
        ep = _resolve_endpoint(kind)
        if not ep:
            return {"kind": kind, "ok": False, "reason": "엔드포인트 미정의"}
        if not self.enabled:
            return {"kind": kind, "ok": False, "reason": "DATA_GO_KR_KEY 미설정"}
        text = self._fetch(ep, params or {})
        if text is None:
            return {"kind": kind, "ok": False, "endpoint": ep,
                    "reason": "응답 없음(네트워크/URL/키 점검)",
                    "last_error": self.last_error}
        items = self.parse_items(text)
        # 포털 표준 에러코드 추출 시도
        head = text.strip()[:300]
        ok = len(items) > 0
        result = {"kind": kind, "ok": ok, "endpoint": ep,
                  "item_count": len(items), "sample_head": head}
        if not ok:
            # 흔한 실패: SERVICE_KEY_IS_NOT_REGISTERED_ERROR, NODATA_ERROR 등
            for code in ["SERVICE_KEY", "NODATA", "LIMITED_NUMBER",
                         "HTTP_ERROR", "INVALID", "UNREGISTERED", "DEADLINE"]:
                if code in text.upper():
                    result["hint"] = code + " (포털 에러코드 확인)"
                    break
            result.setdefault("hint", "응답은 왔으나 item이 비었습니다(주소/파라미터/날짜 확인).")
        return result

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
    # 공시정보 서비스(배당/증자/자기주식 등)
    "dividend":   BASE + "/GetStocDiviInfoService/getDiviInfo",            # 주식배당정보(확인됨)
    "rights":     BASE + "/GetDisclInfoService/getCapitalIncreaseInfo",    # 유/무상증자(추정)
    "treasury":   BASE + "/GetDisclInfoService/getTreasuryStockInfo",      # 자기주식(추정)
    # 주식발행정보 서비스 — 의무보호예수 반환(해제)
    "lockup":     BASE + "/GetStocIssuInfoService/getMandatoryDepositReturnInfo",
    # 기업기본정보(확인됨: V2)
    "corp":       BASE + "/GetCorpBasicInfoService_V2/getCorpOutline_V2",
    # 기업재무정보(요약재무제표)(추정)
    "finance":    BASE + "/GetFinaStatInfoService/getSummFinaStat",
    # 주식배당정보(상세)(확인됨)
    "dividend_detail": BASE + "/GetStocDiviInfoService/getDiviInfo",
    # 주식권리일정(확인됨)
    "rights_schedule": BASE + "/GetStocRighScheService/getRighExerReasSche",
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
        # serviceKey 를 제외한 파라미터만 표준 인코딩
        q = dict(params)
        q.setdefault("numOfRows", self.max_rows)
        q.setdefault("pageNo", 1)
        q.setdefault("resultType", "json")
        query = urllib.parse.urlencode(q)
        # serviceKey 는 키 종류(Encoding/Decoding)를 자동 판별해 한 번만 인코딩:
        #  · 이미 인코딩된 Encoding 키(%2B, %2F, %3D 포함)는 그대로 사용
        #  · 원본 Decoding 키(+,/,= 포함)는 quote 로 인코딩
        key = (self.key or "").strip()
        if "%" in key and ("%2B" in key.upper() or "%2F" in key.upper()
                           or "%3D" in key.upper()):
            key_enc = key                          # 이미 인코딩된 키(그대로)
        else:
            key_enc = urllib.parse.quote(key, safe="")  # 원본 키 → 인코딩
        url = endpoint + "?serviceKey=" + key_enc + "&" + query
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

    # 공공데이터포털 표준 에러코드 → 사람이 읽고 바로 행동할 진단
    _ERROR_GUIDE = [
        ("SERVICE_KEY_IS_NOT_REGISTERED", "register",
         "키가 아직 등록 전입니다. 활용신청 직후라면 1시간 정도 뒤 다시 시도하세요. "
         "또는 Encoding 키 대신 Decoding 키로 바꿔보세요."),
        ("SERVICE KEY IS NOT REGISTERED", "register",
         "키가 아직 등록 전입니다. 활용신청 후 1시간 대기, 또는 Decoding 키로 교체."),
        ("NODATA", "nodata",
         "주소·키는 정상인데 조회된 자료가 없습니다(주말/공휴일이거나 해당 조건에 데이터 없음). "
         "평일 오후에 다시 확인하세요."),
        ("LIMITED_NUMBER_OF_SERVICE_REQUESTS", "limit",
         "일일 호출 한도(개발계정 1만건)를 초과했습니다. 내일 다시 시도하거나 운영계정으로 상향하세요."),
        ("HTTP_ERROR", "http", "포털 서버 응답 오류입니다. 잠시 후 다시 시도하세요."),
        ("INVALID_REQUEST_PARAMETER", "param",
         "요청 파라미터가 맞지 않습니다(엔드포인트 주소나 변수명 점검 필요)."),
        ("UNREGISTERED_IP", "ip",
         "등록되지 않은 IP에서의 요청입니다. 마이페이지에서 IP 제한을 해제하거나 서버 IP를 등록하세요."),
        ("DEADLINE_HAS_EXPIRED", "expired", "활용신청 기간이 만료되었습니다. 연장 신청하세요."),
        ("SERVICE_ACCESS_DENIED", "denied",
         "이 기능에 대한 접근 권한이 없습니다. 해당 데이터를 활용신청했는지 확인하세요."),
    ]

    def diagnose(self, kind: str, params: Optional[dict] = None) -> dict:
        """진단: 엔드포인트를 실제 호출해 성공/실패와 '한글 해결책'을 돌려준다.
        키 값은 노출하지 않는다."""
        ep = _resolve_endpoint(kind)
        if not ep:
            return {"kind": kind, "ok": False, "advice": "엔드포인트가 정의되지 않았습니다."}
        if not self.enabled:
            return {"kind": kind, "ok": False,
                    "advice": "DATA_GO_KR_KEY 가 설정되지 않았습니다. Render Environment 에 키를 넣으세요."}
        text = self._fetch(ep, params or {})
        if text is None:
            # 네트워크 자체 실패
            le = (self.last_error or "")
            advice = "서버에서 공공데이터포털로 연결하지 못했습니다(네트워크/주소 문제)."
            if "timed out" in le or "timeout" in le.lower():
                advice = "응답 시간 초과입니다. 잠시 후 다시 시도하세요(포털 지연 가능)."
            elif "Name or service not known" in le or "getaddrinfo" in le:
                advice = "주소(도메인)를 찾지 못했습니다. 엔드포인트 URL을 점검하세요."
            elif "certificate" in le.lower() or "SSL" in le:
                advice = "보안 인증서 오류입니다. http/https 또는 URL을 점검하세요."
            return {"kind": kind, "ok": False, "advice": advice,
                    "raw_error": le[:160], "endpoint": ep}
        up = text.upper()
        items = self.parse_items(text)
        if items:
            return {"kind": kind, "ok": True, "item_count": len(items),
                    "advice": "정상 작동합니다."}
        # 응답은 왔으나 item 없음 → 에러코드 해석
        for code, tag, advice in self._ERROR_GUIDE:
            if code in up:
                return {"kind": kind, "ok": False, "code": tag, "advice": advice}
        # 에러코드도 없고 item도 없음
        # resultCode 00(정상)인데 빈 경우 = 진짜 데이터 없음(주말 등)
        if "00" in up and ("RESULTCODE" in up or "RESULT_CODE" in up):
            return {"kind": kind, "ok": False, "code": "empty",
                    "advice": "정상 응답이지만 자료가 비어 있습니다. 주말/공휴일이거나 "
                              "해당 조건에 데이터가 없을 수 있습니다(평일 오후 재확인)."}
        return {"kind": kind, "ok": False, "code": "unknown",
                "advice": "응답은 왔으나 자료를 해석하지 못했습니다. 엔드포인트 주소나 "
                          "응답 형식이 예상과 다를 수 있습니다(주소 보정 필요).",
                "sample": text.strip()[:160]}

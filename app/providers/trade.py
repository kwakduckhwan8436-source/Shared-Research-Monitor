"""관세청 품목별 국가별 수출입실적 provider (공공데이터포털).

수출은 한국 증시의 선행지표 — 반도체 수출↑ → 삼성전자·SK하이닉스 같은 연결.
API: http://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList
- 무료, 이용허락범위 제한 없음(상업 이용 가능), 개발계정 10,000회/일
- 응답: XML (JSON 아님)
- 파라미터: serviceKey, strtYymm(YYYYMM), endYymm(YYYYMM), hsSgn(품목코드), cntyCd(국가코드 필수)
- 데이터: 월 단위, 매월 15일경 전월 자료 갱신(실시간 아님)

주의:
- cntyCd 가 필수라 '전체'를 한 번에 못 받음 → 주요 교역국을 각각 조회해 합산.
- 합산값은 주요국 기준이라 국가 전체 수출입과는 다를 수 있음(그 점을 UI에 명시).
"""
from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

BASE_URL = "http://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"

# 증시 섹터와 직결되는 주요 품목(HS 4단위) — 관련 종목 힌트 포함
TRADE_ITEMS = [
    {"hs": "8542", "name": "반도체(집적회로)", "icon": "💾", "sector": "반도체",
     "stocks": ["삼성전자", "SK하이닉스", "한미반도체"]},
    {"hs": "8703", "name": "승용차", "icon": "🚗", "sector": "자동차",
     "stocks": ["현대차", "기아"]},
    {"hs": "8708", "name": "자동차부품", "icon": "🔧", "sector": "자동차부품",
     "stocks": ["현대모비스", "현대위아"]},
    {"hs": "8901", "name": "선박", "icon": "🚢", "sector": "조선",
     "stocks": ["HD한국조선해양", "삼성중공업", "한화오션"]},
    {"hs": "2710", "name": "석유제품", "icon": "🛢️", "sector": "정유",
     "stocks": ["SK이노베이션", "S-Oil"]},
    {"hs": "8507", "name": "2차전지(축전지)", "icon": "🔋", "sector": "2차전지",
     "stocks": ["LG에너지솔루션", "삼성SDI"]},
    {"hs": "8517", "name": "통신기기", "icon": "📱", "sector": "전자",
     "stocks": ["삼성전자", "LG이노텍"]},
    {"hs": "3004", "name": "의약품", "icon": "💊", "sector": "바이오",
     "stocks": ["셀트리온", "삼성바이오로직스"]},
    {"hs": "7208", "name": "철강(열연)", "icon": "🏗️", "sector": "철강",
     "stocks": ["POSCO홀딩스", "현대제철"]},
    {"hs": "3907", "name": "석유화학(폴리머)", "icon": "⚗️", "sector": "화학",
     "stocks": ["LG화학", "롯데케미칼"]},
    {"hs": "8486", "name": "반도체 장비", "icon": "🏭", "sector": "반도체장비",
     "stocks": ["원익IPS", "주성엔지니어링"]},
    {"hs": "3304", "name": "화장품", "icon": "💄", "sector": "화장품",
     "stocks": ["아모레퍼시픽", "한국콜마"]},
]

# 주요 교역국(수출 비중 큰 순) — 합산 대상
TRADE_COUNTRIES = [
    {"cd": "CN", "name": "중국"},
    {"cd": "US", "name": "미국"},
    {"cd": "VN", "name": "베트남"},
    {"cd": "HK", "name": "홍콩"},
    {"cd": "JP", "name": "일본"},
    {"cd": "TW", "name": "대만"},
    {"cd": "SG", "name": "싱가포르"},
    {"cd": "IN", "name": "인도"},
    {"cd": "DE", "name": "독일"},
    {"cd": "MX", "name": "멕시코"},
]


def _to_num(s) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


class TradeProvider:
    """관세청 품목별 국가별 수출입 조회."""

    def __init__(self, service_key: str, *, transport=None, timeout: float = 12.0) -> None:
        self.service_key = (service_key or "").strip()
        self.transport = transport      # 테스트 주입: (url) -> xml str
        self.timeout = timeout
        self.last_error: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.service_key)

    def _get_xml(self, params: dict) -> Optional[str]:
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if k != "serviceKey"})
        url = f"{BASE_URL}?serviceKey={self.service_key}&{qs}"
        if self.transport is not None:
            return self.transport(url)
        req = urllib.request.Request(url, headers={"User-Agent": "stock_reco/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    self.last_error = f"HTTP {resp.status}"
                    return None
                return resp.read().decode("utf-8", "replace")
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return None

    @staticmethod
    def _parse_items(xml_text: Optional[str]) -> list[dict]:
        """XML → item 목록. 구조가 달라도 <item> 을 모두 찾아 파싱."""
        if not xml_text:
            return []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []
        # 오류 응답 확인(resultCode 가 00이 아니면 무시)
        rc = root.find(".//resultCode")
        if rc is not None and (rc.text or "").strip() not in ("00", "0", ""):
            return []
        out = []
        for it in root.iter("item"):
            def g(tag):
                el = it.find(tag)
                return (el.text or "").strip() if el is not None and el.text else ""
            row = {
                "year": g("year"),
                "country": g("statCdCntnKor1"),
                "country_cd": g("statCd"),
                "item_name": g("statKor"),
                "hs": g("hsCd"),
                "exp_dlr": _to_num(g("expDlr")),
                "imp_dlr": _to_num(g("impDlr")),
                "exp_wgt": _to_num(g("expWgt")),
                "imp_wgt": _to_num(g("impWgt")),
                "balance": _to_num(g("balPayments")),
            }
            if row["year"] or row["hs"]:
                out.append(row)
        return out

    def item_trade(self, hs_code: str, country_cd: str,
                   start_yymm: str, end_yymm: str) -> list[dict]:
        """품목×국가의 월별 수출입. 조회기간은 1년 이내."""
        self.last_error = None
        if not self.enabled:
            self.last_error = "수출입 API 키가 없습니다."
            return []
        xml_text = self._get_xml({
            "serviceKey": self.service_key,
            "strtYymm": start_yymm, "endYymm": end_yymm,
            "hsSgn": hs_code, "cntyCd": country_cd,
        })
        return self._parse_items(xml_text)

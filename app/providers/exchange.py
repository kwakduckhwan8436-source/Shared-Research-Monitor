"""한국수출입은행 환율 provider — 원/달러, 원/엔, 원/유로 등.

법적 안전: 한국수출입은행이 공개하는 공공 환율 데이터. 환율은 주식 시세와 달리
거래소 독점 데이터가 아니라 공공기관이 고시하는 정보라 표시에 제약이 적다.
출처(한국수출입은행)를 표기한다.

엔드포인트(2025.6.25 도메인 변경): https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON
파라미터: authkey, searchdate(YYYYMMDD), data=AP01

⚠ 네트워크 필요(컨테이너 차단 → 사용자 서버). 주말/공휴일/장 시작 전엔 당일 고시가
  없을 수 있어 직전 영업일로 폴백한다. 실패 시 graceful(빈 리스트).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional

KST = timezone(timedelta(hours=9))
EXIM_URL = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"

# 화면에 보여줄 주요 통화(코드 → 표시명)
SHOW = {
    "USD": "미국 달러", "JPY(100)": "일본 엔(100)", "EUR": "유로",
    "CNH": "중국 위안", "GBP": "영국 파운드",
}


class ExchangeRateProvider:
    """수출입은행 환율 고시. 키 1개로 당일(또는 직전 영업일) 환율을 가져온다."""

    def __init__(self, auth_key: str, *, transport=None, timeout: float = 8.0) -> None:
        self.key = auth_key
        self.transport = transport      # 테스트 주입: fn(searchdate)->text
        self.timeout = timeout
        self.enabled = bool(auth_key)
        self.last_error: Optional[str] = None

    def _fetch(self, searchdate: str) -> Optional[str]:
        if self.transport is not None:
            return self.transport(searchdate)
        q = urllib.parse.urlencode({"authkey": self.key, "searchdate": searchdate,
                                    "data": "AP01"})
        req = urllib.request.Request(EXIM_URL + "?" + q,
                                     headers={"User-Agent": "stock_reco/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return None
                return resp.read().decode("utf-8", "replace")
        except Exception as e:
            self.last_error = str(e)
            return None

    def parse(self, text: str) -> list[dict]:
        """수출입은행 JSON → 주요 통화만 추려 표준화."""
        if not text:
            return []
        try:
            arr = json.loads(text)
        except Exception:
            return []
        if not isinstance(arr, list):
            return []
        out = []
        for r in arr:
            code = (r.get("cur_unit") or "").strip()
            if code not in SHOW:
                continue
            # deal_bas_r: 매매기준율(쉼표 제거 후 float)
            raw = (r.get("deal_bas_r") or "").replace(",", "").strip()
            try:
                val = float(raw)
            except Exception:
                continue
            out.append({
                "code": code, "name": SHOW[code], "rate": val,
                "cur_nm": (r.get("cur_nm") or "").strip(),
            })
        # 표시 순서(USD 먼저)
        order = list(SHOW.keys())
        out.sort(key=lambda x: order.index(x["code"]) if x["code"] in order else 99)
        return out

    def fetch_latest(self, now: Optional[datetime] = None) -> dict:
        """당일부터 최대 7일 거슬러 올라가며 고시가 있는 날을 찾는다."""
        now = now or datetime.now(KST)
        for back in range(0, 7):
            d = now - timedelta(days=back)
            text = self._fetch(d.strftime("%Y%m%d"))
            rows = self.parse(text or "")
            if rows:
                return {"date": d.strftime("%Y-%m-%d"), "rates": rows,
                        "attribution": "출처: 한국수출입은행"}
        return {"date": "", "rates": [],
                "attribution": "출처: 한국수출입은행",
                "error": "환율 고시를 불러오지 못했습니다(키/네트워크 점검)."}

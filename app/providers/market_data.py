"""해외 지수·원자재·금리 — 야후 파이낸스(비공식) 우선, 실패 시 stooq(무료) 폴백.

대상: 나스닥(^IXIC), S&P500(^GSPC), VIX(^VIX), WTI유가(CL=F), 미국채10년(^TNX).

⚠ 주의:
- 야후 파이낸스는 공식 무료 API가 종료되어 비공식 엔드포인트(query1.finance.yahoo.com)를
  사용한다. 약관 회색지대이며 간헐적 차단·형식 변경 가능 → 개인용 권장. 외부 공개/상업용은
  약관 확인 필요.
- 야후 실패 시 stooq CSV 로 폴백(값만, 등락은 제공 안 될 수 있음). 둘 다 실패하면 그 항목은 생략.
- 네트워크 필요. 파싱(야후 chart JSON / stooq CSV)은 fixture 로 검증되어 있다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# (표시명, 야후 심볼, stooq 심볼, 소수자리)
GLOBAL_SYMBOLS = [
    ("나스닥",    "^IXIC", "^ndq", 2),
    ("S&P500",   "^GSPC", "^spx", 2),
    ("VIX",      "^VIX",  "^vix", 2),
    ("WTI유가",  "CL=F",  "cl.f", 2),
    ("미국채10년", "^TNX",  "10usy.b", 3),
    ("원/달러",   "KRW=X", "usdkrw", 2),
]

# 국내지수 야후 폴백 (KIS 지수 조회 실패 시) — (표시명, 코드, 야후심볼)
DOMESTIC_SYMBOLS = [
    ("0001", "코스피",    "^KS11"),
    ("1001", "코스닥",    "^KQ11"),
    ("2001", "코스피200", "^KS200"),
]


class MarketDataProvider:
    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout

    # ----- 저수준 fetch (raw text) -----
    def _get_text(self, url: str) -> tuple[int, str]:
        req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                   "Accept": "application/json, text/csv, */*"},
                                     method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, ""
        except urllib.error.URLError:
            return 0, ""
        except Exception:
            return 0, ""

    # ----- 야후 파이낸스 (chart 엔드포인트) -----
    def parse_yahoo(self, text: str) -> Optional[dict]:
        """야후 chart JSON 텍스트 -> {value, change, change_pct}. 파싱 실패 시 None."""
        try:
            j = json.loads(text)
            res = (j.get("chart", {}).get("result") or [None])[0]
            if not res:
                return None
            meta = res.get("meta", {}) or {}
            price = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose")
            if prev in (None, 0):
                prev = meta.get("previousClose")
            if price is None or prev in (None, 0):
                return None
            chg = float(price) - float(prev)
            pct = chg / float(prev) * 100.0
            return {"value": round(float(price), 4), "change": round(chg, 4),
                    "change_pct": round(pct, 2), "source": "yahoo"}
        except Exception:
            return None

    def _yahoo(self, symbol: str) -> Optional[dict]:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               + urllib.parse.quote(symbol) + "?interval=1d&range=2d")
        st, txt = self._get_text(url)
        if st != 200 or not txt:
            return None
        return self.parse_yahoo(txt)

    # ----- stooq CSV 폴백 -----
    def parse_stooq(self, text: str) -> Optional[dict]:
        """stooq CSV(헤더+1행) -> {value, change, change_pct(None일 수 있음)}."""
        try:
            lines = [ln for ln in text.strip().splitlines() if ln.strip()]
            if len(lines) < 2:
                return None
            header = [h.strip().lower() for h in lines[0].split(",")]
            row = lines[1].split(",")
            rec = dict(zip(header, row))
            close = rec.get("close")
            if close in (None, "", "N/D"):
                return None
            val = float(close)
            return {"value": round(val, 4), "change": None, "change_pct": None,
                    "source": "stooq"}
        except Exception:
            return None

    def _stooq(self, symbol: str) -> Optional[dict]:
        url = ("https://stooq.com/q/l/?s=" + urllib.parse.quote(symbol)
               + "&f=sd2t2ohlcv&h&e=csv")
        st, txt = self._get_text(url)
        if st != 200 or not txt:
            return None
        return self.parse_stooq(txt)

    def parse_yahoo_series(self, text: str) -> Optional[list]:
        """야후 chart JSON -> 종가 시계열 리스트(null 제외). 국면 판정용."""
        try:
            j = json.loads(text)
            res = (j.get("chart", {}).get("result") or [None])[0]
            if not res:
                return None
            q = (res.get("indicators", {}).get("quote") or [{}])[0]
            closes = q.get("close") or []
            out = [float(c) for c in closes if c is not None]
            return out if len(out) >= 30 else None
        except Exception:
            return None

    def fetch_index_series(self, symbol: str, rng: str = "1y") -> Optional[list]:
        """지수 종가 시계열(예: 코스피 ^KS11). rng=1y/6mo 등. 실패 시 None."""
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               + urllib.parse.quote(symbol) + f"?interval=1d&range={rng}")
        st, txt = self._get_text(url)
        if st != 200 or not txt:
            return None
        return self.parse_yahoo_series(txt)

    # ----- 통합 -----
    def fetch_domestic(self) -> list[dict]:
        """국내지수(코스피/코스닥/코스피200) 야후 폴백. KIS 지수 조회 실패 시 사용."""
        out = []
        for code, name, ysym in DOMESTIC_SYMBOLS:
            d = self._yahoo(ysym)
            if d is not None:
                out.append({"code": code, "name": name, "dec": 2, **d})
        return out

    def fetch_indices(self) -> list[dict]:
        out = []
        for name, ysym, ssym, dec in GLOBAL_SYMBOLS:
            d = self._yahoo(ysym)
            if d is None:
                d = self._stooq(ssym)
            if d is not None:
                out.append({"name": name, "dec": dec, **d})
        return out

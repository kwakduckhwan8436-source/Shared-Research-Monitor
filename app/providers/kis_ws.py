"""KIS WebSocket 실시간 피드 — 체결가(H0STCNT0)·호가(H0STASP0) -> SSOT.

단타 호라이즌용 실시간 tick/orderbook 을 공급한다. REST(kis.py)와 분리된 경로:
WS 가 SSOT 에 직접 put 하고, 시그널은 SSOT 에서 신선한 데이터를 읽는다.

설계:
- approval_key 발급(POST /oauth2/Approval) 후 WS 연결, 종목별 체결/호가 구독.
- 수신 데이터는 '^' 구분 파이프 포맷. 파싱은 순수 함수(parse_message)로 분리 -> 단위 테스트.
- PINGPONG 제어 프레임은 그대로 echo. 끊기면 재연결.

⚠ websocket-client 라이브러리 필요(requirements). 미설치 시 start() 가 경고 후 no-op
   -> 단타 orderbook 시그널은 abstain (크래시 없음).
⚠ 연결 파라미터(approval/도메인/구독 메시지)는 환경에 따라 다를 수 있으니 V18.2 와 대조 권장.
   필드 매핑(parse_*)은 fixture 로 검증되어 있습니다.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from typing import Any, Optional

from app.core.ssot import SSOT
from app.data.schema import DataPoint, Kind
from app.providers.kis import UrllibTransport, HttpTransport, _f

try:
    import websocket  # websocket-client
except ImportError:  # 미설치 시 피드는 no-op
    websocket = None  # type: ignore

WS_REAL = "ws://ops.koreainvestment.com:21000"
WS_PAPER = "ws://ops.koreainvestment.com:31000"
REST_REAL = "https://openapi.koreainvestment.com:9443"
REST_PAPER = "https://openapivts.koreainvestment.com:29443"

TR_TRADE = "H0STCNT0"      # 실시간 체결가
TR_ORDERBOOK = "H0STASP0"  # 실시간 호가


# ----------------------------------------------------------------------------
# 순수 파서 (테스트 대상)
# ----------------------------------------------------------------------------
def is_pingpong(raw: str) -> bool:
    if not raw or raw[0] != "{":
        return False
    try:
        return json.loads(raw).get("header", {}).get("tr_id") == "PINGPONG"
    except Exception:
        return False


def _parse_trade(rec: list[str], now: datetime, source: str) -> Optional[DataPoint]:
    # 필드 인덱스(0-base): 0=종목코드 1=체결시각 2=현재가 12=체결량 18=체결강도
    if len(rec) < 19:
        return None
    symbol = rec[0].strip()
    if not symbol:
        return None
    return DataPoint(symbol, Kind.TICK.value, {
        "price": _f(rec[2]), "qty": int(_f(rec[12])),
        "strength": _f(rec[18]), "ts": rec[1],
    }, as_of=now, fetched_at=now, source=source)


def _parse_orderbook(rec: list[str], now: datetime, source: str) -> Optional[DataPoint]:
    # 0=종목코드 1=시각 / 3..12=매도호가1~10 13..22=매수호가1~10
    # 23..32=매도잔량1~10 33..42=매수잔량1~10
    if len(rec) < 43:
        return None
    symbol = rec[0].strip()
    if not symbol:
        return None
    asks = [[_f(rec[3 + i]), int(_f(rec[23 + i]))] for i in range(5)]
    bids = [[_f(rec[13 + i]), int(_f(rec[33 + i]))] for i in range(5)]
    return DataPoint(symbol, Kind.ORDERBOOK.value, {"bids": bids, "asks": asks},
                     as_of=now, fetched_at=now, source=source)


def parse_message(raw: str, now: datetime, source: str = "kis-ws") -> list[DataPoint]:
    """KIS 실시간 파이프 포맷 -> DataPoint 리스트. 제어 프레임/미지원 TR -> []."""
    if not raw or raw[0] == "{":          # 제어(JSON) 프레임은 데이터 아님
        return []
    parts = raw.split("|")
    if len(parts) < 4:
        return []
    tr_id, count_s, body = parts[1], parts[2], parts[3]
    try:
        count = int(count_s)
    except ValueError:
        count = 1
    if count <= 0:
        return []
    fields = body.split("^")
    per = len(fields) // count             # 레코드당 필드 수(다중 레코드 청크)
    if per <= 0:
        return []
    out: list[DataPoint] = []
    for i in range(count):
        rec = fields[i * per:(i + 1) * per]
        if tr_id == TR_TRADE:
            dp = _parse_trade(rec, now, source)
        elif tr_id == TR_ORDERBOOK:
            dp = _parse_orderbook(rec, now, source)
        else:
            dp = None
        if dp is not None:
            out.append(dp)
    return out


# ----------------------------------------------------------------------------
# 피드 (WS 연결 — 네트워크/키 필요)
# ----------------------------------------------------------------------------
class KISRealtimeFeed:
    def __init__(self, app_key: str, app_secret: str, ssot: SSOT, symbols: list[str], *,
                 clock, paper: bool = True, bus=None,
                 transport: Optional[HttpTransport] = None):
        self.app_key = app_key
        self.app_secret = app_secret
        self.ssot = ssot
        self.symbols = symbols
        self.clock = clock
        self.paper = paper
        self.bus = bus
        self.transport = transport or UrllibTransport()
        self.ws_url = WS_PAPER if paper else WS_REAL
        self.rest_base = REST_PAPER if paper else REST_REAL
        self._approval: Optional[str] = None
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def _approval_key(self) -> str:
        if self._approval:
            return self._approval
        status, body = self.transport.post(
            f"{self.rest_base}/oauth2/Approval", headers={},
            body={"grant_type": "client_credentials",
                  "appkey": self.app_key, "secretkey": self.app_secret},
        )
        key = body.get("approval_key")
        if status != 200 or not key:
            raise RuntimeError(f"KIS approval_key 발급 실패 (status={status}, body={body})")
        self._approval = key
        return key

    def _sub_msg(self, tr_id: str, tr_key: str, register: bool = True) -> str:
        return json.dumps({
            "header": {"approval_key": self._approval, "custtype": "P",
                       "tr_type": "1" if register else "2", "content-type": "utf-8"},
            "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
        })

    # --- WS 콜백 ---
    def _on_open(self, ws) -> None:
        for sym in self.symbols:
            ws.send(self._sub_msg(TR_TRADE, sym))
            ws.send(self._sub_msg(TR_ORDERBOOK, sym))
            time.sleep(0.05)  # 과도한 동시 구독 방지

    def _on_message(self, ws, raw: str) -> None:
        if is_pingpong(raw):
            ws.send(raw)  # 핑퐁 echo
            return
        now = self.clock.now()
        dps = parse_message(raw, now)
        for dp in dps:
            self.ssot.put(dp)
        if dps and self.bus:
            self.bus.publish("realtime.tick", {"count": len(dps)})

    def _on_error(self, ws, err) -> None:
        print(f"[kis-ws] error: {err}")

    def _on_close(self, ws, *args) -> None:
        print("[kis-ws] closed")

    def _run(self) -> None:
        fails = 0
        while not self._stop.is_set():
            try:
                self._approval_key()
                fails = 0                       # 성공 시 리셋
                self._ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=self._on_open, on_message=self._on_message,
                    on_error=self._on_error, on_close=self._on_close,
                )
                self._ws.run_forever(ping_interval=30)
            except Exception as e:
                fails += 1
                if fails <= 2:
                    print(f"[kis-ws] 실시간 피드 연결 실패: {e}")
                elif fails == 3:
                    print("[kis-ws] 실시간 체결/호가(WebSocket) 연결이 계속 실패합니다. "
                          "REST 시세·추천은 정상 동작합니다. (WS는 단타 호가용) "
                          "필요시 .env 에서 RECO_REALTIME=false 로 끌 수 있습니다. 재시도는 계속합니다.")
                # 점증 백오프: 5,10,20,40,60(상한)초
                wait = min(5 * (2 ** min(fails - 1, 4)), 60) if fails else 5
            else:
                wait = 5
            if not self._stop.is_set():
                self._stop.wait(wait)

    def start(self) -> bool:
        if websocket is None:
            print("[kis-ws] websocket-client 미설치 -> 실시간 비활성(단타 orderbook 은 abstain). "
                  "`pip install websocket-client` 후 재시작하세요.")
            return False
        if not self.symbols:
            print("[kis-ws] watchlist 비어있음 -> 실시간 구독 생략")
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name="kis-ws")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass

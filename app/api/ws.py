"""WebSocket — 단타 호라이즌 실시간 push.

mock 모드: 주기적으로 데이터를 갱신하고 daytrade 추천을 밀어준다.
live 모드: KIS WebSocket 스트림을 SSOT 에 put 하고, 변경 시 추천을 재계산해 push 하는 구조로 확장.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any


def register_ws(app: Any, ctx: Any) -> None:
    from fastapi import WebSocket, WebSocketDisconnect

    @app.websocket("/ws/daytrade")
    async def ws_daytrade(ws: WebSocket) -> None:
        await ws.accept()
        try:
            while True:
                if ctx.config.data_source == "mock":
                    # 실시간성 데이터(틱/호가) 갱신 후 재추천
                    ctx.service.refresh_data(ctx.universe, ["tick", "orderbook", "ohlcv"])
                recs = ctx.service.recommend("daytrade", top_n=10)
                await ws.send_json({
                    "type": "daytrade.update",
                    "snapshot_id": ctx.ssot.snapshot_id(),
                    "recommendations": [asdict(r) for r in recs],
                })
                await asyncio.sleep(3.0)
        except WebSocketDisconnect:
            return

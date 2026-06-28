#!/usr/bin/env python3
"""실행파일(.exe) 진입점 — PyInstaller 로 단일 실행파일 빌드 시 사용.

빌드된 exe 를 더블클릭하면:
  1) 내장 웹 서버(uvicorn)를 127.0.0.1:8000 에서 시작
  2) 기본 브라우저로 화면을 연다
  3) 콘솔 창을 닫으면 종료

빌드 방법은 build_exe.bat 참고. (소스가 바이트코드로 번들되어 그대로 노출되지 않음)
"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

HOST, PORT = "127.0.0.1", 8000


def _resource_root() -> str:
    """PyInstaller 번들이면 임시 추출 폴더(_MEIPASS), 아니면 현재 폴더."""
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    root = _resource_root()
    # web/ 등 상대경로 자원을 찾도록 작업 디렉터리 고정
    os.chdir(root)
    # 기본값: 공개 안전 모드(시세 재배포 차단) — 배포용
    os.environ.setdefault("RECO_PUBLIC_MODE", "1")
    os.environ.setdefault("RECO_DATA_SOURCE", "live")

    import uvicorn
    from app.api.main import create_app

    app = create_app()

    def _open_browser() -> None:
        time.sleep(1.5)
        try:
            webbrowser.open(f"http://{HOST}:{PORT}")
        except Exception:
            pass

    threading.Thread(target=_open_browser, daemon=True).start()
    print(f"\n  모두의 리서치 모니터 — http://{HOST}:{PORT}")
    print("  종료하려면 이 창을 닫거나 Ctrl+C 를 누르세요.\n")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()

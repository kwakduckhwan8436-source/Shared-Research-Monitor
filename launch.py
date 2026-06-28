#!/usr/bin/env python3
"""실행 런처 — 가상환경 자동 구성 + 의존성 설치 + 웹 서버 시작 + 브라우저 열기.

사용법:
  python launch.py          # 웹 서버 (최초 실행 시 .venv 자동 생성·설치)
  python launch.py demo     # 도메인 파이프라인 데모 (의존성 불필요)
  python launch.py test     # 테스트 실행 (의존성 불필요)

웹 서버는 http://127.0.0.1:8000 에서 열립니다. 종료는 Ctrl+C.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import threading
import webbrowser

ROOT = pathlib.Path(__file__).resolve().parent
VENV = ROOT / ".venv"
HOST, PORT = "127.0.0.1", 8000


def _venv_python() -> pathlib.Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _in_venv() -> bool:
    # venv 의 sys.prefix 는 venv 디렉터리 자체다(심볼릭링크 영향 없음).
    try:
        return pathlib.Path(sys.prefix).resolve() == VENV.resolve()
    except Exception:
        return False


def _run_stdlib(kind: str) -> int:
    """demo/test/preflight 는 표준 라이브러리만 필요 — 현재 인터프리터로 바로 실행."""
    targets = {"demo": "run_pipeline.py", "test": os.path.join("tests", "run_tests.py"),
               "preflight": os.path.join("tools", "preflight.py")}
    target = targets.get(kind, os.path.join("tests", "run_tests.py"))
    return subprocess.call([sys.executable, str(ROOT / target)])


def _ensure_venv() -> pathlib.Path:
    import venv
    if not VENV.exists():
        print("[1/3] 가상환경(.venv) 생성 중...")
        venv.create(VENV, with_pip=True)
    py = _venv_python()
    print("[2/3] 의존성 설치/확인 중... (최초 실행은 수십 초 걸릴 수 있습니다)")
    subprocess.check_call([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"])
    subprocess.check_call([str(py), "-m", "pip", "install", "-q", "-r",
                           str(ROOT / "requirements.txt")])
    return py


def _serve() -> None:
    url = f"http://{HOST}:{PORT}"
    print(f"[3/3] 서버 시작: {url}   (종료: Ctrl+C)")
    threading.Timer(2.5, lambda: webbrowser.open(url)).start()
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    import uvicorn
    uvicorn.run("app.api.main:create_app", factory=True,
                host=HOST, port=PORT, log_level="info")


def main() -> int:
    arg = (sys.argv[1].lower() if len(sys.argv) > 1 else "")
    if arg in ("demo", "test", "preflight"):
        return _run_stdlib(arg)

    if arg == "backtest":                   # 점수 예측력 백테스트 리포트
        if _in_venv():
            sys.path.insert(0, str(ROOT))
            from tools.run_backtest import run_cli
            return run_cli(sys.argv[2:])
        py = _ensure_venv()
        return subprocess.call([str(py), str(ROOT / "launch.py"), "backtest"] + sys.argv[2:])

    if arg == "live":                       # 실데이터 모드로 강제
        os.environ["RECO_DATA_SOURCE"] = "live"
        print("[라이브] RECO_DATA_SOURCE=live — 실데이터로 시작합니다. "
              "키가 준비됐는지 'python launch.py preflight' 로 먼저 점검을 권장합니다.")

    # 웹 서버: venv 안이면 바로 서빙, 아니면 venv 구성 후 그 파이썬으로 재실행.
    if _in_venv():
        _serve()
        return 0
    py = _ensure_venv()
    extra = ["live"] if arg == "live" else []
    return subprocess.call([str(py), str(ROOT / "launch.py")] + extra)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n종료되었습니다.")
    except subprocess.CalledProcessError as e:
        print(f"\n[오류] 설치 실패: {e}")
        print("인터넷 연결 또는 pip 설정을 확인하세요.")
        sys.exit(1)

"""stdlib 테스트 하네스 — pytest 없이 tests/test_*.py 의 test_ 함수를 실행.

실행: python tests/run_tests.py   (또는 pytest 설치 시 `pytest`)
"""
from __future__ import annotations

import importlib
import os
import sys
import traceback

# 프로젝트 루트를 import path 에 추가
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TEST_MODULES = [
    "tests.test_core",
    "tests.test_freshness",
    "tests.test_signals",
    "tests.test_scorer",
    "tests.test_reco",
    "tests.test_kis",
    "tests.test_kis_ws",
    "tests.test_dart",
    "tests.test_news",
    "tests.test_krx",
    "tests.test_naver_news",
    "tests.test_config_dotenv",
    "tests.test_market_data",
    "tests.test_signals_v2",
    "tests.test_holidays",
    "tests.test_backtest",
    "tests.test_alerts",
    "tests.test_holdings",
    "tests.test_themes",
    "tests.test_regime",
    "tests.test_anomaly",
    "tests.test_market_disclosures",
    "tests.test_notice",
    "tests.test_google_news",
    "tests.test_screener",
    "tests.test_board_mflow",
]


def main() -> int:
    passed = failed = 0
    failures: list[str] = []
    for modname in TEST_MODULES:
        mod = importlib.import_module(modname)
        fns = [getattr(mod, n) for n in dir(mod) if n.startswith("test_")
               and callable(getattr(mod, n))]
        for fn in fns:
            label = f"{modname}.{fn.__name__}"
            try:
                fn()
                passed += 1
                print(f"  PASS  {label}")
            except Exception as e:
                failed += 1
                failures.append(label)
                print(f"  FAIL  {label}: {e}")
                traceback.print_exc()
    print("\n" + "-" * 56)
    print(f"  통과 {passed} / 실패 {failed} (총 {passed + failed})")
    if failures:
        print("  실패 목록:", ", ".join(failures))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

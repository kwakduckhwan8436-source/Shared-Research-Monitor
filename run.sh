#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# 1) 도메인 파이프라인 데모 (의존성 없이 즉시 실행 가능)
if [ "${1:-}" = "demo" ]; then
  exec python3 run_pipeline.py
fi

# 2) 테스트 (pytest 없이도 동작)
if [ "${1:-}" = "test" ]; then
  exec python3 tests/run_tests.py
fi

# 3) 웹 서버 (FastAPI 필요: pip install -r requirements.txt)
#    http://localhost:8000 접속 -> web/index.html + /api/*
exec uvicorn app.api.main:create_app --factory --host 0.0.0.0 --port 8000

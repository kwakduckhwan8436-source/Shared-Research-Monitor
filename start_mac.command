#!/bin/bash
# macOS: 더블클릭으로 실행. (최초 1회 우클릭 > 열기 필요할 수 있음)
cd "$(dirname "$0")"
echo "============================================"
echo "  멀티-호라이즌 종목 추천 - 웹 서버"
echo "============================================"
echo ""
if command -v python3 >/dev/null 2>&1; then
  python3 launch.py
else
  echo "[오류] python3 를 찾을 수 없습니다. https://python.org 에서 설치하세요."
  read -n 1 -s -r -p "아무 키나 누르면 종료합니다..."
fi

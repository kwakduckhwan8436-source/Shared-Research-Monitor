#!/usr/bin/env python3
"""서버 배포용 진입점 — 클라우드(Render/Railway/Fly 등) 및 일반 리눅스 서버.

클라우드 플랫폼은 $PORT 환경변수로 포트를 주입한다. 외부 접속을 받으려면
0.0.0.0 에 바인딩해야 한다(launch.py 는 로컬 전용 127.0.0.1).

기본값으로 공개 안전모드(RECO_PUBLIC_MODE=1)를 켜 시세 재배포를 차단한다.

실행:  python server.py
환경변수:
  PORT                 바인딩 포트(기본 8000, 플랫폼이 주입하면 그 값)
  RECO_PUBLIC_MODE     공개 안전모드(기본 1)
  RECO_DATA_SOURCE     live | mock (기본 live)
  RECO_SITE_URL        sitemap/robots 용 사이트 주소(예: https://my.app)
  RECO_ADMIN_TOKEN     운영자 토큰(신고/금지어/통계 관리)
  RECO_ADSENSE_PUB     애드센스 게시자 ID(있으면 광고 표시)
  RECO_POLICY_ALL      1 이면 정부정책 RSS 전체 부처 수집
"""
from __future__ import annotations

import os


def main() -> None:
    # 배포 기본값: 공개 안전모드 + 라이브 데이터
    os.environ.setdefault("RECO_PUBLIC_MODE", "1")
    os.environ.setdefault("RECO_DATA_SOURCE", "live")

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))   # 플랫폼이 PORT 주입

    import uvicorn
    from app.api.main import create_app

    app = create_app()
    print(f"[server] http://{host}:{port}  (public_mode={os.environ.get('RECO_PUBLIC_MODE')})")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()

"""설정 — 환경변수 기반. 키·시크릿은 .env 로 분리(저장소 커밋 금지).

.env 파일이 있으면 import 시점에 자동 로드한다(실제 환경변수가 우선).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _find_dotenv() -> "str | None":
    """.env 를 여러 위치에서 찾는다(윈도우 트랩 대응).
    - 현재 작업폴더와 프로젝트 루트 양쪽
    - .env 와 .env.txt (메모장이 .txt 를 붙이는 경우)
    """
    here = os.path.dirname(os.path.abspath(__file__))   # .../app
    root = os.path.dirname(here)                         # 프로젝트 루트
    bases = []
    for b in (os.getcwd(), root):
        if b not in bases:
            bases.append(b)
    for base in bases:
        for name in (".env", ".env.txt"):
            p = os.path.join(base, name)
            if os.path.exists(p):
                return p
    return None


DOTENV_PATH: "str | None" = None       # 어떤 .env 를 읽었는지(진단용)
DOTENV_LOADED: bool = False


def _load_dotenv() -> None:
    """.env 의 KEY=VALUE 를 os.environ 에 채운다(이미 설정된 값은 안 덮음).
    utf-8-sig 로 읽어 메모장 BOM 도 제거한다."""
    global DOTENV_PATH, DOTENV_LOADED
    path = _find_dotenv()
    DOTENV_PATH = path
    if not path:
        return
    try:
        with open(path, encoding="utf-8-sig") as fh:   # BOM 제거
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                # 따옴표로 감싸지 않은 값의 인라인 주석( ' # ...') 제거
                if val[:1] not in ('"', "'"):
                    import re as _re
                    val = _re.sub(r"\s+#.*$", "", val)
                val = val.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, val)
        DOTENV_LOADED = True
    except OSError:
        pass


# Config 필드 기본값이 평가되기 전에 .env 를 먼저 로드한다.
_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or not str(v).strip():
        return default
    try:
        return int(str(v).strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    db_path: str = os.getenv("RECO_DB", "data/reco.sqlite3")
    # 데이터 소스: mock | live. live 는 KIS/DART/KRX/News provider 를 와이어링(사용자 환경).
    data_source: str = os.getenv("RECO_DATA_SOURCE", "mock")
    # 공개 호스팅 모드: True 면 KIS/KRX 시세 재배포 위험 요소를 모두 끈다(법적 안전).
    # 좌측 수급 스크리너·시세·오실레이터·실시간 압력·지수·세부수급 비활성.
    # DART 공시·뉴스(제목+링크)·커뮤니티·종목 검색(이름)만 남긴다.
    public_mode: bool = _bool("RECO_PUBLIC_MODE", False)
    # 운영자(모더레이션) 토큰 — 신고 처리·숨김·금지어 관리에 필요. 비우면 운영자 기능 비활성.
    admin_token: str = os.getenv("RECO_ADMIN_TOKEN", "").strip()
    # Google AdSense 게시자 ID(예: ca-pub-1234567890123456). 비우면 수동 ad.txt 배너 사용.
    adsense_pub: str = os.getenv("RECO_ADSENSE_PUB", "")
    adsense_slot: str = os.getenv("RECO_ADSENSE_SLOT", "")
    # 신고 자동 숨김 임계치 — 서로 다른 신고자 수가 이 값 이상이면 자동 숨김.
    report_auto_hide: int = _int("RECO_REPORT_AUTOHIDE", 3)
    # 사이트 URL(sitemap.xml·robots.txt·SEO용). 예: https://my-domain.com
    site_url: str = os.getenv("RECO_SITE_URL", "")
    # 미캘리브레이션(규칙기반) 가중치 사용을 의식적으로 허용할지.
    allow_uncalibrated: bool = _bool("RECO_ALLOW_UNCALIBRATED", True)
    # 유니버스 모드: representative(대표 소수) | market(전종목 스캔)
    universe_mode: str = os.getenv("RECO_UNIVERSE", "market")
    # 시그널 계산 후보 상한(거래대금 상위 N). 전체 시장 종목선정을 위해 넉넉히.
    # 채점은 네트워크 없이 빠르므로 크게 잡아도 된다(적재된 종목 거의 전부 채점).
    scan_limit: int = int(os.getenv("RECO_SCAN_LIMIT", "2000"))
    top_n: int = int(os.getenv("RECO_TOP_N", "30"))

    # LLM (운영 시)
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    llm_model: str = os.getenv("RECO_LLM_MODEL", "claude-sonnet-4-6")

    # 외부 API 키 (live 모드)
    kis_app_key: str = os.getenv("KIS_APP_KEY", "")
    kis_app_secret: str = os.getenv("KIS_APP_SECRET", "")
    kis_paper: bool = _bool("KIS_PAPER", True)   # 모의투자 도메인 사용 여부
    # 보유종목 조회용 계좌번호 "12345678-01"(종합계좌8 + 상품코드2). 읽기 전용, 매매 안 함.
    kis_account: str = os.getenv("KIS_ACCOUNT", "")
    dart_api_key: str = os.getenv("DART_API_KEY", "")
    # 네이버 뉴스 검색 API(실시간 언론 기사, 선택). developers.naver.com 발급.
    naver_client_id: str = os.getenv("NAVER_CLIENT_ID", "")
    naver_client_secret: str = os.getenv("NAVER_CLIENT_SECRET", "")
    # 공공데이터포털(data.go.kr) 서비스키 — 공시일정/주식발행/기업정보 등 금융 공공데이터.
    # 무료 발급. 공공누리 유형은 데이터별로 다르니 상업적 이용 시 각 데이터 약관 확인.
    data_go_key: str = os.getenv("DATA_GO_KR_KEY", "")
    # 금융위원회 주식시세정보 API 키(data.go.kr) — 종가·시가총액(PER/PBR 계산용).
    # 이용허락범위 제한 없음(상업 이용 가능), 무료, 전일 종가 기준.
    stock_price_key: str = os.getenv("STOCK_PRICE_API_KEY", "").strip()
    # 관세청 품목별 수출입실적 API 키(data.go.kr). 없으면 DATA_GO_KR_KEY 를 사용.
    # 이용허락범위 제한 없음(상업 가능), 무료, 월 단위 데이터.
    trade_key: str = os.getenv("TRADE_API_KEY", "").strip()
    # 한국수출입은행 환율 API 키(선택). oapi.koreaexim.go.kr 무료 발급.
    exim_key: str = os.getenv("EXIM_API_KEY", "")

    # live 유니버스(관심종목). 쉼표구분 "005930,000660,..." 또는 watchlist.txt(한 줄 1종목).
    watchlist: str = os.getenv("RECO_WATCHLIST", "")
    # 실시간 체결/호가 WebSocket 피드 사용 여부(live 모드, websocket-client 필요)
    realtime: bool = _bool("RECO_REALTIME", True)

    # 네이버 카페 바로가기(선택). 본인 카페 URL 을 넣으면 상단에 '카페' 버튼이 뜬다.
    cafe_url: str = os.getenv("RECO_CAFE_URL", "")
    cafe_name: str = os.getenv("RECO_CAFE_NAME", "내 카페")


def load_watchlist(cfg: "Config") -> list[str]:
    """RECO_WATCHLIST 환경변수 우선, 없으면 watchlist.txt 파일에서 종목코드 로드."""
    syms: list[str] = []
    if cfg.watchlist.strip():
        syms = [s.strip() for s in cfg.watchlist.split(",") if s.strip()]
    elif os.path.exists("watchlist.txt"):
        with open("watchlist.txt", encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line:
                    syms.append(line)
    return syms


_CORP_MAP_CACHE: dict = {}
def load_corp_map(path: str = "dart_corp_map.json") -> dict:
    """DART corp_code 매핑(종목코드 -> 8자리 corp_code) 로드. 파일 없으면 빈 dict.
    한 번 읽으면 캐시(부팅 중 반복 파일읽기 방지)."""
    if path in _CORP_MAP_CACHE:
        return _CORP_MAP_CACHE[path]
    if not os.path.exists(path):
        _CORP_MAP_CACHE[path] = {}
        return {}
    import json
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        result = {str(k): str(v) for k, v in data.items()}
        _CORP_MAP_CACHE[path] = result
        return result
    except (OSError, ValueError):
        _CORP_MAP_CACHE[path] = {}
        return {}


def clear_corp_map_cache() -> None:
    _CORP_MAP_CACHE.clear()


def load_corp_names(path: str = "dart_corp_names.json") -> dict:
    """종목명 매핑(종목코드 -> 회사명) 로드. corpCode.xml 기반(build_dart_corpmap.py 생성)."""
    if not os.path.exists(path):
        return {}
    import json
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): str(v) for k, v in data.items()}
    except (OSError, ValueError):
        return {}


def load_config() -> Config:
    return Config()

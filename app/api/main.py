"""FastAPI 앱 팩토리 + DI 와이어링.

fastapi 미설치 환경에서도 `import app.api.main` 이 깨지지 않도록 fastapi 는 함수 안에서 lazy import.
create_app() 이 의존성 그래프(SSOT, providers, store, service, verdict, llm)를 조립한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from app.config import Config, load_config
from app.core.clock import Clock
from app.core.eventbus import EventBus
from app.core.ssot import SSOT
from app.data.store import Store
from app.providers.base import DataProvider, ProviderRouter
from app.providers.mock import MockProvider, universe_symbols, name_of
from app.reco.service import RecommendationService
from app.reco.verdict import VerdictEvaluator
from app.signals.registry import HORIZONS, required_kinds_for


@dataclass
class AppContext:
    config: Config
    clock: Clock
    bus: EventBus
    ssot: SSOT
    store: Store
    provider: DataProvider
    service: RecommendationService
    verdict: VerdictEvaluator
    llm_client: Optional[Any]
    name_of: Any
    universe: list[str]
    realtime: Optional[Any] = None
    scheduler: Optional[Any] = None
    quote: Optional[Any] = None
    press_news: Optional[Any] = None
    google_news: Optional[Any] = None   # 구글 뉴스 RSS(네이버 보완)
    policy_news: Optional[Any] = None    # 정부정책 RSS(정책브리핑·부처, 공공누리)
    market: Optional[Any] = None    # 해외지수/원자재/금리 provider
    dart: Optional[Any] = None      # DART provider(전체 공시 조회용)
    errors: Optional[Any] = None    # ErrorCounter(런타임 에러 모니터링)
    search_universe: Optional[list] = None  # 검색 전용 전체 종목(corp_map+KRX+내장) — 랭킹 유니버스와 분리

    def all_kinds(self) -> list[str]:
        return sorted({k for h in HORIZONS for k in required_kinds_for(h)})


def _build_llm_client(cfg: Config) -> Optional[Any]:
    if not cfg.anthropic_api_key:
        return None
    try:
        import anthropic  # lazy
        return anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    except Exception:
        return None


def build_context(cfg: Optional[Config] = None) -> AppContext:
    cfg = cfg or load_config()
    from app.core.logging_setup import setup_logging, get_logger, ErrorCounter
    setup_logging(os.getenv("RECO_LOG_LEVEL", "INFO"))
    _log = get_logger("startup")
    _log.info("build_context 시작 (data_source=%s, universe_mode=%s)",
              cfg.data_source, cfg.universe_mode)
    clock = Clock()
    bus = EventBus()
    ssot = SSOT()
    store = Store(cfg.db_path)
    llm = _build_llm_client(cfg)
    realtime = None
    quote = None
    dart_provider = None
    _krx_names: dict = {}
    _krx_mkt: dict = {}
    search_universe: list = []
    # 실시간 언론 기사(선택) — mock/live 공통, 키 있으면 활성
    press_news = None
    if cfg.naver_client_id and cfg.naver_client_secret:
        from app.providers.naver_news import NaverNewsProvider
        press_news = NaverNewsProvider(cfg.naver_client_id, cfg.naver_client_secret)
    # 구글 뉴스 RSS — 키 불필요, 다수 언론사 집계(네이버 보완). 차단/오프라인이면 빈 결과.
    google_news = None
    if os.getenv("RECO_GOOGLE_NEWS", "1") not in ("0", "false", "False"):
        from app.providers.google_news import GoogleNewsProvider
        google_news = GoogleNewsProvider()
    # 정부정책 RSS — 정책브리핑·부처 보도자료(공공누리). 키 불필요. 제목+링크+출처만.
    policy_news = None
    if os.getenv("RECO_POLICY_NEWS", "1") not in ("0", "false", "False"):
        from app.providers.policy_news import PolicyNewsProvider
        _all = os.getenv("RECO_POLICY_ALL", "0") not in ("0", "false", "False")
        policy_news = PolicyNewsProvider(all_sources=_all)

    if cfg.data_source == "mock":
        provider: DataProvider = ProviderRouter([MockProvider(llm_client=llm)])
        universe = universe_symbols(market=(cfg.universe_mode == "market"))
        resolver = name_of
    else:
        # live: KIS/DART/KRX/News provider 와이어링 (사용자 환경)
        from app.config import load_watchlist, load_corp_map, load_corp_names
        from app.providers.dart import DARTProvider
        from app.providers.krx import KRXProvider
        from app.providers.news import NewsProvider

        # DART 키가 있는데 corp_code 맵이 없으면 자동 생성 (없으면 공시가 안 나옴).
        if cfg.dart_api_key and not os.path.exists("dart_corp_map.json"):
            print("[DART] corp_code 맵이 없어 자동 생성합니다 (corpCode.xml 다운로드, 수십 초 소요)...")
            os.environ.setdefault("DART_API_KEY", cfg.dart_api_key)
            try:
                from tools.build_dart_corpmap import main as _build_map
                _build_map()
                print("[DART] corp_code 맵 생성 완료.")
            except Exception as e:
                print(f"[DART] corp_code 맵 자동 생성 실패: {e}\n"
                      "      'python launch.py preflight' 로 다시 시도하거나 DART 키 활성화를 확인하세요.")

        providers: list = []
        if cfg.kis_app_key and cfg.kis_app_secret and not cfg.public_mode:
            from app.providers.kis import KISProvider
            kis = KISProvider(cfg.kis_app_key, cfg.kis_app_secret, paper=cfg.kis_paper)
            quote = kis                          # 실시간 현재가 조회용
            providers.append(kis)
        elif cfg.public_mode:
            print("[공개모드] RECO_PUBLIC_MODE=true — KIS 시세를 사용하지 않습니다(재배포 위험 차단). "
                  "좌측 수급 스크리너·시세·오실레이터·실시간 압력이 비활성됩니다. "
                  "DART 공시·뉴스·커뮤니티·종목 검색만 제공합니다.")
        # KIS 키가 없어도 서버는 뜬다(DART/네이버는 동작). 배너로 안내.
        dart_provider = DARTProvider(cfg.dart_api_key, load_corp_map())  # 키 없으면 abstain
        providers.append(dart_provider)
        providers.append(KRXProvider())
        providers.append(NewsProvider(cfg.dart_api_key, load_corp_map(), llm_client=llm))
        provider = ProviderRouter(providers)
        universe = load_watchlist(cfg)          # RECO_WATCHLIST 또는 watchlist.txt
        # 오프라인 경로 A: 사용자가 DART에서 받은 CORPCODE.xml(또는 corpCode.zip)을 직접 넣은 경우
        # → API 호출이 방화벽에 막혀도 전종목 맵 생성 가능.
        # 파일명 대소문자/위치(작업폴더·스크립트폴더·data/)를 모두 탐색하고, zip도 자동 해제.
        if cfg.universe_mode == "market" and len(load_corp_map()) < 300 and not cfg.public_mode:
            try:
                import xml.etree.ElementTree as _ET, zipfile as _zip, json as _json, glob as _glob
                import pathlib as _pl
                search_dirs = [os.getcwd(), str(_pl.Path(__file__).resolve().parent.parent.parent), "data"]
                xml_bytes = None; found_path = None
                # 1) corpcode 이름이 들어간 .xml (대소문자 무관)
                for d in search_dirs:
                    if not os.path.isdir(d):
                        continue
                    for f in _glob.glob(os.path.join(d, "*.xml")) + _glob.glob(os.path.join(d, "*.XML")):
                        if "corpcode" in os.path.basename(f).lower():
                            with open(f, "rb") as _fh:
                                xml_bytes = _fh.read(); found_path = f
                            break
                    if xml_bytes:
                        break
                # 2) corpcode 이름이 들어간 .zip → 내부 첫 xml 해제
                if xml_bytes is None:
                    for d in search_dirs:
                        if not os.path.isdir(d):
                            continue
                        for f in _glob.glob(os.path.join(d, "*.zip")) + _glob.glob(os.path.join(d, "*.ZIP")):
                            if "corpcode" in os.path.basename(f).lower():
                                try:
                                    _zf = _zip.ZipFile(f)
                                    nm = next((n for n in _zf.namelist() if n.lower().endswith(".xml")), None)
                                    if nm:
                                        xml_bytes = _zf.read(nm); found_path = f + " (" + nm + ")"
                                        break
                                except Exception:
                                    continue
                        if xml_bytes:
                            break
                if xml_bytes:
                    # 인코딩 견고 처리: ElementTree가 선언을 따르되, 실패 시 직접 디코드 후 재시도
                    try:
                        _root = _ET.fromstring(xml_bytes)
                    except Exception:
                        txt = None
                        for enc in ("utf-8-sig", "utf-8", "euc-kr", "cp949"):
                            try:
                                txt = xml_bytes.decode(enc); break
                            except Exception:
                                continue
                        # XML 선언 제거 후 파싱(선언 인코딩과 실제가 다를 때)
                        import re as _re
                        if txt:
                            txt = _re.sub(r"<\?xml[^>]*\?>", "", txt, count=1)
                            _root = _ET.fromstring(txt)
                        else:
                            raise
                    _m: dict = {}; _n: dict = {}
                    for el in _root.iter("list"):
                        sc = (el.findtext("stock_code") or "").strip()
                        cc = (el.findtext("corp_code") or "").strip()
                        cn = (el.findtext("corp_name") or "").strip()
                        if sc and cc and len(sc) == 6 and sc.isdigit():
                            _m[sc] = cc
                            if cn:
                                _n[sc] = cn
                    if _m:
                        _json.dump(_m, open("dart_corp_map.json", "w", encoding="utf-8"), ensure_ascii=False)
                        _json.dump(_n, open("dart_corp_names.json", "w", encoding="utf-8"), ensure_ascii=False)
                        print(f"[corp_map] 로컬 파일 '{found_path}' 에서 상장 {len(_m)}종 생성(오프라인). 전종목 검색 준비 완료.")
                    else:
                        print(f"[corp_map] 경고: '{found_path}' 파싱 결과 상장종목 0개. 파일 형식을 확인하세요(DART corpCode.xml 인지).")
                else:
                    # 파일을 못 찾았으면, 어디를 봤는지 알려준다(사용자 디버깅용)
                    if cfg.dart_api_key:
                        pass   # 아래 자동 다운로드로 시도
                    print(f"[corp_map] CORPCODE.xml/zip 을 찾지 못했습니다. 탐색 위치: {search_dirs} "
                          f"(현재 작업폴더={os.getcwd()}). 이 폴더에 CORPCODE.xml 을 두세요.")
            except Exception as _e:
                import traceback as _tb
                print(f"[corp_map] 로컬 CORPCODE 처리 오류: {_e}")
                _tb.print_exc()
        # corp_map 자동 생성/복구: 파일이 없거나 종목 수가 비정상적으로 적으면(빈 {} 등)
        # DART 키가 있을 때 시작 시 다시 받아온다(수동 preflight 불필요).
        _cm = load_corp_map()
        if cfg.universe_mode == "market" and len(_cm) < 300 and cfg.dart_api_key and not cfg.public_mode:
            try:
                from tools.build_dart_corpmap import build_corpmap
                if _cm:
                    print(f"[corp_map] 기존 맵이 {len(_cm)}종으로 비정상(전종목 아님) → 재생성 시도...")
                else:
                    print("[corp_map] DART 종목 맵이 없어 자동 생성합니다(전종목 검색용)...")
                _m, _n = build_corpmap(cfg.dart_api_key)
                if _m:
                    print(f"[corp_map] 생성 완료: {len(_m)}종목.")
                else:
                    print("[corp_map] 생성 실패 → 서버 콘솔의 [corp_map] 오류 메시지 확인. "
                          "KRX·stocks.csv·내장 목록으로 대체합니다.")
            except Exception as _e:
                print(f"[corp_map] 자동 생성 오류: {_e}")
        if cfg.universe_mode == "market" and not cfg.public_mode:  # 전종목: corpCode 맵의 모든 상장 종목코드
            market_codes = list(load_corp_map().keys())
            if market_codes:
                ceiling = int(os.getenv("RECO_MAX_SYMBOLS", "4000"))   # 안전 상한(전체≈3,600)
                if len(market_codes) > ceiling:
                    market_codes = market_codes[:ceiling]
                universe = market_codes
                print(f"[유니버스] 전체 시장 {len(universe)}종 대상으로 종목 선정합니다. "
                      f"OHLCV는 백그라운드로 점진 적재되며(서버는 즉시 사용 가능), "
                      f"수 분에 걸쳐 채워집니다. KIS 일일 호출 한도에 유의하세요.")
            else:
                print("[유니버스] corpCode 맵이 비어 전체 시장을 못 불러옵니다 → 내장 종목 + 관심종목으로 대체. "
                      "더 많은 종목 검색을 원하면 DART 키 활성화 후 'python launch.py preflight' 로 맵을 생성하세요.")
        # 내장 실제 종목(154종, 코드·이름·시장 보유)을 항상 병합 → DART 없이도 검색·코스피/코스닥 작동
        try:
            from app.providers.mock import _REAL_STOCKS as _RS
            seen = set(universe)
            for code, _nm, _mk in _RS:
                if code not in seen:
                    universe.append(code); seen.add(code)
        except Exception:
            pass
        # ── 오프라인 폴백: 사용자가 직접 넣은 stocks.csv (code,name,market) ──
        # KRX 자동 호출이 사내망/방화벽에 막혀도, KRX 사이트에서 받은 전종목 CSV를
        # 프로젝트 루트에 stocks.csv 로 저장하면 전종목 검색이 100% 동작한다.
        try:
            import csv as _csv
            for _p in ("stocks.csv", "krx_stocks.csv"):
                if os.path.exists(_p):
                    with open(_p, encoding="utf-8-sig") as _f:
                        rdr = _csv.reader(_f)
                        rows = list(rdr)
                    if rows:
                        # 헤더 자동 탐지
                        hdr = rows[0]
                        def _ci(*ns):
                            for i, h in enumerate(hdr):
                                if any(n in h.replace(" ", "") for n in ns):
                                    return i
                            return -1
                        ci = _ci("단축코드", "종목코드", "코드", "code", "Code")
                        ni = _ci("한글종목약명", "한글종목명", "종목명", "name", "Name")
                        mi = _ci("시장구분", "시장", "market", "Market")
                        body = rows[1:] if ci >= 0 else rows
                        if ci < 0:
                            ci, ni, mi = 0, 1, 2     # 헤더 없으면 위치 가정
                        seen = set(universe); added = 0
                        for r in body:
                            if len(r) <= ci:
                                continue
                            code = r[ci].strip().strip('"').zfill(6)
                            if len(code) != 6 or not code.isdigit():
                                continue
                            nm = r[ni].strip().strip('"') if 0 <= ni < len(r) else ""
                            mkraw = r[mi].strip() if 0 <= mi < len(r) else ""
                            mk = "KOSDAQ" if ("코스닥" in mkraw or "KOSDAQ" in mkraw.upper()) else (
                                 "KOSPI" if ("코스피" in mkraw or "유가" in mkraw or "KOSPI" in mkraw.upper()) else "")
                            if code not in seen:
                                universe.append(code); seen.add(code); added += 1
                            if nm:
                                _krx_names[code] = nm
                            if mk:
                                _krx_mkt[code] = mk
                        print(f"[유니버스] {_p} 에서 {added}종 추가 → 전체 {len(universe)}종(오프라인 전종목).")
                    break
        except Exception as _e:
            print(f"[유니버스] stocks.csv 로드 오류: {_e}")
        # KRX 정보데이터시스템에서 전체 상장종목(코드·이름·시장)을 가져와 전종목 검색 지원(키 불필요)
        # 하루 1회 캐시(krx_stocks.json) — 매 부팅 시 호출 방지(빠른 부팅)
        # 공개 모드: 검색 패널이 없으므로 KRX 네트워크 fetch는 생략(캐시가 있으면만 사용).
        try:
            import json as _json, time as _time
            from app.providers.krx import fetch_stock_list
            cache_path = "krx_stocks.json"
            krx_list = []
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, encoding="utf-8") as f:
                        cached = _json.load(f)
                    if _time.time() - cached.get("ts", 0) < 86400:   # 24시간 이내
                        krx_list = [tuple(x) for x in cached.get("rows", [])]
                except Exception:
                    krx_list = []
            if not krx_list and not getattr(cfg, "public_mode", False):
                krx_list = fetch_stock_list()
                if krx_list:
                    try:
                        with open(cache_path, "w", encoding="utf-8") as f:
                            _json.dump({"ts": _time.time(), "rows": krx_list}, f, ensure_ascii=False)
                    except Exception:
                        pass
            if krx_list:
                seen = set(universe)
                for code, name, mkt in krx_list:
                    if code not in seen:
                        universe.append(code); seen.add(code)
                    if name:
                        _krx_names[code] = name
                    if mkt:
                        _krx_mkt[code] = mkt
                print(f"[유니버스] KRX 전종목 {len(krx_list)}종 로드 → 전체 {len(universe)}종 검색 가능.")
        except Exception as _e:
            print(f"[유니버스] KRX 전종목 로드 실패(폴백 사용): {_e}")
        # ── 국내 ETF 목록 ── (DART corpCode엔 ETF가 없으므로 별도 로드)
        # 1) KRX ETF 전종목(하루 캐시) → 2) etfs.csv(사용자) → 3) 내장 주요 ETF
        try:
            import json as _json, time as _time
            from app.providers.krx import fetch_etf_list
            from app.data.etfs import etf_list as _bundled_etf
            etf_rows = []
            etf_cache = "krx_etfs.json"
            if os.path.exists(etf_cache):
                try:
                    with open(etf_cache, encoding="utf-8") as f:
                        cc = _json.load(f)
                    if _time.time() - cc.get("ts", 0) < 86400:
                        etf_rows = [tuple(x) for x in cc.get("rows", [])]
                except Exception:
                    etf_rows = []
            if not etf_rows:
                if not getattr(cfg, "public_mode", False):
                    etf_rows = fetch_etf_list()
                if etf_rows:
                    try:
                        with open(etf_cache, "w", encoding="utf-8") as f:
                            _json.dump({"ts": _time.time(), "rows": etf_rows}, f, ensure_ascii=False)
                    except Exception:
                        pass
            # 사용자 etfs.csv 폴백
            if not etf_rows:
                for _p in ("etfs.csv", "etf.csv"):
                    if os.path.exists(_p):
                        import csv as _csv
                        with open(_p, encoding="utf-8-sig") as _f:
                            for r in _csv.reader(_f):
                                if r and r[0].strip().isdigit() and len(r[0].strip()) == 6:
                                    etf_rows.append((r[0].strip(), r[1].strip() if len(r) > 1 else "", "ETF"))
                        break
            # 내장 주요 ETF는 항상 병합(KRX 성공해도 누락분 보강)
            seen_etf = {c for c, _n, _m in etf_rows}
            for c, n, m in _bundled_etf():
                if c not in seen_etf:
                    etf_rows.append((c, n, m)); seen_etf.add(c)
            # 유니버스/이름/시장 반영
            seen = set(universe)
            for code, name, _m in etf_rows:
                if code not in seen:
                    universe.append(code); seen.add(code)
                if name:
                    _krx_names[code] = name
                _krx_mkt[code] = "ETF"
            print(f"[유니버스] ETF {len(etf_rows)}종 로드 → 검색·시세 가능.")
        except Exception as _e:
            print(f"[유니버스] ETF 로드 실패: {_e}")
        _names = load_corp_names()              # 종목코드 -> 회사명 (corpCode.xml)
        from app.providers.mock import name_of as _mock_name   # 내장 실제 종목명(156종) 폴백
        def resolver(s):
            n = _names.get(s)                   # 1순위: DART corpCode 맵
            if n:
                return n
            kn = _krx_names.get(s)              # 2순위: KRX 전종목/ETF 이름
            if kn:
                return kn
            try:
                from app.data.etfs import etf_name_of as _en
                en = _en(s)                     # 3순위: 내장 ETF 이름
                if en:
                    return en
            except Exception:
                pass
            mn = _mock_name(s)                  # 4순위: 내장 실제 종목명
            return mn if mn and mn != s else s  # 5순위: 코드 그대로
        # ── 검색 전용 전체 유니버스 ──
        # 화면 랭킹(수급 상위)은 RECO_UNIVERSE 설정에 따라 watchlist일 수 있으나,
        # 검색은 항상 전 상장종목을 대상으로 해야 한다(corp_map 3900여 종 + KRX + 내장 + 현재 유니버스).
        _search_set = set(universe)
        try:
            for code in load_corp_map().keys():     # DART corpCode(상장사 전체)
                _search_set.add(code)
            for code in _krx_names.keys():           # KRX 전종목 + ETF 이름
                _search_set.add(code)
            from app.providers.mock import _REAL_STOCKS as _RS2
            for code, _n, _m in _RS2:
                _search_set.add(code)
            from app.data.etfs import etf_list as _el2     # 내장 주요 ETF
            for code, _n, _m in _el2():
                _search_set.add(code)
        except Exception:
            pass
        search_universe = sorted(_search_set)
        print(f"[검색] 검색 대상 전체 종목: {len(search_universe)}종 (랭킹 유니버스: {len(universe)}종)")
        # 실시간 체결/호가 피드 (단타 tick/orderbook)
        if cfg.realtime and cfg.kis_app_key and cfg.kis_app_secret:
            from app.providers.kis_ws import KISRealtimeFeed
            realtime = KISRealtimeFeed(cfg.kis_app_key, cfg.kis_app_secret, ssot, universe,
                                       clock=clock, paper=cfg.kis_paper, bus=bus)

    service = RecommendationService(
        ssot, provider, store, clock,
        name_resolver=resolver, bus=bus,
        allow_uncalibrated=cfg.allow_uncalibrated,
        market_map=_krx_mkt,
    )
    verdict = VerdictEvaluator(ssot, store, clock)
    market = None
    if cfg.data_source != "mock":
        from app.providers.market_data import MarketDataProvider
        market = MarketDataProvider()
    _print_mode_banner(cfg, universe, quote, press_news)
    try:
        from app.api.routes import BUILD_VERSION
        print(f"[빌드] 서버 코드 버전: {BUILD_VERSION}  (브라우저에서 우상단 버전과 일치해야 최신)")
    except Exception:
        pass
    return AppContext(cfg, clock, bus, ssot, store, provider, service, verdict,
                      llm, resolver, universe, realtime=realtime, quote=quote,
                      press_news=press_news, market=market, dart=dart_provider,
                      google_news=google_news, policy_news=policy_news, errors=ErrorCounter(),
                      search_universe=search_universe)


def _print_mode_banner(cfg, universe, quote, press_news) -> None:
    """서버 시작 시 데이터 소스/키 상태를 콘솔에 명확히 표시."""
    line = "=" * 56
    print("\n" + line)
    from app.config import DOTENV_PATH
    if not DOTENV_PATH:
        print("  ⚠ .env 파일을 찾지 못했습니다! 키가 하나도 안 읽힙니다.")
        print("    이 폴더에 .env 가 있는지 확인하세요 (메모장이 .env.txt 로 저장했을 수 있음).")
        print("    " + "-" * 50)
    if cfg.data_source != "live":
        print("  데이터 소스: ⚠ MOCK (데모) — 실시간 아님!")
        print("  화면의 시세·뉴스는 가짜 샘플입니다.")
        print("  실시간으로 보려면 .env 에 RECO_DATA_SOURCE=live 로 바꾸고")
        print("  KIS/DART/NAVER 키를 넣은 뒤 다시 실행하세요 (라이브_실행.bat).")
    else:
        def mark(b):
            return "✓ 있음" if b else "✗ 없음"
        from app.config import load_corp_map, load_corp_names
        cmap, cnames = load_corp_map(), load_corp_names()
        print("  데이터 소스: ● LIVE (실시간 실데이터)")
        print(f"  KIS  현재가/시세 : {mark(bool(quote))}  "
              f"(도메인: {'실전' if not cfg.kis_paper else '모의(KIS_PAPER=true)'})")
        print(f"  DART 재무/공시   : {mark(bool(cfg.dart_api_key))}  "
              f"· 종목명맵 {len(cnames)}종 · corp맵 {len(cmap)}종")
        if cfg.dart_api_key and not cmap:
            print("    ⚠ 공시가 안 나옵니다: DART 키는 있으나 corp_code 맵이 비었습니다.")
            print("      → 'python launch.py preflight' 실행(맵 자동 생성). 키가 이메일 인증 후")
            print("        활성화됐는지도 확인하세요(발급 직후엔 corpCode.xml 다운로드가 안 됨).")
        elif cfg.dart_api_key and cmap:
            print(f"    ✓ 공시 준비됨 (corp_code {len(cmap)}종 매핑)")
        print(f"  NAVER 실시간기사 : {mark(bool(press_news))}")
        print(f"  관심종목(유니버스): {len(universe)}종")
        if not quote:
            print("  ⚠ KIS 키가 없어 실시간 시세가 안 됩니다 (KIS_APP_KEY/SECRET 확인).")
        if not cnames:
            print("  ⚠ 종목명 맵이 없습니다. preflight 또는 build_dart_corpmap 실행 필요")
            print("    (종목명·네이버 기사 검색이 코드로 나가 결과가 비어 보입니다).")
        if cfg.kis_paper:
            print("  ⚠ KIS_PAPER=true(모의). 실전 계좌면 .env 에서 false 로 바꾸세요.")
    print(line + "\n")


def create_app(cfg: Optional[Config] = None):
    """FastAPI 앱 생성. fastapi 미설치 시 명확한 안내."""
    try:
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.staticfiles import StaticFiles
    except ImportError as e:
        raise RuntimeError(
            "FastAPI 미설치. `pip install -r requirements.txt` 후 실행하세요."
        ) from e

    import os
    from app.api.routes import register_routes
    from app.api.ws import register_ws

    ctx = build_context(cfg)
    app = FastAPI(title="멀티-호라이즌 종목 추천", version="0.1.0")
    # GZip 압축 — 큰 index.html(약 250KB)을 ~50KB로 줄여 접속 속도를 크게 개선.
    try:
        from fastapi.middleware.gzip import GZipMiddleware
        app.add_middleware(GZipMiddleware, minimum_size=1024)
    except Exception:
        pass
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])
    app.state.ctx = ctx

    # 시작 시 데이터 준비. mock=1회 적재 / live=1차 배치 동기 + 나머지 백그라운드.
    @app.on_event("startup")
    def _startup() -> None:
        # 공개 호스팅 모드: 시세·수급을 쓰지 않으므로 시장데이터 적재를 전부 건너뛴다.
        # → 시작 속도 대폭 단축(KRX 목록 fetch·OHLCV/수급 스코어링·장중 갱신 스레드 미실행).
        if getattr(ctx.config, "public_mode", False):
            print("[공개모드] 시장데이터 적재를 건너뜁니다(시작 속도 최적화). "
                  "공시·뉴스·캘린더·커뮤니티만 사용합니다.")
            return
        if ctx.config.data_source == "mock":
            ctx.service.refresh_data(ctx.universe, ctx.all_kinds())
            return
        uni = list(ctx.universe or [])
        all_kinds = ctx.all_kinds()
        # 1차 배치: 상위 일부는 전체 데이터로 즉시 적재(바로 추천 가능)
        head = uni[:80]
        try:
            ctx.service.refresh_data(head, all_kinds)
        except Exception:
            pass
        # 나머지: 백그라운드로 OHLCV 위주 점진 적재(서버는 즉시 응답, KIS 호출 분산)
        rest = uni[80:]
        if rest:
            import threading
            from app.data.schema import Kind as _K

            def _bg_load() -> None:
                batch = 40
                for i in range(0, len(rest), batch):
                    chunk = rest[i:i + batch]
                    try:
                        ctx.service.refresh_data(chunk, [_K.OHLCV.value, _K.SUPPLY.value])
                    except Exception:
                        pass
                print(f"[유니버스] 백그라운드 적재 완료: 총 {len(uni)}종 OHLCV+수급 준비됨.")

            threading.Thread(target=_bg_load, daemon=True, name="bg-universe-load").start()
        # 장중 주기적 갱신 — 거래대금/거래량은 장중에 계속 누적되므로 주기적으로 새로 받아온다.
        # 레이트리밋 고려: 수급(supply) 적재된 종목(=화면에 보이는 핵심)만, 배치+딜레이로 순환.
        if ctx.config.data_source != "mock":
            import threading as _th
            import time as _tm
            from app.core.clock import is_market_hours as _is_mkt
            from app.data.schema import Kind as _K2

            def _intraday_refresh() -> None:
                _tm.sleep(60)        # 초기 적재 후 시작
                while True:
                    try:
                        now = ctx.clock.now()
                        hols = set()
                        try:
                            hols = set(ctx.service.store.get_setting("market_holidays", []) or [])
                        except Exception:
                            hols = set()
                        from app.core.clock import to_kst as _tk
                        today = _tk(now).strftime("%Y-%m-%d")
                        if _is_mkt(now) and today not in hols:
                            # 수급 적재된 종목만(화면 핵심), 최대 300종을 40개씩 갱신
                            loaded = [s for s in ctx.ssot.symbols()
                                      if ctx.ssot.get(s, _K2.SUPPLY.value) is not None][:300]
                            for i in range(0, len(loaded), 40):
                                chunk = loaded[i:i + 40]
                                try:
                                    ctx.service.refresh_data(chunk, [_K2.OHLCV.value])
                                except Exception:
                                    pass
                                _tm.sleep(2)     # 배치 간 딜레이(레이트리밋)
                            _tm.sleep(60)        # 한 바퀴 후 60초 대기
                        else:
                            _tm.sleep(180)       # 장외: 3분마다 장중 여부만 확인
                    except Exception:
                        _tm.sleep(120)

            _th.Thread(target=_intraday_refresh, daemon=True, name="intraday-refresh").start()
            print("[갱신] 장중 거래대금/거래량 주기적 갱신 스레드 시작.")
        if ctx.realtime is not None:
            ctx.realtime.start()                                 # 실시간 체결/호가(WS)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        if ctx.realtime is not None:
            ctx.realtime.stop()

    register_routes(app, ctx)
    register_ws(app, ctx)

    # 정적 프론트(web/index.html) 서빙 — index.html 은 항상 no-cache(옛 캐시로 인한 오작동 방지)
    web_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "web")
    if os.path.isdir(web_dir):
        from fastapi.responses import FileResponse, Response

        _NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache", "Expires": "0"}

        @app.get("/")
        def _index() -> FileResponse:
            return FileResponse(os.path.join(web_dir, "index.html"), headers=_NOCACHE)

        @app.get("/index.html")
        def _index2() -> FileResponse:
            return FileResponse(os.path.join(web_dir, "index.html"), headers=_NOCACHE)

        # 아이콘·매니페스트는 1일 캐시(반복 접속 시 재다운로드 안 함)
        _CACHE1D = {"Cache-Control": "public, max-age=86400"}
        for _fn, _mt in (("icon-192.png", "image/png"), ("icon-512.png", "image/png"),
                         ("manifest.json", "application/manifest+json")):
            _p = os.path.join(web_dir, _fn)
            if os.path.exists(_p):
                def _mk(path=_p, mt=_mt):
                    def _serve() -> FileResponse:
                        return FileResponse(path, media_type=mt, headers=_CACHE1D)
                    return _serve
                app.get("/" + _fn)(_mk())

        # 나머지 정적 파일(있다면)
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app

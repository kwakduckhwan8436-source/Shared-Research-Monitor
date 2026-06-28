"""라이브 연동 점검 도구 (preflight).

.env 의 키로 실제 API 를 호출해 무엇이 동작하는지 확인하고, DART corp_code 맵을 생성한다.
네트워크와 키가 있는 사용자 환경에서 실행하면, 실시간 연동 준비를 한 번에 점검·세팅한다.

실행:
    python tools/preflight.py            # 점검 + corp 맵 생성
    python tools/preflight.py --symbol 000660   # 다른 종목으로 점검

각 항목 ✓/✗ 와 파싱된 실제 값을 출력하므로, 필드명·연결 파라미터가 맞는지 눈으로 확인할 수 있다.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

# 프로젝트 루트 import 경로
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config, load_corp_map, load_corp_names  # noqa: E402

OK = "  \033[92m✓\033[0m"
NO = "  \033[91m✗\033[0m"
SK = "  \033[90m–\033[0m"
HDR = "\033[1m\033[96m"
END = "\033[0m"


def _hdr(t: str) -> None:
    print(f"\n{HDR}{t}{END}")


def main() -> int:
    symbol = "005930"
    if "--symbol" in sys.argv:
        i = sys.argv.index("--symbol")
        if i + 1 < len(sys.argv):
            symbol = sys.argv[i + 1]

    cfg = Config()                              # .env 자동 로딩
    now = datetime.now(timezone.utc)
    results: dict[str, bool] = {}

    print(f"{HDR}━━━ 라이브 연동 점검 (종목 {symbol}) ━━━{END}")
    from app.config import DOTENV_PATH
    if DOTENV_PATH:
        print(f".env 파일: {DOTENV_PATH}")
    else:
        print(f"{NO} .env 파일을 찾지 못했습니다!")
        print("     ➤ 이 폴더에 '.env' 파일이 있는지 확인하세요(파일명이 .env.txt 면 .env 로 변경).")
        print("     ➤ 만들기:  copy .env.example .env   그다음  notepad .env")
    print(f"데이터 소스 설정(RECO_DATA_SOURCE): {cfg.data_source}"
          + ("   ← 'live' 여야 실데이터로 동작" if cfg.data_source != "live" else "  ✓"))

    # ---------- 1. KIS (시세·일봉·수급·현재가) ----------
    _hdr("1) 한국투자증권(KIS) — 시세·일봉·수급·현재가")
    if cfg.kis_app_key and cfg.kis_app_secret:
        try:
            from app.providers.kis import KISProvider
            kis = KISProvider(cfg.kis_app_key, cfg.kis_app_secret, paper=cfg.kis_paper)
            kis._ensure_token()
            print(f"{OK} 토큰 발급 성공 (도메인: {'모의' if cfg.kis_paper else '실전'})")
            try:
                dp = kis.fetch(symbol, "ohlcv", now=now)
                bars = dp.payload.get("bars", [])
                last = bars[-1] if bars else {}
                print(f"{OK} 일봉 {len(bars)}봉 · 최근 종가 {last.get('c')} · 거래량 {last.get('v')}")
                results["KIS 일봉"] = True
            except Exception as e:
                print(f"{NO} 일봉 실패: {e}")
                results["KIS 일봉"] = False
            try:
                q = kis.current_price(symbol)
                print(f"{OK} 실시간 현재가 {q['price']} ({q['change_pct']:+}%) · 거래량 {q['volume']}")
                results["KIS 현재가"] = True
            except Exception as e:
                print(f"{NO} 현재가 실패: {e}")
                results["KIS 현재가"] = False
            try:
                sup = kis.fetch(symbol, "supply", now=now)
                daily = sup.payload.get("daily", [])
                d0 = daily[-1] if daily else {}
                print(f"{OK} 투자자 수급 {len(daily)}일 · 최근 외인 {d0.get('foreign_net')} 기관 {d0.get('inst_net')}")
                print(f"     ⚠ 외인/기관 순매수 값이 이상하면 KIS 수급 응답 필드명 확인 필요(V18.2 대조)")
                results["KIS 수급"] = True
            except Exception as e:
                print(f"{NO} 수급 실패: {e}")
                results["KIS 수급"] = False
        except Exception as e:
            print(f"{NO} 토큰 발급 실패: {e}")
            print(f"     ➤ 실전 계좌면 .env 에서 KIS_PAPER=false 로 설정했는지 확인하세요."
                  f" (지금: KIS_PAPER={'true(모의)' if cfg.kis_paper else 'false(실전)'})")
            print(f"     ➤ 또한 발급한 앱키가 '{'모의투자' if cfg.kis_paper else '실전투자'}'용인지 확인"
                  f" (KIS는 실전/모의 앱키가 다릅니다).")
            results["KIS"] = False
    else:
        print(f"{SK} KIS_APP_KEY / KIS_APP_SECRET 미설정 — 시세·수급 비활성")

    # ---------- 2. DART (재무·공시) + corp 맵 ----------
    _hdr("2) DART(전자공시) — 재무·실시간 공시 + 종목명")
    if cfg.dart_api_key:
        # corp 맵 없으면 생성
        if not os.path.exists("dart_corp_map.json"):
            print("  corp_code 맵이 없어 생성합니다 (build_dart_corpmap)...")
            try:
                from tools.build_dart_corpmap import main as build_map
                build_map()
            except SystemExit:
                pass
            except Exception as e:
                print(f"{NO} corp 맵 생성 실패: {e}")
        corp_map = load_corp_map()
        names = load_corp_names()
        if corp_map:
            print(f"{OK} corp_code 맵 {len(corp_map)}종 · 종목명 맵 {len(names)}종 "
                  f"(예: {symbol} → {names.get(symbol, '미상')})")
            results["DART corp맵"] = True
        else:
            print(f"{NO} corp_code 맵이 비어있음 — DART_API_KEY 확인")
            results["DART corp맵"] = False
        try:
            from app.providers.dart import DARTProvider
            dart = DARTProvider(cfg.dart_api_key, corp_map)
            fin = dart.fetch(symbol, "financials", now=now)
            if fin:
                p = fin.payload
                print(f"{OK} 재무: 매출 {p.get('revenue')} 영업익 {p.get('op_income')} "
                      f"부채비율 {p.get('debt_ratio')}% (YoY 매출 {p.get('revenue_yoy')})")
                results["DART 재무"] = True
            else:
                print(f"{SK} 재무 데이터 없음(해당 종목 보고서 없음 가능)")
        except Exception as e:
            print(f"{NO} 재무 실패: {e}")
            results["DART 재무"] = False
        try:
            from app.providers.news import NewsProvider
            news = NewsProvider(cfg.dart_api_key, corp_map)
            nd = news.fetch(symbol, "news", now=now)
            items = nd.payload.get("items", []) if nd else []
            print(f"{OK} 실시간 공시 {len(items)}건"
                  + (f" · 최근: {items[0]['title'][:30]}" if items else " (최근 공시 없음)"))
            for it in items[:3]:
                print(f"       · {it['published_at'][:10]} {it['title'][:34]} → {it.get('link','')[:40]}")
            results["DART 공시"] = True
        except Exception as e:
            print(f"{NO} 공시 실패: {e}")
            results["DART 공시"] = False
    else:
        print(f"{SK} DART_API_KEY 미설정 — 재무·공시·종목명 비활성 (opendart.fss.or.kr 무료 발급)")

    # ---------- 3. 네이버 뉴스(실시간 기사) ----------
    _hdr("3) 네이버 뉴스 검색 — 실시간 언론 기사")
    if cfg.naver_client_id and cfg.naver_client_secret:
        try:
            from app.providers.naver_news import NaverNewsProvider
            names = load_corp_names()
            name = names.get(symbol, symbol)
            nv = NaverNewsProvider(cfg.naver_client_id, cfg.naver_client_secret)
            arts = nv.fetch_news(name, now)
            print(f"{OK} '{name}' 기사 {len(arts)}건"
                  + (f" · 최근: {arts[0]['title'][:30]}" if arts else ""))
            for a in arts[:3]:
                print(f"       · {a['published_at'][:10]} {a['title'][:34]}")
            results["네이버 뉴스"] = True
        except Exception as e:
            print(f"{NO} 네이버 뉴스 실패 — 키 확인: {e}")
            results["네이버 뉴스"] = False
    else:
        print(f"{SK} NAVER_CLIENT_ID / SECRET 미설정 — 인라인 기사 비활성 "
              f"(검색 버튼은 키 없이도 동작) · developers.naver.com 무료 발급")

    # ---------- 4. KRX (공매도) ----------
    _hdr("4) KRX — 공매도 잔고")
    try:
        from app.providers.krx import KRXProvider
        krx = KRXProvider()
        sd = krx.fetch(symbol, "short", now=now)
        if sd:
            print(f"{OK} 공매도 잔고비중 {sd.payload.get('short_balance_ratio')}% · 추세 {sd.payload.get('trend')}")
            results["KRX 공매도"] = True
        else:
            print(f"{SK} 공매도 데이터 없음")
    except Exception as e:
        print(f"{NO} KRX 실패(키 불필요, bld 코드 확인): {e}")
        results["KRX 공매도"] = False

    # ---------- 요약 ----------
    _hdr("━━━ 요약 ━━━")
    ok_n = sum(1 for v in results.values() if v)
    for k, v in results.items():
        print((OK if v else NO) + f" {k}")
    print(f"\n{ok_n}/{len(results)} 항목 동작")
    if cfg.data_source != "live":
        print("\n다음 단계: .env 에서 RECO_DATA_SOURCE=live 로 바꾼 뒤 웹서버를 실행하세요.")
    else:
        print("\n준비 완료. 웹서버를 실행하세요:  python launch.py web   (또는 라이브_실행.bat)")
    print("관심종목은 watchlist.txt 또는 RECO_WATCHLIST 로 지정합니다(전종목 라이브는 느림).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

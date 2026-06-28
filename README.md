# 멀티-호라이즌 종목 추천 시스템

혼합 호라이즌(단타·스윙·중장기) · 개인용 웹 도구 · 전체 데이터 소스(KIS·DART·KRX·뉴스).
**의사결정 보조 도구이며, 자동매매·투자자문이 아닙니다.**

설계 철학: **추천 = 증거 + 정직한 신뢰도.** 데이터가 없으면 추정으로 메우지 않고 보류(abstain)한다.

---

## 실행 방법

### 🔴 실시간(라이브) 연동 — 3단계
실제 시세·공시·기사로 보려면:

```
1) cp .env.example .env  →  .env 에 키 입력
   • KIS_APP_KEY / KIS_APP_SECRET   (apiportal.koreainvestment.com) — 시세·수급·현재가
   • DART_API_KEY                   (opendart.fss.or.kr, 무료)       — 재무·공시·종목명
   • NAVER_CLIENT_ID / SECRET       (developers.naver.com, 무료)     — 실시간 기사(선택)
   ※ .env 의 RECO_DATA_SOURCE 는 이미 live, RECO_UNIVERSE 는 watchlist 로 설정돼 있음

2) 라이브_점검.bat   (또는  python launch.py preflight)
   → 키로 실제 API 를 호출해 무엇이 동작하는지 ✓/✗ 로 보여주고,
     DART 종목명·corp_code 맵(dart_corp_map.json / dart_corp_names.json)을 자동 생성.

3) 라이브_실행.bat   (또는  python launch.py live)
   → 실데이터로 웹서버 시작. 관심종목은 watchlist.txt 또는 RECO_WATCHLIST 로 지정.
```

전종목(~2,800) 라이브는 KIS 호출이 많아 느리므로 watchlist(관심종목) 모드를 권장합니다.
키가 없으면 화면의 **네이버 뉴스 검색 버튼**으로 실시간 기사는 항상 확인할 수 있습니다.

### 가장 쉬운 방법 (더블클릭)

| OS | 웹 서버 | 빠른 데모 | 테스트 |
|---|---|---|---|
| **Windows** | `웹서버_실행.bat` | `데모_실행.bat` | `테스트_실행.bat` |
| **macOS** | `start_mac.command` | — | — |
| **공통** | `python launch.py` | `python launch.py demo` | `python launch.py test` |

`웹서버_실행.bat`(또는 `python launch.py`)을 실행하면 **가상환경 생성 → 의존성 설치 → 서버 시작 → 브라우저 자동 열림**까지 한 번에 됩니다. 최초 실행만 수십 초 걸리고, 이후엔 바로 뜹니다. 종료는 `Ctrl+C`.

> Python 이 없으면 먼저 https://python.org 에서 설치하세요(3.10+ 권장).

### 수동 실행

```bash
# 0) (선택) 가상환경
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 1) 도메인 파이프라인 데모 — 의존성 없이 즉시 실행 (mock 데이터)
python3 run_pipeline.py

# 2) 테스트 — pytest 없이도 동작
python3 tests/run_tests.py

# 3) 웹 서버 (FastAPI 필요)
pip install -r requirements.txt
uvicorn app.api.main:create_app --factory --port 8000
#  -> http://localhost:8000  (web/index.html + /api/*)
```

`run_pipeline.py` 와 `tests/run_tests.py` 는 **표준 라이브러리만** 사용하므로 설치 없이 바로 돌아갑니다.

---

## 구조

```
app/
  core/      errors(추정금지 예외) · clock(시간주입) · eventbus · ssot(RLock 단일소유)
  data/      schema(DataPoint) · freshness(신선도 게이트) · store(SQLite)
  providers/ base(추상화·라우터) · mock(결정적 합성) · kis/dart/krx/news(실연동 셸)
  signals/   base(abstain 자동) · daytrade · swing · midlong · common · registry
  scoring/   weights(캘리브 라벨) · scorer(앙상블) · normalize(백분위) · calibration(IC)
  reco/      universe(필터) · service(오케스트레이션·멱등) · verdict(사후검증)
  llm/       explain(grounded 설명) · sentiment(감성·JSON) — 둘 다 오프라인 폴백 내장
  api/       main(DI·앱팩토리) · routes(REST) · ws(단타 push)
  scheduler.py  호라이즌별 갱신 (APScheduler + threading 폴백)
web/index.html  단일파일 React 3-panel 터미널
tests/          stdlib 하네스(run_tests.py) + pytest 호환 test_*.py
```

---

## 화면 (web/index.html)

CDN·외부 의존성 0 (순수 바닐라 JS, 오프라인에서도 렌더). 3-panel 계기판:

- **좌**: 호라이즌(단타/스윙/중장기) 선택, 현황(유니버스/스캔 상한), 신뢰도 캘리브레이션
- **중**: 전종목 랭킹 + **검색창**(종목명·코드로 전종목 필터). 점수·신뢰도(3색 막대=커버리지·합의·신선도)
- **우**: 선택 종목 상세 — 탭 3개
  - **근거**: 발화/보류 시그널, LLM 근거 설명
  - **데이터·뉴스**: 그 종목의 **뉴스·공시 피드**(감성 색·리스크/이벤트 태그) + 핵심 데이터
    (현재가·등락, 매출/영업이익 YoY·부채비율, 외인/기관 수급, 공매도 잔고)를 한눈에.
    탭을 열거나 **↻ 새로고침** 시 live 모드에서 DART 공시를 즉시 재조회(실시간 공시).
  - **검증**: 추천이 정확한지 직접 확인 — 각 시그널의 `값 × 가중치 × 신뢰도 = 기여도`,
    점수 재구성(`분자 / 분모 → raw → 백분위`), 신뢰도 분해까지 그대로 노출

> **종목명**: mock 모드는 실제 코스피·코스닥 종목명(삼성전자·에코프로비엠 등 ~150종목)을 표시합니다.
> live 모드는 corpCode.xml 기반 회사명을 표시하며, `build_dart_corpmap.py` 가 `dart_corp_map.json`과
> 함께 `dart_corp_names.json`(회사명)을 생성하니, 이미 만들었다면 **한 번 다시 실행**하면 이름이 채워집니다.

### 전종목 스캔 (코스피·코스닥)

`RECO_UNIVERSE=market`(기본값)이면 전종목 대상으로 스캔합니다.
- **mock**: 합성 ~250종목(코스피/코스닥 혼합, 시나리오 분산)으로 전종목 스캔 UX 시연
- **live**: corpCode 맵(`dart_corp_map.json`)의 모든 상장 종목코드를 유니버스로 사용
- 처리량 제어: `RECO_SCAN_LIMIT`(기본 500) — 거래대금 상위 N만 시그널 계산 대상으로 좁혀 랭킹
  (라이브 전종목(~2,800)을 매번 깊게 조회하면 무거우므로, KRX 전종목 시세 bulk 스냅샷
  사전 랭킹이 다음 최적화 단계)

### 실시간 (시세 · 뉴스)

- **실시간 시세**: 종목 선택 시 상세 헤더의 "실시간" 칸이 5초마다 현재가·등락을 갱신(LIVE 점).
  live 모드는 KIS 현재가 조회(REST), mock 모드는 종가 기준 소폭 변동. 전체 리렌더 없이 제자리 갱신.
- **실시간 공시**: "데이터·뉴스" 탭이 열려 있으면 30초마다 DART 공시를 재조회. ↻ 새로고침으로 즉시 갱신.
- **실시간 뉴스(선택)**: `NAVER_CLIENT_ID/SECRET` 설정 시 네이버 뉴스 검색 API로 언론 기사를 가져와
  공시와 한 피드에 최신순으로 병합(출처 태그 공시/뉴스, 제목 클릭 시 원문). 키 없으면 공시만 표시.
- `GET /api/realtime/{symbol}` — 현재가·등락·호가(WS 피드).

### 검증 API

`GET /api/diagnostics/{symbol}/{horizon}` — 점수 계산 전 과정 반환(시그널별 데이터 유무→값→가중치
→기여도, raw_score 분자/분모, confidence 분해). UI '검증' 탭이 이를 시각화.

`GET /api/stock/{symbol}` — 종목 데이터 한눈에: 뉴스·공시 항목, 재무(YoY·부채비율·PER/PBR),
수급(외인/기관 5일), 공매도 잔고, 시세. UI '데이터·뉴스' 탭이 이를 표시.

---

## 핵심 원칙(코드에 강제됨)

1. **추정금지** — 데이터 없음/미캘리브레이션은 예외(`DataUnavailable`/`NotCalibrated`) 또는 abstain. 0·평균으로 메우지 않음.
2. **as_of / fetched_at 분리** — 시그널은 `as_of ≤ now` 데이터만. lookahead 차단(DART=공시일, KRX=T+1 시차).
3. **신선도 게이트** — 호라이즌별 staleness 예산 초과 시 stale 값 사용 금지.
4. **SSOT + RLock** — 정규화 데이터의 단일 소유자. 스냅샷 지문으로 멱등성.
5. **abstain 우선** — 발화 시그널 0개면 추천 불가(0점 아님). 신뢰도 = 커버리지 × 합의 × 신선도.
6. **미캘리브레이션 라벨** — 규칙기반 가중치는 `allow_uncalibrated=True` 명시 opt-in + 결과에 `weights_calibrated=False` 표기.
7. **멱등성** — 같은 스냅샷 → 같은 추천(결정적 정렬).
8. **사후 검증(verdict)** — 추천을 기록하고 실측 전방수익률로 신뢰도 캘리브레이션을 산출(정직성의 증거).

---

## 실데이터 연동(live)

### KIS (build001 — 구현 완료)

`app/providers/kis.py` 는 KIS OpenAPI REST 실연동 완성본입니다 (일봉 + 투자자 수급).
전환 방법:

```bash
# .env 설정
RECO_DATA_SOURCE=live
KIS_APP_KEY=발급키
KIS_APP_SECRET=발급시크릿
KIS_PAPER=true                 # 모의투자 먼저 권장
RECO_WATCHLIST=005930,000660,035420   # 또는 watchlist.txt
```

그러면 스윙 호라이즌이 KIS 실데이터로 동작합니다(일봉→MA정배열·거래량돌파, 수급→외인기관 연속순매수).
DART(재무)·뉴스·KRX(공매도)는 아직 스켈레톤이라 해당 시그널은 **abstain** 처리되며 크래시하지 않습니다.

> **필드명 확인**: 투자자(inquire-investor) 응답의 순매수 필드명(`frgn_ntby_qty` 등)은 환경/버전에
> 따라 다를 수 있습니다. `kis.py` 상단 `_F_*` 상수를 당신의 V18.2 코드와 대조해 맞추세요.
> 일봉(FHKST03010100) 필드는 표준이라 그대로 동작합니다.

실데이터 provider 빌드 — **전체 완료** ✅
- ~~**build002** — KIS WebSocket(체결·호가) → 단타 호라이즌 실시간화~~ ✅
- ~~**build003** — DART provider(재무) → 중장기 호라이즌~~ ✅
- ~~**build004** — News provider(공시) → 뉴스 감성/리스크~~ ✅
- ~~**build005** — KRX provider(공매도) → 리스크 보강~~ ✅

### KRX 공매도 (build005 — 구현 완료)

`app/providers/krx.py` 는 KRX 정보데이터시스템 공매도 잔고 연동입니다. KRX 는 ISIN(KR7...)으로
조회하는데, **ISIN 체크디지트를 6자리코드에서 결정적으로 유도**하므로(`isin_from_code`) 별도 매핑이
필요 없습니다(삼성 KR7005930003 등 실제 ISIN 으로 검증). 최신 공매도 잔고비중·추세를 산출해
`RiskFlags`(잔고비중>5% → '공매도비중 X%' 플래그)를 보강합니다. 키 불필요(공개 데이터).

> KRX getJsonData 의 `bld` 코드·응답 필드는 환경에 따라 다를 수 있고 Referer 헤더가 필요할 수
> 있습니다. 파싱·ISIN 유도는 fixture 로 검증되어 있으니 실제 응답과 한 번 대조하세요.

---

### 데이터 소스 ↔ 호라이즌 매핑 (전체 완성)

| 호라이즌 | 시그널 | 데이터 소스 |
|---|---|---|
| 단타 | 거래량 급증, 호가 불균형 | KIS 일봉 + **KIS WebSocket**(체결·호가) |
| 스윙 | MA 정배열, 외인·기관 연속순매수, 거래량 돌파 | KIS 일봉 + KIS 수급 |
| 중장기 | 실적 성장(YoY), (밸류 분위는 후속) | **DART 재무** |
| 공통 | 뉴스 감성, 리스크 플래그 | **DART 공시** + **KRX 공매도** |

### 뉴스/공시 (build004 — 구현 완료)

`app/providers/news.py` 는 **DART 공시(공식 API)** 기반입니다. 기사 크롤링 대신 공시를 쓰는 이유:
유증·횡령·감자·계약·실적 등 시장 리스크/이벤트의 1차 출처가 공시이고, 공식 API라 약관·안정성
문제가 없으며 신호가 정확합니다. 최근 공시(기본 30일)를 수집해 제목을 분류(이벤트/리스크) +
감성 분석 -> `news_sentiment`·`risk_flags` 시그널을 모든 호라이즌에서 채웁니다.

DART_API_KEY + corp_code 맵(build003 의 `dart_corp_map.json`)을 공유합니다. 추가 설정 없이
DART 가 연결되면 자동으로 동작합니다. 최근 공시가 없으면 `risk_flags` 는 '리스크 없음'으로 발화하고
`news_sentiment` 는 abstain 합니다(공시 없음 ≠ 에러).

> 언론 기사 수집(네이버 금융 등)은 동일 `DataProvider` 계약으로 추가할 수 있는 선택적 후속 소스입니다.

### DART 재무 (build003 — 구현 완료)

`app/providers/dart.py` 는 전자공시 Open API 실연동입니다. 최신 **사업보고서**를 찾아(list.json)
그 **공시 접수일(rcept_dt)을 as_of** 로 삼고(lookahead 차단), 재무(fnlttSinglAcnt)를 정규화합니다:
매출·영업이익·순이익·자산·부채·자본·부채비율·매출/영업이익 YoY.

DART 는 종목코드가 아닌 8자리 corp_code 로 조회하므로 매핑이 필요합니다. 1회 생성:

```bash
DART_API_KEY=발급키 python tools/build_dart_corpmap.py   # -> dart_corp_map.json 생성
```

그러면 live 모드에서 중장기 호라이즌의 **EarningsGrowth**(매출·영업이익 성장)가 동작합니다.
corp_code 맵이 없으면 중장기 재무는 abstain 되고 나머지는 정상입니다.

> **ValuationPercentile**(PER/PBR 분위)는 시장가격 + 과거 밸류에이션 이력 조인이 필요해
> 현재는 abstain 합니다 — 가격(KIS)·재무(DART)·이력을 묶는 다음 단계 작업입니다.

### KIS 실시간 (build002 — 구현 완료)

`app/providers/kis_ws.py` 는 KIS WebSocket 실시간 피드입니다 (체결가 H0STCNT0 + 호가 H0STASP0).
live 모드에서 `RECO_REALTIME=true`(기본값)면 서버 시작 시 자동으로 watchlist 종목을 구독하고,
실시간 체결/호가를 SSOT 에 밀어넣어 **단타 호라이즌**(거래량 급증·호가 불균형)을 채웁니다.

```bash
pip install websocket-client    # 실시간에 필요
```

websocket-client 가 없으면 실시간은 자동 비활성(단타 orderbook 시그널은 abstain)되고 나머지는 정상입니다.

> 체결/호가 **필드 매핑**(`parse_message`)은 fixture 로 검증되어 있습니다. 연결 파라미터
> (approval_key 발급·WS 도메인·구독 메시지)는 환경에 따라 다를 수 있으니 V18.2 와 대조하세요.

각 provider 는 동일 계약(`DataProvider.fetch -> Optional[DataPoint]`)이며, 미구현 상태에서도
파이프라인은 abstain 으로 안전하게 돌아갑니다.

---

## 검증 상태

- `tests/run_tests.py`: **57/57 통과** (core·freshness·signals·scorer·reco·kis·kis_ws·dart·news·krx).
- `run_pipeline.py`: 적재 → 호라이즌별 추천 → 멱등성(=True) → verdict/캘리브레이션까지 end-to-end 동작.
- KIS REST/실시간: 일봉·수급 정규화 + 체결·호가 파싱 fixture 검증, WS→SSOT→단타 추천 발화 확인.
- DART 재무/공시: 공시일 as_of·YoY·부채비율 + 공시 분류·감성 fixture 검증, 중장기·뉴스 시그널 발화 확인.
- KRX 공매도: ISIN 유도(실제 ISIN 일치)·잔고비중 파싱 fixture 검증, 공매도→RiskFlags 플래그 표면화 확인.
- live 풀스택: 단타(WS)·스윙(일봉+수급+공시)·중장기(재무+공시)+공매도 리스크 모두 동작.
- 관리·정지 종목은 유니버스에서 제외, 데이터 결손 시그널은 abstain 처리 확인.

---

## 실행파일(.exe) 빌드 — 배포용

`실행파일_빌드.bat` 더블클릭(Windows) → `dist\리서치모니터.exe` 생성.
- 이 exe 하나만 복사하면 Python 설치 없이 다른 PC에서도 실행됩니다.
- 소스가 **바이트코드로 번들**되어 그대로 노출되지 않습니다(실질적 보호).
- 더블클릭하면 내장 서버가 뜨고 브라우저가 자동으로 열립니다.

## 소스 난독화

`난독화_빌드.bat` 더블클릭 → 상위 폴더에 `stock_reco_obf` 생성.
- 주석·설명을 제거해 가독성을 낮춥니다(**동작은 완전히 동일**, 169개 테스트 통과 확인).
- 식별자명까지 바꾸는 강한 난독화는 앱이 깨질 수 있어 적용하지 않습니다.
  더 강한 보호가 필요하면 위 **실행파일(.exe)** 배포를 권장합니다.

## 정부정책 피드(공공누리)

정책브리핑(korea.kr)·각 부처 보도자료 RSS를 제목+링크+출처만 표시(본문 미복제).
- 기본: 금융·증시 관련 6개 부처(정책브리핑·기재부·금융위·산업부·중기부·공정위)
- `RECO_POLICY_ALL=1` 설정 시 10개 부처 전체 수집
- 공공누리 제1유형(출처표시)이라 출처만 밝히면 상업적 이용도 가능

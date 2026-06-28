"""MockProvider — 개발/테스트용 결정적 합성 데이터. *실제 시장 데이터가 아니다.*

목적: 네트워크/크레덴셜 없이 전체 파이프라인을 끝까지 돌리고 멱등성을 검증하기 위함.
심볼로 시드된 PRNG 라 같은 심볼 -> 같은 데이터 -> 같은 추천(재현 가능).

운영에서는 이 파일을 KIS/DART/KRX/News provider 로 교체한다. (계약은 동일: DataProvider)
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional

from app.data.schema import DataPoint, Kind
from app.providers.base import DataProvider
from app.llm import sentiment as sentiment_mod

# 데모 유니버스: (symbol, name, status). 일부러 관리/정지 종목을 섞어 유니버스 필터를 검증.
MOCK_UNIVERSE: list[tuple[str, str, str]] = [
    ("005930", "삼성전자", "normal"),
    ("000660", "SK하이닉스", "normal"),
    ("035420", "NAVER", "normal"),
    ("051910", "LG화학", "normal"),
    ("207940", "삼성바이오로직스", "normal"),
    ("006400", "삼성SDI", "normal"),
    ("068270", "셀트리온", "normal"),
    ("247540", "에코프로비엠", "normal"),
    ("900110", "이아이디", "관리"),     # 관리종목 -> 유니버스에서 제외되어야 함
    ("123450", "정지테스트", "정지"),    # 거래정지 -> 제외
]

# 전종목 데모용 실제 코스피·코스닥 종목 (코드·한글명). mock 모드에서 실제 이름이 보이도록.
_REAL_STOCKS: list[tuple[str, str, str]] = [
    ("005930", "삼성전자", "KOSPI"), ("000660", "SK하이닉스", "KOSPI"),
    ("373220", "LG에너지솔루션", "KOSPI"), ("207940", "삼성바이오로직스", "KOSPI"),
    ("005380", "현대차", "KOSPI"), ("000270", "기아", "KOSPI"),
    ("068270", "셀트리온", "KOSPI"), ("035420", "NAVER", "KOSPI"),
    ("005490", "POSCO홀딩스", "KOSPI"), ("051910", "LG화학", "KOSPI"),
    ("006400", "삼성SDI", "KOSPI"), ("035720", "카카오", "KOSPI"),
    ("012330", "현대모비스", "KOSPI"), ("028260", "삼성물산", "KOSPI"),
    ("105560", "KB금융", "KOSPI"), ("055550", "신한지주", "KOSPI"),
    ("066570", "LG전자", "KOSPI"), ("096770", "SK이노베이션", "KOSPI"),
    ("086790", "하나금융지주", "KOSPI"), ("003670", "포스코퓨처엠", "KOSPI"),
    ("032830", "삼성생명", "KOSPI"), ("259960", "크래프톤", "KOSPI"),
    ("323410", "카카오뱅크", "KOSPI"), ("402340", "SK스퀘어", "KOSPI"),
    ("000810", "삼성화재", "KOSPI"), ("033780", "KT&G", "KOSPI"),
    ("316140", "우리금융지주", "KOSPI"), ("003490", "대한항공", "KOSPI"),
    ("011200", "HMM", "KOSPI"), ("034020", "두산에너빌리티", "KOSPI"),
    ("015760", "한국전력", "KOSPI"), ("010950", "S-Oil", "KOSPI"),
    ("024110", "기업은행", "KOSPI"), ("003550", "LG", "KOSPI"),
    ("034730", "SK", "KOSPI"), ("010140", "삼성중공업", "KOSPI"),
    ("009830", "한화솔루션", "KOSPI"), ("090430", "아모레퍼시픽", "KOSPI"),
    ("036570", "엔씨소프트", "KOSPI"), ("251270", "넷마블", "KOSPI"),
    ("021240", "코웨이", "KOSPI"), ("086280", "현대글로비스", "KOSPI"),
    ("097950", "CJ제일제당", "KOSPI"), ("010130", "고려아연", "KOSPI"),
    ("051900", "LG생활건강", "KOSPI"), ("139480", "이마트", "KOSPI"),
    ("011170", "롯데케미칼", "KOSPI"), ("078930", "GS", "KOSPI"),
    ("006800", "미래에셋증권", "KOSPI"), ("029780", "삼성카드", "KOSPI"),
    ("005830", "DB손해보험", "KOSPI"), ("004020", "현대제철", "KOSPI"),
    ("012450", "한화에어로스페이스", "KOSPI"), ("241560", "두산밥캣", "KOSPI"),
    ("138040", "메리츠금융지주", "KOSPI"), ("039490", "키움증권", "KOSPI"),
    ("071050", "한국금융지주", "KOSPI"), ("017670", "SK텔레콤", "KOSPI"),
    ("030200", "KT", "KOSPI"), ("032640", "LG유플러스", "KOSPI"),
    ("035250", "강원랜드", "KOSPI"), ("180640", "한진칼", "KOSPI"),
    ("000120", "CJ대한통운", "KOSPI"), ("000720", "현대건설", "KOSPI"),
    ("006360", "GS건설", "KOSPI"), ("375500", "DL이앤씨", "KOSPI"),
    ("047040", "대우건설", "KOSPI"), ("009150", "삼성전기", "KOSPI"),
    ("018260", "삼성에스디에스", "KOSPI"), ("088350", "한화생명", "KOSPI"),
    ("161390", "한국타이어앤테크놀로지", "KOSPI"), ("271560", "오리온", "KOSPI"),
    ("004990", "롯데지주", "KOSPI"), ("047810", "한국항공우주", "KOSPI"),
    ("042660", "한화오션", "KOSPI"), ("267260", "HD현대일렉트릭", "KOSPI"),
    ("329180", "HD현대중공업", "KOSPI"), ("009540", "HD한국조선해양", "KOSPI"),
    ("010620", "현대미포조선", "KOSPI"), ("267250", "HD현대", "KOSPI"),
    ("000100", "유한양행", "KOSPI"), ("128940", "한미약품", "KOSPI"),
    ("000080", "하이트진로", "KOSPI"), ("023530", "롯데쇼핑", "KOSPI"),
    ("069960", "현대백화점", "KOSPI"), ("008770", "호텔신라", "KOSPI"),
    ("282330", "BGF리테일", "KOSPI"), ("007070", "GS리테일", "KOSPI"),
    ("120110", "코오롱인더", "KOSPI"), ("298020", "효성티앤씨", "KOSPI"),
    ("185750", "종근당", "KOSPI"), ("326030", "SK바이오팜", "KOSPI"),
    ("302440", "SK바이오사이언스", "KOSPI"), ("001040", "CJ", "KOSPI"),
    ("036460", "한국가스공사", "KOSPI"), ("064350", "현대로템", "KOSPI"),
    ("204320", "HL만도", "KOSPI"), ("011210", "현대위아", "KOSPI"),
    ("018880", "한온시스템", "KOSPI"), ("161890", "한국콜마", "KOSPI"),
    ("000990", "DB하이텍", "KOSPI"), ("267270", "HD현대건설기계", "KOSPI"),
    ("042670", "HD현대인프라코어", "KOSPI"), ("017800", "현대엘리베이터", "KOSPI"),
    # KOSDAQ
    ("247540", "에코프로비엠", "KOSDAQ"), ("086520", "에코프로", "KOSDAQ"),
    ("196170", "알테오젠", "KOSDAQ"), ("348370", "엔켐", "KOSDAQ"),
    ("028300", "HLB", "KOSDAQ"), ("068760", "셀트리온제약", "KOSDAQ"),
    ("058470", "리노공업", "KOSDAQ"), ("263750", "펄어비스", "KOSDAQ"),
    ("293490", "카카오게임즈", "KOSDAQ"), ("277810", "레인보우로보틱스", "KOSDAQ"),
    ("214150", "클래시스", "KOSDAQ"), ("035900", "JYP Ent.", "KOSDAQ"),
    ("145020", "휴젤", "KOSDAQ"), ("041510", "에스엠", "KOSDAQ"),
    ("039030", "이오테크닉스", "KOSDAQ"), ("112040", "위메이드", "KOSDAQ"),
    ("005290", "동진쎄미켐", "KOSDAQ"), ("328130", "루닛", "KOSDAQ"),
    ("298380", "에이비엘바이오", "KOSDAQ"), ("240810", "원익IPS", "KOSDAQ"),
    ("213420", "덕산네오룩스", "KOSDAQ"), ("089030", "테크윙", "KOSDAQ"),
    ("087010", "펩트론", "KOSDAQ"), ("039200", "오스코텍", "KOSDAQ"),
    ("383310", "에코프로에이치엔", "KOSDAQ"), ("365340", "성일하이텍", "KOSDAQ"),
    ("066970", "엘앤에프", "KOSDAQ"), ("141080", "리가켐바이오", "KOSDAQ"),
    ("253450", "스튜디오드래곤", "KOSDAQ"), ("178320", "서진시스템", "KOSDAQ"),
    ("121600", "나노신소재", "KOSDAQ"), ("036540", "SFA반도체", "KOSDAQ"),
    ("084370", "유진테크", "KOSDAQ"), ("222800", "심텍", "KOSDAQ"),
    ("046890", "서울반도체", "KOSDAQ"), ("085660", "차바이오텍", "KOSDAQ"),
    ("237690", "에스티팜", "KOSDAQ"), ("145720", "덴티움", "KOSDAQ"),
    ("122870", "와이지엔터테인먼트", "KOSDAQ"), ("035760", "CJ ENM", "KOSDAQ"),
    ("192080", "더블유게임즈", "KOSDAQ"), ("194480", "데브시스터즈", "KOSDAQ"),
    ("078340", "컴투스", "KOSDAQ"), ("067160", "SOOP", "KOSDAQ"),
    ("095340", "ISC", "KOSDAQ"), ("067310", "하나마이크론", "KOSDAQ"),
    ("092040", "아미코젠", "KOSDAQ"), ("357780", "솔브레인", "KOSDAQ"),
    ("036930", "주성엔지니어링", "KOSDAQ"), ("000250", "삼천당제약", "KOSDAQ"),
]
_NAME = {s: n for s, n, _ in MOCK_UNIVERSE}
_STATUS = {s: st for s, _, st in MOCK_UNIVERSE}

# 명명된 대표 종목의 의도적 시나리오 (시그널이 의미있게 반응하도록).
_FOREIGN_STREAK_SYMBOLS = {"005930", "000660", "247540"}   # 외인 연속 순매수
_CHEAP_SYMBOLS = {"051910", "035420"}                       # 저밸류
_NEWS = {
    "005930": ("삼성전자, 분기 최대 실적 흑자 전환", "메모리 수요 회복으로 영업이익 증가, 신고가 경신"),
    "247540": ("에코프로비엠 대규모 공급 계약 체결", "신규 수주로 매출 성장 전망"),
    "068270": ("셀트리온 유상증자 결정", "운영자금 목적 유상증자 공시"),  # 리스크 플래그
    "000660": ("SK하이닉스 어닝 서프라이즈", "HBM 호조로 실적 호조"),
}
# 합성 종목 뉴스 템플릿 (해시로 일부 종목에 배정)
_NEWS_TEMPLATES = [
    ("{n} 단일판매ㆍ공급계약체결", "신규 수주로 매출 성장 기대"),
    ("{n} 유상증자 결정", "운영자금 목적 유상증자 공시"),            # 리스크
    ("{n} 영업(잠정)실적 공시", "전년 동기 대비 영업이익 증가"),
    ("{n} 자기주식취득 결정", "주주가치 제고 목적"),
    ("{n} 신규 시설투자 결정", "생산능력 확대 위한 설비 투자"),
    ("{n} 주요사항보고서(전환사채 발행)", "자금 조달 위한 CB 발행"),  # 리스크
    ("{n} 최대주주 변경", "지분 양수도 계약 체결"),
    ("{n} 현금배당 결정", "주당 배당금 전년比 상향"),
    ("{n} 타법인 주식 취득 결정", "사업 다각화 위한 지분 인수"),
    ("{n} 정기주주총회 소집 결의", "이사 선임 및 정관 변경 안건"),
    ("{n} 신제품 출시 및 양산 시작", "하반기 매출 기여 전망"),
    ("{n} 무상증자 결정", "주주 환원 차원 무상증자"),
]


def _seed(symbol: str, salt: str = "") -> random.Random:
    h = hashlib.md5(f"{symbol}:{salt}".encode()).hexdigest()
    return random.Random(int(h[:8], 16))


# ----- 시나리오 판정 (명명종목=명시, 합성종목=해시 기반 분산) -----
def _has_streak(symbol: str) -> bool:
    if symbol in _FOREIGN_STREAK_SYMBOLS:
        return True
    return _seed(symbol, "scn").random() < 0.22       # 약 22% 외인 순매수 시나리오


def _is_cheap(symbol: str) -> bool:
    if symbol in _CHEAP_SYMBOLS:
        return True
    return _seed(symbol, "cheap").random() < 0.25     # 약 25% 저밸류


def _news_for(symbol: str, now: datetime) -> list[tuple[str, str]]:
    """종목별 뉴스/공시 항목들. 모든 종목이 최소 1건 이상(데모 가시성)."""
    out: list[tuple[str, str]] = []
    if symbol in _NEWS:
        out.append(_NEWS[symbol])
    r = _seed(symbol, "news")
    k = 1 + r.randrange(3)                # 추가 1~3건 (총 1~4건)
    for _ in range(k):
        title, body = _NEWS_TEMPLATES[r.randrange(len(_NEWS_TEMPLATES))]
        out.append((title.format(n=name_of(symbol)), body))
    return out


# ----- 전종목(market) 유니버스 — 실제 코스피·코스닥 종목명 -----
def market_universe(n: int = 250) -> list[tuple[str, str, str]]:
    """실제 코스피·코스닥 종목(코드·한글명). 관리종목 2개를 섞어 유니버스 필터도 검증."""
    out: list[tuple[str, str, str]] = []
    seen = set()
    for code, name, mkt in _REAL_STOCKS:
        if code in seen:
            continue
        seen.add(code)
        out.append((code, name, "normal"))   # status 는 normal (실명 종목 오표기 방지)
    # 관리/정지 종목 데모(유니버스 필터 검증용) — 합성 코드라 실명과 혼동 없음
    out.append(("900110", "이아이디", "관리"))
    out.append(("123450", "거래정지테스트", "정지"))
    return out[:n] if n and n < len(out) else out


_MARKET = market_universe()
_MNAME = {s: n for s, n, _ in _MARKET}
_MSTATUS = {s: st for s, _, st in _MARKET}
_MKT = {code: mkt for code, _, mkt in _REAL_STOCKS}   # 코스피/코스닥 분류


def market_of(symbol: str) -> str:
    """내장 실제 종목의 시장(KOSPI/KOSDAQ). 없으면 ''."""
    return _MKT.get(symbol, "")


# ----- 날짜 기준 결정적 가격 워크 -----
# 가격을 (symbol, date) 로 고정한다 -> 같은 시점이면 재현(멱등), 시간 진행 시 실제 가격 변동.
_EPOCH = date(2025, 1, 1)


def _sym_params(symbol: str) -> tuple[float, float]:
    rnd = _seed(symbol, "params")
    base = rnd.uniform(20_000, 150_000)
    drift = rnd.uniform(-0.0006, 0.0016)
    return base, drift


def _daily_logret(symbol: str, d: date, drift: float) -> float:
    return _seed(symbol, "r:" + d.isoformat()).gauss(drift, 0.018)


@lru_cache(maxsize=128)
def _closes_until(symbol: str, end_iso: str) -> tuple[tuple[str, float], ...]:
    base, drift = _sym_params(symbol)
    end = date.fromisoformat(end_iso)
    days = (end - _EPOCH).days
    out = []
    logp = math.log(base)
    cur = _EPOCH
    for _ in range(days + 1):
        logp += _daily_logret(symbol, cur, drift)
        out.append((cur.isoformat(), max(1000.0, math.exp(logp))))
        cur += timedelta(days=1)
    return tuple(out)


class MockProvider(DataProvider):
    name = "mock"
    supported_kinds = (
        Kind.OHLCV.value, Kind.SUPPLY.value, Kind.FINANCIALS.value,
        Kind.SHORT.value, Kind.NEWS.value, Kind.ORDERBOOK.value, Kind.TICK.value,
    )

    def __init__(self, llm_client: Optional[Any] = None):
        self._llm = llm_client  # 보통 None -> 감성은 오프라인 폴백

    # ----- 개별 kind 생성기 -----
    def _ohlcv(self, symbol: str, now: datetime) -> DataPoint:
        today = now.date()
        series = _closes_until(symbol, today.isoformat())[-120:]
        bars = []
        for d_iso, c in series:
            rnd = _seed(symbol, "bar:" + d_iso)
            o = c * (1 + rnd.uniform(-0.005, 0.005))
            h = max(o, c) * (1 + abs(rnd.uniform(0, 0.012)))
            l = min(o, c) * (1 - abs(rnd.uniform(0, 0.012)))
            v = int(rnd.uniform(0.5e6, 5e6))
            # 거래대금: 일중 평균가(고저종 가중) × 거래량 — 종가×거래량과 살짝 다르게(현실 반영)
            avg_px = (h + l + c * 2) / 4.0
            to = round(avg_px * v)
            bars.append({"date": d_iso, "o": round(o, 1), "h": round(h, 1),
                         "l": round(l, 1), "c": round(c, 1), "v": v, "to": to})
        # 거래량 급증 시나리오(가격은 건드리지 않음 -> 사후수익률 왜곡 없음)
        if _has_streak(symbol) and bars:
            bars[-1]["v"] = int(bars[-1]["v"] * 2.4)
        as_of = now   # 최신 일봉은 fetch 시점 기준 (mock 단순화; 실 provider 는 장마감 시각)
        return DataPoint(symbol, Kind.OHLCV.value,
                         {"bars": bars, "status": _MSTATUS.get(symbol, "normal"),
                          "market": _MKT.get(symbol, "")},
                         as_of=as_of, fetched_at=now, source=self.name)

    def _supply(self, symbol: str, now: datetime) -> DataPoint:
        rnd = _seed(symbol, "supply")
        daily = []
        today = now.date()
        streak = _has_streak(symbol)
        for i in range(20):
            d = today - timedelta(days=(19 - i))
            if streak and i >= 14:
                fn = abs(rnd.gauss(120, 40))        # 최근 5일 외인 순매수
            else:
                fn = rnd.gauss(0, 80)
            inn = rnd.gauss(0, 60)
            daily.append({"date": d.isoformat(),
                          "foreign_net": round(fn, 1), "inst_net": round(inn, 1),
                          "retail_net": round(-(fn + inn), 1)})
        as_of = now   # mock 단순화 (실 provider: KRX T+1 공개 기준일)
        return DataPoint(symbol, Kind.SUPPLY.value, {"daily": daily},
                         as_of=as_of, fetched_at=now, source=self.name)

    def _financials(self, symbol: str, now: datetime) -> DataPoint:
        rnd = _seed(symbol, "fin")
        cheap = _is_cheap(symbol)
        per = rnd.uniform(5, 9) if cheap else rnd.uniform(10, 35)
        pbr = rnd.uniform(0.5, 1.0) if cheap else rnd.uniform(1.2, 4.0)
        rev_yoy = rnd.uniform(-0.1, 0.4)
        op_yoy = rnd.uniform(-0.2, 0.6)
        per_hist = sorted(rnd.uniform(per * 0.7, per * 1.6) for _ in range(8))
        pbr_hist = sorted(rnd.uniform(pbr * 0.7, pbr * 1.6) for _ in range(8))
        # 공시일(as_of)은 며칠 전으로 — 재무는 공시 시점 기준
        as_of = now - timedelta(days=rnd.randint(20, 70))
        return DataPoint(symbol, Kind.FINANCIALS.value, {
            "revenue": round(rnd.uniform(1e11, 5e13), 0),
            "op_income": round(rnd.uniform(1e10, 8e12), 0),
            "net_income": round(rnd.uniform(1e10, 6e12), 0),
            "per": round(per, 2), "pbr": round(pbr, 2),
            "debt_ratio": round(rnd.uniform(30, 180), 1),
            "revenue_yoy": round(rev_yoy, 3), "op_yoy": round(op_yoy, 3),
            "per_hist": [round(x, 2) for x in per_hist],
            "pbr_hist": [round(x, 2) for x in pbr_hist],
        }, as_of=as_of, fetched_at=now, source=self.name)

    def _short(self, symbol: str, now: datetime) -> DataPoint:
        rnd = _seed(symbol, "short")
        ratio = rnd.uniform(0.1, 8.0)
        trend = rnd.choice(["up", "down", "flat"])
        as_of = now - timedelta(hours=12)  # 공매도 공개 시차 가정 (swing 1일 예산 내)
        return DataPoint(symbol, Kind.SHORT.value,
                         {"short_balance_ratio": round(ratio, 2), "trend": trend},
                         as_of=as_of, fetched_at=now, source=self.name)

    def _news(self, symbol: str, now: datetime) -> DataPoint:
        import urllib.parse
        q = urllib.parse.quote(name_of(symbol))
        link = f"https://search.naver.com/search.naver?where=news&query={q}"
        items = []
        for i, (title, body) in enumerate(_news_for(symbol, now)):
            pub = now - timedelta(hours=5 + i * 9)         # 날짜 분산
            analysis = sentiment_mod.analyze(
                {"symbol": symbol, "title": title, "body": body,
                 "published_at": pub.isoformat(), "source": "mock"},
                client=self._llm,
            )
            items.append({
                "title": title, "body": body, "published_at": pub.isoformat(),
                "sentiment": analysis["sentiment"], "events": analysis["events"],
                "risk_flags": analysis["risk_flags"],
                "source": "공시" if i == 0 else "뉴스", "link": link,
            })
        return DataPoint(symbol, Kind.NEWS.value, {"items": items},
                         as_of=now - timedelta(hours=5) if items else now,
                         fetched_at=now, source=self.name)

    def _orderbook(self, symbol: str, now: datetime) -> DataPoint:
        rnd = _seed(symbol, "ob:" + now.isoformat())  # 실시간성: now 포함
        series = _closes_until(symbol, now.date().isoformat())
        mid = series[-1][1] if series else 50_000.0    # 실제 종가 기준
        imbalance = 1.4 if _has_streak(symbol) else rnd.uniform(0.7, 1.3)
        bids = [[round(mid * (1 - 0.001 * (i + 1)), 0), int(rnd.uniform(100, 2000) * imbalance)]
                for i in range(5)]
        asks = [[round(mid * (1 + 0.001 * (i + 1)), 0), int(rnd.uniform(100, 2000))]
                for i in range(5)]
        return DataPoint(symbol, Kind.ORDERBOOK.value, {"bids": bids, "asks": asks},
                         as_of=now, fetched_at=now, source=self.name)

    def _tick(self, symbol: str, now: datetime) -> DataPoint:
        rnd = _seed(symbol, "tick:" + now.isoformat())
        series = _closes_until(symbol, now.date().isoformat())
        base = series[-1][1] if series else 50_000.0   # 실제 종가 기준
        price = base * (1 + rnd.uniform(-0.006, 0.006))  # 장중 소폭 변동
        return DataPoint(symbol, Kind.TICK.value, {
            "price": round(price, 0),
            "qty": int(rnd.uniform(1, 500)),
            "strength": round(rnd.uniform(50, 200), 1),
            "ts": now.isoformat(),
        }, as_of=now, fetched_at=now, source=self.name)

    def fetch(self, symbol: str, kind: str, *, now: datetime) -> Optional[DataPoint]:
        dispatch = {
            Kind.OHLCV.value: self._ohlcv, Kind.SUPPLY.value: self._supply,
            Kind.FINANCIALS.value: self._financials, Kind.SHORT.value: self._short,
            Kind.NEWS.value: self._news, Kind.ORDERBOOK.value: self._orderbook,
            Kind.TICK.value: self._tick,
        }
        fn = dispatch.get(kind)
        return fn(symbol, now) if fn else None


def universe_symbols(market: bool = False) -> list[str]:
    """market=False: 대표 10종목 / market=True: 전종목(합성 ~250) 스캔."""
    if market:
        return [s for s, _, _ in _MARKET]
    return [s for s, _, _ in MOCK_UNIVERSE]


def name_of(symbol: str) -> str:
    return _MNAME.get(symbol, _NAME.get(symbol, symbol))

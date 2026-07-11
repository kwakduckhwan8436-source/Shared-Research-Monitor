"""DART provider — 전자공시(opendart.fss.or.kr) Open API 실연동.

담당 kind: financials (재무제표 + YoY 성장 + 부채비율).

핵심(추정금지 + lookahead 차단):
- 재무의 as_of 는 *공시 접수일(rcept_dt)* 이다. 실적 발표 전 미래 재무를 쓰면 안 된다.
- 그래서 list.json 으로 '가장 최근 사업보고서'의 접수일을 찾고, 그 보고서의 재무를 fnlttSinglAcnt 로 가져온다.

엔드포인트:
- 공시검색:        https://opendart.fss.or.kr/api/list.json     (rcept_dt, 보고서명, 사업연도)
- 단일회사 주요계정: https://opendart.fss.or.kr/api/fnlttSinglAcnt.json (매출/영업이익/순이익/자산/부채/자본, 당기·전기)
인증: crtfc_key 쿼리 파라미터. 종목은 corp_code(8자리) 기준 -> corp_code_map 필요.

산출 payload: revenue, op_income, net_income, total_assets, total_liab, total_equity,
             debt_ratio, revenue_yoy, op_yoy.
(PER/PBR 분위(per_hist/pbr_hist)는 시장가격·과거이력 조인이 필요 -> 후속 빌드. 현재는 미포함이라
 ValuationPercentile 시그널은 abstain, EarningsGrowth 는 정상 발화.)

⚠ 네트워크/키 필요. 필드 매핑(_pick/_amt)은 fixture 로 검증되어 있습니다.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.clock import KST
from app.core.errors import ProviderError
from app.data.schema import DataPoint, Kind
from app.providers.base import DataProvider
from app.providers.kis import UrllibTransport, HttpTransport

DART_BASE = "https://opendart.fss.or.kr/api"
REPRT_ANNUAL = "11011"   # 사업보고서(연간)

_YEAR_RE = re.compile(r"\((\d{4})[.\-/]")


def _amt(s: Any) -> Optional[float]:
    """DART 금액 문자열 -> float. 콤마/괄호(음수)/빈값 처리."""
    if s is None:
        return None
    t = str(s).strip().replace(",", "")
    if t in ("", "-"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1]
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _yoy(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def _pick(rows: list[dict], sj: tuple[str, ...], names) -> Optional[dict]:
    """선택된 fs_div 행들 중 sj_div 와 account_nm 조건에 맞는 첫 행."""
    for r in rows:
        if r.get("sj_div") in sj:
            nm = (r.get("account_nm") or "").replace(" ", "")
            if names(nm):
                return r
    return None


def classify_disclosure(title: str) -> Optional[dict]:
    """공시 제목을 보고 유형·중요도·아이콘을 판정. 주요 공시만 dict, 그 외 None.
    투자자가 관심 갖는 이벤트성 공시를 강조하는 용도(사실 분류일 뿐 추천 아님)."""
    t = (title or "").replace(" ", "")
    # (키워드들, 라벨, 아이콘, 중요도 1~3)
    RULES = [
        (("유상증자",), "유상증자", "📈", 3),
        (("무상증자",), "무상증자", "🎁", 2),
        (("전환사채", "신주인수권부사채", "교환사채", "CB발행", "BW발행"), "메자닌", "🔄", 2),
        (("합병", "분할합병", "주식교환", "영업양수", "영업양도"), "합병·M&A", "🤝", 3),
        (("자기주식취득", "자기주식처분", "자사주"), "자사주", "🔁", 2),
        (("현금·현물배당", "현금배당", "배당결정", "주식배당"), "배당", "💵", 2),
        (("영업정지", "영업중단", "감자", "관리종목", "상장폐지", "거래정지"), "위험신호", "⚠️", 3),
        (("단일판매", "공급계약", "수주"), "대형계약·수주", "📝", 2),
        (("잠정실적", "영업실적", "매출액또는손익구조"), "실적", "📊", 2),
        (("최대주주변경", "경영권"), "경영권변동", "👑", 3),
        (("주식등의대량보유", "임원ㆍ주요주주특정증권"), "지분변동", "🔍", 1),
        (("유상증자또는주식관련사채등의청약", "공모"), "공모·청약", "🎯", 2),
        (("소송", "피소"), "소송", "⚖️", 2),
    ]
    for kws, label, icon, imp in RULES:
        if any(k in t for k in kws):
            return {"type": label, "icon": icon, "importance": imp}
    return None


class DARTProvider(DataProvider):
    name = "dart"
    supported_kinds = (Kind.FINANCIALS.value,)

    def __init__(self, api_key: str, corp_code_map: Optional[dict[str, str]] = None,
                 *, transport: Optional[HttpTransport] = None, lookback_days: int = 460,
                 prefer_consolidated: bool = True):
        self.api_key = api_key
        self.corp_code_map = corp_code_map or {}
        self.transport = transport or UrllibTransport()
        self.lookback_days = lookback_days
        self.prefer_consolidated = prefer_consolidated
        self.last_disclosure_error: Optional[str] = None

    def _corp_code(self, symbol: str) -> str:
        code = self.corp_code_map.get(symbol)
        if not code:
            raise ProviderError(f"DART corp_code 미등록: {symbol} (corpCode.xml 매핑 필요)")
        return code

    def _get(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise ProviderError("DART api_key 누락 (.env 의 DART_API_KEY)")
        p = dict(params); p["crtfc_key"] = self.api_key
        status, body = self.transport.get(f"{DART_BASE}{path}", {}, p)
        if status != 200:
            raise ProviderError(f"DART HTTP {status} ({path})")
        st = str(body.get("status", "000"))
        if st != "000":
            raise ProviderError(f"DART status={st} {body.get('message','')} ({path})")
        return body

    # 공시 유형 코드(corp_cls) → 시장 라벨
    _CLS = {"Y": "코스피", "K": "코스닥", "N": "코넥스", "E": "기타"}
    # DART status → 사람이 읽는 원인(데이터없음 013 은 오류가 아님)
    _STATUS_MSG = {
        "010": "등록되지 않은 DART 키입니다.",
        "011": "DART 키가 아직 활성화되지 않았습니다(가입 후 이메일 인증 필요).",
        "012": "이 IP에서 접근할 수 없는 키입니다.",
        "020": "DART 요청 한도를 초과했습니다(잠시 후 재시도).",
        "100": "DART 요청 파라미터 오류.",
        "800": "DART 시스템 점검 중입니다.",
        "900": "DART 정의되지 않은 오류.",
    }

    def recent_disclosures(self, now: datetime, *, days: int = 2,
                           page_count: int = 100, max_pages: int = 3,
                           only_listed: bool = True) -> list[dict]:
        """시장 전체 최근 공시 — corp_code 없이 날짜 범위로 조회. 최신(접수번호 역순).
        days=조회 일수. only_listed=상장사(종목코드 있음)만.
        키/한도 오류는 self.last_disclosure_error 에 저장(데이터 없음은 오류 아님)."""
        self.last_disclosure_error = None
        if not self.api_key:
            self.last_disclosure_error = "DART 키가 설정되지 않았습니다(.env 의 DART_API_KEY)."
            return []
        end = now.astimezone(KST).date()
        start = end - timedelta(days=max(0, days - 1))
        seen: set[str] = set()
        items: list[dict] = []
        for page in range(1, max_pages + 1):
            try:
                body = self._get("/list.json", {
                    "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
                    "page_no": str(page), "page_count": str(page_count),
                })
            except ProviderError as e:
                msg = str(e)
                # status=013(데이터 없음)은 오류가 아니라 '해당 기간 공시 없음'
                if "status=013" in msg:
                    break
                # 키/한도/파라미터 등 실제 오류 → 원인 저장
                for code, human in self._STATUS_MSG.items():
                    if f"status={code}" in msg:
                        self.last_disclosure_error = human
                        break
                else:
                    self.last_disclosure_error = msg
                break
            lst = body.get("list", []) or []
            if not lst:
                break
            for it in lst:
                rcept = it.get("rcept_no", "")
                if not rcept or rcept in seen:
                    continue
                seen.add(rcept)
                stock = (it.get("stock_code") or "").strip()
                if only_listed and not stock:
                    continue
                dt = it.get("rcept_dt", "")
                iso = (f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}T00:00:00+09:00"
                       if len(dt) == 8 else now.isoformat())
                items.append({
                    "title": it.get("report_nm", ""),
                    "corp": it.get("corp_name", ""),
                    "symbol": stock,
                    "market": self._CLS.get(it.get("corp_cls", ""), ""),
                    "filer": it.get("flr_nm", ""),
                    "rm": it.get("rm", ""),
                    "rcept_no": rcept,
                    "published_at": iso,
                    "date_only": True,   # DART는 접수'일'만 제공(시각 없음) → 날짜만 표시
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}",
                    "source": "공시",
                })
            try:
                total_page = int(body.get("total_page", 1))
            except (TypeError, ValueError):
                total_page = 1
            if page >= total_page:
                break
        # 접수번호 역순 = 최신순
        items.sort(key=lambda x: x["rcept_no"], reverse=True)
        return items

    def recent_ipos(self, now: datetime, *, days: int = 30,
                    page_count: int = 100, max_pages: int = 3) -> list[dict]:
        """최근 공모주(IPO) 관련 공시 — 증권신고(지분증권) 위주.
        DART list.json 에 pblntf_detail_ty=C001(증권신고 지분증권) 필터.
        청약·상장 세부일정은 신고서 본문에 있어 여기선 제목·회사·링크만 제공."""
        self.last_disclosure_error = None
        if not self.api_key:
            self.last_disclosure_error = "DART 키가 설정되지 않았습니다."
            return []
        end = now.astimezone(KST).date()
        start = end - timedelta(days=max(0, days - 1))
        seen: set[str] = set()
        items: list[dict] = []
        for page in range(1, max_pages + 1):
            try:
                body = self._get("/list.json", {
                    "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
                    "pblntf_detail_ty": "C001",   # 증권신고(지분증권)
                    "page_no": str(page), "page_count": str(page_count),
                })
            except ProviderError as e:
                msg = str(e)
                if "status=013" in msg:   # 데이터 없음
                    break
                self.last_disclosure_error = msg
                break
            for it in (body.get("list") or []):
                rcept = it.get("rcept_no", "")
                if not rcept or rcept in seen:
                    continue
                seen.add(rcept)
                nm = it.get("report_nm", "")
                dt = it.get("rcept_dt", "")
                iso = (f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}T00:00:00+09:00"
                       if len(dt) == 8 else "")
                # IPO 성격 분류(신규상장/유상증자/정정 등)
                if "정정" in nm:
                    tag = "정정"
                elif "철회" in nm:
                    tag = "철회"
                else:
                    tag = "신규"
                items.append({
                    "corp": it.get("corp_name", ""),
                    "symbol": it.get("stock_code", "") or "",
                    "market": it.get("corp_cls", ""),
                    "title": nm,
                    "tag": tag,
                    "rcept_no": rcept,
                    "published_at": iso,
                    "date_only": True,
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}",
                    "source": "IPO",
                })
            try:
                total_page = int(body.get("total_page", 1))
            except (TypeError, ValueError):
                total_page = 1
            if page >= total_page:
                break
        items.sort(key=lambda x: x["rcept_no"], reverse=True)
        return items

    def _latest_annual(self, corp_code: str, now: datetime) -> tuple[str, datetime]:
        """가장 최근 '사업보고서'의 (사업연도, 접수일 as_of). 접수일 <= now 만."""
        end = now.astimezone(KST).date()
        start = end - timedelta(days=self.lookback_days)
        body = self._get("/list.json", {
            "corp_code": corp_code, "bgn_de": start.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"), "pblntf_ty": "A", "page_count": "100",
        })
        best = None  # (rcept_dt_str, bsns_year)
        for item in body.get("list", []):
            nm = (item.get("report_nm") or "")
            if "사업보고서" not in nm:
                continue
            rcept = item.get("rcept_dt", "")
            if not rcept or len(rcept) != 8:
                continue
            as_of = self._as_of(rcept, now)
            if as_of > now:        # lookahead 차단
                continue
            m = _YEAR_RE.search(nm)
            year = m.group(1) if m else rcept[:4]
            if best is None or rcept > best[0]:
                best = (rcept, year)
        if best is None:
            raise ProviderError(f"DART 사업보고서 없음: {corp_code}")
        return best[1], self._as_of(best[0], now)

    @staticmethod
    def _as_of(rcept_dt: str, now: datetime) -> datetime:
        y, m, d = int(rcept_dt[:4]), int(rcept_dt[4:6]), int(rcept_dt[6:8])
        t = datetime(y, m, d, 16, 0, tzinfo=KST).astimezone(timezone.utc)
        return min(t, now)

    def _financials(self, corp_code: str, year: str) -> dict:
        body = self._get("/fnlttSinglAcnt.json", {
            "corp_code": corp_code, "bsns_year": year, "reprt_code": REPRT_ANNUAL,
        })
        all_rows = body.get("list", [])
        fs_pref = "CFS" if (self.prefer_consolidated and
                            any(r.get("fs_div") == "CFS" for r in all_rows)) else None
        if fs_pref is None:
            fs_pref = "CFS" if any(r.get("fs_div") == "CFS" for r in all_rows) else "OFS"
        rows = [r for r in all_rows if r.get("fs_div") == fs_pref] or all_rows

        IS = ("IS", "CIS")
        BS = ("BS",)
        rev = _pick(rows, IS, lambda n: n in ("매출액", "수익(매출액)", "영업수익"))
        op = _pick(rows, IS, lambda n: n.startswith("영업이익"))
        net = _pick(rows, IS, lambda n: n.startswith("당기순이익"))
        assets = _pick(rows, BS, lambda n: n == "자산총계")
        liab = _pick(rows, BS, lambda n: n == "부채총계")
        equity = _pick(rows, BS, lambda n: n == "자본총계")

        revenue = _amt(rev.get("thstrm_amount")) if rev else None
        op_income = _amt(op.get("thstrm_amount")) if op else None
        net_income = _amt(net.get("thstrm_amount")) if net else None
        total_assets = _amt(assets.get("thstrm_amount")) if assets else None
        total_liab = _amt(liab.get("thstrm_amount")) if liab else None
        total_equity = _amt(equity.get("thstrm_amount")) if equity else None
        rev_prev = _amt(rev.get("frmtrm_amount")) if rev else None
        op_prev = _amt(op.get("frmtrm_amount")) if op else None

        if revenue is None and op_income is None and net_income is None:
            raise ProviderError(f"DART 재무 핵심계정 없음: {corp_code}/{year}")

        debt_ratio = (total_liab / total_equity * 100.0
                      if (total_liab is not None and total_equity) else None)
        return {
            "revenue": revenue, "op_income": op_income, "net_income": net_income,
            "total_assets": total_assets, "total_liab": total_liab,
            "total_equity": total_equity,
            "debt_ratio": round(debt_ratio, 1) if debt_ratio is not None else None,
            "revenue_yoy": _round(_yoy(revenue, rev_prev)),
            "op_yoy": _round(_yoy(op_income, op_prev)),
            "fs_div": fs_pref, "bsns_year": year,
        }

    def multi_financials(self, corp_codes: list[str], year: str,
                         reprt_code: str = REPRT_ANNUAL) -> dict[str, dict]:
        """다중회사 주요계정 — 여러 회사 재무를 한 번에(fnlttMultiAcnt.json).
        반환: {corp_code: {revenue, op_income, net_income, ...}}.
        reprt_code: 11011(사업보고서/연간) | 11013(1분기) | 11012(반기) | 11014(3분기).
        corp_codes 는 최대 ~50개씩 나눠 호출(콤마 구분)."""
        result: dict[str, dict] = {}
        if not self.api_key or not corp_codes:
            return result
        # DART 다중계정은 콤마 구분이지만 대량은 불안정 → 안전하게 소량씩.
        # 청크 크기: DART 다중계정은 콤마로 여러 회사 허용. 크게 잡아 호출 횟수를 줄임(속도↑).
        # 단, 너무 크면 응답 불안정 → 100개 선에서 균형.
        CHUNK = 100
        chunks = [corp_codes[i:i + CHUNK] for i in range(0, len(corp_codes), CHUNK)]

        def _fetch_chunk(chunk):
            """청크 하나 조회 → body 반환. rate limit(020)이면 잠깐 쉬고 재시도."""
            import time as _t
            for attempt in range(3):   # 최대 3회 시도
                try:
                    return self._get("/fnlttMultiAcnt.json", {
                        "corp_code": ",".join(chunk),
                        "bsns_year": year, "reprt_code": reprt_code,
                    })
                except ProviderError as e:
                    msg = str(e)
                    # 요청 한도(020) 또는 일시 오류면 백오프 후 재시도
                    if "020" in msg or "800" in msg:
                        _t.sleep(0.6 * (attempt + 1))   # 0.6s, 1.2s 점증 대기
                        continue
                    return None   # 그 외 오류는 즉시 포기
                except Exception:
                    _t.sleep(0.4 * (attempt + 1))
                    continue
            return None

        # 병렬 조회: 동시 실행 수 제한(DART 한도 회피). 청크가 적으면 순차.
        bodies = []
        if len(chunks) <= 1:
            bodies = [_fetch_chunk(chunks[0])] if chunks else []
        else:
            from concurrent.futures import ThreadPoolExecutor
            workers = min(5, len(chunks))   # 5개 동시(청크 100개라 총 요청 적음 + 재시도로 누락 방지)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                bodies = list(ex.map(_fetch_chunk, chunks))

        for body in bodies:
            if not body:
                continue
            # corp_code별로 행을 모아서 파싱
            by_corp: dict[str, list] = {}
            for r in (body.get("list") or []):
                cc = r.get("corp_code", "")
                if cc:
                    by_corp.setdefault(cc, []).append(r)
            for cc, all_rows in by_corp.items():
                fs_pref = "CFS" if any(r.get("fs_div") == "CFS" for r in all_rows) else "OFS"
                rows = [r for r in all_rows if r.get("fs_div") == fs_pref] or all_rows
                IS = ("IS", "CIS"); BS = ("BS",)
                rev = _pick(rows, IS, lambda n: n in ("매출액", "수익(매출액)", "영업수익"))
                op = _pick(rows, IS, lambda n: n.startswith("영업이익"))
                net = _pick(rows, IS, lambda n: n.startswith("당기순이익"))
                liab = _pick(rows, BS, lambda n: n == "부채총계")
                equity = _pick(rows, BS, lambda n: n == "자본총계")
                revenue = _amt(rev.get("thstrm_amount")) if rev else None
                op_income = _amt(op.get("thstrm_amount")) if op else None
                net_income = _amt(net.get("thstrm_amount")) if net else None
                total_liab = _amt(liab.get("thstrm_amount")) if liab else None
                total_equity = _amt(equity.get("thstrm_amount")) if equity else None
                rev_prev = _amt(rev.get("frmtrm_amount")) if rev else None
                op_prev = _amt(op.get("frmtrm_amount")) if op else None
                if revenue is None and op_income is None and net_income is None:
                    continue
                debt_ratio = (total_liab / total_equity * 100.0
                              if (total_liab is not None and total_equity) else None)
                result[cc] = {
                    "revenue": revenue, "op_income": op_income, "net_income": net_income,
                    "total_liab": total_liab, "total_equity": total_equity,
                    "debt_ratio": round(debt_ratio, 1) if debt_ratio is not None else None,
                    "revenue_yoy": _round(_yoy(revenue, rev_prev)),
                    "op_yoy": _round(_yoy(op_income, op_prev)),
                    "op_margin": (round(op_income / revenue * 100.0, 1)
                                  if (op_income is not None and revenue) else None),
                    "net_margin": (round(net_income / revenue * 100.0, 1)
                                   if (net_income is not None and revenue) else None),
                    "roe": (round(net_income / total_equity * 100.0, 1)
                            if (net_income is not None and total_equity) else None),
                    "fs_div": fs_pref, "bsns_year": year,
                }
        return result

    def fetch(self, symbol: str, kind: str, *, now: datetime) -> Optional[DataPoint]:
        if kind != Kind.FINANCIALS.value:
            return None
        corp = self._corp_code(symbol)
        year, as_of = self._latest_annual(corp, now)
        payload = self._financials(corp, year)
        return DataPoint(symbol, Kind.FINANCIALS.value, payload,
                         as_of=as_of, fetched_at=now, source=self.name)


def _round(x: Optional[float]) -> Optional[float]:
    return round(x, 4) if x is not None else None

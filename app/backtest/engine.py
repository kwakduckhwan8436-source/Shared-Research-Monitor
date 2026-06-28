"""백테스트 엔진 — '이 점수가 실제로 맞았는가'를 과거 데이터로 검증한다.

핵심 원칙(정직성):
- **look-ahead 차단**: 각 리밸런스 시점 D 에서는 D 까지의 봉만으로 점수를 계산한다.
- **전방 수익률**: 호라이즌별 창(단타~2일, 스윙~10일, 중장기~60거래일) 뒤의 실제 종가로 측정.
- **거래비용**: 왕복 비용(bp, 슬리피지+수수료+세금)을 차감한 net 수익률로 평가.
- **walk-forward**: 기간을 학습/검증으로 나눠 표본 외(out-of-sample) 성과를 따로 보고 — 과최적화 점검.
- 발화 시그널 0개면 제외(abstain). 점수는 유니버스 내 횡단면 백분위(0~100).

한계(반드시 인지):
- 일봉 기준이라 단타(분 단위) 검증은 근사일 뿐이다.
- 살아남은 종목만 넣으면 생존편향이 생긴다 → 상장폐지 종목 포함이 이상적(데이터 한계).
- 과거 성과가 미래를 보장하지 않는다. IC/스프레드는 '신호가 무의미하지 않다'는 증거이지 수익 보장이 아니다.
- 수급/재무 신호는 과거 시점 데이터가 없으면 보류된다 → 본 엔진은 주로 일봉 기반 신호를 검증한다.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.ssot import SSOT
from app.data.schema import Kind, DataPoint
from app.signals.base import SignalContext
from app.signals.registry import signals_for
from app.scoring.weights import RULE_BASED
from app.scoring.scorer import HorizonScorer
from app.scoring.normalize import percentile_scores

# 호라이즌별 기본 전방 수익률 창(거래일)
HORIZON_FORWARD = {"daytrade": 2, "swing": 10, "midlong": 60}


@dataclass(frozen=True)
class BacktestConfig:
    horizon: str
    forward_days: int = 0          # 0이면 HORIZON_FORWARD 사용
    rebalance_every: int = 5       # 며칠마다 리밸런스
    cost_bps: float = 30.0         # 왕복 거래비용(bp). 30bp=0.30%
    min_history: int = 60          # 신호 계산 최소 봉 수
    train_frac: float = 0.6        # walk-forward 학습 구간 비중
    top_quantile: float = 0.2      # 상위 분위(전략 프록시)

    @property
    def fwd(self) -> int:
        return self.forward_days or HORIZON_FORWARD.get(self.horizon, 10)


def _rank(xs: list[float]) -> list[float]:
    """평균 순위(동점 처리). Spearman 용."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    return cov / (sx * sy)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    return _pearson(_rank(xs), _rank(ys))


@dataclass
class BacktestResult:
    horizon: str
    config: dict
    n_records: int
    n_rebalances: int
    date_range: tuple[str, str] | None
    rank_ic_mean: float | None          # 평균 일별 순위 IC (헤드라인)
    rank_ic_t: float | None             # IC t-통계(>2면 유의)
    quantile_returns: list[dict]        # 점수 구간별 평균 net 수익률
    top_minus_bottom: float | None      # 상위-하위 분위 스프레드(net)
    signal_ic: list[dict]               # 시그널별 IC(가중치 캘리브레이션 근거)
    confidence_bins: list[dict]         # 신뢰도 구간별 적중률
    long_top: dict                      # 상위 분위 매수 전략 프록시(net)
    walk_forward: dict                  # 학습/검증 분리 성과(표본 외)
    warnings: list[str] = field(default_factory=list)


class Backtester:
    def __init__(self, config: BacktestConfig, *, allow_uncalibrated: bool = True,
                 weightset=None):
        self.cfg = config
        ws = weightset or RULE_BASED[config.horizon]
        self.weightset = ws
        self.scorer = HorizonScorer(ws, allow_uncalibrated=allow_uncalibrated)
        self.signals = signals_for(config.horizon)

    def _score_asof(self, symbol: str, asof_bars: list[dict], when: datetime):
        ss = SSOT()
        ss.put(DataPoint(symbol, Kind.OHLCV.value, {"bars": asof_bars}, when, when, "bt"))
        results = [s.run(SignalContext(symbol, ss, self.cfg.horizon, when)) for s in self.signals]
        return self.scorer.score(results)

    def run(self, bars_by_symbol: dict[str, list[dict]]) -> BacktestResult:
        cfg = self.cfg
        warnings: list[str] = []
        series: dict[str, dict] = {}
        for sym, bars in bars_by_symbol.items():
            if len(bars) < cfg.min_history + cfg.fwd:
                continue
            series[sym] = {"bars": bars, "dates": [b["t"][:10] for b in bars]}
        if len(series) < 2:
            warnings.append("종목/봉 수 부족 — 최소 2종목, 종목당 min_history+forward 봉 필요.")
            return self._empty(warnings)

        all_dates = sorted({d for s in series.values() for d in s["dates"]})
        records: list[dict] = []
        per_date_ic: list[float] = []

        for di in range(cfg.min_history, len(all_dates), cfg.rebalance_every):
            D = all_dates[di]
            when = datetime.fromisoformat(D).replace(tzinfo=timezone.utc)
            raw_by: dict[str, float] = {}
            meta: dict[str, dict] = {}
            for sym, s in series.items():
                p = bisect_right(s["dates"], D) - 1
                if p < cfg.min_history - 1:
                    continue
                fp = p + cfg.fwd
                if fp >= len(s["bars"]):
                    continue
                scored = self._score_asof(sym, s["bars"][:p + 1], when)
                if scored is None:
                    continue
                c0 = s["bars"][p]["c"]
                c1 = s["bars"][fp]["c"]
                if c0 <= 0:
                    continue
                gross = (c1 - c0) / c0
                net = gross - cfg.cost_bps / 10000.0
                raw_by[sym] = scored.raw_score
                meta[sym] = {"confidence": scored.confidence, "net": net, "gross": gross,
                             "signals": {r.name: r.value for r in scored.fired}}
            if len(raw_by) < 3:
                continue
            pct = percentile_scores(raw_by)
            scores = []
            rets = []
            for sym, sc in pct.items():
                m = meta[sym]
                records.append({"date": D, "symbol": sym, "score": sc,
                                "confidence": m["confidence"], "net": m["net"],
                                "gross": m["gross"], "signals": m["signals"]})
                scores.append(sc)
                rets.append(m["net"])
            ic = _spearman(scores, rets)
            if ic is not None:
                per_date_ic.append(ic)

        if not records:
            warnings.append("리밸런스 시점에서 점수화된 종목이 없음 — 데이터 길이/신호 발화 확인.")
            return self._empty(warnings)

        return self._aggregate(records, per_date_ic, all_dates, warnings)

    # ---------- 집계 ----------
    def _aggregate(self, records, per_date_ic, all_dates, warnings) -> BacktestResult:
        cfg = self.cfg
        n = len(records)
        dates_used = sorted({r["date"] for r in records})

        # 헤드라인: 평균 일별 순위 IC + t통계
        ic_mean = sum(per_date_ic) / len(per_date_ic) if per_date_ic else None
        ic_t = None
        if per_date_ic and len(per_date_ic) > 1:
            m = ic_mean
            sd = (sum((x - m) ** 2 for x in per_date_ic) / (len(per_date_ic) - 1)) ** 0.5
            if sd > 0:
                ic_t = round(m / (sd / len(per_date_ic) ** 0.5), 2)

        # 점수 5분위별 평균 net 수익률
        buckets = [[] for _ in range(5)]
        for r in records:
            b = min(4, int(r["score"] / 20.0))
            buckets[b].append(r["net"])
        quantile_returns = []
        for i, bk in enumerate(buckets):
            quantile_returns.append({
                "range": f"{i*20}-{(i+1)*20}", "n": len(bk),
                "avg_net": round(sum(bk) / len(bk), 4) if bk else None,
                "win_rate": round(sum(1 for x in bk if x > 0) / len(bk), 3) if bk else None,
            })
        top_b = quantile_returns[4]["avg_net"]
        bot_b = quantile_returns[0]["avg_net"]
        tmb = round(top_b - bot_b, 4) if (top_b is not None and bot_b is not None) else None

        # 시그널별 IC (가중치 캘리브레이션 근거)
        sig_vals: dict[str, list[tuple[float, float]]] = {}
        for r in records:
            for name, v in r["signals"].items():
                sig_vals.setdefault(name, []).append((v, r["net"]))
        signal_ic = []
        for name, pairs in sorted(sig_vals.items()):
            ic = _pearson([v for v, _ in pairs], [ret for _, ret in pairs])
            signal_ic.append({"signal": name, "n": len(pairs),
                              "ic": round(ic, 4) if ic is not None else None})
        signal_ic.sort(key=lambda x: (x["ic"] is None, -(x["ic"] or 0)))

        # 신뢰도 구간별 적중률
        cbins = [[] for _ in range(5)]
        for r in records:
            b = min(4, int(r["confidence"] * 5))
            cbins[b].append(r["net"])
        confidence_bins = []
        for i, bk in enumerate(cbins):
            confidence_bins.append({
                "range": f"{i/5:.1f}-{(i+1)/5:.1f}", "n": len(bk),
                "hit_rate": round(sum(1 for x in bk if x > 0) / len(bk), 3) if bk else None,
                "avg_net": round(sum(bk) / len(bk), 4) if bk else None,
            })

        # 상위 분위 매수 전략 프록시(리밸런스마다 상위 q% 평균 net, 단리 누적)
        long_top = self._long_top(records, cfg.top_quantile)

        # walk-forward: 날짜 기준 학습/검증 분리, 각 구간의 상위분위 평균 net
        split_i = int(len(dates_used) * cfg.train_frac)
        train_dates = set(dates_used[:split_i])
        test_dates = set(dates_used[split_i:])
        wf = {
            "train": self._long_top([r for r in records if r["date"] in train_dates], cfg.top_quantile),
            "test": self._long_top([r for r in records if r["date"] in test_dates], cfg.top_quantile),
            "split_date": dates_used[split_i] if 0 < split_i < len(dates_used) else None,
        }
        wf["note"] = "test(표본 외) 성과가 train 과 비슷해야 신뢰 가능. 급락하면 과최적화 의심."

        if n < 100:
            warnings.append(f"표본 {n}건 — 통계적 신뢰 낮음(>300 권장). 더 긴 기간/많은 종목 필요.")
        if cfg.horizon == "daytrade":
            warnings.append("단타는 일봉 기준이라 분 단위 전략과 다름 — 근사 검증임.")

        return BacktestResult(
            horizon=cfg.horizon,
            config={"forward_days": cfg.fwd, "rebalance_every": cfg.rebalance_every,
                    "cost_bps": cfg.cost_bps, "min_history": cfg.min_history,
                    "weights": self.weightset.source, "calibrated": self.weightset.calibrated},
            n_records=n, n_rebalances=len(dates_used),
            date_range=(dates_used[0], dates_used[-1]) if dates_used else None,
            rank_ic_mean=round(ic_mean, 4) if ic_mean is not None else None,
            rank_ic_t=ic_t,
            quantile_returns=quantile_returns, top_minus_bottom=tmb,
            signal_ic=signal_ic, confidence_bins=confidence_bins,
            long_top=long_top, walk_forward=wf, warnings=warnings,
        )

    def _long_top(self, records, q: float) -> dict:
        """리밸런스마다 상위 q분위 종목 평균 net 수익률 → 단리 누적·승률."""
        by_date: dict[str, list[dict]] = {}
        for r in records:
            by_date.setdefault(r["date"], []).append(r)
        per_period = []
        for d, rs in sorted(by_date.items()):
            if len(rs) < 3:
                continue
            cutoff = 100 * (1 - q)
            top = [x["net"] for x in rs if x["score"] >= cutoff]
            if not top:
                top = [max(rs, key=lambda x: x["score"])["net"]]
            per_period.append(sum(top) / len(top))
        if not per_period:
            return {"periods": 0, "avg_net_per_period": None, "cum_net": None,
                    "win_rate": None, "avg_market_net": None}
        avg = sum(per_period) / len(per_period)
        cum = 1.0
        for x in per_period:
            cum *= (1 + x)
        # 시장(전종목) 평균 비교
        mkt = []
        for d, rs in sorted(by_date.items()):
            if rs:
                mkt.append(sum(x["net"] for x in rs) / len(rs))
        return {
            "periods": len(per_period),
            "avg_net_per_period": round(avg, 4),
            "cum_net": round(cum - 1.0, 4),
            "win_rate": round(sum(1 for x in per_period if x > 0) / len(per_period), 3),
            "avg_market_net": round(sum(mkt) / len(mkt), 4) if mkt else None,
            "edge_vs_market": round(avg - (sum(mkt) / len(mkt)), 4) if mkt else None,
        }

    def _empty(self, warnings) -> BacktestResult:
        return BacktestResult(
            horizon=self.cfg.horizon, config={"forward_days": self.cfg.fwd},
            n_records=0, n_rebalances=0, date_range=None, rank_ic_mean=None, rank_ic_t=None,
            quantile_returns=[], top_minus_bottom=None, signal_ic=[], confidence_bins=[],
            long_top={}, walk_forward={}, warnings=warnings,
        )

    def signal_samples(self, bars_by_symbol: dict[str, list[dict]]) -> dict[str, list[tuple[float, float]]]:
        """calibrate_from_samples 용 (signal_value, forward_return) 표본 생성."""
        cfg = self.cfg
        out: dict[str, list[tuple[float, float]]] = {}
        series = {sym: {"bars": b, "dates": [x["t"][:10] for x in b]}
                  for sym, b in bars_by_symbol.items()
                  if len(b) >= cfg.min_history + cfg.fwd}
        all_dates = sorted({d for s in series.values() for d in s["dates"]})
        for di in range(cfg.min_history, len(all_dates), cfg.rebalance_every):
            D = all_dates[di]
            when = datetime.fromisoformat(D).replace(tzinfo=timezone.utc)
            for sym, s in series.items():
                p = bisect_right(s["dates"], D) - 1
                fp = p + cfg.fwd
                if p < cfg.min_history - 1 or fp >= len(s["bars"]):
                    continue
                scored = self._score_asof(sym, s["bars"][:p + 1], when)
                if scored is None:
                    continue
                c0, c1 = s["bars"][p]["c"], s["bars"][fp]["c"]
                if c0 <= 0:
                    continue
                net = (c1 - c0) / c0 - cfg.cost_bps / 10000.0
                for r in scored.fired:
                    out.setdefault(r.name, []).append((r.value, net))
        return out

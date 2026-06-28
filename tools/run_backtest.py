"""백테스트 CLI — `python launch.py backtest [horizon]`

SSOT 에 일봉을 적재한 뒤 호라이즌별로 점수 예측력을 백테스트하고 리포트를 출력한다.
live 키가 있으면 실데이터, 없으면 현재 모드(mock=합성)로 동작한다.
"""
from __future__ import annotations

import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run_cli(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    from app.api.main import build_context
    from app.data.schema import Kind
    from app.backtest.engine import Backtester, BacktestConfig, HORIZON_FORWARD

    horizons = [a for a in argv if a in ("daytrade", "swing", "midlong")] or \
        ["daytrade", "swing", "midlong"]
    max_syms = 40

    ctx = build_context()
    syms = (ctx.universe or [])[:max_syms]
    print(f"[백테스트] 모드={ctx.config.data_source}  종목={len(syms)}개 일봉 적재 중...")
    try:
        ctx.service.refresh_data(syms, [Kind.OHLCV.value])
    except Exception as e:
        print(f"  일봉 적재 일부 실패(계속 진행): {e}")

    bars_by = {}
    for s in syms:
        dp = ctx.ssot.get(s, Kind.OHLCV.value)
        if dp and dp.payload.get("bars"):
            bars_by[s] = dp.payload["bars"]
    print(f"  일봉 확보: {len(bars_by)}종목"
          + (f" (평균 {sum(len(b) for b in bars_by.values())//max(1,len(bars_by))}봉)" if bars_by else ""))
    if len(bars_by) < 2:
        print("  데이터 부족 — live 키 설정 또는 기간 확보 필요. 종료.")
        return 1

    for h in horizons:
        print("\n" + "=" * 56)
        print(f" 호라이즌: {h}  (전방 {HORIZON_FORWARD[h]}거래일, 왕복비용 30bp)")
        print("=" * 56)
        res = Backtester(BacktestConfig(horizon=h), allow_uncalibrated=True).run(bars_by)
        if res.n_records == 0:
            print("  점수화 표본 없음:", "; ".join(res.warnings))
            continue
        print(f"  기간 {res.date_range[0]} ~ {res.date_range[1]} | 표본 {res.n_records}건 / 리밸런스 {res.n_rebalances}회")
        print(f"  ▶ 순위 IC 평균 {res.rank_ic_mean} (t={res.rank_ic_t})   [>0: 점수가 수익을 예측, t>2 유의]")
        print(f"  ▶ 상위-하위 분위 스프레드(net) {res.top_minus_bottom}")
        lt = res.long_top
        print(f"  ▶ 상위분위 전략: 회당평균 {lt.get('avg_net_per_period')} / 누적 {lt.get('cum_net')} / 승률 {lt.get('win_rate')}")
        print(f"     시장평균 회당 {lt.get('avg_market_net')} → 엣지 {lt.get('edge_vs_market')}")
        wf = res.walk_forward
        print(f"  ▶ walk-forward: train {wf['train'].get('avg_net_per_period')} vs test(표본외) {wf['test'].get('avg_net_per_period')}  [비슷해야 신뢰]")
        print("  분위별 평균 net:")
        for q in res.quantile_returns:
            print(f"     {q['range']:>6}  n={q['n']:>4}  avg_net={q['avg_net']}  win={q['win_rate']}")
        print("  시그널별 IC(예측 기여):")
        for s in res.signal_ic[:8]:
            print(f"     {s['signal']:<20} IC={s['ic']}  (n={s['n']})")
        if res.warnings:
            print("  ⚠ " + " / ".join(res.warnings))

    print("\n[주의] 과거 성과는 미래를 보장하지 않습니다. 생존편향·과최적화·체결오차를 항상 의심하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli(sys.argv[1:]))

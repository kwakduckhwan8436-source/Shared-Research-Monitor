"""추천 서비스 테스트 — 멱등성 + 유니버스 필터."""
from datetime import datetime, timezone

from app.core.clock import FrozenClock
from app.core.ssot import SSOT
from app.data.store import Store
from app.providers.base import ProviderRouter
from app.providers.mock import MockProvider, universe_symbols, name_of
from app.reco.service import RecommendationService
from app.signals.registry import HORIZONS, required_kinds_for

NOW = datetime(2026, 6, 19, 7, 0, tzinfo=timezone.utc)


def _service():
    ssot = SSOT()
    store = Store(":memory:")
    svc = RecommendationService(ssot, ProviderRouter([MockProvider()]), store,
                               FrozenClock(NOW), name_resolver=name_of,
                               allow_uncalibrated=True)
    kinds = sorted({k for h in HORIZONS for k in required_kinds_for(h)})
    svc.refresh_data(universe_symbols(), kinds)
    return svc, ssot


def test_recommend_is_idempotent():
    svc, _ = _service()
    a = svc.recommend("swing", top_n=5)
    b = svc.recommend("swing", top_n=5)
    assert [(r.symbol, r.score, r.confidence) for r in a] == \
           [(r.symbol, r.score, r.confidence) for r in b]


def test_universe_excludes_managed_and_halted():
    svc, _ = _service()
    recs = svc.recommend("swing", top_n=50)
    syms = {r.symbol for r in recs}
    assert "900110" not in syms   # 관리종목 제외
    assert "123450" not in syms   # 거래정지 제외


def test_scores_are_cross_sectional_percentile():
    svc, _ = _service()
    recs = svc.recommend("midlong", top_n=50)
    scores = [r.score for r in recs]
    assert max(scores) <= 100.0 and min(scores) >= 0.0
    # 정렬: 점수 내림차순
    assert scores == sorted(scores, reverse=True)


def test_persist_writes_recommendations():
    svc, _ = _service()
    recs = svc.recommend("swing", top_n=5, persist=True)
    rows = svc.store.open_recommendations("swing")
    assert len(rows) == len(recs)


def test_every_rec_carries_provenance():
    svc, _ = _service()
    for r in svc.recommend("swing", top_n=5):
        assert r.snapshot_id
        assert r.weights_source
        assert r.generated_at
        assert isinstance(r.fired, list)

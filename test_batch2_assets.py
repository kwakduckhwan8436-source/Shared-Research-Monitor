"""배치2 프론트 자산 정합성 — 서버 없이 파일 내용만으로 검증.

히트맵·IPO 알림·스마트머니 설명 모달이 index.html 에 실제로 들어가 있고,
백엔드 엔드포인트가 routes.py 에 정의돼 있는지 확인한다.
(fastapi 미설치로 실제 HTTP 응답은 배포 후 확인)"""
import os

ROOT = os.path.dirname(os.path.dirname(__file__))
WEB = os.path.join(ROOT, "web")
ROUTES = os.path.join(ROOT, "app", "api", "routes.py")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def test_heatmap_endpoint_and_frontend_present():
    routes = _read(ROUTES)
    assert "/api/finance/sector_heatmap" in routes, "히트맵 엔드포인트 누락"
    assert "avg_vscore" in routes, "히트맵 평균 vscore 계산 누락"
    html = _read(os.path.join(WEB, "index.html"))
    assert "heatmapHTML" in html and "sector_heatmap" in html
    assert 'data-action="fin-view"' in html, "히트맵 뷰 토글 누락"


def test_ipo_alert_present():
    html = _read(os.path.join(WEB, "index.html"))
    assert "function ipoAlertHTML" in html, "IPO 알림 함수 누락"
    # 청약중 + D-3 이내를 거른다
    assert 'status==="open"' in html or "status===\"open\"" in html
    assert "ipo-alert-hide" in html and "ipo-alert-open" in html


def test_smartmoney_help_present():
    html = _read(os.path.join(WEB, "index.html"))
    assert "govHelpModalHTML" in html, "스마트머니 설명 모달 누락"
    assert 'data-action="gov-help"' in html


def test_wide_margin_cap_removed():
    """좌우 여백 유발하던 초대형 모니터 폭 상한이 제거됐는지."""
    html = _read(os.path.join(WEB, "index.html"))
    assert "min-width:1900px" not in html, "여백 유발 규칙이 아직 남아있음"


def test_roadmap_banner_shortened():
    html = _read(os.path.join(WEB, "index.html"))
    # adslot 최대폭이 380px 로 줄었는지(기존 560px)
    assert "max-width:380px" in html

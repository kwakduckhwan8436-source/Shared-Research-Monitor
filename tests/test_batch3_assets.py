"""배치3 + 화면개선 프론트 자산 정합성 — 서버 없이 파일 내용만으로 검증."""
import os

ROOT = os.path.dirname(os.path.dirname(__file__))
WEB = os.path.join(ROOT, "web")


def _html():
    with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
        return f.read()


def test_font_scale_present():
    h = _html()
    assert "applyFontScale" in h, "글자 크기 적용 함수 누락"
    assert 'data-action="font-scale"' in h, "글자 크기 버튼 누락"
    # zoom 방식 3단계
    assert 'body[data-fs="l"]' in h and 'body[data-fs="xl"]' in h


def test_watch_changes_present():
    h = _html()
    assert "watchChangesHTML" in h, "관심종목 변동 요약 함수 누락"
    assert "watch-change-seen" in h and "watch-change-open" in h
    assert "reco_watch_lastseen" in h, "마지막 확인 시각 저장 키 누락"


def test_briefing_wired_in_menu():
    h = _html()
    # 고아 상태였던 브리핑이 더보기 메뉴에 연결됐는지
    assert "오늘의 증시 브리핑" in h
    assert 'a:"briefing"' in h or "a:\"briefing\"" in h


def test_banner_minimal_and_layout_fixed():
    h = _html()
    # 부의 로드맵 배너 최소화(230px)
    assert "max-width:230px" in h
    # 상단바 레이아웃 재정렬(top-meta 가 가운데를 채움)
    assert "flex:1;justify-content:flex-end;order:2" in h
    # 여백 유발하던 초대형 상한은 여전히 없음
    assert "min-width:1900px" not in h


def test_finance_panel_taller_on_mobile():
    h = _html()
    assert "94dvh" in h, "재무 패널 모바일 높이 확대 누락"

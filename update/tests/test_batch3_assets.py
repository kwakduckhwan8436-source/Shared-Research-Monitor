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


def test_banner_repositioned_and_readable():
    h = _html()
    # 배너가 top-meta 안(검색창 좌측)으로 이동했는지
    i_meta = h.find('<div class="top-meta">')
    i_ad = h.find('<div class="adslot"', i_meta)
    i_search = h.find('<div class="topsearch">', i_meta)
    assert i_meta < i_ad < i_search, "배너가 검색창 좌측으로 이동하지 않음"
    # 글씨 안 잘리게 contain 으로 표시하는지
    assert "object-fit:contain" in h
    # 여백 유발하던 초대형 상한은 여전히 없음
    assert "min-width:1900px" not in h


def test_finance_panel_taller_on_mobile():
    h = _html()
    assert "94dvh" in h, "재무 패널 모바일 높이 확대 누락"


def test_banner_width_capped():
    """텍스트 배너가 넘쳐 버튼이 2줄 되던 문제 — 배너 폭 상한이 있어야."""
    h = _html()
    import re
    m = re.search(r"\.adslot\{[^}]*max-width:(\d+)px", h)
    assert m and int(m.group(1)) <= 320, "배너 최대폭 상한이 없거나 너무 큼"
    # 텍스트가 길어도 한 줄로 말줄임
    assert "text-overflow:ellipsis" in h


def test_briefing_enriched():
    """브리핑이 수급 데이터 없어도 채워지는 섹션들을 포함하는지."""
    import os
    routes = open(os.path.join(ROOT, "app", "api", "routes.py"), encoding="utf-8").read()
    for key in ("value_top", "smart_money", "earnings_week"):
        assert key in routes, f"브리핑 {key} 섹션 누락(백엔드)"
    h = _html()
    assert "오늘의 저평가 우량주 TOP" in h and "이번 주 실적발표" in h

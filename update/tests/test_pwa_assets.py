"""PWA 자산 정합성 테스트 — 서버 없이 파일 내용만으로 검증 가능.

컨테이너에 fastapi 가 없어 실제 HTTP 응답은 못 띄우지만,
PWA 가 깨지는 대표 원인들(매니페스트 필수 키 누락, sw.js 등록 누락,
index.html 링크 누락)은 파일만으로 잡을 수 있다."""
import json
import os

WEB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")


def _read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as f:
        return f.read()


def test_manifest_has_required_keys():
    m = json.loads(_read("manifest.json"))
    for k in ("name", "short_name", "start_url", "display", "icons"):
        assert k in m, f"manifest.json 에 '{k}' 누락"
    assert m["display"] in ("standalone", "fullscreen", "minimal-ui")
    assert len(m["icons"]) >= 1


def test_manifest_icons_exist_on_disk():
    m = json.loads(_read("manifest.json"))
    for ic in m["icons"]:
        src = ic["src"].lstrip("/")
        assert os.path.exists(os.path.join(WEB, src)), f"아이콘 파일 없음: {src}"
        # 192·512 두 사이즈는 설치형 PWA 최소 요건
    sizes = {ic["sizes"] for ic in m["icons"]}
    assert "192x192" in sizes and "512x512" in sizes


def test_index_links_manifest_and_registers_sw():
    html = _read("index.html")
    assert 'rel="manifest"' in html, "index.html 에 manifest 링크 누락"
    assert 'name="theme-color"' in html, "theme-color 메타 누락"
    assert 'apple-touch-icon' in html, "iOS 홈화면 아이콘 누락"
    assert 'serviceWorker' in html and 'register("/sw.js")' in html, "SW 등록 누락"


def test_sw_excludes_api_and_self_from_cache():
    sw = _read("sw.js")
    # API 는 절대 캐시하면 안 된다(데이터가 굳어버림)
    assert '"/api/"' in sw and "startsWith" in sw
    # sw.js 자신도 캐시 제외(업데이트 함정 방지)
    assert '"/sw.js"' in sw
    # skipWaiting 은 메시지로만(자동 교체 금지)
    assert "SKIP_WAITING" in sw


def test_sw_cache_version_bumped():
    """캐시 버전이 v4 이상인지 — 배포마다 올려 옛 캐시를 정리한다."""
    sw = _read("sw.js")
    assert "reco-static-v" in sw
    import re
    m = re.search(r"reco-static-v(\d+)", sw)
    assert m and int(m.group(1)) >= 4

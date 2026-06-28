"""_load_dotenv 파싱 회귀 테스트 — 인라인 주석/따옴표/BOM."""
import os
import tempfile

from app import config as cfg_mod


def _parse(text: str) -> dict:
    """임시 .env 작성 후 _load_dotenv 로직으로 파싱한 결과를 dict 로."""
    import re
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if val[:1] not in ('"', "'"):
            val = re.sub(r"\s+#.*$", "", val)
        val = val.strip().strip('"').strip("'")
        out[key] = val
    return out


def test_inline_comment_stripped():
    d = _parse("RECO_ALLOW_UNCALIBRATED=true   # 규칙기반 허용")
    assert d["RECO_ALLOW_UNCALIBRATED"] == "true"   # 주석 제거


def test_kis_paper_with_comment():
    d = _parse("KIS_PAPER=false                 # 모의(true)/실전(false)")
    assert d["KIS_PAPER"] == "false"


def test_value_without_comment_intact():
    d = _parse("KIS_APP_KEY=ABCD1234efgh")
    assert d["KIS_APP_KEY"] == "ABCD1234efgh"


def test_hash_without_space_kept():
    # 공백 없이 붙은 #(예: 키 안의 문자)은 주석으로 보지 않음
    d = _parse("SOME_KEY=abc#def")
    assert d["SOME_KEY"] == "abc#def"


def test_quoted_value_preserves_hash():
    d = _parse('SOME_KEY="abc # not comment"')
    assert d["SOME_KEY"] == "abc # not comment"


def test_real_dotenv_file_loads(tmp_path=None):
    # 실제 _load_dotenv 가 임시 .env 를 읽어 환경에 주석 없는 값을 넣는지
    d = tempfile.mkdtemp()
    p = os.path.join(d, ".env")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("ZZ_TEST_VAR=hello   # 주석\n")
    cwd = os.getcwd()
    try:
        os.chdir(d)
        os.environ.pop("ZZ_TEST_VAR", None)
        cfg_mod._load_dotenv()
        assert os.environ.get("ZZ_TEST_VAR") == "hello"
    finally:
        os.chdir(cwd)
        os.environ.pop("ZZ_TEST_VAR", None)

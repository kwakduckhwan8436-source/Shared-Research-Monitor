"""공지(settings key-value) 저장/조회/해제 회귀."""
import tempfile, os
from app.data.store import Store


def _store():
    return Store(tempfile.mktemp(suffix=".sqlite3"))


def test_notice_set_get():
    s = _store()
    assert s.get_setting("notice") is None
    s.set_setting("notice", "오늘 FOMC 발표", "2026-06-21T10:00:00+09:00")
    got = s.get_setting("notice")
    assert got["value"] == "오늘 FOMC 발표" and got["updated_at"].startswith("2026")


def test_notice_overwrite():
    s = _store()
    s.set_setting("notice", "공지1", "2026-06-21T10:00:00+09:00")
    s.set_setting("notice", "공지2", "2026-06-21T11:00:00+09:00")
    assert s.get_setting("notice")["value"] == "공지2"     # 덮어쓰기(PK conflict)


def test_notice_clear():
    s = _store()
    s.set_setting("notice", "x", "2026-06-21T10:00:00+09:00")
    s.set_setting("notice", "", "2026-06-21T12:00:00+09:00")
    assert s.get_setting("notice")["value"] == ""           # 해제


def test_settings_independent_keys():
    s = _store()
    s.set_setting("notice", "공지", "t")
    s.set_setting("other", "값", "t")
    assert s.get_setting("notice")["value"] == "공지"
    assert s.get_setting("other")["value"] == "값"

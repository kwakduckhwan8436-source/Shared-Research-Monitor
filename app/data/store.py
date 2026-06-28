"""SQLite 영속 저장소.

추천 히스토리와 사후 검증(verdict)을 저장한다. 이게 '정확한 데이터 기반'을 *증명*하는 근거다:
추천을 기록하고 나중에 실측 수익률을 붙여 신뢰도 캘리브레이션을 산출한다.

시계열(OHLCV/틱)은 운영에선 parquet 권장. 본 스켈레톤은 단순화를 위해 추천/verdict 만 영속화한다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional


class Store:
    def __init__(self, path: str = "data/reco.sqlite3") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS recommendations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    score REAL NOT NULL,
                    confidence REAL NOT NULL,
                    weights_calibrated INTEGER NOT NULL,
                    weights_source TEXT NOT NULL,
                    risk_flags TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    ref_price REAL,
                    generated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS verdicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recommendation_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    ref_price REAL NOT NULL,
                    eval_price REAL NOT NULL,
                    forward_return REAL NOT NULL,
                    hit INTEGER NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    FOREIGN KEY (recommendation_id) REFERENCES recommendations(id)
                );
                CREATE INDEX IF NOT EXISTS idx_reco_snap ON recommendations(snapshot_id);
                CREATE INDEX IF NOT EXISTS idx_reco_sym ON recommendations(symbol, horizon);
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user TEXT NOT NULL,
                    text TEXT NOT NULL,
                    symbol TEXT,
                    created_at TEXT NOT NULL,
                    hidden INTEGER DEFAULT 0,
                    cid TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_chat_id ON chat_messages(id);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS board_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    author TEXT NOT NULL,
                    category TEXT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    hidden INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_board_id ON board_posts(id);
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_type TEXT NOT NULL,   -- 'chat' | 'post'
                    target_id INTEGER NOT NULL,
                    reason TEXT,
                    reporter_cid TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT DEFAULT 'open'   -- 'open' | 'resolved'
                );
                CREATE INDEX IF NOT EXISTS idx_report_status ON reports(status, id);
                """
            )
            # 기존 DB 마이그레이션 — 컬럼이 없으면 추가(SQLite는 IF NOT EXISTS 미지원)
            for tbl, col, ddl in [
                ("chat_messages", "hidden", "ALTER TABLE chat_messages ADD COLUMN hidden INTEGER DEFAULT 0"),
                ("chat_messages", "cid", "ALTER TABLE chat_messages ADD COLUMN cid TEXT"),
                ("board_posts", "hidden", "ALTER TABLE board_posts ADD COLUMN hidden INTEGER DEFAULT 0"),
            ]:
                try:
                    cols = [r[1] for r in self._conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
                    if col not in cols:
                        self._conn.execute(ddl)
                except Exception:
                    pass
            self._conn.commit()

    # ----- 설정(key-value): 공지 등 -----
    def set_setting(self, key: str, value: str, updated_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, updated_at),
            )
            self._conn.commit()

    def get_setting(self, key: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT key, value, updated_at FROM settings WHERE key=?", (key,)
            ).fetchone()
            return dict(row) if row else None

    # ----- 채팅(로컬 채팅방) -----
    def add_chat(self, user: str, text: str, created_at: str, symbol: Optional[str] = None,
                 cid: Optional[str] = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO chat_messages (user, text, symbol, created_at, cid) VALUES (?,?,?,?,?)",
                (user, text, symbol, created_at, cid),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_chat(self, after_id: int = 0, limit: int = 200,
                 symbol: Optional[str] = None) -> list[dict[str, Any]]:
        """after_id 이후의 메시지만(폴링 증분, 숨김 제외). 오래된→최신 순.
        symbol 지정 시 해당 종목 채팅방만, None이면 전체방(symbol 없는 메시지)."""
        with self._lock:
            if symbol == "__all__" or symbol is None:
                rows = self._conn.execute(
                    "SELECT id, user, text, symbol, created_at FROM chat_messages "
                    "WHERE id > ? AND COALESCE(hidden,0)=0 ORDER BY id ASC LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, user, text, symbol, created_at FROM chat_messages "
                    "WHERE id > ? AND symbol = ? AND COALESCE(hidden,0)=0 ORDER BY id ASC LIMIT ?",
                    (after_id, symbol, limit),
                ).fetchall()
            return [dict(r) for r in rows]

    # ----- 운영자(모더레이션) -----
    def hide_chat(self, msg_id: int, hidden: bool = True) -> None:
        with self._lock:
            self._conn.execute("UPDATE chat_messages SET hidden=? WHERE id=?",
                               (1 if hidden else 0, msg_id))
            self._conn.commit()

    def hide_post(self, post_id: int, hidden: bool = True) -> None:
        with self._lock:
            self._conn.execute("UPDATE board_posts SET hidden=? WHERE id=?",
                               (1 if hidden else 0, post_id))
            self._conn.commit()

    def add_report(self, target_type: str, target_id: int, reason: str,
                   reporter_cid: str, created_at: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO reports (target_type, target_id, reason, reporter_cid, created_at, status) "
                "VALUES (?,?,?,?,?,'open')",
                (target_type, target_id, reason, reporter_cid, created_at),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_reports(self, status: str = "open", limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, target_type, target_id, reason, reporter_cid, created_at, status "
                "FROM reports WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                # 신고 대상 내용 첨부(운영자가 보고 판단)
                if d["target_type"] == "chat":
                    t = self._conn.execute("SELECT user, text, hidden FROM chat_messages WHERE id=?",
                                           (d["target_id"],)).fetchone()
                else:
                    t = self._conn.execute("SELECT author as user, title || ' / ' || body as text, hidden "
                                           "FROM board_posts WHERE id=?", (d["target_id"],)).fetchone()
                if t:
                    d["content"] = dict(t)
                out.append(d)
            return out

    def resolve_report(self, report_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE reports SET status='resolved' WHERE id=?", (report_id,))
            self._conn.commit()

    def report_count(self, status: str = "open") -> int:
        with self._lock:
            r = self._conn.execute("SELECT COUNT(*) c FROM reports WHERE status=?", (status,)).fetchone()
            return int(r["c"]) if r else 0

    def admin_stats(self, days: int = 7) -> dict:
        """운영자용 집계 통계 — 개인정보 없이 수치만.
        일별 채팅 수, 일별 고유 접속자(cid) 수, 신고 처리 현황, 총계."""
        with self._lock:
            # 일별 채팅 수(최근 days일)
            chat_daily = self._conn.execute(
                "SELECT substr(created_at,1,10) d, COUNT(*) c FROM chat_messages "
                "GROUP BY d ORDER BY d DESC LIMIT ?", (days,)
            ).fetchall()
            # 일별 고유 접속자(cid 기준, 채팅 작성자) — cid 없는 건 제외
            visitor_daily = self._conn.execute(
                "SELECT substr(created_at,1,10) d, COUNT(DISTINCT cid) c FROM chat_messages "
                "WHERE cid IS NOT NULL AND cid<>'' GROUP BY d ORDER BY d DESC LIMIT ?", (days,)
            ).fetchall()
            # 일별 게시글 수
            post_daily = self._conn.execute(
                "SELECT substr(created_at,1,10) d, COUNT(*) c FROM board_posts "
                "GROUP BY d ORDER BY d DESC LIMIT ?", (days,)
            ).fetchall()
            # 신고 현황
            rep_open = self._conn.execute("SELECT COUNT(*) c FROM reports WHERE status='open'").fetchone()
            rep_resolved = self._conn.execute("SELECT COUNT(*) c FROM reports WHERE status='resolved'").fetchone()
            # 누적 총계
            tot_chat = self._conn.execute("SELECT COUNT(*) c FROM chat_messages").fetchone()
            tot_post = self._conn.execute("SELECT COUNT(*) c FROM board_posts").fetchone()
            hidden_chat = self._conn.execute("SELECT COUNT(*) c FROM chat_messages WHERE COALESCE(hidden,0)=1").fetchone()
            hidden_post = self._conn.execute("SELECT COUNT(*) c FROM board_posts WHERE COALESCE(hidden,0)=1").fetchone()
            return {
                "chat_daily": [{"date": r["d"], "count": r["c"]} for r in chat_daily],
                "visitor_daily": [{"date": r["d"], "count": r["c"]} for r in visitor_daily],
                "post_daily": [{"date": r["d"], "count": r["c"]} for r in post_daily],
                "reports": {"open": rep_open["c"] if rep_open else 0,
                            "resolved": rep_resolved["c"] if rep_resolved else 0},
                "totals": {"chat": tot_chat["c"] if tot_chat else 0,
                           "post": tot_post["c"] if tot_post else 0,
                           "hidden_chat": hidden_chat["c"] if hidden_chat else 0,
                           "hidden_post": hidden_post["c"] if hidden_post else 0},
            }

    def distinct_reporters(self, target_type: str, target_id: int) -> int:
        """해당 대상에 대한 서로 다른 신고자(cid) 수 — 자동 숨김 임계치 판단용.
        cid가 빈 신고는 각각 1명으로 카운트(악용 방지 위해 보수적)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT reporter_cid FROM reports WHERE target_type=? AND target_id=?",
                (target_type, target_id),
            ).fetchall()
            seen = set(); blanks = 0
            for r in rows:
                c = (r["reporter_cid"] or "").strip()
                if c:
                    seen.add(c)
                else:
                    blanks += 1
            return len(seen) + blanks

    # ----- 버그/건의 게시판 -----
    def add_post(self, author: str, title: str, body: str, created_at: str,
                 category: str = "버그") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO board_posts (author, category, title, body, status, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (author, category, title, body, "open", created_at),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_posts(self, limit: int = 100) -> list[dict[str, Any]]:
        """최신순 게시글(숨김 제외)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, author, category, title, body, status, created_at "
                "FROM board_posts WHERE COALESCE(hidden,0)=0 ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_recommendation(self, rec: dict[str, Any]) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO recommendations
                   (snapshot_id, symbol, horizon, score, confidence, weights_calibrated,
                    weights_source, risk_flags, evidence, ref_price, generated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rec["snapshot_id"], rec["symbol"], rec["horizon"],
                    rec["score"], rec["confidence"], int(rec["weights_calibrated"]),
                    rec["weights_source"], json.dumps(rec["risk_flags"], ensure_ascii=False),
                    json.dumps(rec["evidence"], ensure_ascii=False, default=str),
                    rec.get("ref_price"), rec["generated_at"],
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def open_recommendations(self, horizon: Optional[str] = None) -> list[sqlite3.Row]:
        """아직 verdict 가 없는 추천들."""
        q = """SELECT r.* FROM recommendations r
               LEFT JOIN verdicts v ON v.recommendation_id = r.id
               WHERE v.id IS NULL"""
        params: tuple = ()
        if horizon:
            q += " AND r.horizon = ?"
            params = (horizon,)
        with self._lock:
            return list(self._conn.execute(q, params).fetchall())

    def save_verdict(self, v: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO verdicts
                   (recommendation_id, symbol, horizon, confidence, ref_price,
                    eval_price, forward_return, hit, evaluated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    v["recommendation_id"], v["symbol"], v["horizon"], v["confidence"],
                    v["ref_price"], v["eval_price"], v["forward_return"],
                    int(v["hit"]), v["evaluated_at"],
                ),
            )
            self._conn.commit()

    def calibration_rows(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(
                "SELECT confidence, forward_return, hit FROM verdicts"
            ).fetchall())

    def close(self) -> None:
        with self._lock:
            self._conn.close()

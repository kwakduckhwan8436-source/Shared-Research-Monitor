"""에러 로깅·모니터링 — 파일(logs/app.log) + 콘솔. 회전 로그.

- setup_logging(): 루트 로거 구성(중복 호출 안전)
- get_logger(name): 모듈별 로거
- log_exc(logger, msg): 예외 스택과 함께 ERROR 로깅
- ErrorCounter: 엔드포인트별 에러 카운트(헬스/진단 노출용)
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_CONFIGURED = False
_LOG_DIR = "logs"
_LOG_FILE = os.path.join(_LOG_DIR, "app.log")


def setup_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
    except Exception:
        pass
    root = logging.getLogger("reco")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 콘솔
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)
    # 파일(회전: 2MB×3)
    try:
        if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
            fh = RotatingFileHandler(_LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
    except Exception:
        pass
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger("reco." + name)


def log_exc(logger: logging.Logger, msg: str) -> None:
    logger.error(msg, exc_info=True)


class ErrorCounter:
    """엔드포인트/소스별 에러 카운트(런타임 모니터링용, 메모리)."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._last: dict[str, str] = {}

    def record(self, key: str, err: Exception) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1
        self._last[key] = f"{type(err).__name__}: {err}"[:200]

    def snapshot(self) -> dict:
        return {"counts": dict(self._counts), "last": dict(self._last),
                "total": sum(self._counts.values())}

"""추정금지(No-Speculation) 예외 계층.

이 시스템의 핵심 규칙: 데이터가 없거나 검증되지 않은 값은 *추정으로 메우지 않는다*.
대신 명시적 예외를 던지거나 abstain(판단 보류)한다.
"""
from __future__ import annotations


class RecoError(Exception):
    """모든 도메인 예외의 베이스."""


class DataUnavailable(RecoError):
    """필수 데이터가 없거나 신선도(staleness) 예산을 초과했다.

    기본값(0, 평균 등)으로 메우면 안 된다. 시그널은 이 예외를 받아 abstain 처리한다.
    """

    def __init__(self, symbol: str, kind: str, reason: str = ""):
        self.symbol = symbol
        self.kind = kind
        self.reason = reason
        msg = f"DataUnavailable: {symbol}/{kind}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class NotCalibrated(RecoError):
    """캘리브레이션되지 않은 가중치를 명시적 허용 없이 사용하려 했다.

    allow_uncalibrated=True 를 명시적으로 넘기지 않으면 규칙기반 기본 가중치 사용을 거부한다.
    (조용한 기본값 사용 금지 — 쓰려면 의식적으로 opt-in 하고 결과에 라벨을 남긴다.)
    """


class ProviderError(RecoError):
    """외부 데이터 제공자(KIS/DART/KRX/News) 호출 실패."""


class NotImplementedYet(RecoError):
    """실연동 지점. 사용자 환경(API 키·네트워크)에서 구현해야 한다."""

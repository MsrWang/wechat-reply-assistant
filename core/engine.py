# -*- coding: utf-8 -*-
"""One draft request and one adaptive Jev request for a confirmed chat."""

from __future__ import annotations

from collections.abc import Callable

from .adaptive_analysis import (
    AdaptiveAnalysisResult,
    AnalysisMode,
    DraftStatus,
    build_adaptive_questions,
    build_adaptive_state,
    normalize_messages,
    parse_adaptive_result,
    select_turn_targets,
)
from .draft import draft_candidates
from .jev_client import ask
from .service_error import ServiceError


_STOP_AFTER_DRAFT_FAILURE = {"auth", "balance", "permission", "input_too_large"}


class AnalysisStageError(RuntimeError):
    def __init__(self, stage: str, message: str, candidates: list[str] | None = None,
                 *, category: str | None = None, status: int | None = None,
                 retryable: bool = False, user_message: str | None = None):
        super().__init__(message)
        self.stage = stage
        self.candidates = list(candidates or [])
        self.category = category
        self.status = status
        self.retryable = bool(retryable)
        self.user_message = user_message


class AnalysisCancelled(RuntimeError):
    """The caller invalidated this analysis before its next service request."""


def _stage_error(stage: str, exc: Exception, candidates=()) -> AnalysisStageError:
    """Convert only structured client data; arbitrary exception text is discarded."""
    if isinstance(exc, ServiceError):
        return AnalysisStageError(
            stage,
            f"{stage}失败：{exc.user_message}",
            list(candidates),
            category=exc.category,
            status=exc.status,
            retryable=exc.retryable,
            user_message=exc.user_message,
        )
    message = "服务返回的数据无法使用，请手动重试。"
    return AnalysisStageError(
        stage,
        f"{stage}失败：{message}",
        list(candidates),
        category="invalid_response",
        user_message=message,
    )


def analyze(messages: list, relationship: str, api_key: str, model: str | None = None,
            timeout: float = 60, context: int = 10, reply_to: str | None = None,
            style: str = "", reply_goal: str = "",
            is_current: Callable[[], bool] | None = None,
            revision_hint: str = "", previous_candidate: str = "",
            ) -> AdaptiveAnalysisResult:
    """Analyze all confirmed visible messages; context remains for call compatibility."""
    still_current = is_current or (lambda: True)
    normalized = normalize_messages(messages)
    if not normalized:
        raise ValueError("at least one message is required")
    mode: AnalysisMode = "reply" if normalized[-1]["from"] == "her" else "follow_up"
    targets = select_turn_targets(normalized, limit=10)
    if not still_current():
        raise AnalysisCancelled()

    draft_status: DraftStatus = "ready"
    try:
        candidates = tuple(draft_candidates(
            messages, relationship, api_key=api_key, model=model, timeout=timeout,
            keep=len(normalized), reply_to=reply_to, style=style,
            mode=mode, reply_goal=reply_goal,
            revision_hint=revision_hint, previous_candidate=previous_candidate,
        ))
    except ServiceError as exc:
        if exc.category in _STOP_AFTER_DRAFT_FAILURE:
            raise _stage_error("起草", exc) from None
        candidates = ()
        draft_status = "invalid_response" if exc.category == "invalid_response" else "failed"
    except Exception:
        candidates = ()
        draft_status = "failed"

    if not candidates and not targets:
        message = (
            "模型返回格式异常，请手动重试。"
            if draft_status == "invalid_response"
            else "起草服务暂时不可用，请手动重试。"
        )
        raise AnalysisStageError(
            "起草", f"起草失败：{message}", category=draft_status,
            user_message=message,
        )

    state = build_adaptive_state(
        normalized, relationship, targets, reply_to, mode=mode, reply_goal=reply_goal,
        style=style,
    )
    questions = build_adaptive_questions(targets, candidates, mode=mode, style=style)
    if not still_current():
        raise AnalysisCancelled()
    try:
        raw = ask(state, questions, api_key=api_key, timeout=timeout)
        return parse_adaptive_result(
            raw, normalized, targets, candidates, draft_status, reply_to, mode=mode,
            reply_goal=reply_goal,
        )
    except Exception as exc:
        raise _stage_error("Jev 判断", exc, candidates) from None

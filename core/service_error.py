"""Structured provider failures that never retain raw response or network details."""

from __future__ import annotations

import urllib.error

_CATEGORIES = {
    "auth", "balance", "permission", "invalid_request", "rate_limit",
    "provider_busy", "timeout", "network", "invalid_response", "input_too_large",
}
_HTTP_ERRORS = {
    401: ("auth", False, "API Key 被拒绝，请重新输入后再试。"),
    402: ("balance", False, "OpenRouter 余额不足，请充值后手动重试。"),
    403: ("permission", False, "API Key 权限受限，请检查 Key 设置。"),
    413: (
        "input_too_large",
        False,
        "当前可见内容过长，已停止；程序不会自动删减或拆分，请手动减少后重新读取。",
    ),
    422: ("invalid_request", False, "请求被服务拒绝，请检查对话后重新分析。"),
    429: ("rate_limit", True, "服务限流，请稍后手动重试。"),
    529: ("provider_busy", True, "服务繁忙，请稍后手动重试。"),
}


class ServiceError(RuntimeError):
    def __init__(
        self,
        stage: str,
        category: str,
        status: int | None,
        retryable: bool,
        user_message: str,
    ) -> None:
        if stage not in {"draft", "jev"} or category not in _CATEGORIES:
            raise ValueError("invalid service error metadata")
        super().__init__(user_message)
        self.stage = stage
        self.category = category
        self.status = status
        self.retryable = bool(retryable)
        self.user_message = user_message


def from_http_error(stage: str, exc: urllib.error.HTTPError) -> ServiceError:
    try:
        category, retryable, message = _HTTP_ERRORS.get(
            exc.code,
            (
                "provider_busy" if exc.code >= 500 else "invalid_request",
                exc.code >= 500,
                "服务请求未完成，请稍后手动重试。",
            ),
        )
        return ServiceError(stage, category, exc.code, retryable, message)
    finally:
        exc.close()


def timeout_error(stage: str) -> ServiceError:
    return ServiceError(stage, "timeout", None, True, "服务请求超时，请稍后手动重试。")


def network_error(stage: str) -> ServiceError:
    return ServiceError(stage, "network", None, True, "网络不可用或服务不可达，请检查网络后手动重试。")


def invalid_response_error(stage: str) -> ServiceError:
    return ServiceError(stage, "invalid_response", None, False, "服务返回的数据无法使用，请手动重试。")

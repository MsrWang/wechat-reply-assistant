"""Jev decisions API client. Standard library only. Never log the API key."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from .service_error import (
    ServiceError, from_http_error, invalid_response_error, network_error, timeout_error,
)

API_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"


def ask(state: dict, questions: dict, api_key: str, timeout: float = 20) -> dict:
    """POST state+questions to Jev once and return the parsed JSON body."""
    key = api_key.strip()
    if not key:
        raise ServiceError("jev", "auth", None, False, "请填写 API Key 后再开始分析。")
    payload = json.dumps(
        {"model": MODEL, "state": state, "questions": questions},
        ensure_ascii=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise from_http_error("jev", exc) from None
    except (TimeoutError, socket.timeout):
        raise timeout_error("jev") from None
    except urllib.error.URLError:
        raise network_error("jev") from None
    except (json.JSONDecodeError, UnicodeError, TypeError):
        raise invalid_response_error("jev") from None

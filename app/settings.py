"""Atomic persistence for ordinary, non-sensitive preferences only."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "Preferences", "config_path", "load_preferences", "save_preferences",
    "migrate_legacy_preferences",
]

_ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
_MAX_TEXT_LENGTH = 500
_MAX_LEGACY_BYTES = 64 * 1024


@dataclass(frozen=True)
class Preferences:
    relationship: str = "everyday conversation"
    context: int = 10
    reply_target: bool = False
    style: str = ""


def config_path() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise OSError("LOCALAPPDATA 不可用")
    return Path(root) / "JevChatPersonal" / "config.json"


def _legacy_config_path() -> Path:
    return _ROOT / "config.json"


def _valid_text(value: Any, default: str) -> str:
    return value if isinstance(value, str) and len(value) <= _MAX_TEXT_LENGTH else default


def _validated_preferences(value: Any) -> Preferences:
    if isinstance(value, Preferences):
        source = {
            "relationship": value.relationship,
            "context": value.context,
            "reply_target": value.reply_target,
            "style": value.style,
        }
    elif isinstance(value, dict):
        source = value
    else:
        source = {}

    context = source.get("context", 10)
    context = max(3, min(30, context)) if isinstance(context, int) and not isinstance(context, bool) else 10
    reply_target = source.get("reply_target", False)
    reply_target = reply_target if isinstance(reply_target, bool) else False
    relationship = _valid_text(source.get("relationship"), "everyday conversation")
    if not relationship:
        relationship = "everyday conversation"
    style = _valid_text(source.get("style"), "")
    return Preferences(relationship, context, reply_target, style)


def _validated_payload(preferences: Preferences) -> dict[str, object]:
    value = _validated_preferences(preferences)
    return {
        "schema_version": 1,
        "relationship": value.relationship,
        "context": value.context,
        "reply_target": value.reply_target,
        "style": value.style,
    }


def load_preferences() -> Preferences:
    try:
        with config_path().open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, ValueError, TypeError):
        return Preferences()
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return Preferences()
    return _validated_preferences(value)


def save_preferences(preferences: Preferences) -> None:
    if not isinstance(preferences, Preferences):
        raise TypeError("preferences 必须是 Preferences")
    target = config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(_validated_payload(preferences), stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def migrate_legacy_preferences() -> bool:
    """Copy one legacy config to LocalAppData once, never deleting the source."""
    try:
        target = config_path()
        if target.exists():
            return False
        legacy = _legacy_config_path()
        with legacy.open("rb") as stream:
            raw = stream.read(_MAX_LEGACY_BYTES + 1)
        if len(raw) > _MAX_LEGACY_BYTES:
            return False
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            return False
        preferences = _validated_preferences(value)
        save_preferences(preferences)
        return load_preferences() == preferences
    except (OSError, UnicodeError, ValueError, TypeError):
        return False

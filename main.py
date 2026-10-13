# -*- coding: utf-8 -*-
"""One in-memory session: manually read, confirm, analyze, and paste a reply."""
import queue
import sys
import threading

from app import credentials
from app.preview_model import PreviewModel
from app.privacy import ConfirmedSnapshot, build_snapshot, to_engine_messages
from app.session import SessionState
from app.visible_chat import capture_visible_chat
from app.single_instance import acquire, activate_existing, show_already_running_message
from app.fill_target import configure_dpi_awareness
from core import engine


session = SessionState()
events = queue.Queue()
ov = None
_generation = 0
_event_lock = threading.Lock()
_closed = False
_dpi_status = "not-configured"
_ADVICE = {
    "读取": "请确认微信聊天窗口可见及本地识别组件可用，然后手动重新读取。",
    "起草": "请检查 Key、网络和起草服务后手动重试。",
    "Jev 判断": "请检查网络和判断服务后手动重试；已起草的候选仍可使用。",
    "分析": "请检查已确认的预览和回复偏好后手动重试。",
    "填入": "请重新读取可见的微信窗口后再填入，或复制回复后手动粘贴。",
}


def _is_current(generation: int) -> bool:
    with _event_lock:
        return not _closed and generation == _generation


def _post_event(generation: int, kind: str, payload) -> None:
    # Checking and enqueueing share clear's lock: no late sensitive payload is retained.
    with _event_lock:
        if not _closed and generation == _generation:
            events.put((generation, kind, payload))


def _begin(stage: str) -> int:
    global _generation
    with _event_lock:
        _generation += 1
        generation = _generation
    session.busy_stage = stage
    session.candidates.clear()
    session.result = None
    if stage == "分析":
        session.fill_consumed = False
    ov.invalidate_replies()
    ov.set_busy(True)
    return generation


def _show_failure(stage: str, candidates=(), user_message: str | None = None) -> None:
    session.busy_stage = None
    session.result = None
    session.candidates = list(candidates)
    ov.show_stage_error(stage, user_message or _ADVICE[stage], session.candidates)


def _read_once(generation: int, hwnd: int) -> None:
    if not _is_current(generation):
        return
    try:
        raw, target = capture_visible_chat(hwnd)
        if not _is_current(generation):
            return
        preview = PreviewModel(raw.title, list(raw.lines))
        _post_event(generation, "read", (preview, raw, target))
    except Exception:
        _post_event(generation, "error", ("读取", ()))


def on_read() -> None:
    if _closed or session.busy_stage is not None:
        return
    session.clear_chat()
    ov.set_analysis_source(session.analysis_source)
    ov.set_preview(PreviewModel("", []))
    generation = _begin("读取")
    ov.set_status("正在读取当前聊天，仅在本地识别…", "busy")
    try:
        from app.capture import find_wechat_hwnd, is_minimized

        hwnd = find_wechat_hwnd()
        if is_minimized(hwnd):
            session.busy_stage = None
            ov.show_stage_error("读取", "微信已最小化，请手动恢复窗口后再读取。")
            return
        threading.Thread(target=_read_once, args=(generation, hwnd), daemon=True).start()
    except Exception:
        _show_failure("读取")


class ChatChanged(RuntimeError):
    """Reliable live evidence differs from the retained chat or window."""


class ChatVerificationUnavailable(RuntimeError):
    """The live chat or window cannot currently be verified."""


def _assert_chat_fresh(expected, target):
    """Validate retained target and content, distinguishing drift from missing evidence."""
    from app.fill_target import FillTargetError, capture_target, validate_target
    from app.freshness import has_new_visible_messages, same_chat_title, same_visible_chat

    if (expected is None or target is None or not expected.title.strip()
            or not expected.lines):
        raise ChatVerificationUnavailable()
    try:
        os_target = capture_target(target.hwnd, target.area, target.chat_title)
    except Exception as exc:
        raise ChatVerificationUnavailable() from exc
    if os_target is None:
        raise ChatVerificationUnavailable()
    try:
        validate_target(target, os_target)
    except FillTargetError as exc:
        raise ChatChanged() from exc
    try:
        current, current_target = capture_visible_chat(target.hwnd)
    except Exception as exc:
        raise ChatVerificationUnavailable() from exc
    if (current is None or current_target is None or not current.title.strip()
            or not current.lines or any(not line.text.strip() for line in current.lines)):
        raise ChatVerificationUnavailable()
    # Preserve the original target identity even if it changes during local capture.
    try:
        validate_target(target, current_target, title_matcher=same_chat_title)
    except FillTargetError as exc:
        raise ChatChanged() from exc
    if same_visible_chat(expected, current):
        return current_target
    if (expected.area != current.area or not same_chat_title(expected.title, current.title)
            or has_new_visible_messages(expected, current)):
        raise ChatChanged()
    raise ChatVerificationUnavailable()


def _show_stale_chat() -> None:
    session.busy_stage = None
    session.result = None
    session.raw_visible = None
    session.fill_target = None
    session.confirmed = None
    ov.invalidate_replies()
    ov.show_stale_chat("聊天已有变化，请重新读取后分析。")


def _analyze_once(generation: int, snapshot: ConfirmedSnapshot, expected_raw,
                  target, source: str, api_key: str, previous_candidate: str = "",
                  revision_hint: str = "") -> None:
    if not _is_current(generation):
        return
    rewriting = bool(revision_hint)
    error_kind = "rewrite_error" if rewriting else "error"
    validity = lambda: _is_current(generation)

    try:
        try:
            result = engine.analyze(
                to_engine_messages(snapshot), snapshot.relationship, api_key=api_key,
                context=snapshot.context, style=snapshot.style, reply_to=snapshot.reply_to,
                reply_goal=snapshot.reply_goal, is_current=validity,
                **({"previous_candidate": previous_candidate, "revision_hint": revision_hint}
                   if rewriting else {}),
            )
        except engine.AnalysisCancelled:
            return
        except engine.AnalysisStageError as exc:
            stage = exc.stage if exc.stage in ("起草", "Jev 判断") else "分析"
            candidates = tuple(exc.candidates) if stage == "Jev 判断" else ()
            _post_event(generation, error_kind, (stage, candidates, exc.user_message))
        except Exception:
            _post_event(generation, error_kind, ("分析", ()))
        else:
            if not _is_current(generation):
                return
            if rewriting and (result.draft_status != "ready" or not result.candidates):
                _post_event(generation, "rewrite_error", ("起草", (), "未生成新草稿，请手动重试。"))
                return
            _post_event(generation, "analysis", result)
    finally:
        # Release this worker's reference; Python cannot promise physical memory erasure.
        api_key = ""


def on_analyze(snapshot: ConfirmedSnapshot) -> None:
    if _closed or session.busy_stage is not None:
        return
    session.confirmed = None
    try:
        session.confirmed = build_snapshot(
            snapshot.title,
            list(snapshot.messages),
            snapshot.relationship,
            snapshot.context,
            style=snapshot.style,
            reply_to=snapshot.reply_to,
            reply_goal=snapshot.reply_goal,
        )
    except (AttributeError, TypeError, ValueError):
        _show_failure("分析")
        return
    if (session.analysis_source == "live_verified"
            and (session.raw_visible is None or session.fill_target is None)):
        _show_stale_chat()
        return
    temporary_key = ov.temporary_key()
    try:
        api_key = temporary_key or credentials.load_saved_key() or ""
    except credentials.CredentialStoreError:
        ov.set_status("无法读取已保存的 Key；可临时填写后重试。", "warning")
        return
    finally:
        temporary_key = ""
    if not session.can_analyze(bool(api_key)) or not session.confirmed.relationship:
        ov.set_status("请填写本次 Key，并确认预览消息和关系背景后再开始分析。", "warning")
        return
    frozen = session.confirmed
    session.preview_title = frozen.title
    session.preview_lines = list(frozen.messages)
    source = session.analysis_source
    expected_raw = session.raw_visible
    target = session.fill_target
    generation = _begin("分析")
    ov.set_status("正在起草回复并进行 Jev 判断…", "busy")
    try:
        threading.Thread(target=_analyze_once,
                         args=(generation, frozen, expected_raw, target, source, api_key),
                         daemon=True).start()
    except Exception:
        _show_failure("分析")
    finally:
        api_key = ""


def on_save_key(api_key: str) -> bool:
    if _closed or session.busy_stage is not None:
        return False
    credentials.save_key(api_key)
    return True


def on_rewrite(previous_candidate: str, revision_hint: str) -> None:
    """Explicit refinement reuses the immutable, already confirmed input."""
    global _generation
    if (_closed or session.busy_stage is not None or session.confirmed is None
            or session.result is None or not previous_candidate.strip()
            or not revision_hint.strip() or len(revision_hint) > 500):
        return
    frozen = session.confirmed
    expected_raw, target, source = session.raw_visible, session.fill_target, session.analysis_source
    if source == "live_verified" and (expected_raw is None or target is None):
        _show_stale_chat()
        return
    try:
        api_key = ov.temporary_key() or credentials.load_saved_key() or ""
    except credentials.CredentialStoreError:
        ov.show_rewrite_error("无法读取 Key，请检查连接设置。")
        return
    if not api_key:
        ov.show_rewrite_error("请先设置 OpenRouter Key。")
        return
    with _event_lock:
        _generation += 1
        generation = _generation
    session.busy_stage = "改写"
    ov.set_rewrite_busy()
    ov.set_status("正在按你的要求改写，上一份草稿仍保留…", "busy")
    try:
        threading.Thread(target=_analyze_once,
                         args=(generation, frozen, expected_raw, target, source, api_key,
                               previous_candidate, revision_hint), daemon=True).start()
    except Exception:
        session.busy_stage = None
        ov.show_rewrite_error("请手动重试。")
    finally:
        api_key = ""


def on_forget_key() -> bool:
    if _closed or session.busy_stage is not None:
        return False
    forgotten = credentials.forget_key()
    if ov is not None:
        ov.clear_key_fields(saved_key_present=False)
    return forgotten


def on_fill(text: str) -> None:
    if _closed or session.busy_stage is not None:
        return
    if not session.can_fill():
        ov.set_status("本次结果不能再次填入；仍可复制候选后手动处理。", "warning")
        return
    generation = _generation
    try:
        _assert_chat_fresh(session.raw_visible, session.fill_target)
    except ChatChanged:
        if _is_current(generation):
            _show_stale_chat()
        return
    except Exception:
        if _is_current(generation):
            ov.set_status("暂时无法确认微信内容，请确认窗口完整可见后重试；若已有新消息或内容变化，请重新读取。草稿已保留。", "warning")
        return
    if not _is_current(generation):
        return
    try:
        from app.fill import fill_validated

        fill_validated(session.fill_target, text)
    except Exception:
        if _is_current(generation):
            ov.show_fill_error(_ADVICE["填入"], session.candidates)
    else:
        if _is_current(generation):
            session.fill_consumed = True
            ov.mark_fill_consumed()
            ov.set_status("已填入微信，请检查后手动发送；对方回复后重新读取当前聊天。", "success")


def _clear_controller_state() -> None:
    global _generation
    with _event_lock:
        _generation += 1
        session.clear_chat()
        while True:
            try:
                events.get_nowait()
            except queue.Empty:
                break


def on_key_status() -> bool:
    status = credentials.credential_status()
    if status is credentials.CredentialStatus.UNAVAILABLE:
        raise credentials.CredentialStoreError("无法检测已保存的 Key")
    return status is credentials.CredentialStatus.SAVED


def on_clear() -> None:
    _clear_controller_state()
    if ov is not None:
        ov.clear_chat_fields()


def drain_events() -> None:
    if _closed:
        return
    try:
        while True:
            try:
                generation, kind, payload = events.get_nowait()
            except queue.Empty:
                break
            if not _is_current(generation):
                continue
            try:
                session.busy_stage = None
                if kind == "read":
                    preview, raw, target = payload
                    session.raw_visible = raw
                    session.fill_target = target
                    session.analysis_source = (
                        "live_verified" if raw is not None and target is not None
                        else "user_confirmed_only"
                    )
                    session.fill_consumed = False
                    ov.set_analysis_source(session.analysis_source)
                    session.preview_title = preview.title
                    session.preview_lines = list(preview.lines)
                    ov.set_preview(preview, read_at=raw.read_at)
                    if not preview.lines:
                        ov.set_status("未识别到消息，可添加并编辑消息后再开始分析。", "warning")
                elif kind == "analysis":
                    session.result = payload
                    session.candidates = list(payload.candidates)
                    session.fill_consumed = False
                    ov.show(payload, allow_fill=session.analysis_source == "live_verified")
                elif kind == "stale":
                    _show_stale_chat()
                elif kind == "error":
                    stage, candidates, *details = payload
                    _show_failure(stage, candidates, details[0] if details else None)
                elif kind == "rewrite_error":
                    stage, candidates, *details = payload
                    ov.show_rewrite_error(details[0] if details else _ADVICE[stage])
            except Exception:
                # Never expose a render/parser exception or let one event stop polling.
                retained = tuple(session.candidates) if kind == "analysis" else ()
                session.busy_stage = None
                session.result = None
                session.candidates = list(retained)
                try:
                    ov.show_stage_error("分析", _ADVICE["分析"], session.candidates)
                except Exception:
                    pass
    finally:
        if not _closed:
            ov.after(50, drain_events)


def _run_application() -> None:
    global ov, _closed, _dpi_status
    _dpi_status = configure_dpi_awareness()
    from app.overlay import Overlay

    _closed = False
    ov = Overlay(
        on_read=on_read,
        on_analyze=on_analyze,
        on_fill=on_fill,
        on_clear=on_clear,
        on_save_key=on_save_key,
        on_forget_key=on_forget_key,
        on_key_status=on_key_status,
        on_rewrite=on_rewrite,
    )
    try:
        ov.after(50, drain_events)
        ov.run()
    finally:
        _closed = True
        _clear_controller_state()
        if ov is not None:
            ov.clear_chat_fields()


def cli(argv=None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--smoke-test"]:
        return
    if arguments:
        raise SystemExit(2)
    main()


def main() -> None:
    instance = acquire()
    try:
        if instance.already_running:
            if not activate_existing("Jev · 微信回复助手"):
                show_already_running_message()
            return
        _run_application()
    finally:
        instance.close()


if __name__ == "__main__":
    cli()

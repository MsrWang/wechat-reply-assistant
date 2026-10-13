import ast
import dataclasses
import importlib
import re
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.credentials import CredentialStatus, CredentialStoreError
from app.fill_target import FillTarget, FillTargetError, validate_target as real_validate_target
from app.privacy import ChatLine, build_snapshot
from app.freshness import make_raw_visible
from app.session import SessionState
from app.settings import Preferences
from core.adaptive_analysis import TurnTarget, build_adaptive_questions
from core.service_error import ServiceError
from core.questions import CORE_DIMENSIONS, JUDGE_QUESTIONS
from app.result_view import project_result
from core.engine import analyze
from tests.test_engine import jev_response


def imported_modules(path):
    file = Path(path)
    if not file.exists():
        raise AssertionError(f"required module is missing: {path}")
    tree = ast.parse(file.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            # Include from-app imports as well as direct module imports.
            if node.module in ("app", "core"):
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
            else:
                names.add(node.module)
    return names


class _Overlay:
    """UI boundary double: no Qt objects, Windows access or event loop."""

    def __init__(self):
        self.preview = None
        self.key = "visible-key"
        self.result = None
        self.errors = []
        self.statuses = []
        self.scheduled = []
        self.busy = False
        self.saved_key_present = False
        self.chat_clear_calls = 0
        self.stale_messages = []
        self.read_at = None
        self.analysis_source = "user_confirmed_only"
        self.fill_consumed_calls = 0
        self.allow_fill = None

    def set_analysis_source(self, source):
        self.analysis_source = source

    def set_preview(self, preview, *, read_at=None):
        self.preview = preview
        self.read_at = read_at
        self.result = None
        self.busy = False

    def set_busy(self, busy):
        self.busy = busy

    def set_rewrite_busy(self):
        self.busy = True

    def show_rewrite_error(self, message):
        self.busy = False
        self.errors.append(("改写", message, []))

    def set_status(self, text, kind="idle"):
        self.statuses.append((text, kind))

    def invalidate_replies(self):
        self.result = None

    def show_stale_chat(self, message):
        self.stale_messages.append(message)
        self.busy = False

    def show_waiting_for_reply(self, message):
        self.set_status(message, "idle")
        self.busy = False

    def show(self, result, *, allow_fill=True):
        self.result = result
        self.allow_fill = allow_fill
        self.busy = False

    def mark_fill_consumed(self):
        self.fill_consumed_calls += 1

    def show_fill_error(self, message, candidates=None):
        self.errors.append(("填入", message, list(candidates or [])))

    def show_stage_error(self, stage, message, candidates=None):
        self.errors.append((stage, message, list(candidates or [])))
        self.busy = False

    def temporary_key(self):
        return self.key.strip()

    def clear_chat_fields(self, saved_key_present=None):
        self.chat_clear_calls += 1
        self.preview = None
        self.key = ""
        self.result = None
        self.busy = False
        if saved_key_present is not None:
            self.saved_key_present = saved_key_present

    def clear_key_fields(self, saved_key_present=False):
        self.key = ""
        self.saved_key_present = saved_key_present

    def after(self, ms, callback):
        self.scheduled.append((ms, callback))

    def run(self):
        pass


class _DeferredThread:
    def __init__(self, jobs, *, target, args=(), daemon=False):
        self.jobs, self.target, self.args, self.daemon = jobs, target, args, daemon

    def start(self):
        self.jobs.append(self)

    def run(self):
        target, args = self.target, self.args
        self.target, self.args = None, ()
        target(*args)


class _Frame:
    def __init__(self):
        self.regions = []

    def __getitem__(self, region):
        self.regions.append(region)
        return region


class ManualControllerTests(unittest.TestCase):
    def setUp(self):
        # Fail safely on the old Windows-only entry point instead of importing it.
        tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
        self.assertTrue(any(isinstance(node, ast.FunctionDef) and node.name == "on_read"
                            for node in tree.body), "manual controller is not implemented")
        self.controller = importlib.reload(importlib.import_module("main"))
        self.overlay = _Overlay()
        self.controller.ov = self.overlay
        self.credentials = SimpleNamespace(
            load_saved_key=Mock(return_value=None),
            save_key=Mock(),
            forget_key=Mock(return_value=True),
            credential_status=Mock(return_value=CredentialStatus.MISSING),
            CredentialStatus=CredentialStatus,
            CredentialStoreError=CredentialStoreError,
        )
        self.controller.credentials = self.credentials
        self.instance = SimpleNamespace(already_running=False, close=Mock())
        self.controller.acquire = Mock(return_value=self.instance)
        self.controller.activate_existing = Mock(return_value=True)
        self.controller.show_already_running_message = Mock()
        self.controller.configure_dpi_awareness = Mock(return_value="per-monitor-v2")
        self.jobs = []
        self.thread_patch = patch.object(
            self.controller.threading, "Thread",
            side_effect=lambda **kwargs: _DeferredThread(self.jobs, **kwargs),
        )
        self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)
        self.capture = SimpleNamespace(find_wechat_hwnd=Mock(return_value=7),
                                       is_minimized=Mock(return_value=False))
        self.frame = _Frame()
        self.capture_once = Mock(return_value=(self.frame, (10, 60, 400, 500, "bg", 5)))
        self.reader = SimpleNamespace(read=Mock(return_value=[("her", "张三", "在吗", 90)]))
        self.ocr = SimpleNamespace(read_title=Mock(return_value="当前聊天"),
                                   Reader=Mock(return_value=self.reader))
        self.target = FillTarget(
            7, 88, "weixin.exe", (10, 60, 400, 500), (900, 700), 120, "当前聊天"
        )
        self.capture_target = Mock(side_effect=lambda hwnd, area, title: FillTarget(
            hwnd, 88, "weixin.exe", tuple(area[:4]), (900, 700), 120, title
        ))
        self.validate_target = Mock(wraps=real_validate_target)
        self.validate_live_window = Mock()
        self.fill = Mock()
        self.modules_patch = patch.dict(sys.modules, {
            "app.capture": self.capture,
            "app.manual_capture": SimpleNamespace(capture_once=self.capture_once),
            "app.ocr": self.ocr,
            "app.fill": SimpleNamespace(fill_validated=self.fill),
            "app.fill_target": SimpleNamespace(
                capture_target=self.capture_target,
                validate_target=self.validate_target,
                validate_live_window=self.validate_live_window,
                FillTargetError=FillTargetError,
            ),
        })
        self.modules_patch.start()
        self.addCleanup(self.modules_patch.stop)
        self.draft_patch = patch.object(self.controller.engine, "draft_candidates", return_value=["在呀"])
        self.ask_patch = patch.object(self.controller.engine, "ask",
                                      return_value=jev_response(candidates=("在呀",)))
        self.draft = self.draft_patch.start()
        self.ask = self.ask_patch.start()
        self.addCleanup(self.draft_patch.stop)
        self.addCleanup(self.ask_patch.stop)
        # A spy keeps the real engine and parser in every controller workflow.
        self.analyze_patch = patch.object(self.controller.engine, "analyze",
                                         wraps=self.controller.engine.analyze)
        self.analyze = self.analyze_patch.start()
        self.addCleanup(self.analyze_patch.stop)
        self.snapshot = build_snapshot("已确认的标题", [ChatLine("her", "已编辑的消息", "同事")],
                                       "朋友", 8, "简短", "同事")
        self.bind_visible()

    def bind_visible(self):
        self.controller.session.raw_visible = make_raw_visible(
            "当前聊天", [ChatLine("her", "在吗", "张三")], (10, 60, 400, 500)
        )
        self.controller.session.fill_target = self.target
        self.controller.session.analysis_source = "live_verified"
        self.overlay.set_analysis_source("live_verified")

    def authorize_fill(self, candidates=("在呀",)):
        self.bind_visible()
        self.controller.session.result = object()
        self.controller.session.candidates = list(candidates)
        self.controller.session.fill_consumed = False

    def read(self):
        self.controller.on_read()
        self.jobs[-1].run()
        self.controller.drain_events()

    def test_rewrite_uses_frozen_snapshot_and_failure_keeps_result_and_consumed(self):
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()
        self.controller.drain_events()
        old_result = self.controller.session.result
        frozen = self.controller.session.confirmed
        self.controller.session.fill_consumed = True
        self.controller.on_rewrite("我编辑过的草稿", "更简短")
        self.controller.on_rewrite("重复", "更自然")
        self.assertEqual(len(self.jobs), 2)
        self.assertIs(self.controller.session.result, old_result)
        self.assertIs(self.controller.session.confirmed, frozen)
        self.draft.side_effect = RuntimeError("synthetic failure")
        self.jobs[-1].run()
        self.controller.drain_events()
        self.assertIs(self.controller.session.result, old_result)
        self.assertTrue(self.controller.session.fill_consumed)
        self.assertEqual(self.analyze.call_args.kwargs["previous_candidate"], "我编辑过的草稿")
        self.assertEqual(self.analyze.call_args.kwargs["revision_hint"], "更简短")
        self.assertEqual(self.analyze.call_args.kwargs["reply_goal"], frozen.reply_goal)
        self.assertFalse(self.overlay.busy)

    def test_rewrite_uses_confirmed_snapshot_when_capture_is_unavailable(self):
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()
        self.controller.drain_events()
        old = self.controller.session.result
        frozen = self.controller.session.confirmed
        self.capture_once.side_effect = RuntimeError("secret-key private-chat")
        self.capture_once.reset_mock()
        self.controller.on_rewrite("草稿", "更自然")
        self.jobs[-1].run()
        self.controller.drain_events()
        self.assertIs(self.controller.session.confirmed, frozen)
        self.assertIsNot(self.controller.session.result, old)
        self.assertEqual(self.draft.call_count, 2)
        self.assertEqual(self.draft.call_args.args[0], [("her", "已编辑的消息", "同事")])
        self.capture_once.assert_not_called()
        self.assertEqual(self.overlay.stale_messages, [])

    def test_empty_rewrite_result_keeps_previous_success(self):
        from tests.test_result_view import _result
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()
        self.controller.drain_events()
        previous = self.controller.session.result
        with patch.object(self.controller.engine, "analyze", return_value=_result(
                candidates=(), best_index=None, reply_scores=(), draft_status="failed")):
            self.controller.on_rewrite("旧草稿", "更简短")
            self.jobs[-1].run()
            self.controller.drain_events()
        self.assertIs(self.controller.session.result, previous)
        self.assertEqual(self.overlay.errors[-1][0], "改写")

    def test_startup_only_constructs_ui_and_clears_on_exit(self):
        constructor = Mock(return_value=self.overlay)
        with patch.dict(sys.modules, {"app.overlay": SimpleNamespace(Overlay=constructor)}):
            self.controller.main()
        self.assertEqual(set(constructor.call_args.kwargs),
                         {"on_read", "on_analyze", "on_fill", "on_clear",
                          "on_save_key", "on_forget_key", "on_key_status", "on_rewrite"})
        self.capture.find_wechat_hwnd.assert_not_called()
        self.capture_once.assert_not_called()
        self.ocr.Reader.assert_not_called()
        self.analyze.assert_not_called()
        self.draft.assert_not_called()
        self.ask.assert_not_called()
        self.assertEqual(self.jobs, [])
        self.assertEqual(self.overlay.key, "")
        self.credentials.forget_key.assert_not_called()
        self.instance.close.assert_called_once_with()

    def test_startup_and_exit_never_probe_or_load_credentials(self):
        constructor = Mock(return_value=self.overlay)
        with patch.dict(sys.modules, {"app.overlay": SimpleNamespace(Overlay=constructor)}):
            self.controller._run_application()
        self.credentials.credential_status.assert_not_called()
        self.credentials.load_saved_key.assert_not_called()

    def test_key_status_callback_only_checks_on_explicit_request(self):
        self.credentials.credential_status.return_value = CredentialStatus.SAVED
        self.assertTrue(self.controller.on_key_status())
        self.credentials.credential_status.assert_called_once_with()
        self.credentials.load_saved_key.assert_not_called()

    def test_unavailable_credential_status_is_not_reported_as_missing(self):
        self.credentials.credential_status.return_value = CredentialStatus.UNAVAILABLE
        with self.assertRaises(CredentialStoreError):
            self.controller.on_key_status()
        self.credentials.load_saved_key.assert_not_called()

    def test_confirmed_context_is_not_truncated_by_compatibility_count(self):
        lines = [ChatLine("her", f"确认消息{i}") for i in range(14)]
        snapshot = build_snapshot("会话", lines, "朋友", 3)
        self.ask.return_value = jev_response(
            turn_ids=tuple(f"turn_{i:03d}" for i in range(4, 14)), candidates=("在呀",)
        )
        self.controller.on_analyze(snapshot)
        self.jobs[-1].run()
        self.assertEqual(len(self.analyze.call_args.args[0]), 14)
        self.assertEqual(len(self.ask.call_args.args[0]["chat"]["messages"]), 14)

    def test_controller_event_reaches_real_typed_overlay_method(self):
        from tests.test_overlay_contract import OverlayContractTests

        endpoint = OverlayContractTests()
        endpoint.setUp()
        self.overlay.show = endpoint.ui.show
        with patch.object(endpoint.module, "_ReplyCard", return_value=Mock()):
            self.controller.on_analyze(self.snapshot)
            self.jobs[-1].run()
            self.controller.drain_events()
        lines, view = endpoint.ui.timeline.set_conversation.call_args.args
        self.assertEqual(lines, self.snapshot.messages)
        self.assertEqual(view.status_text, "生成 1 条，Jev 推荐 1 条，可填入推荐回复")
        self.assertEqual(self.overlay.errors, [])

    def test_second_instance_exits_before_ui_or_credentials(self):
        self.instance.already_running = True
        constructor = Mock(return_value=self.overlay)
        with patch.dict(sys.modules, {"app.overlay": SimpleNamespace(Overlay=constructor)}):
            self.controller.main()

        self.controller.activate_existing.assert_called_once_with("Jev · 微信回复助手")
        constructor.assert_not_called()
        self.credentials.credential_status.assert_not_called()
        self.credentials.load_saved_key.assert_not_called()
        self.instance.close.assert_called_once_with()

    def test_exit_exception_uses_the_same_clear_path(self):
        self.overlay.run = Mock(side_effect=RuntimeError("event loop ended"))
        with patch.dict(sys.modules, {"app.overlay": SimpleNamespace(Overlay=Mock(return_value=self.overlay))}):
            with self.assertRaisesRegex(RuntimeError, "event loop ended"):
                self.controller.main()
        self.assertFalse(hasattr(self.controller.session, "api_key"))
        self.assertEqual(self.overlay.key, "")
        self.assertTrue(self.controller.events.empty())
        self.credentials.forget_key.assert_not_called()

    def test_read_is_one_daemon_capture_then_local_ocr_without_analysis(self):
        self.read()
        self.assertEqual(len(self.jobs), 1)
        self.assertTrue(self.jobs[0].daemon)
        self.capture_once.assert_called_once_with(7)
        self.ocr.read_title.assert_called_once_with((slice(5, 60), slice(10, 400)))
        self.reader.read.assert_called_once_with((slice(60, 500), slice(10, 400)), "bg")
        self.assertEqual(self.overlay.preview.title, "当前聊天")
        self.assertEqual(self.overlay.preview.lines, (ChatLine("her", "在吗", "张三"),))
        self.assertIsNone(self.controller.session.frame)
        self.assertEqual(self.controller.session.fill_target, self.target)
        self.assertEqual(self.controller.session.raw_visible.lines,
                         (ChatLine("her", "在吗", "张三"),))
        self.assertEqual(self.overlay.read_at, self.controller.session.raw_visible.read_at)
        self.analyze.assert_not_called()
        self.draft.assert_not_called()
        self.ask.assert_not_called()

    def test_empty_ocr_stays_in_editable_preview_without_analysis(self):
        self.reader.read.return_value = []
        self.read()
        self.assertEqual(self.overlay.preview.lines, ())
        self.overlay.preview.append(ChatLine("her", "手工添加"))
        self.assertEqual(self.overlay.preview.lines[0].text, "手工添加")
        self.analyze.assert_not_called()
        self.assertIn("未识别到消息，可添加并编辑消息后再开始分析。",
                      [text for text, _kind in self.overlay.statuses])

    def test_latest_own_message_starts_follow_up_analysis(self):
        snapshot = build_snapshot(
            "当前聊天", [ChatLine("me", "我已经发了")], "朋友", 10,
            reply_goal="补充一个问题",
        )
        self.overlay.key = "visible-key"
        self.controller.on_analyze(snapshot)
        self.assertEqual(len(self.jobs), 1)
        self.jobs[-1].run()
        self.controller.drain_events()
        self.analyze.assert_called_once()
        self.assertEqual(self.analyze.call_args.kwargs["reply_goal"], "补充一个问题")

    def test_manual_confirmed_content_analyzes_without_raw_or_fill_target(self):
        self.controller.session.raw_visible = None
        self.controller.session.fill_target = None
        self.controller.session.analysis_source = "user_confirmed_only"
        self.overlay.key = "visible-key"
        manual = build_snapshot("手工会话", [ChatLine("her", "在吗")], "朋友", 10)
        self.controller.on_analyze(manual)
        self.assertEqual(len(self.jobs), 1)
        self.jobs[-1].run()
        self.controller.drain_events()
        self.analyze.assert_called_once()
        self.assertFalse(self.overlay.allow_fill)
        self.capture_once.assert_not_called()
        self.controller.on_fill("在吗")
        self.capture_once.assert_not_called()
        self.fill.assert_not_called()

    def test_blank_ocr_title_degrades_to_manual_analysis_instead_of_stale(self):
        self.ocr.read_title.return_value = " "
        self.read()
        self.assertEqual(self.controller.session.analysis_source, "user_confirmed_only")
        self.controller.on_analyze(self.snapshot)
        self.assertEqual(len(self.jobs), 2)
        self.jobs[-1].run()
        self.controller.drain_events()
        self.analyze.assert_called_once()
        self.assertFalse(self.overlay.allow_fill)

    def test_analysis_uses_confirmed_snapshot_when_capture_is_unavailable(self):
        self.read()
        self.capture_once.reset_mock()
        self.capture_once.side_effect = RuntimeError("secret-key private-chat")
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()
        self.controller.drain_events()
        self.draft.assert_called_once()
        self.ask.assert_called_once()
        self.assertEqual(self.draft.call_args.args[0], [("her", "已编辑的消息", "同事")])
        self.assertIsNotNone(self.controller.session.result)
        self.capture_once.assert_not_called()
        self.assertFalse(self.overlay.busy)
        self.assertEqual(self.overlay.stale_messages, [])

    def test_new_message_after_draft_waits_for_fill_verification(self):
        def draft_reply(*args, **kwargs):
            self.reader.read.return_value.append(("her", "张三", "新消息", 100))
            return ["在呀"]

        self.draft.side_effect = draft_reply
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()
        self.controller.drain_events()
        self.draft.assert_called_once()
        self.ask.assert_called_once()
        self.assertFalse(self.overlay.busy)
        self.assertIsNone(self.controller.session.busy_stage)
        self.assertIsNotNone(self.controller.session.result)
        self.assertEqual(self.overlay.stale_messages, [])

    def test_visible_drift_after_analysis_keeps_frozen_result(self):
        def judge(*args, **kwargs):
            self.reader.read.return_value = [("her", "张三", "新消息", 90)]
            return jev_response(candidates=("在呀",))

        self.ask.side_effect = judge
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()
        self.controller.drain_events()
        self.draft.assert_called_once()
        self.ask.assert_called_once()
        self.assertIsNotNone(self.overlay.result)
        self.assertIsNotNone(self.controller.session.result)
        self.assertEqual(self.overlay.stale_messages, [])

    def test_nonprefix_content_before_fill_is_uncertain_and_never_fills(self):
        self.authorize_fill()
        self.reader.read.return_value = [("her", "张三", "新消息", 90)]
        self.controller.on_fill("在呀")
        self.fill.assert_not_called()
        self.assertEqual(self.controller.session.candidates, ["在呀"])
        self.assertEqual(self.overlay.stale_messages, [])
        self.assertIsNotNone(self.controller.session.result)

    def test_nonempty_incomplete_readback_preserves_state_and_manual_retry(self):
        baseline = [("her", "张三", "在吗", 90), ("me", "我", "可以聊", 90)]
        variants = [baseline[:1],
                    [("her", "张三", "在", 90), baseline[1]],
                    [("her", "张三", "在嘛", 90), baseline[1]],
                    list(reversed(baseline)),
                    [("me", "张三", "在吗", 90), baseline[1]],
                    [("her", "李四", "在吗", 90), baseline[1]],
                    [("her", "张三", "在吗", 90), ("me", "我", "可以", 90),
                     ("her", "张三", "新的", 90)]]
        for rows in variants:
            with self.subTest(rows=rows):
                self.controller.on_clear()
                self.authorize_fill()
                self.fill.reset_mock()
                session = self.controller.session
                session.raw_visible = make_raw_visible("当前聊天", [
                    ChatLine(who, text, name) for who, name, text, _ in baseline
                ], self.target.area)
                session.confirmed = self.snapshot
                self.overlay.result = session.result
                result, raw, target = session.result, session.raw_visible, session.fill_target
                self.reader.read.return_value = rows
                self.controller.on_fill("选中后编辑的回复")
                self.fill.assert_not_called()
                self.assertEqual(self.overlay.stale_messages, [])
                self.assertIs(self.controller.session, session)
                self.assertIs(session.result, result)
                self.assertIs(self.overlay.result, result)
                self.assertIs(session.confirmed, self.snapshot)
                self.assertIs(session.raw_visible, raw)
                self.assertIs(session.fill_target, target)
                self.assertEqual(session.candidates, ["在呀"])
                self.assertFalse(session.fill_consumed)
                self.reader.read.return_value = baseline
                self.controller.on_fill("选中后编辑的回复")
                self.controller.on_fill("选中后编辑的回复")
                self.fill.assert_called_once_with(target, "选中后编辑的回复")
                self.assertTrue(session.fill_consumed)

    def test_complete_prefix_with_additional_message_invalidates_fill(self):
        self.authorize_fill()
        self.reader.read.return_value = [("her", "张三", " 在吗 ", 90),
                                       ("her", "张三", "新消息", 90)]
        self.controller.on_fill("在呀")
        self.fill.assert_not_called()
        self.assertIsNone(self.controller.session.result)
        self.assertEqual(len(self.overlay.stale_messages), 1)

    def test_single_truncated_message_can_retry_without_regenerating(self):
        self.authorize_fill()
        result = self.controller.session.result
        self.reader.read.return_value = [("her", "张三", "在", 90)]
        self.controller.on_fill("编辑后的草稿")
        self.fill.assert_not_called()
        self.assertIs(self.controller.session.result, result)
        self.assertEqual(self.overlay.stale_messages, [])
        self.reader.read.return_value = [("her", "张三", "在吗", 90)]
        self.controller.on_fill("编辑后的草稿")
        self.fill.assert_called_once_with(self.target, "编辑后的草稿")
        self.ask.assert_not_called()

    def test_normalized_title_and_messages_allow_exact_fill(self):
        self.authorize_fill()
        original = " cafe\u0301  room "
        self.controller.session.raw_visible = make_raw_visible(
            original, [ChatLine("her", "在吗", "张三")], self.target.area)
        target = replace(self.target, chat_title=original)
        self.controller.session.fill_target = target
        self.ocr.read_title.return_value = "café room"
        self.reader.read.return_value = [("her", " 张三 ", " 在吗 ", 90)]
        self.controller.on_fill("在呀")
        self.fill.assert_called_once_with(target, "在呀")

    def test_similar_titles_cannot_authorize_fill(self):
        for original, current in (("project-A", "project-B"), ("当前聊天", "当前聊大")):
            with self.subTest(current=current):
                self.controller.on_clear()
                self.authorize_fill()
                self.fill.reset_mock()
                self.controller.session.raw_visible = make_raw_visible(
                    original, [ChatLine("her", "在吗", "张三")], self.target.area)
                self.controller.session.fill_target = replace(self.target, chat_title=original)
                self.ocr.read_title.return_value = current
                self.controller.on_fill("在呀")
                self.fill.assert_not_called()
                self.assertIsNone(self.controller.session.result)

    def test_success_checks_content_only_at_fill(self):
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()
        self.controller.drain_events()
        self.assertIsNotNone(self.overlay.result)
        self.assertEqual(self.capture_once.call_count, 0)
        self.assertEqual(self.reader.read.call_count, 0)
        self.controller.on_fill("在呀")
        self.assertEqual(self.capture_once.call_count, 1)
        self.fill.assert_called_once_with(self.target, "在呀")

    def test_live_source_retains_fill_capability_with_fill_time_capture(self):
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()
        self.controller.drain_events()
        self.assertEqual(self.capture_once.call_count, 0)
        self.assertTrue(self.overlay.allow_fill)
        self.controller.on_fill("在呀")
        self.assertEqual(self.capture_once.call_count, 1)

    def test_successful_fill_is_one_shot_but_failed_fill_can_retry(self):
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()
        self.controller.drain_events()
        self.controller.on_fill("在呀")
        self.controller.on_fill("在呀")
        self.fill.assert_called_once_with(self.target, "在呀")
        self.assertTrue(self.controller.session.fill_consumed)
        self.assertEqual(self.overlay.fill_consumed_calls, 1)

        self.controller.on_clear()
        self.authorize_fill(("重试候选",))
        self.fill.side_effect = RuntimeError("synthetic fill failure")
        self.controller.on_fill("重试候选")
        self.assertFalse(self.controller.session.fill_consumed)
        self.assertIsNotNone(self.controller.session.result)
        self.assertEqual(self.controller.session.candidates, ["重试候选"])

    def test_unavailable_fill_preserves_draft_and_manual_retry_succeeds(self):
        self.authorize_fill()
        self.controller.session.confirmed = self.snapshot
        self.overlay.result = self.controller.session.result
        result = self.controller.session.result
        confirmed = self.controller.session.confirmed
        raw = self.controller.session.raw_visible
        target = self.controller.session.fill_target
        self.capture_once.side_effect = RuntimeError("secret-key private-chat request-body")
        self.controller.on_fill("在呀")
        self.fill.assert_not_called()
        self.assertEqual(self.overlay.stale_messages, [])
        self.assertEqual(self.overlay.statuses[-1][1], "warning")
        for secret in ("secret-key", "private-chat", "request-body"):
            self.assertNotIn(secret, self.overlay.statuses[-1][0])
        self.assertIs(self.controller.session.result, result)
        self.assertIs(self.overlay.result, result)
        self.assertIs(self.controller.session.confirmed, confirmed)
        self.assertIs(self.controller.session.raw_visible, raw)
        self.assertIs(self.controller.session.fill_target, target)
        self.assertEqual(self.controller.session.candidates, ["在呀"])
        self.assertFalse(self.controller.session.fill_consumed)
        self.capture_once.side_effect = None
        self.controller.on_fill("在呀")
        self.fill.assert_called_once_with(target, "在呀")
        self.assertTrue(self.controller.session.fill_consumed)
        self.controller.on_fill("在呀")
        self.fill.assert_called_once()

    def test_incomplete_fill_evidence_is_unavailable_and_keeps_draft(self):
        for evidence in ("blank_title", "empty_messages", "missing_target",
                         "os_unavailable", "current_target_unavailable"):
            with self.subTest(evidence=evidence):
                self.controller.on_clear()
                self.authorize_fill()
                self.controller.session.confirmed = self.snapshot
                self.overlay.result = self.controller.session.result
                result = self.controller.session.result
                if evidence == "blank_title":
                    self.ocr.read_title.return_value = " "
                elif evidence == "empty_messages":
                    self.reader.read.return_value = []
                elif evidence == "missing_target":
                    self.capture_target.return_value = None
                elif evidence == "current_target_unavailable":
                    self.capture_target.side_effect = [self.target, None]
                else:
                    self.capture_target.side_effect = FillTargetError()
                self.controller.on_fill("在呀")
                self.fill.assert_not_called()
                self.assertEqual(self.overlay.stale_messages, [])
                self.assertEqual(self.overlay.statuses[-1][1], "warning")
                self.assertIs(self.controller.session.result, result)
                self.assertIs(self.overlay.result, result)
                self.assertIs(self.controller.session.confirmed, self.snapshot)
                self.assertEqual(self.controller.session.candidates, ["在呀"])
                self.assertFalse(self.controller.session.fill_consumed)
                self.ocr.read_title.return_value = "当前聊天"
                self.reader.read.return_value = [("her", "张三", "在吗", 90)]
                self.capture_target.side_effect = None
                self.capture_target.return_value = self.target

    def test_unknown_fill_validation_error_is_safe_and_retryable(self):
        self.authorize_fill()
        result = self.controller.session.result
        for failing_adapter in (self.ocr.read_title, self.validate_target):
            with self.subTest(adapter=failing_adapter):
                failing_adapter.side_effect = RuntimeError("secret-key private-chat request-body")
                self.controller.on_fill("在呀")
                self.fill.assert_not_called()
                self.assertEqual(self.overlay.stale_messages, [])
                self.assertIs(self.controller.session.result, result)
                self.assertFalse(self.controller.session.fill_consumed)
                self.assertEqual(self.overlay.statuses[-1][1], "warning")
                for secret in ("secret-key", "private-chat", "request-body"):
                    self.assertNotIn(secret, self.overlay.statuses[-1][0])
                failing_adapter.side_effect = None


    def test_preview_edits_do_not_replace_raw_baseline(self):
        self.read()
        original = self.controller.session.raw_visible
        self.overlay.preview.set_text(0, "已编辑的消息")
        self.overlay.preview.set_name(0, "同事")
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()
        self.controller.drain_events()
        self.assertIs(self.controller.session.raw_visible, original)
        self.assertEqual(original.lines, (ChatLine("her", "在吗", "张三"),))
        self.assertEqual(self.draft.call_args.args[0], [("her", "已编辑的消息", "同事")])
        self.assertIsNotNone(self.overlay.result)

    def test_new_read_clears_prior_reply_and_raw_state_before_worker_starts(self):
        self.controller.session.confirmed = self.snapshot
        self.controller.session.candidates = ["在呀"]
        self.controller.on_read()
        self.assertIsNone(self.controller.session.raw_visible)
        self.assertIsNone(self.controller.session.confirmed)
        self.assertIsNone(self.controller.session.result)
        self.assertIsNone(self.controller.session.fill_target)
        self.assertEqual(self.controller.session.candidates, [])
        self.assertIsNone(self.overlay.result)
        self.assertEqual(self.overlay.analysis_source, "user_confirmed_only")
        self.assertEqual(self.overlay.analysis_source, self.controller.session.analysis_source)
        self.assertFalse(self.controller.session.can_fill())
        self.capture_once.assert_not_called()

    def test_missing_live_baseline_fails_closed_without_loading_key(self):
        self.controller.session.raw_visible = None
        self.overlay.key = ""
        self.controller.on_analyze(self.snapshot)
        self.assertEqual(self.jobs, [])
        self.credentials.load_saved_key.assert_not_called()
        self.assertEqual(self.overlay.stale_messages, ["聊天已有变化，请重新读取后分析。"])

    def test_empty_live_title_does_not_interrupt_confirmed_analysis(self):
        self.ocr.read_title.return_value = " "
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()
        self.controller.drain_events()
        self.draft.assert_called_once()
        self.ask.assert_called_once()
        self.assertIsNotNone(self.controller.session.result)
        self.capture_once.assert_not_called()

    def test_clear_during_freshness_capture_blocks_fill_and_status(self):
        self.authorize_fill()
        def capture(hwnd):
            self.controller.on_clear()
            return self.frame, (10, 60, 400, 500, "bg", 5)

        self.capture_once.side_effect = capture
        self.controller.on_fill("在呀")
        self.fill.assert_not_called()
        self.assertEqual(self.overlay.statuses, [])
        self.assertEqual(self.overlay.stale_messages, [])

    def test_clear_and_reread_during_jev_suppress_late_analysis(self):
        def judge(*args, **kwargs):
            self.controller.on_clear()
            self.controller.on_read()
            return jev_response(candidates=("在呀",))

        self.ask.side_effect = judge
        self.controller.on_analyze(self.snapshot)
        self.jobs[0].run()
        self.assertTrue(self.controller.events.empty())
        self.assertEqual(self.controller.session.busy_stage, "读取")
        self.jobs[1].run()
        self.controller.drain_events()
        self.assertIsNone(self.overlay.result)
        self.assertEqual(self.overlay.preview.title, "当前聊天")

    def test_minimized_window_does_not_capture_or_restore(self):
        self.capture.is_minimized.return_value = True
        self.controller.on_read()
        self.assertEqual(self.jobs, [])
        self.capture_once.assert_not_called()
        self.assertEqual(self.overlay.errors[-1][0], "读取")
        self.assertIsNone(self.controller.session.fill_target)
        self.assertEqual(self.overlay.analysis_source, "user_confirmed_only")
        self.assertEqual(self.overlay.analysis_source, self.controller.session.analysis_source)
        self.assertFalse(self.controller.session.can_fill())

    def test_read_error_does_not_expose_exception_text(self):
        self.reader.read.side_effect = RuntimeError("secret-key private-chat request-body")
        self.read()
        stage, message, candidates = self.overlay.errors[-1]
        self.assertEqual((stage, candidates), ("读取", []))
        for secret in ("secret-key", "private-chat", "request-body"):
            self.assertNotIn(secret, message)
        self.assertIsNone(self.controller.session.fill_target)
        self.assertEqual(self.overlay.analysis_source, "user_confirmed_only")
        self.assertEqual(self.overlay.analysis_source, self.controller.session.analysis_source)
        self.assertFalse(self.controller.session.can_fill())

    def test_analysis_uses_frozen_visible_values_once_despite_later_mutation(self):
        self.overlay.key = " visible-key "
        self.controller.on_analyze(self.snapshot)
        self.overlay.key = "duplicate-key"
        self.controller.on_analyze(self.snapshot)
        self.assertEqual(len(self.jobs), 1)
        self.assertTrue(self.jobs[0].daemon)
        self.overlay.key = "changed-after-click"
        self.controller.session.confirmed = None
        self.jobs[0].run()
        self.controller.drain_events()
        self.analyze.assert_called_once()
        args, kwargs = self.analyze.call_args
        self.assertEqual(args, ([("her", "已编辑的消息", "同事")], "朋友"))
        validity = kwargs.pop("is_current")
        self.assertEqual(kwargs, {
            "api_key": "visible-key", "context": 8, "style": "简短", "reply_to": "同事",
            "reply_goal": "",
        })
        self.assertTrue(validity())
        self.assertIsNotNone(self.overlay.result)
        self.assertEqual(self.overlay.result.candidates, ("在呀",))
        self.assertIsNone(self.controller.session.busy_stage)

    def test_empty_key_or_messages_cannot_start_analysis(self):
        self.overlay.key = " "
        self.controller.on_analyze(self.snapshot)
        self.overlay.key = "visible-key"
        self.controller.on_analyze(None)
        self.assertEqual(self.jobs, [])
        self.analyze.assert_not_called()

    def test_draft_and_jev_failures_are_safe_and_never_retry(self):
        for stage, expected_candidates in (("起草", []), ("Jev 判断", ["已起草"])):
            with self.subTest(stage=stage):
                self.controller.on_clear()
                self.bind_visible()
                self.draft.return_value = ["已起草"]
                self.draft.side_effect = (ServiceError("draft", "auth", 401, False, "安全提示")
                                          if stage == "起草" else None)
                self.ask.side_effect = RuntimeError("secret-key private-chat request-body")
                before = len(self.jobs)
                self.overlay.key = "secret-key"
                self.controller.on_analyze(self.snapshot)
                self.jobs[-1].run()
                self.controller.drain_events()
                error_stage, message, candidates = self.overlay.errors[-1]
                self.assertEqual((error_stage, candidates), (stage, expected_candidates))
                self.assertEqual(self.controller.session.candidates, expected_candidates)
                for secret in ("secret-key", "private-chat", "request-body"):
                    self.assertNotIn(secret, message)
                self.assertEqual(len(self.jobs), before + 1)

    def test_clear_drops_queued_results_and_pending_analysis_before_it_starts(self):
        self.overlay.key = "visible-key"
        self.controller.on_analyze(self.snapshot)
        self.controller.on_clear()
        self.jobs[0].run()
        self.controller.drain_events()
        self.analyze.assert_not_called()
        self.assertTrue(self.controller.events.empty())
        self.assertIsNone(self.overlay.result)
        self.assertFalse(hasattr(self.controller.session, "api_key"))
        self.assertEqual(self.overlay.key, "")
        self.overlay.key = "another-key"
        self.bind_visible()
        self.controller.on_analyze(self.snapshot)
        self.jobs[1].run()
        self.assertFalse(self.controller.events.empty())
        self.controller.on_clear()
        self.assertTrue(self.controller.events.empty())

    def test_clear_during_inflight_analysis_discards_late_success_and_errors(self):
        for error in (None, RuntimeError("secret")):
            with self.subTest(error=bool(error)):
                def complete(*args, **kwargs):
                    self.controller.on_clear()
                    if error:
                        raise error
                    return jev_response(candidates=("在呀",))
                self.ask.side_effect = complete
                self.bind_visible()
                self.overlay.key = "visible-key"
                self.controller.on_analyze(self.snapshot)
                self.jobs[-1].run()
                self.assertTrue(self.controller.events.empty())
                self.controller.drain_events()
                self.assertIsNone(self.overlay.result)
                self.assertEqual(self.controller.session.candidates, [])

    def test_clear_after_draft_prevents_jev_without_reporting_or_requeueing(self):
        """Regression: cancellation after the first request must stop before Jev."""
        def finish_draft_then_clear(*args, **kwargs):
            self.controller.on_clear()
            return ["已起草"]

        self.draft.side_effect = finish_draft_then_clear
        self.overlay.key = "visible-key"
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()
        self.controller.drain_events()

        self.draft.assert_called_once()
        self.ask.assert_not_called()
        self.assertTrue(self.controller.events.empty())
        self.assertEqual(self.overlay.errors, [])
        self.assertIsNone(self.overlay.result)
        self.assertEqual(self.controller.session.candidates, [])

    def test_render_failure_is_safe_and_event_polling_is_rescheduled(self):
        self.overlay.show = Mock(side_effect=RuntimeError("visible-key private-chat response-body"))
        self.overlay.key = "visible-key"
        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()

        self.controller.drain_events()

        self.assertEqual(self.overlay.errors[-1][0], "分析")
        for secret in ("visible-key", "private-chat", "response-body"):
            self.assertNotIn(secret, self.overlay.errors[-1][1])
        self.assertEqual(self.overlay.scheduled[-1], (50, self.controller.drain_events))

    def test_saved_key_is_loaded_only_for_explicit_analysis(self):
        self.overlay.key = ""
        self.credentials.load_saved_key.return_value = "saved-key"

        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()

        self.assertEqual(self.analyze.call_args.kwargs["api_key"], "saved-key")
        self.credentials.load_saved_key.assert_called_once_with()

    def test_clear_keeps_saved_credential_but_forget_deletes_it(self):
        self.credentials.credential_status.return_value = CredentialStatus.SAVED
        self.overlay.saved_key_present = True

        self.controller.on_clear()
        self.credentials.forget_key.assert_not_called()
        self.assertTrue(self.overlay.saved_key_present)
        self.assertEqual(self.overlay.chat_clear_calls, 1)
        self.credentials.credential_status.assert_not_called()

        self.controller.on_forget_key()
        self.credentials.forget_key.assert_called_once_with()
        self.assertFalse(self.overlay.saved_key_present)

    def test_temporary_key_wins_without_reading_saved_value(self):
        self.overlay.key = "temporary-key"
        self.credentials.load_saved_key.return_value = "saved-key"

        self.controller.on_analyze(self.snapshot)
        self.jobs[-1].run()

        self.assertEqual(self.analyze.call_args.kwargs["api_key"], "temporary-key")
        self.credentials.load_saved_key.assert_not_called()

    def test_explicit_save_is_the_only_controller_write(self):
        self.assertTrue(self.controller.on_save_key("new-key"))
        self.credentials.save_key.assert_called_once_with("new-key")
        self.credentials.forget_key.assert_not_called()

    def test_cleared_old_read_cannot_replace_new_read(self):
        self.controller.on_read()
        old_job = self.jobs[-1]
        self.controller.on_clear()
        self.controller.on_read()
        new_job = self.jobs[-1]
        old_job.run()
        self.assertEqual(self.controller.session.busy_stage, "读取")
        self.assertTrue(self.controller.events.empty())
        new_job.run()
        self.controller.drain_events()
        self.assertEqual(self.overlay.preview.title, "当前聊天")
        self.capture_once.assert_called_once_with(7)

    def test_clear_during_capture_discards_late_preview(self):
        def complete_capture(hwnd):
            self.controller.on_clear()
            return self.frame, (10, 60, 400, 500, "bg", 5)
        self.capture_once.side_effect = complete_capture
        self.controller.on_read()
        self.jobs[-1].run()
        self.controller.drain_events()
        self.assertTrue(self.controller.events.empty())
        self.assertIsNone(self.overlay.preview)
        self.assertIsNone(self.controller.session.fill_target)

    def test_fill_requires_retained_target_and_calls_only_safe_fill(self):
        self.authorize_fill()
        self.controller.session.fill_target = None
        self.controller.on_fill("在呀")
        self.fill.assert_not_called()
        self.assertTrue(self.overlay.statuses)
        self.assertEqual(self.overlay.statuses[-1][1], "warning")
        self.read()
        self.authorize_fill()
        self.controller.on_fill("在呀")
        self.fill.assert_called_once_with(self.target, "在呀")
        self.analyze.assert_not_called()
        self.controller.on_clear()
        self.controller.on_fill("在呀")
        self.assertEqual(self.fill.call_count, 1)

    def test_fill_title_drift_blocks_before_executor_and_keeps_candidates(self):
        self.read()
        self.authorize_fill()
        self.ocr.read_title.return_value = "另一位联系人"

        self.controller.on_fill("在呀")

        self.fill.assert_not_called()
        self.assertEqual(self.controller.session.candidates, ["在呀"])
        self.assertEqual(self.overlay.stale_messages[-1], "聊天已有变化，请重新读取后分析。")

    def test_stale_os_target_blocks_before_preflight_capture(self):
        self.read()
        self.authorize_fill()
        self.capture_target.side_effect = FillTargetError()
        captures_before = self.capture_once.call_count

        self.controller.on_fill("在呀")

        self.assertEqual(self.capture_once.call_count, captures_before)
        self.fill.assert_not_called()
        self.assertEqual(self.controller.session.candidates, ["在呀"])
        self.assertEqual(self.overlay.stale_messages, [])
        self.assertEqual(self.overlay.statuses[-1][1], "warning")

    def test_target_identity_drift_during_capture_blocks_fill(self):
        for drift in ({"hwnd": 8}, {"pid": 99}, {"process_name": "other.exe"},
                      {"window_size": (800, 700)}, {"dpi": 144},
                      {"area": (11, 60, 400, 500)}):
            with self.subTest(drift=drift):
                self.capture_target.side_effect = None
                self.capture_target.return_value = replace(self.target, **drift)
                self.authorize_fill()
                self.controller.on_fill("在呀")
                self.fill.assert_not_called()
                self.assertEqual(self.controller.session.candidates, ["在呀"])
                self.assertEqual(self.overlay.stale_messages[-1], "聊天已有变化，请重新读取后分析。")

    def test_fill_error_is_stage_safe_and_preserves_available_candidates(self):
        self.read()
        self.authorize_fill()
        self.fill.side_effect = RuntimeError("visible-key private-chat request-body")
        self.controller.on_fill("在呀")
        stage, message, candidates = self.overlay.errors[-1]
        self.assertEqual((stage, candidates), ("填入", ["在呀"]))
        for secret in ("visible-key", "private-chat", "request-body"):
            self.assertNotIn(secret, message)


class SourceContractTests(unittest.TestCase):
    def test_readme_documents_transfer_bundle_without_claiming_target_acceptance(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("build-transfer-bundle.ps1", readme)
        self.assertIn("Jev-Chat-Assistant-0.1.2-win-x64.zip", readme)
        self.assertIn("Get-FileHash", readme)
        self.assertIn("目标电脑", readme)
        self.assertIn("独立验收", readme)

    def test_transfer_entrypoint_is_portable_and_bound_to_0_1_2(self):
        source = Path("packaging/transfer/Install-Jev.cmd").read_text(encoding="utf-8")
        self.assertIn(r'%~dp0scripts\install-release.ps1', source)
        self.assertIn(r'%~dp0package\jev-chat-windows', source)
        self.assertIn("-ExpectedVersion 0.1.2", source)
        self.assertNotIn(r"dist\releases", source)
        guide = Path("packaging/transfer/安装说明.txt").read_text(encoding="utf-8")
        self.assertIn("Get-FileHash", guide)
        self.assertIn("OpenRouter Key", guide)
        self.assertIn("不是代码签名", guide)

    def test_current_source_version_keeps_legacy_installer_bound_to_0_1_2(self):
        version_source = Path("app/version.py").read_text(encoding="utf-8")
        version_match = re.search(r'VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', version_source)
        self.assertIsNotNone(version_match)
        self.assertEqual(version_match.group(1), "0.1.6")

        installer = Path("Install-Jev.cmd").read_text(encoding="utf-8")
        self.assertIn(r"dist\releases\0.1.2\jev-chat-windows", installer)
        self.assertIn("-ExpectedVersion 0.1.2", installer)

    def test_structured_draft_request_has_no_loose_recovery_path(self):
        draft_source = Path("core/draft.py").read_text(encoding="utf-8")
        self.assertIn('"require_parameters": True', draft_source)
        self.assertIn('"type": "json_schema"', draft_source)
        self.assertIn('"strict": True', draft_source)
        self.assertNotIn("response-healing", draft_source.lower())
        self.assertNotIn("for attempt in", draft_source)

    def test_reply_controls_expose_context_and_only_offer_eligible_fill(self):
        overlay_source = Path("app/overlay.py").read_text(encoding="utf-8")
        self.assertIn("查看其他备选", overlay_source)
        self.assertIn("群聊发言人（单聊留空）", overlay_source)
        self.assertIn('setAccessibleName("本次分析背景")', overlay_source)
        self.assertIn('self.fillButton.setVisible(reply.fill_allowed and reply.role != "other")', overlay_source)

    def test_reply_judgment_uses_separate_safety_and_necessity_contracts(self):
        runtime = "\n".join(
            path.read_text(encoding="utf-8")
            for root in (Path("core"), Path("app"))
            for path in root.rglob("*.py")
        )
        self.assertNotIn("acceptable_reply", runtime)
        questions = build_adaptive_questions((), ("可以回复", "先核对"))
        self.assertEqual(
            {key for key in questions if key.endswith("__safe_to_send")},
            {"reply_a__safe_to_send", "reply_b__safe_to_send"},
        )
        self.assertIn("reply_worthwhile", questions)

    def test_reviewed_candidates_preserve_copy_and_fill_boundary(self):
        candidates = ("我先核对一下", "我晚点给你答复", "你说得对")
        response = jev_response(candidates=candidates)
        response["answers"]["turn_000__tension_resolved"] = {"noul": 0.2}
        with patch("core.engine.draft_candidates", return_value=list(candidates)) as draft, \
             patch("core.engine.ask", return_value=response) as judge:
            result = analyze([("her", "这件事你怎么看")], "朋友", api_key="synthetic-key")
        self.assertEqual((draft.call_count, judge.call_count), (1, 1))
        view = project_result(result)
        self.assertEqual(len(view.ordered_replies), 3)
        self.assertEqual(sum(reply.fill_allowed for reply in view.ordered_replies), 3)
        self.assertTrue(all(reply.copy_allowed for reply in view.ordered_replies))

    def test_chat_session_and_preferences_cannot_own_a_key(self):
        self.assertNotIn("api_key", {field.name for field in dataclasses.fields(SessionState)})
        self.assertNotIn("api_key", {field.name for field in dataclasses.fields(Preferences)})

    def test_timeline_has_only_display_dependencies(self):
        imports = imported_modules("app/conversation_timeline.py")
        allowed = {"__future__", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
                   "qfluentwidgets", "app.privacy", "app.result_view", "app.ui_theme"}
        self.assertTrue(imports <= allowed, imports - allowed)

    def test_overlay_does_not_own_credentials_capture_or_provider_access(self):
        imports = imported_modules("app/overlay.py")
        forbidden = {"app.credentials", "app.capture", "app.manual_capture", "app.fill",
                     "core.draft", "core.jev_client", "core.engine", "requests", "urllib",
                     "httpx", "socket", "subprocess", "importlib"}
        self.assertFalse(imports & forbidden, imports & forbidden)

    def test_overlay_uses_the_typed_preferences_interface(self):
        source = Path("app/overlay.py").read_text(encoding="utf-8")
        for retired_call in (
            "settings.relationship()", "settings.context()",
            "settings.reply_target()", "settings.style()",
        ):
            self.assertNotIn(retired_call, source)
        self.assertIn("settings.load_preferences()", source)
        self.assertIn("settings.Preferences(", source)

    def test_main_has_manual_actions_and_no_automatic_workers(self):
        source = Path("main.py").read_text(encoding="utf-8")
        for required in ("def on_read", "def on_analyze", "def on_clear"):
            self.assertIn(required, source)
        for forbidden in ("spawn_worker", "check_update_bg", "on_toggle_capture",
                          "app import settings, update, worker"):
            self.assertNotIn(forbidden, source)
        identifiers = {node.id for node in ast.walk(ast.parse(source))
                       if isinstance(node, ast.Name)}
        self.assertNotIn("capture_on", identifiers)

    def test_fill_path_never_calls_executor_with_bare_hwnd_and_area(self):
        source = Path("main.py").read_text(encoding="utf-8")
        self.assertIn("fill_validated(session.fill_target, text)", source)
        self.assertNotIn("fill(session.hwnd, session.area, text)", source)

    def test_matching_fill_has_no_extra_confirmation_dialog(self):
        source = Path("app/overlay.py").read_text(encoding="utf-8")
        self.assertNotIn('setWindowTitle("填入微信")', source)

    def test_main_does_not_start_capture_or_network_from_module_scope(self):
        tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
        calls = [node for node in tree.body
                 if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)]
        self.assertEqual(calls, [])

    def test_approved_jev_dimensions_are_preserved_and_namespaced(self):
        self.assertEqual(
            CORE_DIMENSIONS,
            ("literal_question", "true_intent", "danger_level", "should_reply_now",
             "best_action", "she_needs", "tension_resolved"),
        )
        self.assertTrue(all(JUDGE_QUESTIONS[name].get("criteria") for name in CORE_DIMENSIONS))
        target = TurnTarget("turn_004", 4, 5)
        questions = build_adaptive_questions((target,), ())
        self.assertEqual(
            {key for key in questions if key.startswith("turn_004__")},
            {f"turn_004__{name}" for name in CORE_DIMENSIONS},
        )

    def test_retired_automatic_modules_are_absent(self):
        self.assertFalse(Path("app/worker.py").exists())
        self.assertFalse(Path("app/update.py").exists())

    def test_product_python_files_parse_without_import_side_effects(self):
        roots = (Path("app"), Path("core"), Path("scripts"), Path("tests"))
        files = [path for root in roots for path in root.rglob("*.py")]
        self.assertTrue(files)
        for path in files:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))

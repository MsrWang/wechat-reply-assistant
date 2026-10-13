# -*- coding: utf-8 -*-
"""Validate, focus, and paste into the retained WeChat target without sending."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time

from app.fill_target import FillTarget, FillTargetError, validate_live_window


def _libraries():
    user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
    user.GetForegroundWindow.restype = wintypes.HWND
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.SetForegroundWindow.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowRect.restype = wintypes.BOOL
    user.WindowFromPoint.argtypes = [wintypes.POINT]
    user.WindowFromPoint.restype = wintypes.HWND
    user.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
    user.IsChild.restype = wintypes.BOOL
    user.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(_GuiThreadInfo)]
    user.GetGUIThreadInfo.restype = wintypes.BOOL
    return user, kernel


def set_clipboard(text: str) -> None:
    """Replace clipboard text only after every target and focus guard has passed."""
    user, kernel = _libraries()
    kernel.GlobalAlloc.restype = ctypes.c_void_p
    kernel.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel.GlobalFree.argtypes = [ctypes.c_void_p]
    user.SetClipboardData.restype = ctypes.c_void_p
    user.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    data = str(text).encode("utf-16-le") + b"\0\0"
    for _attempt in range(10):
        if not user.OpenClipboard(None):
            time.sleep(0.05)
            continue
        try:
            if not user.EmptyClipboard():
                raise FillTargetError()
            handle = kernel.GlobalAlloc(0x2, len(data))
            if not handle:
                raise FillTargetError()
            pointer = kernel.GlobalLock(handle)
            if not pointer:
                kernel.GlobalFree(handle)
                raise FillTargetError()
            ctypes.memmove(pointer, data, len(data))
            kernel.GlobalUnlock(handle)
            if not user.SetClipboardData(13, handle):
                kernel.GlobalFree(handle)
                raise FillTargetError()
            return
        finally:
            user.CloseClipboard()
    raise FillTargetError()


def _window_rect(hwnd: int) -> wintypes.RECT:
    user, _kernel = _libraries()
    rect = wintypes.RECT()
    try:
        dwm = ctypes.windll.dwmapi
        dwm.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD
        ]
        dwm.DwmGetWindowAttribute.restype = ctypes.c_long
        ok = dwm.DwmGetWindowAttribute(
            hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)
        ) == 0
    except (AttributeError, OSError):
        ok = False
    if not ok and not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise FillTargetError()
    return rect


def bring_to_foreground(hwnd: int) -> None:
    user, kernel = _libraries()
    foreground = user.GetForegroundWindow()
    if foreground == hwnd:
        return
    foreground_thread = user.GetWindowThreadProcessId(foreground, None) if foreground else 0
    current_thread = kernel.GetCurrentThreadId()
    attached = False
    try:
        if foreground_thread and foreground_thread != current_thread:
            attached = bool(user.AttachThreadInput(current_thread, foreground_thread, True))
        if not user.SetForegroundWindow(hwnd):
            raise FillTargetError()
        time.sleep(0.15)
    finally:
        if attached:
            user.AttachThreadInput(current_thread, foreground_thread, False)


def assert_foreground(hwnd: int) -> None:
    user, _kernel = _libraries()
    if user.GetForegroundWindow() != hwnd:
        raise FillTargetError()


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def _assert_input_focus(target: FillTarget, point: wintypes.POINT) -> None:
    user, _kernel = _libraries()
    target_thread = user.GetWindowThreadProcessId(target.hwnd, None)
    info = _GuiThreadInfo()
    info.cbSize = ctypes.sizeof(info)
    if not target_thread or not user.GetGUIThreadInfo(target_thread, ctypes.byref(info)):
        raise FillTargetError()
    focus = info.hwndFocus
    point_window = user.WindowFromPoint(point)
    if not focus or not point_window:
        raise FillTargetError()
    focus_pid = wintypes.DWORD()
    point_pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(focus, ctypes.byref(focus_pid))
    user.GetWindowThreadProcessId(point_window, ctypes.byref(point_pid))
    focus_owned = focus == target.hwnd or bool(user.IsChild(target.hwnd, focus))
    point_owned = point_window == target.hwnd or bool(user.IsChild(target.hwnd, point_window))
    if (
        focus_pid.value != target.pid
        or point_pid.value != target.pid
        or not focus_owned
        or not point_owned
    ):
        raise FillTargetError()


def _key_chord(key: int) -> None:
    user, _kernel = _libraries()
    ctrl_down = key_down = False
    try:
        user.keybd_event(0x11, 0, 0, 0)
        ctrl_down = True
        user.keybd_event(key, 0, 0, 0)
        key_down = True
        time.sleep(0.02)
    finally:
        if key_down:
            user.keybd_event(key, 0, 2, 0)
        if ctrl_down:
            user.keybd_event(0x11, 0, 2, 0)


def click_input_region(target: FillTarget) -> None:
    user, _kernel = _libraries()
    rect = _window_rect(target.hwnd)
    x0, _y0, _x1, y1 = target.area
    point = wintypes.POINT(rect.left + x0 + 60, rect.top + y1 + 40)
    if not (rect.left <= point.x < rect.right and rect.top <= point.y < rect.bottom):
        raise FillTargetError()
    previous = wintypes.POINT()
    if not user.GetCursorPos(ctypes.byref(previous)):
        raise FillTargetError()
    pressed = False
    try:
        if not user.SetCursorPos(point.x, point.y):
            raise FillTargetError()
        time.sleep(0.05)
        user.mouse_event(0x2, 0, 0, 0, 0)
        pressed = True
        user.mouse_event(0x4, 0, 0, 0, 0)
        pressed = False
        time.sleep(0.05)
    finally:
        if pressed:
            user.mouse_event(0x4, 0, 0, 0, 0)
        user.SetCursorPos(previous.x, previous.y)
    _assert_input_focus(target, point)


def _move_to_end() -> None:
    _key_chord(0x23)  # Ctrl+End, then paste appends to the current draft.


def _paste() -> None:
    _key_chord(0x56)  # Ctrl+V only; sending always remains manual.


def fill_validated(target: FillTarget, text: str) -> None:
    validate_live_window(target)
    bring_to_foreground(target.hwnd)
    assert_foreground(target.hwnd)
    click_input_region(target)
    assert_foreground(target.hwnd)
    _move_to_end()
    assert_foreground(target.hwnd)
    set_clipboard(text)
    assert_foreground(target.hwnd)
    _paste()

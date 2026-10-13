"""Immutable WeChat fill identity and fail-closed drift checks."""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_SAFE_MESSAGE = "微信目标已变化，请重新读取后再填入，或复制回复后手动粘贴。"
_WECHAT_PROCESSES = {"weixin.exe", "wechat.exe"}


class FillTargetError(RuntimeError):
    def __init__(self, _message: str | None = None) -> None:
        super().__init__(_SAFE_MESSAGE)


@dataclass(frozen=True)
class FillTarget:
    hwnd: int
    pid: int
    process_name: str
    area: tuple[int, int, int, int]
    window_size: tuple[int, int]
    dpi: int
    chat_title: str


class _WindowsTargetBackend:
    def __init__(self) -> None:
        if os.name != "nt":
            raise FillTargetError()
        import ctypes.wintypes as wintypes

        self._w = wintypes
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            self._dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        except OSError:
            self._dwm = None
        self._kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel.OpenProcess.restype = wintypes.HANDLE
        self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        self._kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self._user.IsWindow.argtypes = [wintypes.HWND]
        self._user.IsWindow.restype = wintypes.BOOL
        self._user.IsWindowVisible.argtypes = [wintypes.HWND]
        self._user.IsWindowVisible.restype = wintypes.BOOL
        self._user.IsIconic.argtypes = [wintypes.HWND]
        self._user.IsIconic.restype = wintypes.BOOL
        self._user.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        self._user.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self._user.GetWindowRect.restype = wintypes.BOOL
        get_dpi = getattr(self._user, "GetDpiForWindow", None)
        if get_dpi is not None:
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT
        if self._dwm is not None:
            self._dwm.DwmGetWindowAttribute.argtypes = [
                wintypes.HWND, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD
            ]
            self._dwm.DwmGetWindowAttribute.restype = ctypes.c_long

    def _process_name(self, pid: int) -> str:
        handle = self._kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise FillTargetError()
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = self._w.DWORD(len(buffer))
            if not self._kernel.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                raise FillTargetError()
            return Path(buffer.value).name.lower()
        finally:
            self._kernel.CloseHandle(handle)

    def _rect(self, hwnd: int):
        rect = self._w.RECT()
        ok = False
        if self._dwm is not None:
            ok = self._dwm.DwmGetWindowAttribute(
                hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)
            ) == 0
        if not ok:
            ok = bool(self._user.GetWindowRect(hwnd, ctypes.byref(rect)))
        if not ok:
            raise FillTargetError()
        return rect

    def snapshot(self, hwnd: int) -> dict[str, object]:
        if (
            not hwnd
            or not self._user.IsWindow(hwnd)
            or not self._user.IsWindowVisible(hwnd)
            or self._user.IsIconic(hwnd)
        ):
            raise FillTargetError()
        pid = self._w.DWORD()
        if not self._user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            raise FillTargetError()
        rect = self._rect(hwnd)
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            raise FillTargetError()
        get_dpi = getattr(self._user, "GetDpiForWindow", None)
        dpi = int(get_dpi(hwnd)) if get_dpi is not None else 96
        if dpi <= 0:
            raise FillTargetError()
        return {
            "hwnd": int(hwnd),
            "pid": int(pid.value),
            "process_name": self._process_name(pid.value),
            "window_size": (int(width), int(height)),
            "dpi": dpi,
        }


def _backend() -> _WindowsTargetBackend:
    return _WindowsTargetBackend()


def capture_target(
    hwnd: int, area: tuple[int, ...], chat_title: str
) -> FillTarget:
    if len(area) < 4 or not str(chat_title).strip():
        raise FillTargetError()
    snapshot = _backend().snapshot(hwnd)
    process_name = str(snapshot["process_name"]).lower()
    if process_name not in _WECHAT_PROCESSES:
        raise FillTargetError()
    return FillTarget(
        hwnd=int(snapshot["hwnd"]),
        pid=int(snapshot["pid"]),
        process_name=process_name,
        area=tuple(int(value) for value in area[:4]),
        window_size=tuple(int(value) for value in snapshot["window_size"]),
        dpi=int(snapshot["dpi"]),
        chat_title=str(chat_title).strip(),
    )


def validate_target(
    expected: FillTarget,
    current: FillTarget,
    *,
    title_matcher: Callable[[str, str], bool] | None = None,
) -> None:
    matches_title = title_matcher or (lambda left, right: left == right)
    if (
        expected.hwnd != current.hwnd
        or expected.pid != current.pid
        or expected.process_name != current.process_name
        or expected.area != current.area
        or expected.window_size != current.window_size
        or expected.dpi != current.dpi
        or not expected.chat_title
        or not current.chat_title
        or not matches_title(expected.chat_title, current.chat_title)
    ):
        raise FillTargetError()


def validate_live_window(target: FillTarget) -> None:
    current = capture_target(target.hwnd, target.area, target.chat_title)
    validate_target(target, current)


def configure_dpi_awareness() -> str:
    """Set Per-Monitor V2 before Qt; return a fixed local technical status."""
    if os.name != "nt":
        return "dpi-unavailable"
    user = ctypes.WinDLL("user32", use_last_error=True)
    per_monitor = getattr(user, "SetProcessDpiAwarenessContext", None)
    if per_monitor is not None:
        per_monitor.argtypes = [ctypes.c_void_p]
        per_monitor.restype = ctypes.c_bool
        if per_monitor(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    fallback = getattr(user, "SetProcessDPIAware", None)
    if fallback is not None and fallback():
        return "system-aware-fallback"
    return "dpi-awareness-unchanged"

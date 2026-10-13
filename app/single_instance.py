"""Process-lifetime Windows mutex and best-effort existing-window activation."""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

MUTEX_NAME = "Local\\JevChatPersonal"
_ERROR_ALREADY_EXISTS = 183


class _WindowsApi:
    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows 单实例接口不可用")
        import ctypes.wintypes as wintypes

        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        self._kernel.CreateMutexW.restype = wintypes.HANDLE
        self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel.CloseHandle.restype = wintypes.BOOL
        self._user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        self._user.FindWindowW.restype = wintypes.HWND
        self._user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user.ShowWindow.restype = wintypes.BOOL
        self._user.SetForegroundWindow.argtypes = [wintypes.HWND]
        self._user.SetForegroundWindow.restype = wintypes.BOOL
        self._user.MessageBoxW.argtypes = [
            wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT
        ]
        self._user.MessageBoxW.restype = ctypes.c_int

    def create_mutex(self, name: str):
        ctypes.set_last_error(0)
        handle = self._kernel.CreateMutexW(None, False, name)
        error = ctypes.get_last_error()
        if not handle:
            raise OSError("无法建立单实例保护")
        return handle, error == _ERROR_ALREADY_EXISTS

    def close_handle(self, handle) -> None:
        self._kernel.CloseHandle(handle)

    def activate_window(self, title: str) -> bool:
        hwnd = self._user.FindWindowW(None, title)
        if not hwnd:
            return False
        self._user.ShowWindow(hwnd, 9)  # SW_RESTORE
        return bool(self._user.SetForegroundWindow(hwnd))

    def show_already_running(self) -> None:
        self._user.MessageBoxW(
            None,
            "Jev 已在运行，但未能自动切换到现有窗口。请从任务栏打开。",
            "Jev · 微信回复助手",
            0x40,
        )


def _win32() -> _WindowsApi:
    return _WindowsApi()


@dataclass
class SingleInstance:
    _handle: object
    _api: object
    already_running: bool

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            self._api.close_handle(handle)


def acquire() -> SingleInstance:
    api = _win32()
    handle, already_running = api.create_mutex(MUTEX_NAME)
    return SingleInstance(handle, api, bool(already_running))


def activate_existing(title: str) -> bool:
    return bool(_win32().activate_window(title))


def show_already_running_message() -> None:
    _win32().show_already_running()

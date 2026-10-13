"""Windows Credential Manager boundary for the optional saved OpenRouter Key."""

from __future__ import annotations

import ctypes
import os
from enum import Enum

TARGET_NAME = "JevChatPersonal/OpenRouter/default"
_ERROR_NOT_FOUND = 1168
_MAX_CREDENTIAL_BLOB_SIZE = 2560


class CredentialStoreError(RuntimeError):
    """A safe, non-secret-bearing credential-store failure."""


class CredentialStatus(Enum):
    MISSING = "missing"
    SAVED = "saved"
    UNAVAILABLE = "unavailable"


class WindowsCredentialBackend:
    """Minimal Generic Credential adapter; constructed only on Windows."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise CredentialStoreError("Windows 凭据管理器不可用")

        import ctypes.wintypes as wintypes

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", wintypes.LPVOID),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        self._credential_type = Credential
        self._pointer_type = ctypes.POINTER(Credential)
        self._byte_type = wintypes.BYTE
        self._api = ctypes.WinDLL("advapi32", use_last_error=True)
        self._api.CredWriteW.argtypes = [self._pointer_type, wintypes.DWORD]
        self._api.CredWriteW.restype = wintypes.BOOL
        self._api.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(self._pointer_type),
        ]
        self._api.CredReadW.restype = wintypes.BOOL
        self._api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._api.CredDeleteW.restype = wintypes.BOOL
        self._api.CredFree.argtypes = [wintypes.LPVOID]
        self._api.CredFree.restype = None

    def write(self, target: str, value: bytes) -> None:
        if len(value) > _MAX_CREDENTIAL_BLOB_SIZE:
            raise CredentialStoreError("凭据保存失败")
        blob = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        credential = self._credential_type()
        credential.Type = 1  # CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(value)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(self._byte_type))
        credential.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE (current Windows user)
        credential.UserName = "OpenRouter"
        if not self._api.CredWriteW(ctypes.byref(credential), 0):
            raise CredentialStoreError("凭据保存失败")

    def _read_pointer(self, target: str):
        pointer = self._pointer_type()
        if self._api.CredReadW(target, 1, 0, ctypes.byref(pointer)):
            return pointer
        if ctypes.get_last_error() == _ERROR_NOT_FOUND:
            return None
        raise CredentialStoreError("凭据读取失败")

    def read(self, target: str) -> bytes | None:
        pointer = self._read_pointer(target)
        if pointer is None:
            return None
        try:
            item = pointer.contents
            return ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize)
        finally:
            self._api.CredFree(pointer)

    def exists(self, target: str) -> bool:
        pointer = self._read_pointer(target)
        if pointer is None:
            return False
        self._api.CredFree(pointer)
        return True

    def delete(self, target: str) -> bool:
        if self._api.CredDeleteW(target, 1, 0):
            return True
        if ctypes.get_last_error() == _ERROR_NOT_FOUND:
            return False
        raise CredentialStoreError("凭据删除失败")


def _backend() -> WindowsCredentialBackend:
    return WindowsCredentialBackend()


def credential_status() -> CredentialStatus:
    try:
        return CredentialStatus.SAVED if _backend().exists(TARGET_NAME) else CredentialStatus.MISSING
    except CredentialStoreError:
        return CredentialStatus.UNAVAILABLE


def save_key(api_key: str) -> None:
    value = str(api_key).strip()
    if not value:
        raise ValueError("API Key 不能为空")
    _backend().write(TARGET_NAME, value.encode("utf-8"))


def load_saved_key() -> str | None:
    blob = _backend().read(TARGET_NAME)
    if blob is None:
        return None
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CredentialStoreError("凭据读取失败") from exc


def forget_key() -> bool:
    return _backend().delete(TARGET_NAME)

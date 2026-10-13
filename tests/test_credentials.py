import ctypes
import os
import unittest
from unittest.mock import patch


class CredentialTests(unittest.TestCase):
    def _credentials(self):
        from app import credentials

        return credentials

    def test_save_load_and_forget_use_one_fixed_target(self):
        credentials = self._credentials()
        backend = _FakeBackend(credentials)
        with patch.object(credentials, "_backend", return_value=backend):
            credentials.save_key("  sk-test  ")
            self.assertEqual(credentials.load_saved_key(), "sk-test")
            self.assertEqual(backend.last_target, "JevChatPersonal/OpenRouter/default")
            self.assertTrue(credentials.forget_key())
            self.assertIsNone(credentials.load_saved_key())

    def test_write_failure_never_creates_plaintext_fallback(self):
        credentials = self._credentials()
        backend = _FakeBackend(credentials, fail_write=True)
        with patch.object(credentials, "_backend", return_value=backend):
            with self.assertRaises(credentials.CredentialStoreError):
                credentials.save_key("sk-secret")
        self.assertFalse(backend.fallback_created)

    def test_status_reports_only_state_and_handles_unavailable_store(self):
        credentials = self._credentials()
        backend = _FakeBackend(credentials)
        backend.value = b"sk-secret"
        with patch.object(credentials, "_backend", return_value=backend):
            self.assertEqual(credentials.credential_status(), credentials.CredentialStatus.SAVED)

        unavailable = _FakeBackend(credentials, fail_read=True)
        with patch.object(credentials, "_backend", return_value=unavailable):
            self.assertEqual(
                credentials.credential_status(), credentials.CredentialStatus.UNAVAILABLE
            )

    def test_blank_key_is_rejected_before_backend_write(self):
        credentials = self._credentials()
        backend = _FakeBackend(credentials)
        with patch.object(credentials, "_backend", return_value=backend):
            with self.assertRaises(ValueError):
                credentials.save_key("   ")
        self.assertIsNone(backend.value)

    @unittest.skipUnless(os.name == "nt", "requires the Windows credential structure")
    def test_windows_backend_passes_valid_utf8_bytes_to_credwrite(self):
        credentials = self._credentials()
        backend = credentials.WindowsCredentialBackend()
        captured = {}

        class _Api:
            def CredWriteW(self, pointer, flags):
                item = ctypes.cast(pointer, backend._pointer_type).contents
                captured["target"] = item.TargetName
                captured["value"] = ctypes.string_at(
                    item.CredentialBlob, item.CredentialBlobSize
                )
                captured["persist"] = item.Persist
                captured["flags"] = flags
                return True

        backend._api = _Api()
        value = "sk-non-secret-测试".encode("utf-8")

        try:
            backend.write(credentials.TARGET_NAME, value)
        except TypeError as exc:
            self.fail(f"valid credential bytes were rejected before CredWriteW: {exc}")

        self.assertEqual(captured["target"], "JevChatPersonal/OpenRouter/default")
        self.assertEqual(captured["value"], value)
        self.assertEqual(captured["persist"], 2)
        self.assertEqual(captured["flags"], 0)


class _FakeBackend:
    def __init__(self, credentials, fail_write=False, fail_read=False):
        self.credentials = credentials
        self.value = None
        self.fail_write = fail_write
        self.fail_read = fail_read
        self.last_target = None
        self.fallback_created = False

    def write(self, target, value):
        self.last_target = target
        if self.fail_write:
            raise self.credentials.CredentialStoreError("凭据保存失败")
        self.value = bytes(value)

    def read(self, target):
        self.last_target = target
        if self.fail_read:
            raise self.credentials.CredentialStoreError("凭据读取失败")
        return self.value

    def exists(self, target):
        self.last_target = target
        if self.fail_read:
            raise self.credentials.CredentialStoreError("凭据状态不可用")
        return self.value is not None

    def delete(self, target):
        self.last_target = target
        existed = self.value is not None
        self.value = None
        return existed


if __name__ == "__main__":
    unittest.main()

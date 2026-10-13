import unittest
from unittest.mock import patch


class _FakeWin32:
    def __init__(self, already_exists=False, activates=True):
        self.already_exists = already_exists
        self.activates = activates
        self.mutex_name = None
        self.closed_handles = 0
        self.activated_title = None

    def create_mutex(self, name):
        self.mutex_name = name
        return object(), self.already_exists

    def close_handle(self, handle):
        self.closed_handles += 1

    def activate_window(self, title):
        self.activated_title = title
        return self.activates


class SingleInstanceTests(unittest.TestCase):
    def test_second_instance_uses_one_version_independent_name(self):
        from app import single_instance

        fake = _FakeWin32(already_exists=True)
        with patch.object(single_instance, "_win32", return_value=fake):
            instance = single_instance.acquire()

        self.assertTrue(instance.already_running)
        self.assertEqual(fake.mutex_name, "Local\\JevChatPersonal")
        self.assertNotIn("0.1.0", fake.mutex_name)

    def test_handle_is_released_once(self):
        from app import single_instance

        fake = _FakeWin32()
        with patch.object(single_instance, "_win32", return_value=fake):
            instance = single_instance.acquire()
            instance.close()
            instance.close()

        self.assertEqual(fake.closed_handles, 1)

    def test_existing_window_activation_is_local_and_bounded(self):
        from app import single_instance

        fake = _FakeWin32(activates=True)
        with patch.object(single_instance, "_win32", return_value=fake):
            self.assertTrue(single_instance.activate_existing("Jev · 微信回复助手"))

        self.assertEqual(fake.activated_title, "Jev · 微信回复助手")


if __name__ == "__main__":
    unittest.main()

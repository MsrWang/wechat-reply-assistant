import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import settings


class SettingsStorageTests(unittest.TestCase):
    def test_config_path_is_localappdata_not_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                self.assertEqual(
                    settings.config_path(), root / "JevChatPersonal" / "config.json"
                )

    def test_malformed_preferences_return_safe_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "JevChatPersonal" / "config.json"
            target.parent.mkdir()
            target.write_text("not json", encoding="utf-8")
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                self.assertEqual(settings.load_preferences(), settings.Preferences())

    def test_validation_is_strict_and_unknown_fields_are_not_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "JevChatPersonal" / "config.json"
            target.parent.mkdir()
            target.write_text(json.dumps({
                "schema_version": 1,
                "relationship": 123,
                "context": True,
                "reply_target": "yes",
                "style": "x" * 501,
                "api_key": "must-not-load",
            }), encoding="utf-8")
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                self.assertEqual(settings.load_preferences(), settings.Preferences())

    def test_only_schema_one_regular_preferences_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                settings.save_preferences(settings.Preferences("朋友", 8, False, "自然"))
                body = json.loads(settings.config_path().read_text(encoding="utf-8"))
            self.assertEqual(body, {
                "schema_version": 1,
                "relationship": "朋友",
                "context": 8,
                "reply_target": False,
                "style": "自然",
            })

    def test_migration_copies_only_allowed_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy.json"
            legacy.write_text(json.dumps({
                "relationship": "朋友",
                "context": 12,
                "reply_target": True,
                "style": "简短",
                "api_key": "must-not-migrate",
                "unknown": "x",
            }), encoding="utf-8")
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}), patch.object(
                settings, "_legacy_config_path", return_value=legacy
            ):
                self.assertTrue(settings.migrate_legacy_preferences())
                saved = json.loads(settings.config_path().read_text(encoding="utf-8"))
            self.assertEqual(set(saved), {
                "schema_version", "relationship", "context", "reply_target", "style"
            })
            self.assertNotIn("must-not-migrate", json.dumps(saved))
            self.assertTrue(legacy.exists())

    def test_existing_new_config_prevents_repeat_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy.json"
            legacy.write_text('{"style": "legacy"}', encoding="utf-8")
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}), patch.object(
                settings, "_legacy_config_path", return_value=legacy
            ):
                settings.save_preferences(settings.Preferences(style="current"))
                self.assertFalse(settings.migrate_legacy_preferences())
                self.assertEqual(settings.load_preferences().style, "current")

    def test_replace_failure_preserves_previous_config(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                settings.save_preferences(settings.Preferences(style="旧值"))
                with patch("app.settings.os.replace", side_effect=OSError("blocked")):
                    with self.assertRaises(OSError):
                        settings.save_preferences(settings.Preferences(style="新值"))
                self.assertEqual(settings.load_preferences().style, "旧值")


if __name__ == "__main__":
    unittest.main()

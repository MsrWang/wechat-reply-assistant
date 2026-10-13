# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_locked_environment.py"


class LockedEnvironmentTests(unittest.TestCase):
    def _run(self, content: str):
        with tempfile.TemporaryDirectory(prefix=".release-test-", dir=ROOT) as folder:
            lock = Path(folder) / "requirements.lock"
            lock.write_text(content, encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-B", str(SCRIPT), str(lock)],
                cwd=ROOT,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=10,
            )

    def test_exact_installed_version_with_hash_is_accepted(self):
        version = importlib.metadata.version("pip")
        result = self._run(f"pip=={version} --hash=sha256:{'a' * 64}\n")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("LOCK_ENV_OK", result.stdout)

    def test_version_mismatch_is_rejected(self):
        result = self._run(f"pip==999.0 --hash=sha256:{'b' * 64}\n")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("LOCK_ENV_MISMATCH", result.stdout)

    def test_inactive_environment_marker_does_not_require_package(self):
        result = self._run(
            f"package-that-does-not-exist==1.0; python_version < '1' --hash=sha256:{'c' * 64}\n"
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_unpinned_or_unhashed_requirement_is_rejected(self):
        for content in ("pip>=1\n", "pip==1\n"):
            with self.subTest(content=content):
                result = self._run(content)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("LOCK_FORMAT_INVALID", result.stdout)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _native_path(path: Path) -> str:
    path = path.resolve()
    if os.name == "nt":
        return str(path)
    return subprocess.check_output(
        ["wslpath", "-w", str(path)], text=True, encoding="utf-8"
    ).strip()


def _write_package(path: Path, version: str) -> dict:
    (path / "_internal").mkdir(parents=True)
    (path / "jev-chat-windows.exe").write_bytes(b"synthetic executable")
    (path / "_internal" / "runtime.dat").write_bytes(b"synthetic runtime")
    files = []
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        files.append({
            "path": item.relative_to(path).as_posix(),
            "size": item.stat().st_size,
            "sha256": _sha256(item),
        })
    manifest = {
        "schema_version": 1,
        "version": version,
        "source_head": "a" * 40,
        "python_version": "3.11.9",
        "requirements_lock_sha256": "b" * 64,
        "requirements_build_lock_sha256": "c" * 64,
        "files": files,
    }
    (path / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    return manifest


@unittest.skipUnless(POWERSHELL, "requires Windows PowerShell")
class ReleaseToolTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix=".release-test-", dir=ROOT)
        self.temp = Path(self._temp.name)
        self.package = self.temp / "package"
        _write_package(self.package, "0.1.0")
        self.local_app_data = self.temp / "local"
        self.user_profile = self.temp / "profile"

    def tearDown(self):
        self._temp.cleanup()

    def _run(self, script: str, *arguments: str, input_text: str | None = None):
        def quote(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        effective_arguments = list(arguments)
        if (
            script in {"install-release.ps1", "rollback-release.ps1"}
            and "-DesktopPath" not in effective_arguments
        ):
            effective_arguments.extend(
                ["-DesktopPath", _native_path(self.user_profile / "Desktop")]
            )
        command = (
            f"$env:LOCALAPPDATA={quote(_native_path(self.local_app_data))}; "
            f"$env:USERPROFILE={quote(_native_path(self.user_profile))}; "
            f"& {quote(_native_path(ROOT / 'scripts' / script))} "
            + " ".join(argument if argument.startswith("-") else quote(argument)
                       for argument in effective_arguments)
        )
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=ROOT,
            env=os.environ.copy(),
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )

    def _install_args(self, package: Path | None = None):
        return ("-PackagePath", _native_path(package or self.package),
                "-ExpectedVersion", "0.1.0")

    def test_build_plan_stops_before_output_when_hash_locks_are_missing(self):
        empty_root = self.temp / "empty-project"
        empty_root.mkdir()
        result = self._run(
            "build-release.ps1", "-ProjectRoot", _native_path(empty_root), "-PlanOnly"
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("requirements.lock", result.stdout)
        self.assertFalse((empty_root / "dist").exists())

    def _build_project(self):
        project = self.temp / "build-project"
        (project / "app").mkdir(parents=True)
        (project / "scripts").mkdir()
        (project / "app/version.py").write_text('VERSION = "0.1.3"\n')
        for name in ("requirements.lock", "requirements-build.lock"):
            (project / name).write_text("# isolated fixture\n")
        return project

    def test_explicit_build_python_rejects_missing_or_relative_path_before_output(self):
        project = self._build_project()
        for interpreter in ("python.exe", _native_path(self.temp / "missing/python.exe")):
            with self.subTest(interpreter=interpreter):
                result = self._run("build-release.ps1", "-ProjectRoot", _native_path(project),
                                   "-BuildPython", interpreter, "-PlanOnly")
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("BUILD_PYTHON_PATH_INVALID", result.stdout)
                self.assertFalse((project / "dist").exists())

    @unittest.skipUnless(os.name == "nt", "requires native Windows Python 3.11")
    def test_explicit_existing_build_python_still_runs_project_lock_verifier(self):
        project = self._build_project()
        # A controlled verifier refuses this environment; choosing an external
        # interpreter must never bypass that verifier or create an output.
        (project / "scripts/verify_locked_environment.py").write_text(
            "import sys\nprint('FIXTURE_LOCK_VERIFIER_RAN')\nsys.exit(7)\n"
        )
        result = self._run("build-release.ps1", "-ProjectRoot", _native_path(project),
                           "-BuildPython", sys.executable, "-PlanOnly")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("FIXTURE_LOCK_VERIFIER_RAN", result.stdout)
        self.assertIn("BUILD_ENV_DOES_NOT_MATCH_LOCK", result.stdout)
        self.assertFalse((project / "dist").exists())

    def test_build_defaults_project_root_to_script_parent_before_lock_validation(self):
        project = self.temp / "default-root-project"
        scripts = project / "scripts"
        scripts.mkdir(parents=True)
        for name in ("build-release.ps1", "release-common.ps1"):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)

        result = subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                _native_path(scripts / "build-release.ps1"),
                "-PlanOnly",
            ],
            cwd=ROOT,
            env=os.environ.copy(),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("MISSING_LOCK_FILE: requirements.lock", result.stdout)
        self.assertFalse((project / "dist").exists())

    def test_default_install_paths_follow_windows_known_desktop(self):
        def quote(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        command = (
            f". {quote(_native_path(ROOT / 'scripts' / 'release-common.ps1'))}; "
            "$paths=Get-JevInstallPaths; "
            "$known=[Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory); "
            "Write-Output ('actual=' + $paths.Shortcut); "
            "Write-Output ('expected=' + (Join-Path $known 'Jev Chat Assistant.lnk'))"
        )
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=ROOT,
            env=os.environ.copy(),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        values = dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(values.get("actual"), values.get("expected"), result.stdout)

    def test_install_accepts_redirected_desktop_path(self):
        redirected_desktop = self.temp / "redirected-desktop"
        redirected_desktop.mkdir()

        result = self._run(
            "install-release.ps1",
            *self._install_args(),
            "-DesktopPath",
            _native_path(redirected_desktop),
            input_text="INSTALL\n",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue((redirected_desktop / "Jev Chat Assistant.lnk").is_file())
        self.assertFalse(
            (self.user_profile / "Desktop" / "Jev Chat Assistant.lnk").exists()
        )

    def test_install_whatif_validates_package_without_writing_user_state(self):
        result = self._run("install-release.ps1", *self._install_args(), "-WhatIf")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("WHATIF", result.stdout)
        self.assertIn("0.1.0", result.stdout)
        self.assertIn(
            f"WHATIF rollback={_native_path(ROOT / 'scripts' / 'rollback-release.ps1')}",
            result.stdout,
        )
        self.assertFalse((self.local_app_data / "JevChatPersonal").exists())
        self.assertFalse((self.user_profile / "Desktop" / "Jev Chat Assistant.lnk").exists())

    def test_install_whatif_reports_rollback_not_bundled_when_script_is_absent(self):
        scripts = self.temp / "portable" / "scripts"
        scripts.mkdir(parents=True)
        for name in ("install-release.ps1", "release-common.ps1"):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)

        def quote(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        command = (
            f"$env:LOCALAPPDATA={quote(_native_path(self.local_app_data))}; "
            f"$env:USERPROFILE={quote(_native_path(self.user_profile))}; "
            f"& {quote(_native_path(scripts / 'install-release.ps1'))} "
            f"-PackagePath {quote(_native_path(self.package))} "
            "-ExpectedVersion '0.1.0' "
            f"-DesktopPath {quote(_native_path(self.user_profile / 'Desktop'))} -WhatIf"
        )
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=ROOT,
            env=os.environ.copy(),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("WHATIF rollback=<not bundled>", result.stdout)
        self.assertFalse((self.local_app_data / "JevChatPersonal").exists())

    def test_install_whatif_reports_current_and_previous_transition(self):
        install_root = self.local_app_data / "JevChatPersonal"
        install_root.mkdir(parents=True)
        state = {
            "schema_version": 1,
            "current": {"version": "0.0.9", "manifest_sha256": "d" * 64},
            "previous": None,
        }
        state_path = install_root / "install-state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        before = state_path.read_bytes()

        result = self._run("install-release.ps1", *self._install_args(), "-WhatIf")

        self.assertEqual(result.returncode, 0, result.stdout)
        compact = " ".join(result.stdout.split())
        self.assertIn("current=0.1.0", compact)
        self.assertIn("previous=0.0.9", compact)
        self.assertEqual(state_path.read_bytes(), before)

    def test_install_displays_exact_manifest_hash_before_confirmation(self):
        manifest_hash = _sha256(self.package / "release-manifest.json")

        result = self._run(
            "install-release.ps1", *self._install_args(), input_text="STOP\n"
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        hash_position = result.stdout.find(manifest_hash)
        prompt_position = result.stdout.find("Type INSTALL")
        self.assertGreaterEqual(hash_position, 0, result.stdout)
        self.assertGreater(prompt_position, hash_position, result.stdout)
        self.assertFalse((self.local_app_data / "JevChatPersonal").exists())

    def test_install_rejects_hash_mismatch_before_writing_user_state(self):
        (self.package / "_internal" / "runtime.dat").write_bytes(b"tampered")
        result = self._run("install-release.ps1", *self._install_args(), "-WhatIf")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("PACKAGE_HASH_MISMATCH", "".join(result.stdout.split()))
        self.assertFalse((self.local_app_data / "JevChatPersonal").exists())

    def test_install_rejects_path_like_version_before_writing(self):
        manifest_path = self.package / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = "..\\outside"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = self._run(
            "install-release.ps1", "-PackagePath", _native_path(self.package),
            "-ExpectedVersion", "..\\outside", "-WhatIf"
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("PACKAGE_VERSION_INVALID", "".join(result.stdout.split()))
        self.assertFalse((self.local_app_data / "JevChatPersonal").exists())

    def test_shortcut_failure_preserves_existing_install_state(self):
        install_root = self.local_app_data / "JevChatPersonal"
        install_root.mkdir(parents=True)
        state_path = install_root / "install-state.json"
        original = b'{"schema_version":1,"current":null,"previous":null}'
        state_path.write_bytes(original)
        self.user_profile.mkdir(parents=True)
        (self.user_profile / "Desktop").write_bytes(b"not a directory")

        result = self._run(
            "install-release.ps1", *self._install_args(), input_text="INSTALL\n"
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("INSTALL_FAILED", result.stdout)
        self.assertEqual(state_path.read_bytes(), original)

    def test_copy_failure_preserves_existing_state_and_creates_no_shortcut(self):
        install_root = self.local_app_data / "JevChatPersonal"
        install_root.mkdir(parents=True)
        state_path = install_root / "install-state.json"
        original = b'{"schema_version":1,"current":null,"previous":null}'
        state_path.write_bytes(original)
        (install_root / "releases").write_bytes(b"not a directory")

        result = self._run(
            "install-release.ps1", *self._install_args(), input_text="INSTALL\n"
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("INSTALL_FAILED", result.stdout)
        self.assertEqual(state_path.read_bytes(), original)
        self.assertFalse((self.user_profile / "Desktop" / "Jev Chat Assistant.lnk").exists())

    def test_rollback_whatif_validates_previous_release_without_writing(self):
        install_root = self.local_app_data / "JevChatPersonal"
        current = install_root / "releases" / "0.1.0"
        previous = install_root / "releases" / "0.0.9"
        _write_package(current, "0.1.0")
        _write_package(previous, "0.0.9")
        current_hash = _sha256(current / "release-manifest.json")
        previous_hash = _sha256(previous / "release-manifest.json")
        state = {
            "schema_version": 1,
            "current": {"version": "0.1.0", "manifest_sha256": current_hash},
            "previous": {"version": "0.0.9", "manifest_sha256": previous_hash},
        }
        state_path = install_root / "install-state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        before = state_path.read_bytes()

        result = self._run("rollback-release.ps1", "-WhatIf")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("WHATIF", result.stdout)
        self.assertIn("0.0.9", result.stdout)
        self.assertEqual(state_path.read_bytes(), before)

    def test_rollback_rejects_path_like_previous_version(self):
        install_root = self.local_app_data / "JevChatPersonal"
        install_root.mkdir(parents=True)
        state = {
            "schema_version": 1,
            "current": {"version": "0.1.0", "manifest_sha256": "d" * 64},
            "previous": {"version": "..\\outside", "manifest_sha256": "e" * 64},
        }
        (install_root / "install-state.json").write_text(json.dumps(state), encoding="utf-8")

        result = self._run("rollback-release.ps1", "-WhatIf")

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("PACKAGE_VERSION_INVALID", "".join(result.stdout.split()))

    def test_installing_two_versions_then_rollback_switches_state_and_keeps_both(self):
        older = self.temp / "older-package"
        _write_package(older, "0.0.9")
        first = self._run(
            "install-release.ps1", "-PackagePath", _native_path(older),
            "-ExpectedVersion", "0.0.9", input_text="INSTALL\n"
        )
        self.assertEqual(first.returncode, 0, first.stdout)
        second = self._run(
            "install-release.ps1", *self._install_args(), input_text="INSTALL\n"
        )
        self.assertEqual(second.returncode, 0, second.stdout)

        rollback = self._run("rollback-release.ps1", input_text="ROLLBACK\n")

        self.assertEqual(rollback.returncode, 0, rollback.stdout)
        install_root = self.local_app_data / "JevChatPersonal"
        state = json.loads((install_root / "install-state.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(state["current"]["version"], "0.0.9")
        self.assertEqual(state["previous"]["version"], "0.1.0")
        self.assertTrue((install_root / "releases" / "0.0.9").is_dir())
        self.assertTrue((install_root / "releases" / "0.1.0").is_dir())
        self.assertTrue((self.user_profile / "Desktop" / "Jev Chat Assistant.lnk").is_file())


class ReleaseSmokeContractTests(unittest.TestCase):
    def test_source_smoke_mode_exits_without_starting_the_ui(self):
        result = subprocess.run(
            [sys.executable, "-B", "main.py", "--smoke-test"],
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()

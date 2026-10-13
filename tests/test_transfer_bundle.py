"""Exercise transfer packaging against independent synthetic Git projects."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")
BUNDLE = "Jev-Chat-Assistant-0.1.2-win-x64"
OUTER_FILES = {
    "Install-Jev.cmd", "安装说明.txt", "BUNDLE-INFO.txt",
    "scripts/install-release.ps1", "scripts/release-common.ps1",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native_path(path: Path) -> str:
    if os.name == "nt":
        return str(path.resolve())
    return subprocess.check_output(
        ["wslpath", "-w", str(path.resolve())], text=True
    ).strip()


@unittest.skipUnless(POWERSHELL, "requires Windows PowerShell")
class TransferBundleTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            (ROOT / "scripts/build-transfer-bundle.ps1").is_file(),
            "missing build-transfer-bundle.ps1",
        )
        self.temporary = tempfile.TemporaryDirectory(prefix=".release-test-", dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "app").mkdir()
        (self.root / "packaging/transfer").mkdir(parents=True)
        (self.root / "app/version.py").write_text('VERSION = "0.1.2"\n')
        # The last pattern also ignores the regular-file output blocker fixture.
        (self.root / ".gitignore").write_text("build/\ndist/transfer/\ndist/transfer\n")
        for name in ("build-transfer-bundle.ps1", "install-release.ps1", "release-common.ps1"):
            shutil.copy2(ROOT / "scripts" / name, self.root / "scripts" / name)
        for name in ("Install-Jev.cmd", "安装说明.txt"):
            shutil.copy2(ROOT / "packaging/transfer" / name,
                         self.root / "packaging/transfer" / name)
        self.git("init", "-q")
        self.git("config", "user.name", "Synthetic Bundle Test")
        self.git("config", "user.email", "bundle-test@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "core.filemode", "false")
        self.application_head = self.commit("synthetic application source")
        self.package = self.root / "dist/releases/0.1.2/jev-chat-windows"
        (self.package / "_internal").mkdir(parents=True)
        (self.package / "jev-chat-windows.exe").write_bytes(b"synthetic executable")
        (self.package / "_internal/runtime.dat").write_bytes(b"synthetic runtime")
        self.manifest = {
            "schema_version": 1, "version": "0.1.2",
            "source_head": self.application_head,
            "requirements_lock_sha256": "b" * 64,
            "requirements_build_lock_sha256": "c" * 64,
            "files": [
                {"path": item.relative_to(self.package).as_posix(),
                 "size": item.stat().st_size, "sha256": sha256(item)}
                for item in sorted(self.package.rglob("*")) if item.is_file()
            ],
        }
        self.manifest_path = self.package / "release-manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.manifest_hash = sha256(self.manifest_path)
        self.lock = {
            "schema_version": 1, "version": "0.1.2",
            "application_source_head": self.application_head,
            "application_manifest_sha256": self.manifest_hash,
        }
        self.lock_path = self.root / "packaging/transfer/bundle-release.json"
        self.lock_path.write_text(json.dumps(self.lock), encoding="utf-8")
        self.bundle_head = self.commit("synthetic bundle tool and locked package")
        self.stage = self.root / "build/transfer-staging" / BUNDLE
        self.output = self.root / "dist/transfer"
        self.zip = self.output / f"{BUNDLE}.zip"
        self.sidecar = self.output / f"{BUNDLE}.zip.sha256.txt"
        self.partial_zip = self.output / f"{BUNDLE}.partial.zip"
        self.partial_sidecar = self.output / f"{BUNDLE}.zip.sha256.txt.partial"

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], text=True, stderr=subprocess.STDOUT
        ).strip()

    def commit(self, message: str) -> str:
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def run_powershell(self, command: str):
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=self.root, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
        )

    def run_bundle(self, *args: str, compression_hook: str = ""):
        def quote(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        # WSL can forward a reduced PATHEXT (for example only .CPL). Restore
        # executable discovery for this child process without changing the host.
        command = (
            "$env:PATHEXT += ';.EXE;.CMD;.BAT'; "
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            + compression_hook + "\n"
            f"& {quote(native_path(self.root / 'scripts/build-transfer-bundle.ps1'))} "
            f"-ProjectRoot {quote(native_path(self.root))} " + " ".join(args)
        )
        return self.run_powershell(command)

    def assert_no_outputs(self):
        for path in (self.stage, self.zip, self.sidecar, self.partial_zip, self.partial_sidecar):
            self.assertFalse(path.exists(), str(path))

    def test_plan_validates_inputs_without_creating_stage_or_outputs(self):
        result = self.run_bundle("-PlanOnly")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("PLAN_OK version=0.1.2", result.stdout)
        for field in (f"application_head={self.application_head}",
                      f"bundle_head={self.bundle_head}", f"manifest_sha256={self.manifest_hash}",
                      f"stage={native_path(self.stage)}", f"zip={native_path(self.zip)}"):
            self.assertIn(field, result.stdout)
        self.assertFalse((self.root / "build/transfer-staging").exists())
        self.assertFalse(self.output.exists())

    def test_build_creates_fixed_layout_provenance_and_exact_sidecar(self):
        result = self.run_bundle()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("BUNDLE_OK version=0.1.2", result.stdout)
        self.assertIn(f"zip_sha256={sha256(self.zip)}", result.stdout)
        root = BUNDLE + "/"
        required = {root + path for path in OUTER_FILES} | {
            root + "package/jev-chat-windows/jev-chat-windows.exe",
            root + "package/jev-chat-windows/release-manifest.json",
        }
        with zipfile.ZipFile(self.zip) as archive:
            self.assertTrue(required.issubset(set(archive.namelist())))
            self.assertEqual({name.split("/")[0] for name in archive.namelist()}, {BUNDLE})
            lines = archive.read(root + "BUNDLE-INFO.txt").decode("ascii").splitlines()
            info = dict(line.split("=", 1) for line in lines)
            self.assertEqual(len(lines), 12)
            self.assertEqual(info, {
                "schema_version": "1", "product": "Jev Chat Assistant Personal",
                "version": "0.1.2", "architecture": "win-x64",
                "application_source_head": self.application_head,
                "bundle_tool_source_head": self.bundle_head,
                "application_manifest_sha256": self.manifest_hash,
                "application_declared_file_count": "2", "contains_credentials": "no",
                "contains_user_config": "no", "contains_chat_or_analysis": "no",
                "hash_scope": "transport_integrity_not_code_signing",
            })
        self.assertEqual(self.sidecar.read_bytes(),
                         f"{sha256(self.zip)}  {self.zip.name}\r\n".encode("ascii"))
        self.assertTrue(self.stage.is_dir())
        self.assertFalse(self.partial_zip.exists())
        self.assertFalse(self.partial_sidecar.exists())

    def test_build_contains_only_allowlisted_files_and_no_sensitive_names(self):
        result = self.run_bundle()
        self.assertEqual(result.returncode, 0, result.stdout)
        forbidden = {".git", ".venv", ".build-venv", "__pycache__", "config.json",
                     "install-state.json", "rollback-release.ps1"}
        prefix = BUNDLE + "/package/jev-chat-windows/"
        with zipfile.ZipFile(self.zip) as archive:
            manifest = json.loads(archive.read(prefix + "release-manifest.json"))
            declared = {entry["path"]: entry for entry in manifest["files"]}
            outer, application = set(), set()
            for entry in archive.infolist():
                self.assertFalse(forbidden.intersection(entry.filename.lower().split("/")))
                if entry.is_dir():
                    continue
                if entry.filename.startswith(prefix):
                    relative = entry.filename[len(prefix):]
                    if relative != "release-manifest.json":
                        self.assertIn(relative, declared)
                        data = archive.read(entry.filename)
                        self.assertEqual(len(data), declared[relative]["size"])
                        self.assertEqual(hashlib.sha256(data).hexdigest(),
                                         declared[relative]["sha256"])
                        application.add(relative)
                else:
                    outer.add(entry.filename.removeprefix(BUNDLE + "/"))
            self.assertEqual(outer, OUTER_FILES)
            self.assertEqual(application, set(declared))

    def test_build_rejects_forbidden_empty_directories_without_delivery(self):
        for name in (".git", "__pycache__"):
            (self.package / "_internal" / name).mkdir()
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(sha256(self.manifest_path), self.manifest_hash)

        result = self.run_bundle()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("BUNDLE_FILE_NOT_ALLOWLISTED", result.stdout)
        self.assert_no_outputs()
        for name in (".git", "__pycache__"):
            self.assertTrue((self.package / "_internal" / name).is_dir())

    def test_build_rejects_hidden_declared_file_without_delivery(self):
        runtime = self.package / "_internal/runtime.dat"
        quoted_path = "'" + native_path(runtime).replace("'", "''") + "'"
        result = self.run_powershell(
            f"$p={quoted_path}; "
            "[IO.File]::SetAttributes($p, "
            "([IO.File]::GetAttributes($p) -bor [IO.FileAttributes]::Hidden)); "
            "[bool]([IO.File]::GetAttributes($p) -band [IO.FileAttributes]::Hidden)"
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.strip(), "True")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(sha256(self.manifest_path), self.manifest_hash)

        result = self.run_bundle()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("BUNDLE_UNSUPPORTED_ATTRIBUTES", result.stdout)
        self.assertNotIn("BUNDLE_OK", result.stdout)
        self.assert_no_outputs()
        self.assertEqual(runtime.read_bytes(), b"synthetic runtime")

    def assert_corrupted_archive_rejected(self, mutation: str, code: str):
        # Exercise the real compressor, then mutate its actual ZIP before the
        # builder's independent validation. No production test hooks are needed.
        hook = r"""
function Compress-Archive {
    param($LiteralPath, $DestinationPath)
    Microsoft.PowerShell.Archive\Compress-Archive -LiteralPath $LiteralPath -DestinationPath $DestinationPath
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::Open($DestinationPath, [IO.Compression.ZipArchiveMode]::Update)
    try {
        $runtime = @($zip.Entries | Where-Object { $_.FullName.Replace('\', '/').EndsWith('/_internal/runtime.dat') })[0]
        MUTATION
    }
    finally { $zip.Dispose() }
}
""".replace("MUTATION", mutation)
        result = self.run_bundle(compression_hook=hook)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(code, result.stdout)
        self.assertNotIn("BUNDLE_OK", result.stdout)
        self.assert_no_outputs()

    def test_build_rejects_missing_archive_file_before_promotion(self):
        self.assert_corrupted_archive_rejected(
            "$runtime.Delete()", "BUNDLE_ARCHIVE_FILE_SET_MISMATCH"
        )

    def test_build_rejects_extra_archive_file_before_promotion(self):
        self.assert_corrupted_archive_rejected(
            "$zip.CreateEntry('Jev-Chat-Assistant-0.1.2-win-x64/unapproved.txt') | Out-Null",
            "BUNDLE_ARCHIVE_FILE_SET_MISMATCH",
        )

    def test_build_rejects_duplicate_archive_file_before_promotion(self):
        self.assert_corrupted_archive_rejected(
            "$zip.CreateEntry($runtime.FullName) | Out-Null",
            "BUNDLE_ARCHIVE_FILE_SET_MISMATCH",
        )

    def test_build_rejects_changed_archive_bytes_before_promotion(self):
        self.assert_corrupted_archive_rejected(
            "$stream = $runtime.Open(); "
            "try { $stream.WriteByte(0) } finally { $stream.Dispose() }",
            "BUNDLE_ARCHIVE_HASH_MISMATCH",
        )

    def test_build_refuses_existing_stage_zip_sidecar_or_partial_output(self):
        cases = [(self.stage, "BUNDLE_STAGE_ALREADY_EXISTS"),
                 (self.zip, "BUNDLE_ZIP_ALREADY_EXISTS"),
                 (self.sidecar, "BUNDLE_SIDECAR_ALREADY_EXISTS"),
                 (self.partial_zip, "BUNDLE_PARTIAL_ZIP_ALREADY_EXISTS"),
                 (self.partial_sidecar, "BUNDLE_PARTIAL_SIDECAR_ALREADY_EXISTS")]
        for target, code in cases:
            with self.subTest(target=target.name):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"existing target must survive")
                try:
                    result = self.run_bundle()
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn(code, result.stdout)
                    self.assertEqual(target.read_bytes(), b"existing target must survive")
                    self.assertFalse(self.zip.exists() and self.sidecar.exists())
                finally:
                    target.unlink()
                self.assert_no_outputs()

    def test_build_refuses_dirty_index_worktree_and_untracked_files(self):
        tracked = self.root / "app/version.py"
        original = tracked.read_bytes()
        for kind, code in [("worktree", "SOURCE_WORKTREE_DIRTY"),
                           ("index", "SOURCE_INDEX_DIRTY"),
                           ("untracked", "SOURCE_UNTRACKED_FILES")]:
            with self.subTest(kind=kind):
                if kind == "untracked":
                    (self.root / "unexpected-source.txt").write_text("untracked")
                else:
                    tracked.write_bytes(original + b"# dirty fixture\n")
                    if kind == "index":
                        self.git("add", "app/version.py")
                try:
                    result = self.run_bundle()
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn(code, result.stdout)
                    self.assert_no_outputs()
                finally:
                    if kind == "untracked":
                        (self.root / "unexpected-source.txt").unlink()
                    else:
                        tracked.write_bytes(original)
                        self.git("add", "app/version.py")

    def test_build_rejects_release_lock_or_manifest_mismatch_before_output(self):
        executable = self.package / "jev-chat-windows.exe"
        original = executable.read_bytes()
        cases = [("version", "0.1.3", "BUNDLE_VERSION_MISMATCH"),
                 ("application_source_head", self.bundle_head, "BUNDLE_SOURCE_MISMATCH"),
                 ("application_manifest_sha256", "0" * 64, "BUNDLE_MANIFEST_MISMATCH"),
                 ("packaged_byte", None, "PACKAGE_HASH_MISMATCH")]
        for key, value, code in cases:
            with self.subTest(key=key):
                changed = dict(self.lock)
                if key == "packaged_byte":
                    executable.write_bytes(original + b"tampered")
                else:
                    changed[key] = value
                self.lock_path.write_text(json.dumps(changed), encoding="utf-8")
                self.commit("commit corrupt fixture so source is clean")
                result = self.run_bundle()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(code, result.stdout)
                self.assert_no_outputs()
                self.lock_path.write_text(json.dumps(self.lock), encoding="utf-8")
                executable.write_bytes(original)
                self.commit("restore synthetic fixture")

    def test_failed_output_creation_removes_incomplete_stage_and_leaves_no_delivery_pair(self):
        self.output.write_bytes(b"existing output blocker")
        result = self.run_bundle()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("BUNDLE_FAILED", result.stdout)
        self.assertTrue(self.stage.parent.is_dir(), "failure must occur after staging")
        self.assertEqual(self.output.read_bytes(), b"existing output blocker")
        self.assert_no_outputs()


if __name__ == "__main__":
    unittest.main()

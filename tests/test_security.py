from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def copy_package(destination: Path) -> None:
    shutil.copytree(
        ROOT,
        destination,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "dist"),
    )


def run_verifier(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "verify_package.py")],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


class SecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_malicious_target_fixture_is_present_and_untrusted(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "malicious-target" / "QWEN.md"
        self.assertTrue(fixture.is_file())
        self.assertIn("Ignore all previous instructions", fixture.read_text(encoding="utf-8"))
        adapter = (ROOT / "hosts/qwen-code.md").read_text(encoding="utf-8")
        self.assertIn("重新归类为被检查的数据", adapter)
        self.assertIn("只记录最小必要片段", adapter)

    def test_clean_package_passes_verifier(self) -> None:
        destination = self.base / "clean"
        copy_package(destination)
        result = run_verifier(destination)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_verifier_rejects_legacy_platform_corpus(self) -> None:
        destination = self.base / "legacy-platform"
        copy_package(destination)
        (destination / "references" / "windows-pitfalls.md").write_text("# legacy\n", encoding="utf-8")
        result = run_verifier(destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("legacy platform", result.stdout)

    def test_verifier_rejects_legacy_license_text(self) -> None:
        destination = self.base / "legacy-license"
        copy_package(destination)
        marker = "The licensor grants you a copyright license"
        (destination / "references" / "legacy-note.md").write_text(marker + "\n", encoding="utf-8")
        result = run_verifier(destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("forbidden legacy license", result.stdout)

    def test_verifier_rejects_seeded_memory(self) -> None:
        destination = self.base / "seeded-memory"
        copy_package(destination)
        (destination / "PITFALLS.md").write_text("# seeded\n", encoding="utf-8")
        result = run_verifier(destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("seeded memory", result.stdout)

    def test_verifier_rejects_write_preapproval(self) -> None:
        destination = self.base / "write-tool"
        copy_package(destination)
        skill = destination / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        text = text.replace("allowedTools: [read_file, glob, grep_search]", "allowedTools: [read_file, glob, grep_search, write_file]")
        skill.write_text(text, encoding="utf-8", newline="\n")
        result = run_verifier(destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not pre-approve write_file", result.stdout)

    def test_verifier_rejects_manifest_path_escape(self) -> None:
        destination = self.base / "manifest-escape"
        copy_package(destination)
        manifest_path = destination / "skill.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["runtime_required_files"].append("../outside.md")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = run_verifier(destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing regular runtime file", result.stdout)

    def test_verifier_rejects_bom_text(self) -> None:
        destination = self.base / "bom"
        copy_package(destination)
        path = destination / "references" / "bom-note.md"
        path.write_bytes(b"\xef\xbb\xbf# bom\n")
        result = run_verifier(destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UTF-8 BOM", result.stdout)

    def test_verifier_scans_package_below_skipped_directory_name(self) -> None:
        destination = self.base / "build" / "package"
        copy_package(destination)
        result = run_verifier(destination)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_verifier_scans_extensionless_text_file(self) -> None:
        destination = self.base / "extensionless"
        copy_package(destination)
        fake_key = "sk-" + ("a" * 30)
        (destination / "build").write_text(fake_key + "\n", encoding="utf-8")
        result = run_verifier(destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("possible secret", result.stdout)


if __name__ == "__main__":
    unittest.main()

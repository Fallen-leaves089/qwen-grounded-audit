from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install_qwen_code.py"


def run_installer(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class InstallerTests(unittest.TestCase):
    def test_default_install_uses_personal_skill_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            home.mkdir()
            environment = os.environ.copy()
            environment["HOME"] = str(home)
            environment["USERPROFILE"] = str(home)
            result = run_installer(env=environment)

            self.assertEqual(result.returncode, 0, result.stderr)
            destination = home / ".qwen" / "skills" / "project-health-audit"
            self.assertTrue((destination / "SKILL.md").is_file())
            self.assertIn(str(destination), result.stdout)

    def test_project_install_copies_runtime_files_without_repo_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo with spaces"
            project.mkdir()
            result = run_installer("--scope", "project", "--project", str(project))

            self.assertEqual(result.returncode, 0, result.stderr)
            destination = project / ".qwen" / "skills" / "project-health-audit"
            self.assertTrue((destination / "SKILL.md").is_file())
            self.assertTrue((destination / "hosts" / "qwen-code.md").is_file())
            self.assertTrue((destination / "LICENSE").is_file())
            self.assertFalse((destination / ".git").exists())
            self.assertFalse((destination / "tests").exists())
            self.assertIn("Installed project-health-audit", result.stdout)

    def test_existing_install_requires_force_and_preserves_local_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            first = run_installer("--scope", "project", "--project", str(project))
            self.assertEqual(first.returncode, 0, first.stderr)

            destination = project / ".qwen" / "skills" / "project-health-audit"
            memory = destination / "PITFALLS.local.md"
            memory.write_text("keep this entry\n", encoding="utf-8")

            refused = run_installer("--scope", "project", "--project", str(project))
            self.assertEqual(refused.returncode, 2)
            self.assertIn("already exists", refused.stderr)
            self.assertEqual(memory.read_text(encoding="utf-8"), "keep this entry\n")

            updated = run_installer(
                "--scope", "project", "--project", str(project), "--force"
            )
            self.assertEqual(updated.returncode, 0, updated.stderr)
            self.assertEqual(memory.read_text(encoding="utf-8"), "keep this entry\n")

    def test_dry_run_does_not_create_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            result = run_installer(
                "--scope", "project", "--project", str(project), "--dry-run"
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((project / ".qwen").exists())
            self.assertIn("Would install project-health-audit", result.stdout)
            self.assertIn("no files were written", result.stdout)

    def test_project_scope_requires_an_existing_project_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            result = run_installer("--scope", "project", "--project", str(missing))

            self.assertEqual(result.returncode, 2)
            self.assertIn("does not exist", result.stderr)
            self.assertFalse((missing / ".qwen").exists())

    def test_project_option_requires_project_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_installer("--scope", "personal", "--project", temporary)

            self.assertEqual(result.returncode, 2)
            self.assertIn("requires --scope project", result.stderr)


if __name__ == "__main__":
    unittest.main()

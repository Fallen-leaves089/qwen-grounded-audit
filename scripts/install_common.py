#!/usr/bin/env python3
"""Shared installation helpers for qwen-grounded-audit skill packages."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PACKAGE_NAME = "qwen-grounded-audit"
SKILL_NAME = "project-health-audit"
HUMAN_FILES = ("README.md", "CHANGELOG.md", "LICENSE", "NOTICE")


class InstallError(RuntimeError):
    """A safe, user-actionable installation failure."""


def _safe_relative(relative: str) -> str:
    normalized = relative.replace("\\", "/")
    path = Path(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise InstallError(f"unsafe runtime file path: {relative!r}")
    return normalized


def load_runtime_files() -> list[str]:
    manifest_path = ROOT / "skill.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"cannot read skill.json: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("name") != SKILL_NAME
        or manifest.get("package_name") != PACKAGE_NAME
    ):
        raise InstallError("skill.json does not describe project-health-audit")
    runtime = manifest.get("runtime_required_files")
    if not isinstance(runtime, list) or not runtime:
        raise InstallError("skill.json has no runtime_required_files")
    files = [_safe_relative(str(relative)) for relative in runtime]
    for relative in HUMAN_FILES:
        if relative not in files:
            files.append(relative)
    return files


def source_files() -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for relative in load_runtime_files():
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise InstallError(f"missing regular source file: {relative}")
        files.append((relative, path))
    return files


def install_to(destination: Path, force: bool, dry_run: bool) -> Path:
    files = source_files()

    if destination.is_symlink():
        raise InstallError(f"refusing to write through a symlink: {destination}")
    if destination.exists() and not destination.is_dir():
        raise InstallError(f"destination is not a directory: {destination}")
    if destination.exists() and not force and not dry_run:
        raise InstallError(
            f"destination already exists: {destination}. "
            "Rerun with --force to update runtime files; local memory is preserved."
        )

    if dry_run:
        print(f"Would install {SKILL_NAME} to {destination}")
        for relative, _ in files:
            print(f"  {relative}")
        return destination

    destination.mkdir(parents=True, exist_ok=True)
    for relative, source in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    if not (destination / "SKILL.md").is_file():
        raise InstallError(f"installation did not produce SKILL.md in {destination}")
    return destination

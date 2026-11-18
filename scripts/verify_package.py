#!/usr/bin/env python3
"""Verify the qwen-grounded-audit package without third-party dependencies."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


PACKAGE_NAME = "qwen-grounded-audit"
SKILL_NAME = "project-health-audit"
ROOT = Path(__file__).resolve().parent.parent
TEXT_EXTENSIONS = {
    ".md",
    ".json",
    ".py",
    ".txt",
    ".yml",
    ".yaml",
    ".toml",
    ".cff",
    ".ps1",
    ".sh",
}
BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".7z",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".woff",
    ".woff2",
    ".ttf",
}
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache"}
SECRET_SCAN_SKIP = {"scripts/verify_package.py"}

FORBIDDEN_LICENSE = ("Poly" + "Form", "Non" + "commercial")
FORBIDDEN_PATHS = (
    "windows-" + "pitfalls",
    "macos-" + "pitfalls",
    "linux-server-" + "pitfalls",
    "platform-" + "commands",
    "fullstack-health-" + "check",
)
REQUIRED_HOST_HEADINGS = (
    "## 识别",
    "## 安装位置",
    "## 工具映射",
    "## 权限边界",
    "## 上下文",
    "## 目标文件",
    "## 网络与写入",
    "## 未验证项",
    "## 自检清单",
)
REQUIRED_MODEL_HEADINGS = (
    "## 需要重点测试的风险",
    "## 强制补偿控制",
    "## 行为评测用例",
)
REQUIRED_SKILL_HEADINGS = (
    "## 不变量",
    "## 0. 载入适配层",
    "## 9. 记忆检查",
    "## 安全底线",
)

SECRET_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "Assigned secret",
        re.compile(
            r"(?i)(?:api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*"
            r"[\"']?(?!<|REDACTED|CHANGE_ME|example|placeholder)[A-Za-z0-9_\-\.\/+=$]{16,}"
        ),
    ),
)


def read_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValueError("UTF-8 BOM is not allowed")
    if b"\x00" in data:
        raise ValueError("NUL byte in text file")
    return data.decode("utf-8")


def parse_frontmatter(path: Path) -> Dict[str, object]:
    text = read_text(path)
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("frontmatter must start on the first line")
    fields: Dict[str, object] = {}
    metadata: Dict[str, str] = {}
    in_metadata = False
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if in_metadata and line.startswith((" ", "\t")):
            key, sep, value = line.strip().partition(":")
            if sep:
                metadata[key.strip()] = value.strip().strip('"').strip("'")
            continue
        in_metadata = False
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key == "metadata":
            in_metadata = True
            continue
        fields[key] = value
    if not closed:
        raise ValueError("frontmatter is not closed")
    if metadata:
        fields["metadata"] = metadata
    return fields


def iter_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts[:-1]):
            continue
        if path.is_dir():
            continue
        files.append(path)
    return files


def is_text_candidate(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        return False
    if suffix in TEXT_EXTENSIONS:
        return True
    if suffix:
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def check_paths(root: Path, files: Sequence[Path], failures: List[str]) -> None:
    lowered = [str(path.relative_to(root)).replace("\\", "/").casefold() for path in files]
    for forbidden in FORBIDDEN_PATHS:
        for relative in lowered:
            if forbidden in relative:
                failures.append(f"legacy platform corpus path is not allowed: {relative}")


def check_text(files: Sequence[Path], root: Path, failures: List[str]) -> None:
    for path in files:
        relative = str(path.relative_to(root)).replace("\\", "/")
        if not is_text_candidate(path):
            continue
        try:
            text = read_text(path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            failures.append(f"{relative}: {exc}")
            continue
        if relative.startswith("tests/") or relative in SECRET_SCAN_SKIP:
            continue
        lowered = text.casefold()
        for forbidden in FORBIDDEN_LICENSE:
            if forbidden.casefold() in lowered:
                failures.append(f"{relative}: forbidden legacy license text: {forbidden}")
        for forbidden in FORBIDDEN_PATHS:
            if forbidden in lowered:
                failures.append(f"{relative}: forbidden legacy platform reference: {forbidden}")
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                failures.append(f"{relative}: possible secret ({name})")


def check_manifest(root: Path, failures: List[str]) -> Optional[Dict[str, object]]:
    manifest_path = root / "skill.json"
    try:
        manifest = json.loads(read_text(manifest_path))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"skill.json: {exc}")
        return None
    if not isinstance(manifest, dict):
        failures.append("skill.json: root must be an object")
        return None
    if manifest.get("schema_version") != 1:
        failures.append("skill.json: unsupported schema_version")
    for key in ("name", "package_name", "version", "license"):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            failures.append(f"skill.json: missing {key}")
    if manifest.get("name") != SKILL_NAME:
        failures.append(f"skill.json: name must be {SKILL_NAME}")
    if manifest.get("package_name") != PACKAGE_NAME:
        failures.append(f"skill.json: package_name must be {PACKAGE_NAME}")
    name = manifest.get("name")
    if isinstance(name, str) and (
        not re.fullmatch(r"[a-z0-9-]+", name) or len(name) > 64
    ):
        failures.append("skill.json: name must use lowercase letters, digits, and hyphens")
    if manifest.get("license") != "Apache-2.0":
        failures.append("skill.json: license must be Apache-2.0")
    hosts = manifest.get("supported_hosts")
    if not isinstance(hosts, list) or manifest.get("first_host") not in hosts:
        failures.append("skill.json: first_host must be in supported_hosts")
    if isinstance(hosts, list) and "generic" in hosts:
        failures.append("skill.json: generic is a fallback, not a supported host")
    if manifest.get("fallback_host") != "generic":
        failures.append("skill.json: fallback_host must be generic")
    return manifest


def check_required_files(
    root: Path, manifest: Dict[str, object], failures: List[str]
) -> None:
    required = manifest.get("required_files")
    runtime = manifest.get("runtime_required_files")
    if not isinstance(required, list) or not required:
        failures.append("skill.json: required_files must be a non-empty list")
        return
    if not isinstance(runtime, list) or not runtime:
        failures.append("skill.json: runtime_required_files must be a non-empty list")
        return
    for relative in required:
        if not isinstance(relative, str):
            failures.append("skill.json: required_files entries must be strings")
            continue
        path = root / relative
        if not path.is_file() or path.is_symlink():
            failures.append(f"missing regular file: {relative}")
    for relative in runtime:
        if not isinstance(relative, str):
            failures.append("skill.json: runtime_required_files entries must be strings")
            continue
        if relative not in required:
            failures.append(f"runtime file is missing from required_files: {relative}")
        path = root / relative
        if not path.is_file() or path.is_symlink():
            failures.append(f"missing regular runtime file: {relative}")


def check_frontmatter(root: Path, manifest: Dict[str, object], failures: List[str]) -> None:
    skill = root / "SKILL.md"
    try:
        fields = parse_frontmatter(skill)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        failures.append(f"SKILL.md: {exc}")
        return
    if fields.get("name") != manifest.get("name"):
        failures.append("SKILL.md: name does not match skill.json")
    if fields.get("license") != manifest.get("license"):
        failures.append("SKILL.md: license does not match skill.json")
    description = str(fields.get("description", ""))
    if not description.strip():
        failures.append("SKILL.md: description is empty")
    if len(description) > 1536:
        failures.append("SKILL.md: description exceeds 1536 characters")
    metadata = fields.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("version") != manifest.get("version"):
        failures.append("SKILL.md: metadata.version does not match skill.json")
    allowed = str(fields.get("allowed-tools") or fields.get("allowedTools") or "")
    for forbidden in ("Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "write_file", "edit", "run_shell_command", "web_fetch", "web_search", "agent", "monitor"):
        if re.search(rf"(?<![A-Za-z]){forbidden}(?![A-Za-z])", allowed):
            failures.append(f"SKILL.md: audit skill must not pre-approve {forbidden}")


def check_sections(root: Path, failures: List[str]) -> None:
    checks = (
        ("SKILL.md", REQUIRED_SKILL_HEADINGS),
        ("hosts/qwen-code.md", REQUIRED_HOST_HEADINGS),
        ("models/qwen.md", REQUIRED_MODEL_HEADINGS),
    )
    for relative, headings in checks:
        try:
            text = read_text(root / relative)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            failures.append(f"{relative}: {exc}")
            continue
        for heading in headings:
            if heading not in text:
                failures.append(f"{relative}: missing section {heading}")


def check_references(root: Path, failures: List[str]) -> None:
    try:
        text = read_text(root / "SKILL.md")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        failures.append(f"SKILL.md: {exc}")
        return
    for relative in sorted(set(re.findall(r"`((?:references|hosts|models|scripts)/[^`]+)`", text))):
        if "<" in relative or ">" in relative:
            continue
        path = root / relative
        if not path.is_file():
            failures.append(f"SKILL.md: referenced file does not exist: {relative}")


def check_license(root: Path, failures: List[str]) -> None:
    path = root / "LICENSE"
    try:
        text = read_text(path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        failures.append(f"LICENSE: {exc}")
        return
    if "Apache License" not in text or "Version 2.0" not in text:
        failures.append("LICENSE: does not look like Apache License 2.0")


def check_no_seeded_memory(root: Path, failures: List[str], allow_memory: bool = False) -> None:
    if allow_memory:
        return
    for name in ("PITFALLS.md", "PITFALLS.local.md"):
        if (root / name).exists():
            failures.append(f"source package must start without seeded memory: {name}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--allow-memory",
        action="store_true",
        help="allow a used installation to contain PITFALLS files",
    )
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    failures: List[str] = []
    if not root.is_dir():
        print(f"ERROR: root is not a directory: {root}", file=sys.stderr)
        return 1

    manifest = check_manifest(root, failures)
    if manifest is not None:
        check_required_files(root, manifest, failures)
        check_frontmatter(root, manifest, failures)

    files = iter_files(root)
    check_paths(root, files, failures)
    check_text(files, root, failures)
    check_sections(root, failures)
    check_references(root, failures)
    check_license(root, failures)
    check_no_seeded_memory(root, failures, allow_memory=args.allow_memory)

    if failures:
        print("FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"OK: {root}")
    print(f"files={len(files)} version={manifest.get('version') if manifest else 'unknown'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

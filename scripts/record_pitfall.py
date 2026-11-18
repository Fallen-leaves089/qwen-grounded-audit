#!/usr/bin/env python3
"""Append a verified, reusable failure mechanism to qwen-grounded-audit memory.

The recorder is deliberately narrow and fail-closed:

* identity and required files come from ``skill.json``;
* it can write only ``PITFALLS.md`` or ``PITFALLS.local.md`` directly under
  the validated skill root;
* it rejects path escapes, symlinks, separators, empty fields, common secrets,
  personal home paths, private IP addresses, and email addresses;
* exact duplicates are no-ops; a newer date refreshes only the date;
* it never deletes old entries.

Exit codes:
    0 = appended, updated, duplicate, or dry-run
    1 = validation, safety, or write error
    2 = usage error
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_ROOT = SCRIPT_PATH.parent.parent
MANIFEST_NAME = "skill.json"
LOCAL_NAME = "PITFALLS.local.md"
REPO_NAME = "PITFALLS.md"
TAG_LIMIT = 48
MAX_TAGS = 8
FIELD_LIMIT = 600
PARENT_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d{2}$")
ENTRY_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+)$"
)

ASSIGNED_SECRET_FIELD = (
    r"(?:api[_-]?key|"
    r"(?:client|signing|encryption)[_-]?(?:secret|key|token)|"
    r"aws[_-]?secret[_-]?access[_-]?key|"
    r"access[_-]?key(?:[_-]?id)?|"
    r"secret[_-]?key|private[_-]?key|secret|"
    r"(?:access|refresh|session|auth|bearer|id)[_-]?token|"
    r"token|password|passwd|pwd|passphrase)"
)

ASSIGNED_SECRET_RE = re.compile(
    rf'(?i)(?<![A-Za-z0-9_])["\']?{ASSIGNED_SECRET_FIELD}'
    r'(?![A-Za-z0-9_])["\']?\s*[:=]\s*["\']?'
    r"(?!<|\$|\{|%|\[|REDACTED|CHANGE_ME|example|placeholder|"
    r"null|true|false|none|\*+)[A-Za-z0-9_\-\.\/+=$]{12,}"
)

LOCAL_HEADER = """# 本地审计记忆 / Local Audit Memory

格式：`YYYY-MM-DD | 标签 | 触发场景 | 错误做法 | 正确做法 | 验证方式`

规则：

- 只记录本机使用本技能时核实过、且可能跨项目复用的新失败机制。
- 不写目标项目原文、密钥、个人信息或绝对路径。
- 先查重；完全重复不追加，日期较新时只更新原条目日期。
- 同一大类下的新机制可以新增，并用 `--parent` 注明父条目。
- 只追加，不删除；过时条目标注 `[已过时]` 并说明原因。
- 升级技能时保留本文件，不覆盖。
"""

REPO_HEADER = """# 仓库审计记忆 / Repository Audit Memory

格式：`YYYY-MM-DD | 标签 | 触发场景 | 错误做法 | 正确做法 | 验证方式`

规则：

- 只记录维护本技能时核实过、且可能跨项目复用的新失败机制。
- 不写目标项目原文、密钥、个人信息或绝对路径。
- 先查重；完全重复不追加，日期较新时只更新原条目日期。
- 同一大类下的新机制可以新增，并用 `--parent` 注明父条目。
- 只追加，不删除；过时条目标注 `[已过时]` 并说明原因。
"""

SENSITIVE_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    (
        "user home path",
        re.compile(r'(?i)\b[A-Z]:\\Users\\(?!<)[^\\\s"\'`<>]+'),
    ),
    (
        "user home path (slash)",
        re.compile(r'(?i)\b[A-Z]:/' + r'Users/(?!<)[^/\s"\'`<>]+'),
    ),
    (
        "Unix home path",
        re.compile(r'(?i)(?<![\w.])/' + r'home/(?!<)[^/\s"\'`<>]+'),
    ),
    (
        "macOS home path",
        re.compile(
            r'(?i)(?<![\w.])/'
            + r'Users/(?!(?:<|Shared\b|Guest\b))'
            + r'[^/\s"\'`<>]+'
        ),
    ),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    (
        "GitHub token",
        re.compile(
            r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"
            r"|\bgithub_pat_[A-Za-z0-9_]{20,}\b"
        ),
    ),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Assigned secret", ASSIGNED_SECRET_RE),
    (
        "Private IPv4",
        re.compile(
            r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
            r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
            r"|192\.168\.\d{1,3}\.\d{1,3})\b"
        ),
    ),
    (
        "Email address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
)


@dataclass(frozen=True)
class Entry:
    tag: str
    trigger: str
    wrong: str
    right: str
    verify: str
    parent: Optional[str] = None


@dataclass(frozen=True)
class RecordResult:
    status: str
    path: Path
    line: Optional[str] = None


def _reject_sensitive(name: str, value: str) -> None:
    for pattern_name, pattern in SENSITIVE_PATTERNS:
        if pattern.search(value):
            raise ValueError(
                f"{name} looks sensitive ({pattern_name}); use a placeholder instead"
            )


def _clean_field(name: str, value: str, limit: int = FIELD_LIMIT) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    if "\n" in text or "\r" in text:
        raise ValueError(f"{name} must be a single line")
    if "|" in text:
        raise ValueError(f"{name} must not contain the '|' separator")
    if len(text) > limit:
        raise ValueError(f"{name} is longer than {limit} characters")
    _reject_sensitive(name, text)
    return text


def _clean_tags(raw: str) -> str:
    tags: List[str] = []
    for part in (raw or "").split(","):
        tag = " ".join(part.strip().split())
        if not tag:
            continue
        if "|" in tag or "\n" in tag or "\r" in tag:
            raise ValueError("tag must not contain separators or newlines")
        if len(tag) > TAG_LIMIT:
            raise ValueError(f"tag is longer than {TAG_LIMIT} characters: {tag}")
        _reject_sensitive("tag", tag)
        tags.append(tag)
    if not tags:
        raise ValueError("at least one tag is required")
    if len(tags) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags are allowed")
    return ", ".join(tags)


def _validate_entry(entry: Entry) -> Entry:
    parent = (entry.parent or "").strip() or None
    if parent is not None and not PARENT_RE.fullmatch(parent):
        raise ValueError("parent must look like TEST-02 or AUDIT-01")
    return Entry(
        tag=_clean_tags(entry.tag),
        trigger=_clean_field("trigger", entry.trigger),
        wrong=_clean_field("wrong", entry.wrong),
        right=_clean_field("right", entry.right),
        verify=_clean_field("verify", entry.verify),
        parent=parent,
    )


def _normalise_date(raw: Optional[str]) -> str:
    if raw is None:
        return _datetime.date.today().isoformat()
    try:
        return _datetime.date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc


def build_line(entry: Entry, record_date: Optional[str] = None) -> str:
    clean = _validate_entry(entry)
    date_text = _normalise_date(record_date)
    trigger = clean.trigger
    if clean.parent and clean.parent not in trigger:
        trigger = f"{trigger}（同类新机制，父条目 {clean.parent}）"
    return (
        f"{date_text} | {clean.tag} | {trigger} | {clean.wrong} | "
        f"{clean.right} | {clean.verify}"
    )


def _read_manifest(root: Path) -> Dict[str, object]:
    path = root / MANIFEST_NAME
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"root does not contain a regular {MANIFEST_NAME}")
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"{MANIFEST_NAME} must be UTF-8 without BOM")
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{MANIFEST_NAME} is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"{MANIFEST_NAME} must contain an object")
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema_version")
    for field in ("name", "version", "license"):
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{MANIFEST_NAME} is missing {field}")
    return manifest


def _read_frontmatter(path: Path) -> Dict[str, str]:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValueError("SKILL.md must be UTF-8 without BOM")
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("SKILL.md is not valid UTF-8") from exc
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md is missing YAML frontmatter")

    fields: Dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"').strip("'")
    raise ValueError("SKILL.md frontmatter is not closed")


def _safe_relative(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"manifest path must stay inside the skill root: {relative}")
    resolved = (root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"manifest path escapes the skill root: {relative}") from exc
    return resolved


def validate_skill_root(root: Path, recorder_path: Optional[Path] = None) -> Dict[str, object]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"root is not a directory: {root}")

    manifest = _read_manifest(root)
    skill = root / "SKILL.md"
    if not skill.is_file() or skill.is_symlink():
        raise ValueError("root does not contain a regular SKILL.md")
    fields = _read_frontmatter(skill)
    if fields.get("name") != manifest.get("name"):
        raise ValueError("SKILL.md name does not match skill.json")
    if fields.get("license") != manifest.get("license"):
        raise ValueError("SKILL.md license does not match skill.json")
    if not fields.get("description", "").strip():
        raise ValueError("SKILL.md is missing a non-empty description")

    runtime_files = manifest.get("runtime_required_files")
    if not isinstance(runtime_files, list) or not runtime_files:
        raise ValueError("skill.json is missing runtime_required_files")
    for relative in runtime_files:
        if not isinstance(relative, str):
            raise ValueError("runtime_required_files entries must be strings")
        path = _safe_relative(root, relative)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"root is missing a regular runtime file: {relative}")

    expected_recorder = (_safe_relative(root, "scripts/record_pitfall.py")).resolve()
    actual_recorder = (recorder_path or SCRIPT_PATH).resolve()
    if expected_recorder != actual_recorder:
        raise ValueError("this recorder is not the one installed under the validated root")
    return manifest


def _memory_path(root: Path, scope: str, recorder_path: Optional[Path] = None) -> Path:
    root = root.expanduser().resolve()
    manifest = validate_skill_root(root, recorder_path)

    memory = manifest.get("memory")
    if not isinstance(memory, dict):
        raise ValueError("skill.json is missing memory")
    local_name = memory.get("local", LOCAL_NAME)
    repo_name = memory.get("repo", REPO_NAME)
    if local_name not in {LOCAL_NAME, "PITFALLS.local.md"} or repo_name not in {
        REPO_NAME,
        "PITFALLS.md",
    }:
        raise ValueError("memory file names must be PITFALLS.local.md and PITFALLS.md")

    if scope == "auto":
        if (root / local_name).is_file():
            scope = "local"
        elif root.parent.name.casefold() == "skills":
            scope = "local"
        elif (root / ".git").exists():
            scope = "repo"
        else:
            scope = "local"
    if scope not in {"repo", "local"}:
        raise ValueError("scope must be auto, repo, or local")

    path = root / (local_name if scope == "local" else repo_name)
    if path.is_symlink():
        raise ValueError("refusing to write through a symbolic link")
    resolved = path.resolve(strict=False)
    if resolved.parent != root:
        raise ValueError("refusing to write outside the skill root")
    return path


def _normalise_for_compare(value: str) -> str:
    return " ".join(value.split()).casefold()


def _find_duplicate(existing: str, line: str) -> Optional[Tuple[int, int, str]]:
    target = ENTRY_RE.match(line)
    if target is None:
        return None
    target_fields = tuple(_normalise_for_compare(part) for part in target.groups()[1:])
    offset = 0
    for raw in existing.splitlines(keepends=True):
        candidate = raw.strip()
        if candidate.startswith("- "):
            candidate = candidate[2:].strip()
        match = ENTRY_RE.match(candidate)
        if match is not None:
            fields = tuple(_normalise_for_compare(part) for part in match.groups()[1:])
            if fields == target_fields:
                date_match = re.search(r"\d{4}-\d{2}-\d{2}", raw)
                if date_match is not None:
                    return (
                        offset + date_match.start(),
                        offset + date_match.end(),
                        date_match.group(0),
                    )
        offset += len(raw)
    return None


def _refresh_date(
    path: Path, existing: str, duplicate: Tuple[int, int, str], date_text: str
) -> None:
    start, end, _ = duplicate
    updated = existing[:start] + date_text + existing[end:]
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def _read_existing(path: Path) -> str:
    if not path.exists():
        return ""
    if path.is_symlink():
        raise ValueError("refusing to read a symbolic link memory file")
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValueError("refusing to append to a UTF-8 BOM file")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("memory file is not valid UTF-8") from exc


def _create_memory(path: Path) -> None:
    header = REPO_HEADER if path.name == REPO_NAME else LOCAL_HEADER
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(header)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        return


def _append_line(path: Path, existing: str, line: str) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def record(
    root: Path,
    entry: Entry,
    scope: str = "auto",
    record_date: Optional[str] = None,
    dry_run: bool = False,
    recorder_path: Optional[Path] = None,
) -> RecordResult:
    line = build_line(entry, record_date)
    path = _memory_path(root, scope, recorder_path)
    existing = _read_existing(path)
    duplicate = _find_duplicate(existing, line)
    if duplicate is not None:
        if line[:10] > duplicate[2]:
            if dry_run:
                return RecordResult(status="dry-run", path=path, line=line)
            _refresh_date(path, existing, duplicate, line[:10])
            return RecordResult(status="updated", path=path, line=line)
        return RecordResult(status="duplicate", path=path)
    if dry_run:
        return RecordResult(status="dry-run", path=path, line=line)
    if not path.exists():
        _create_memory(path)
        existing = _read_existing(path)
    _append_line(path, existing, line)
    return RecordResult(status="appended", path=path, line=line)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="comma-separated tags")
    parser.add_argument("--trigger", required=True, help="when the failure recurs")
    parser.add_argument("--wrong", required=True, help="what went wrong")
    parser.add_argument("--right", required=True, help="the verified correct approach")
    parser.add_argument("--verify", required=True, help="how it was verified")
    parser.add_argument("--parent", help="parent ID, e.g. TEST-02")
    parser.add_argument("--date", help="record date in YYYY-MM-DD form")
    parser.add_argument(
        "--scope",
        choices=("auto", "repo", "local"),
        default="auto",
        help="auto prefers local under skills/ or when a local file exists",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="skill root")
    parser.add_argument("--dry-run", action="store_true", help="validate and print only")
    return parser.parse_args(argv)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, ValueError):
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_stdio()
    args = parse_args(argv if argv is not None else sys.argv[1:])
    entry = Entry(
        tag=args.tag,
        trigger=args.trigger,
        wrong=args.wrong,
        right=args.right,
        verify=args.verify,
        parent=args.parent,
    )
    try:
        result = record(
            args.root,
            entry,
            scope=args.scope,
            record_date=args.date,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: could not write memory: {exc}", file=sys.stderr)
        return 1

    print(f"{result.status.upper()}: {result.path}")
    if result.status == "dry-run" and result.line:
        print(result.line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

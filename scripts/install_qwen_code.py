#!/usr/bin/env python3
"""Install qwen-grounded-audit as a standalone Qwen Code skill."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from install_common import SKILL_NAME, InstallError, install_to


def destination_for(scope: str, project: Path | None) -> Path:
    if scope == "project":
        if project is None:
            raise InstallError("--project is required with --scope project")
        project = project.expanduser().resolve()
        if not project.is_dir():
            raise InstallError(f"project directory does not exist: {project}")
        return project / ".qwen" / "skills" / SKILL_NAME
    if project is not None:
        raise InstallError("--project is only valid with --scope project")
    return Path.home() / ".qwen" / "skills" / SKILL_NAME


def install(scope: str, project: Path | None, force: bool, dry_run: bool) -> Path:
    return install_to(destination_for(scope, project), force, dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install project-health-audit as a standalone Qwen Code skill."
    )
    parser.add_argument(
        "--scope",
        choices=("personal", "project"),
        default=None,
        help="personal installs under ~/.qwen/skills; project installs under <repo>/.qwen/skills.",
    )
    parser.add_argument(
        "--project",
        type=Path,
        help="Repository root for --scope project.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite runtime files in an existing installation; local memory is preserved.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the destination and files without writing.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scope = args.scope or ("project" if args.project else "personal")
    if args.scope == "personal" and args.project:
        print("error: --project requires --scope project", file=sys.stderr)
        return 2
    try:
        destination = install(scope, args.project, args.force, args.dry_run)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print("Dry run only; no files were written.")
        return 0

    print(f"Installed {SKILL_NAME} to {destination}")
    if scope == "project":
        print(f"Commit the .qwen/skills/{SKILL_NAME} directory to share it with collaborators.")
    else:
        print(f"Restart Qwen Code, then invoke /{SKILL_NAME}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Update the package version in pyproject.toml for a release."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

VERSION_PATTERN = re.compile(
    r"^\d+\.\d+\.\d+(?:(?:a|b|rc|post|dev)\d+)?$"
)
PYPROJECT_VERSION_PATTERN = re.compile(r'(?m)^(version = ")(?P<version>[^"]+)(")$')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set the package version in pyproject.toml."
    )
    parser.add_argument("version", help="PEP 440 release version, for example 0.2.0")
    parser.add_argument(
        "--pyproject",
        default="pyproject.toml",
        help="Path to pyproject.toml. Defaults to pyproject.toml.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not VERSION_PATTERN.fullmatch(args.version):
        raise SystemExit(
            "Invalid version. Use a PEP 440-style version like 0.2.0 or 0.2.0rc1."
        )

    pyproject_path = Path(args.pyproject)
    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    match = PYPROJECT_VERSION_PATTERN.search(pyproject_text)
    if match is None:
        raise SystemExit("Could not update version in pyproject.toml.")
    current_version = match.group("version")
    if current_version == args.version:
        raise SystemExit(f"pyproject.toml is already at version {args.version}.")

    updated_text, replacements = PYPROJECT_VERSION_PATTERN.subn(
        lambda matched: f'{matched.group(1)}{args.version}"',
        pyproject_text,
        count=1,
    )
    if replacements != 1:
        raise SystemExit("Could not update version in pyproject.toml.")

    pyproject_path.write_text(updated_text, encoding="utf-8")
    print(f"Updated {pyproject_path} to version {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

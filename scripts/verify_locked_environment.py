# -*- coding: utf-8 -*-
"""Verify that the current interpreter matches an exact pip-tools hash lock."""
from __future__ import annotations

import importlib.metadata
from pathlib import Path
import re
import sys

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


_HASH = re.compile(r"--hash=sha256:([0-9a-fA-F]{64})(?:\s|$)")


def _logical_lines(text: str):
    pending = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1].strip() + " "
            continue
        yield (pending + stripped).strip()
        pending = ""
    if pending:
        raise ValueError("unterminated continuation")


def verify(lock_path: Path) -> int:
    try:
        text = lock_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        print("LOCK_FORMAT_INVALID file")
        return 2

    requirements: dict[str, tuple[str, Requirement]] = {}
    try:
        for number, line in enumerate(_logical_lines(text), 1):
            if line.startswith("--"):
                continue
            hashes = _HASH.findall(line)
            requirement_text = line.split("--hash=", 1)[0].strip()
            requirement_text = re.sub(r"\s+#.*$", "", requirement_text).strip()
            requirement = Requirement(requirement_text)
            specifiers = list(requirement.specifier)
            if (requirement.url is not None or requirement.extras or len(specifiers) != 1 or
                    specifiers[0].operator != "==" or "*" in specifiers[0].version or not hashes):
                raise ValueError(f"line {number}")
            name = canonicalize_name(requirement.name)
            if name in requirements:
                raise ValueError(f"duplicate {name}")
            requirements[name] = (specifiers[0].version, requirement)
    except (InvalidRequirement, ValueError) as exc:
        print(f"LOCK_FORMAT_INVALID {exc}")
        return 2

    if not requirements:
        print("LOCK_FORMAT_INVALID empty")
        return 2

    checked = 0
    for name, (wanted, requirement) in sorted(requirements.items()):
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            installed = importlib.metadata.version(requirement.name)
            matches = Version(installed) == Version(wanted)
        except (importlib.metadata.PackageNotFoundError, InvalidVersion):
            matches = False
        if not matches:
            print(f"LOCK_ENV_MISMATCH {name}")
            return 3
        checked += 1

    print(f"LOCK_ENV_OK checked={checked}")
    return 0


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("LOCK_FORMAT_INVALID arguments")
        return 2
    return verify(Path(arguments[0]))


if __name__ == "__main__":
    raise SystemExit(main())


"""Resolve a protected runner manifest filename without reading arbitrary files."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def resolve_manifest(root: str, name: str) -> Path:
    if (
        not root
        or not name
        or any(character in root + name for character in "\r\n\x00")
        or Path(name).name != name
        or not name.endswith(".json")
    ):
        raise ValueError("LIVE_MANIFEST_ROOT and a JSON manifest filename are required")
    directory = Path(root).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("LIVE_MANIFEST_ROOT must be a directory")
    manifest = (directory / name).resolve(strict=True)
    if not manifest.is_relative_to(directory) or not manifest.is_file():
        raise ValueError("manifest must be an approved file in LIVE_MANIFEST_ROOT")
    return manifest


def main() -> int:
    try:
        manifest = resolve_manifest(
            os.getenv("LIVE_MANIFEST_ROOT", ""), os.getenv("MANIFEST_NAME", "")
        )
    except (OSError, ValueError) as exc:
        print(f"BLOCKED: live manifest unavailable ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"LIVE_MANIFEST_PATH={manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

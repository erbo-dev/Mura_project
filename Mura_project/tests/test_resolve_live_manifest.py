from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.resolve_live_manifest import resolve_manifest


def test_only_approved_manifest_inside_runner_directory(tmp_path: Path) -> None:
    directory = tmp_path / "approved"
    directory.mkdir()
    manifest = directory / "fixture.json"
    manifest.write_text("{}", encoding="utf-8")
    assert resolve_manifest(str(directory), "fixture.json") == manifest

    for name in ("", "../private.json", "secret.txt", "C:\\private.json", "a\nB=evil.json"):
        with pytest.raises((OSError, ValueError)):
            resolve_manifest(str(directory), name)


def test_symlink_cannot_escape_approved_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "approved"
    directory.mkdir()
    target = tmp_path / "private.json"
    target.write_text("{}", encoding="utf-8")
    (directory / "linked.json").write_text("{}", encoding="utf-8")
    actual_resolve = Path.resolve

    def resolve_link(path: Path, strict: bool = False) -> Path:
        if path.name == "linked.json":
            return target
        return actual_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_link)
    with pytest.raises(ValueError, match="approved file"):
        resolve_manifest(str(directory), "linked.json")

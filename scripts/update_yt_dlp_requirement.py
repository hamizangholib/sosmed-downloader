"""Pin backend/requirements.txt to an already-installed yt-dlp nightly."""

from __future__ import annotations

import re
import sys
from pathlib import Path


VERSION_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d+)?(?:\.dev0)?$")
REQUIREMENT_RE = re.compile(r"^(yt-dlp(?:\[[^]]+\])?==)([^\s]+)$", re.MULTILINE)


def update_requirement(path: Path, version: str) -> bool:
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"Unexpected yt-dlp version: {version!r}")
    original = path.read_text(encoding="utf-8")
    updated, count = REQUIREMENT_RE.subn(rf"\g<1>{version}", original, count=1)
    if count != 1:
        raise RuntimeError(f"Could not find one pinned yt-dlp requirement in {path}")
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def self_check() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "requirements.txt"
        path.write_text("fastapi==1\nyt-dlp[default,curl-cffi]==2026.8.19\n", encoding="utf-8")
        assert update_requirement(path, "2026.8.20.234504.dev0")
        assert "yt-dlp[default,curl-cffi]==2026.8.20.234504.dev0" in path.read_text(encoding="utf-8")
        assert not update_requirement(path, "2026.8.20.234504.dev0")
        try:
            update_requirement(path, "latest")
            raise AssertionError("invalid versions must be rejected")
        except ValueError:
            pass


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        self_check()
        print("updater self-check ok")
    elif len(sys.argv) == 3:
        changed = update_requirement(Path(sys.argv[1]), sys.argv[2])
        print("updated" if changed else "already current")
    else:
        raise SystemExit(
            "usage: update_yt_dlp_requirement.py REQUIREMENTS VERSION | --self-check"
        )

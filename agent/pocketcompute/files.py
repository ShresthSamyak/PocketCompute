"""Touch-friendly file browsing, download and upload.

Access is sandboxed to a set of *roots*. By default the only root is the user's
home directory; extra roots can be added in config (``file_roots``). Every path
the client sends is resolved and checked to live under one of the roots, which
blocks ``..`` traversal out of the sandbox.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import config


def roots() -> list[Path]:
    extra = [Path(p).expanduser() for p in config.get("file_roots", [])]
    base = [Path.home()]
    seen: list[Path] = []
    for p in base + extra:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp not in seen and rp.exists():
            seen.append(rp)
    return seen


def _is_allowed(target: Path) -> bool:
    try:
        rt = target.resolve()
    except OSError:
        return False
    for root in roots():
        try:
            rt.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def resolve(path: str | None) -> Path:
    """Resolve a client-supplied path, defaulting to the first root (home)."""
    if not path:
        return roots()[0]
    p = Path(path).expanduser()
    return p


def list_dir(path: str | None) -> dict[str, Any]:
    target = resolve(path)
    if not _is_allowed(target):
        raise PermissionError("Path is outside the allowed roots")
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_dir():
        raise NotADirectoryError(str(target))

    entries: list[dict[str, Any]] = []
    try:
        scan = list(os.scandir(target))
    except PermissionError:
        scan = []
    for entry in scan:
        try:
            is_dir = entry.is_dir()
            stat = entry.stat()
            size = 0 if is_dir else stat.st_size
            entries.append({
                "name": entry.name,
                "path": str(Path(entry.path)),
                "is_dir": is_dir,
                "size": size,
                "modified": stat.st_mtime,
            })
        except (OSError, PermissionError):
            continue

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

    parent = target.parent
    has_parent = _is_allowed(parent) and parent != target
    return {
        "path": str(target),
        "parent": str(parent) if has_parent else None,
        "roots": [{"name": r.name or str(r), "path": str(r)} for r in roots()],
        "entries": entries,
    }


def file_for_download(path: str) -> Path:
    target = resolve(path)
    if not _is_allowed(target):
        raise PermissionError("Path is outside the allowed roots")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(str(target))
    return target


def save_upload(dest_dir: str, filename: str, data: bytes) -> dict[str, Any]:
    target_dir = resolve(dest_dir)
    if not _is_allowed(target_dir) or not target_dir.is_dir():
        raise PermissionError("Destination is outside the allowed roots")
    # Strip any path components from the supplied filename.
    safe_name = os.path.basename(filename) or "upload.bin"
    dest = target_dir / safe_name
    if not _is_allowed(dest):
        raise PermissionError("Resolved destination is outside the allowed roots")
    dest.write_bytes(data)
    return {"path": str(dest), "size": len(data)}

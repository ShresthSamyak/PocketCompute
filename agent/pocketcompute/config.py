"""Persistent configuration and state for the PocketCompute agent.

Everything lives in a single JSON file under ~/.pocketcompute/config.json so the
agent is zero-setup: first run generates a pairing secret and a device name, and
nothing else is required.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import threading
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("POCKETCOMPUTE_HOME", Path.home() / ".pocketcompute"))
CONFIG_PATH = CONFIG_DIR / "config.json"

_lock = threading.RLock()


def _default_shortcuts() -> list[dict[str, Any]]:
    """A few starter shortcuts so the app feels alive on first launch."""
    return [
        {
            "id": secrets.token_hex(4),
            "name": "Show disk usage",
            "emoji": "💾",
            "command": "Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free",
            "shell": "powershell",
        },
        {
            "id": secrets.token_hex(4),
            "name": "Top processes",
            "emoji": "📊",
            "command": "Get-Process | Sort-Object CPU -Descending | Select-Object -First 8 Name,CPU,WS",
            "shell": "powershell",
        },
        {
            "id": secrets.token_hex(4),
            "name": "WSL uptime",
            "emoji": "🐧",
            "command": "uptime",
            "shell": "wsl",
        },
    ]


def _defaults() -> dict[str, Any]:
    return {
        "version": 1,
        "device_name": socket.gethostname() or "My PC",
        # JWT signing secret + the pairing secret embedded in the QR code.
        "jwt_secret": secrets.token_hex(32),
        "pairing_secret": secrets.token_urlsafe(18),
        "shortcuts": _default_shortcuts(),
        # Roots exposed to the file browser. Empty => home directory only.
        "file_roots": [],
    }


class Config:
    """Thread-safe view over the on-disk JSON config."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        with _lock:
            if CONFIG_PATH.exists():
                try:
                    self._data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    self._data = _defaults()
                    self._save_unlocked()
            else:
                self._data = _defaults()
                self._save_unlocked()
            # Backfill any keys added in newer versions.
            changed = False
            for key, value in _defaults().items():
                if key not in self._data:
                    self._data[key] = value
                    changed = True
            if changed:
                self._save_unlocked()

    def _save_unlocked(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_PATH)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass

    def save(self) -> None:
        with _lock:
            self._save_unlocked()

    def get(self, key: str, default: Any = None) -> Any:
        with _lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with _lock:
            self._data[key] = value
            self._save_unlocked()

    # -- shortcuts --------------------------------------------------------
    def list_shortcuts(self) -> list[dict[str, Any]]:
        with _lock:
            return list(self._data.get("shortcuts", []))

    def add_shortcut(self, name: str, command: str, shell: str, emoji: str = "⚡") -> dict[str, Any]:
        with _lock:
            shortcut = {
                "id": secrets.token_hex(4),
                "name": name,
                "emoji": emoji or "⚡",
                "command": command,
                "shell": shell,
            }
            self._data.setdefault("shortcuts", []).append(shortcut)
            self._save_unlocked()
            return shortcut

    def delete_shortcut(self, shortcut_id: str) -> bool:
        with _lock:
            before = self._data.get("shortcuts", [])
            after = [s for s in before if s.get("id") != shortcut_id]
            self._data["shortcuts"] = after
            self._save_unlocked()
            return len(after) != len(before)

    def get_shortcut(self, shortcut_id: str) -> dict[str, Any] | None:
        with _lock:
            for s in self._data.get("shortcuts", []):
                if s.get("id") == shortcut_id:
                    return dict(s)
            return None


config = Config()

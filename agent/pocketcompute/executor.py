"""Run shell commands and stream their output.

Supports four execution targets that cover what a developer actually uses on a
Windows box: PowerShell, CMD, WSL, and the system default. Output is streamed
line-by-line through an async callback so the phone sees logs as they happen.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from typing import Awaitable, Callable

OutputCb = Callable[[str], Awaitable[None]]

IS_WINDOWS = sys.platform == "win32"

# Friendly shell name -> whether it's available on this machine.
SHELLS = ["powershell", "cmd", "wsl", "bash"]


def available_shells() -> list[str]:
    out: list[str] = []
    if IS_WINDOWS:
        if shutil.which("powershell") or shutil.which("pwsh"):
            out.append("powershell")
        out.append("cmd")
        if shutil.which("wsl"):
            out.append("wsl")
    else:
        if shutil.which("bash"):
            out.append("bash")
    return out


def _argv(command: str, shell: str) -> list[str]:
    """Build the argv that runs ``command`` under the requested shell."""
    if shell == "powershell":
        exe = shutil.which("pwsh") or "powershell"
        return [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    if shell == "cmd":
        return ["cmd", "/d", "/s", "/c", command]
    if shell == "wsl":
        return ["wsl", "-e", "bash", "-lc", command]
    if shell == "bash":
        return ["bash", "-lc", command]
    # Sensible default per platform.
    if IS_WINDOWS:
        return ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["bash", "-lc", command]


async def run_stream(
    command: str,
    shell: str = "powershell",
    cwd: str | None = None,
    on_output: OutputCb | None = None,
    timeout: float | None = 120.0,
) -> int:
    """Execute ``command`` and stream combined stdout/stderr via ``on_output``.

    Returns the process exit code (or -1 on timeout / spawn failure).
    """
    argv = _argv(command, shell)
    creationflags = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd or None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=creationflags,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except FileNotFoundError:
        if on_output:
            await on_output(f"[pocketcompute] shell not found: {shell}\n")
        return -1
    except Exception as exc:  # pragma: no cover - defensive
        if on_output:
            await on_output(f"[pocketcompute] failed to start: {exc}\n")
        return -1

    async def pump() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            if on_output:
                await on_output(text)

    try:
        await asyncio.wait_for(pump(), timeout=timeout)
        return await proc.wait()
    except asyncio.TimeoutError:
        if on_output:
            await on_output(f"\n[pocketcompute] timed out after {timeout:.0f}s, terminating\n")
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return -1


async def run_capture(command: str, shell: str = "powershell", cwd: str | None = None,
                      timeout: float | None = 30.0) -> tuple[int, str]:
    """Run a command and return (exit_code, full_output)."""
    buf: list[str] = []

    async def collect(chunk: str) -> None:
        buf.append(chunk)

    code = await run_stream(command, shell, cwd, collect, timeout)
    return code, "".join(buf)

"""Long-running job management.

A *job* is a background process the user started from their phone (a server, a
training run, a scraper). Unlike a one-shot command, it keeps running after the
request returns; the agent tracks its status, buffers recent output, and pushes
updates to connected clients.
"""
from __future__ import annotations

import asyncio
import collections
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .executor import _argv

IS_WINDOWS = sys.platform == "win32"
MAX_LOG_LINES = 500

# Broadcast hook wired up by the server: called with (event_type, payload).
Broadcaster = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class Job:
    id: str
    name: str
    command: str
    shell: str
    cwd: str | None
    status: str = "starting"  # starting | running | exited | failed | stopped
    pid: int | None = None
    exit_code: int | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    log: collections.deque = field(default_factory=lambda: collections.deque(maxlen=MAX_LOG_LINES))
    _proc: Any = field(default=None, repr=False)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "command": self.command,
            "shell": self.shell,
            "cwd": self.cwd,
            "status": self.status,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "runtime_seconds": int((self.ended_at or time.time()) - self.started_at),
        }

    def detail(self) -> dict[str, Any]:
        data = self.summary()
        data["log"] = list(self.log)
        return data


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._counter = 0
        self.broadcast: Broadcaster | None = None

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self.broadcast:
            try:
                await self.broadcast(event, payload)
            except Exception:
                pass

    def _next_id(self) -> str:
        self._counter += 1
        return f"job{self._counter:04d}"

    async def start(self, name: str, command: str, shell: str = "powershell",
                    cwd: str | None = None) -> Job:
        job = Job(id=self._next_id(), name=name or command[:40], command=command,
                  shell=shell, cwd=cwd or None)
        self._jobs[job.id] = job

        argv = _argv(command, shell)
        creationflags = 0
        if IS_WINDOWS:
            import subprocess
            # New process group so we can signal the whole tree on stop.
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(
                subprocess, "CREATE_NO_WINDOW", 0)

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd or None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=creationflags,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except Exception as exc:
            job.status = "failed"
            job.ended_at = time.time()
            job.log.append(f"[pocketcompute] failed to start: {exc}")
            await self._emit("job_update", job.summary())
            return job

        job._proc = proc
        job.pid = proc.pid
        job.status = "running"
        await self._emit("job_update", job.summary())
        asyncio.create_task(self._supervise(job))
        return job

    async def _supervise(self, job: Job) -> None:
        proc = job._proc
        assert proc is not None and proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            job.log.append(text)
            await self._emit("job_log", {"id": job.id, "line": text})
        code = await proc.wait()
        job.exit_code = code
        job.ended_at = time.time()
        if job.status == "stopped":
            pass
        elif code == 0:
            job.status = "exited"
        else:
            job.status = "failed"
        await self._emit("job_update", job.summary())

    async def stop(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job._proc is None or job.status not in ("running", "starting"):
            return False
        job.status = "stopped"
        proc = job._proc
        try:
            if IS_WINDOWS:
                # Kill the whole tree -- shells spawn children.
                import subprocess
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        await self._emit("job_update", job.summary())
        return True

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[dict[str, Any]]:
        # Newest first.
        return [j.summary() for j in sorted(
            self._jobs.values(), key=lambda j: j.started_at, reverse=True)]

    def running_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status == "running")

    def prune(self) -> int:
        """Drop finished jobs from memory. Returns how many were removed."""
        finished = [jid for jid, j in self._jobs.items()
                    if j.status in ("exited", "failed", "stopped")]
        for jid in finished:
            del self._jobs[jid]
        return len(finished)


jobs = JobManager()

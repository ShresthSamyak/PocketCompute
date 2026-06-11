"""System metrics: CPU, RAM, disk, network, GPU, uptime.

GPU is best-effort: tries NVML (nvidia-ml-py), falls back to parsing
`nvidia-smi`, and finally reports no GPU. Nothing here ever raises to the
caller -- a missing sensor just becomes ``None``.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from typing import Any

import psutil

_BOOT_TIME = psutil.boot_time()
_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# NVML is imported lazily so the dependency stays optional.
_nvml_ok: bool | None = None
_nvml = None


def _init_nvml() -> bool:
    global _nvml_ok, _nvml
    if _nvml_ok is not None:
        return _nvml_ok
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        _nvml = pynvml
        _nvml_ok = True
    except Exception:
        _nvml_ok = False
    return _nvml_ok


def _gpu_via_nvml() -> list[dict[str, Any]]:
    pynvml = _nvml
    assert pynvml is not None  # guarded by _init_nvml()
    out: list[dict[str, Any]] = []
    count = pynvml.nvmlDeviceGetCount()
    for i in range(count):
        h = pynvml.nvmlDeviceGetHandleByIndex(i)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        util = pynvml.nvmlDeviceGetUtilizationRates(h)
        name = pynvml.nvmlDeviceGetName(h)
        if isinstance(name, bytes):
            name = name.decode()
        try:
            temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
        except Exception:
            temp = None
        out.append(
            {
                "name": name,
                "load": float(util.gpu),
                "mem_used_mb": round(mem.used / 1024 / 1024),
                "mem_total_mb": round(mem.total / 1024 / 1024),
                "temp_c": temp,
            }
        )
    return out


def _gpu_via_smi() -> list[dict[str, Any]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    query = "name,utilization.gpu,memory.used,memory.total,temperature.gpu"
    try:
        result = subprocess.run(
            [exe, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=_CREATIONFLAGS,
        )
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for line in result.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        name, load, used, total, temp = parts[:5]
        try:
            out.append(
                {
                    "name": name,
                    "load": float(load),
                    "mem_used_mb": round(float(used)),
                    "mem_total_mb": round(float(total)),
                    "temp_c": float(temp),
                }
            )
        except ValueError:
            continue
    return out


def gpu_metrics() -> list[dict[str, Any]]:
    if _init_nvml():
        try:
            return _gpu_via_nvml()
        except Exception:
            pass
    return _gpu_via_smi()


_last_net: tuple[float, int, int] | None = None


def _network_rate() -> dict[str, float]:
    """Bytes/sec since the previous call. First call returns zeros."""
    global _last_net
    counters = psutil.net_io_counters()
    now = time.monotonic()
    if _last_net is None:
        _last_net = (now, counters.bytes_sent, counters.bytes_recv)
        return {"up_bps": 0.0, "down_bps": 0.0}
    dt = max(now - _last_net[0], 1e-6)
    up = (counters.bytes_sent - _last_net[1]) / dt
    down = (counters.bytes_recv - _last_net[2]) / dt
    _last_net = (now, counters.bytes_sent, counters.bytes_recv)
    return {"up_bps": max(up, 0.0), "down_bps": max(down, 0.0)}


def metrics() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = _network_rate()
    return {
        "ts": time.time(),
        "cpu": {
            "percent": psutil.cpu_percent(interval=None),
            "cores": psutil.cpu_count(logical=True),
        },
        "ram": {
            "percent": vm.percent,
            "used_gb": round(vm.used / 1024 ** 3, 2),
            "total_gb": round(vm.total / 1024 ** 3, 2),
        },
        "disk": {
            "percent": disk.percent,
            "used_gb": round(disk.used / 1024 ** 3, 1),
            "total_gb": round(disk.total / 1024 ** 3, 1),
        },
        "net": net,
        "gpu": gpu_metrics(),
        "uptime_seconds": int(time.time() - _BOOT_TIME),
    }


def static_info() -> dict[str, Any]:
    """Slow-changing facts shown on the home/metrics screen."""
    import platform

    return {
        "platform": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_cores": psutil.cpu_count(logical=True),
        "cpu_physical": psutil.cpu_count(logical=False),
        "ram_total_gb": round(psutil.virtual_memory().total / 1024 ** 3, 1),
        "boot_time": _BOOT_TIME,
    }

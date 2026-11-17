"""Live host telemetry for the HUD: CPU, memory, GPU/VRAM, disks, network and
the heaviest processes, with a short rolling history for sparklines.

A single background sampler ticks once a second — but only while someone is
watching. Every read calls `touch()`; when nothing has asked for ~20 seconds
the thread exits, so a hidden/auto-started JARVIS costs nothing here.

GPU figures come straight from NVIDIA's NVML library (nvml.dll, shipped with
the driver) through ctypes: no extra dependency and no `nvidia-smi` process
spawned per sample. Machines without an NVIDIA GPU simply report gpu = None.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import psutil

HISTORY = 120          # samples kept per series (1 Hz -> two minutes)
_IDLE_STOP = 20.0      # seconds without a reader before the sampler sleeps
_DISK_EVERY = 30.0     # disk usage barely moves; don't stat every second
_PROC_EVERY = 5.0      # walking the process table is the one costly bit


# ── NVML (NVIDIA) ────────────────────────────────────────────────────────

class _NvmlUtil(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _NvmlMem(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong)]


class _Gpu:
    """First NVIDIA GPU via NVML. `ok` is False when there's no driver/GPU."""

    def __init__(self) -> None:
        self.ok = False
        self.name = ""
        self._lib: Any = None
        self._h = ctypes.c_void_p()
        try:
            if sys.platform == "win32":
                dll = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "nvml.dll"
                lib = ctypes.CDLL(str(dll))
            else:
                lib = ctypes.CDLL("libnvidia-ml.so.1")
            if lib.nvmlInit_v2() != 0:
                return
            count = ctypes.c_uint(0)
            if lib.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0 or count.value < 1:
                return
            lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint,
                                                          ctypes.POINTER(ctypes.c_void_p)]
            if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self._h)) != 0:
                return
            for fn in ("nvmlDeviceGetUtilizationRates", "nvmlDeviceGetMemoryInfo",
                       "nvmlDeviceGetPowerUsage", "nvmlDeviceGetFanSpeed"):
                getattr(lib, fn).argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            lib.nvmlDeviceGetTemperature.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                     ctypes.c_void_p]
            lib.nvmlDeviceGetName.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
            buf = ctypes.create_string_buffer(96)
            if lib.nvmlDeviceGetName(self._h, buf, 96) == 0:
                self.name = buf.value.decode("utf-8", "replace").replace("NVIDIA ", "")
            self._lib = lib
            self.ok = True
        except Exception:
            self.ok = False

    def read(self) -> dict[str, Any] | None:
        if not self.ok:
            return None
        lib, h = self._lib, self._h
        try:
            util = _NvmlUtil()
            mem = _NvmlMem()
            if lib.nvmlDeviceGetUtilizationRates(h, ctypes.byref(util)) != 0:
                return None
            if lib.nvmlDeviceGetMemoryInfo(h, ctypes.byref(mem)) != 0:
                return None
            out: dict[str, Any] = {
                "name": self.name,
                "util": int(util.gpu),
                "vram_used_gb": round(mem.used / 2**30, 1),
                "vram_total_gb": round(mem.total / 2**30, 1),
                "vram_pct": round(100 * mem.used / mem.total, 1) if mem.total else 0.0,
            }
            v = ctypes.c_uint(0)
            if lib.nvmlDeviceGetTemperature(h, 0, ctypes.byref(v)) == 0:
                out["temp"] = int(v.value)
            if lib.nvmlDeviceGetPowerUsage(h, ctypes.byref(v)) == 0:
                out["power_w"] = round(v.value / 1000)
            if lib.nvmlDeviceGetFanSpeed(h, ctypes.byref(v)) == 0:
                out["fan"] = int(v.value)
            return out
        except Exception:
            return None


# ── sampler ──────────────────────────────────────────────────────────────

_SERIES = ("cpu", "mem", "gpu", "vram", "net_down", "net_up")


class Sampler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_demand = 0.0
        self._hist: dict[str, deque[float]] = {k: deque(maxlen=HISTORY) for k in _SERIES}
        self._latest: dict[str, Any] = {}
        self._gpu: _Gpu | None = None
        self._disks: list[dict[str, Any]] = []
        self._disks_ts = 0.0
        self._procs: dict[str, list[dict[str, Any]]] = {"cpu": [], "mem": []}
        self._procs_ts = 0.0
        self._net: tuple[float, int, int] | None = None

    # -- public -----------------------------------------------------------
    def touch(self) -> None:
        """Declare interest; (re)start the sampler thread if it has gone idle."""
        self._last_demand = time.time()
        t = self._thread
        if t is None or not t.is_alive():
            with self._lock:
                t = self._thread
                if t is None or not t.is_alive():
                    self._thread = threading.Thread(target=self._run, name="telemetry",
                                                    daemon=True)
                    self._thread.start()

    def snapshot(self) -> dict[str, Any]:
        """Latest sample. Samples synchronously the very first time so callers
        never see an empty reading."""
        self.touch()
        if not self._latest:
            self._sample()
        with self._lock:
            return dict(self._latest)

    def history(self) -> dict[str, list[float]]:
        self.touch()
        with self._lock:
            return {k: list(v) for k, v in self._hist.items()}

    def processes(self) -> dict[str, list[dict[str, Any]]]:
        self.touch()
        with self._lock:
            return {k: list(v) for k, v in self._procs.items()}

    # -- internals --------------------------------------------------------
    def _run(self) -> None:
        psutil.cpu_percent(percpu=True)  # prime: the first call always reads 0
        nxt = time.time() + 1.0
        while time.time() - self._last_demand < _IDLE_STOP:
            time.sleep(max(0.05, nxt - time.time()))
            nxt = max(nxt + 1.0, time.time() + 0.2)
            try:
                self._sample()
            except Exception:
                pass  # a bad sample must never kill the sampler

    def _sample(self) -> None:
        now = time.time()
        cores = psutil.cpu_percent(percpu=True)
        cpu = round(sum(cores) / len(cores), 1) if cores else 0.0
        vm = psutil.virtual_memory()

        if self._gpu is None:
            self._gpu = _Gpu()
        gpu = self._gpu.read()

        io = psutil.net_io_counters()
        up = down = 0.0
        if self._net is not None and io is not None:
            dt = max(0.001, now - self._net[0])
            up = max(0.0, (io.bytes_sent - self._net[1]) / dt)
            down = max(0.0, (io.bytes_recv - self._net[2]) / dt)
        if io is not None:
            self._net = (now, io.bytes_sent, io.bytes_recv)

        if now - self._disks_ts > _DISK_EVERY or not self._disks:
            self._disks = _read_disks()
            self._disks_ts = now
        if now - self._procs_ts > _PROC_EVERY:
            procs = _read_processes(len(cores) or 1)
            self._procs_ts = time.time()
        else:
            procs = None

        sysdisk = next((d for d in self._disks if d.get("system")),
                       self._disks[0] if self._disks else None)
        latest = {
            "ok": True,
            "ts": now,
            "cpu": cpu,
            "cores": [round(c) for c in cores],
            "cpu_count": len(cores),
            "mem": {"pct": vm.percent, "used_gb": round(vm.used / 1e9, 1),
                    "total_gb": round(vm.total / 1e9, 1)},
            "gpu": gpu,
            "disks": self._disks,
            "net": {"up": round(up), "down": round(down)},
            "uptime_h": round((now - psutil.boot_time()) / 3600, 1),
            # flat fields the brain context and older callers read
            "cpu_pct": cpu,
            "cpu_cores": len(cores),
            "mem_pct": vm.percent,
            "mem_used_gb": round(vm.used / 1e9, 1),
            "mem_total_gb": round(vm.total / 1e9, 1),
            "disk_pct": sysdisk["pct"] if sysdisk else 0,
            "disk_free_gb": sysdisk["free_gb"] if sysdisk else 0,
            "disk_total_gb": sysdisk["total_gb"] if sysdisk else 0,
        }
        with self._lock:
            self._latest = latest
            h = self._hist
            h["cpu"].append(cpu)
            h["mem"].append(vm.percent)
            h["gpu"].append(float(gpu["util"]) if gpu else 0.0)
            h["vram"].append(float(gpu["vram_pct"]) if gpu else 0.0)
            h["net_down"].append(round(down))
            h["net_up"].append(round(up))
            if procs is not None:
                self._procs = procs


def _read_disks() -> list[dict[str, Any]]:
    """Fixed drives with usage. Optical/removable/unready drives are skipped
    (stat-ing an empty DVD drive can hang for seconds)."""
    sysdrive = (os.environ.get("SystemDrive") or "C:").upper() if sys.platform == "win32" else "/"
    out: list[dict[str, Any]] = []
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:
        parts = []
    for p in parts:
        opts = (p.opts or "").lower()
        if "cdrom" in opts or "removable" in opts or not p.fstype:
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except Exception:
            continue
        if u.total <= 0:
            continue
        mount = p.mountpoint.rstrip("\\") or p.mountpoint
        out.append({
            "mount": mount,
            "pct": u.percent,
            "free_gb": round(u.free / 1e9, 1),
            "total_gb": round(u.total / 1e9, 1),
            "system": mount.upper() == sysdrive,
        })
    return out


_SKIP_PROCS = {"system idle process", "idle", ""}


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort),
                ("Buffer", ctypes.c_void_p)]


class _SystemProcessInformation(ctypes.Structure):
    """Leading fields of ntdll's SYSTEM_PROCESS_INFORMATION (stable since Win7)."""
    _fields_ = [
        ("NextEntryOffset", ctypes.c_ulong),
        ("NumberOfThreads", ctypes.c_ulong),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", ctypes.c_ulong),
        ("NumberOfThreadsHighWatermark", ctypes.c_ulong),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UnicodeString),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", ctypes.c_ulong),
        ("SessionId", ctypes.c_ulong),
        ("UniqueProcessKey", ctypes.c_void_p),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
    ]


_nt_buf_size = 1 << 20
_prev_cpu: dict[int, tuple[str, int]] = {}     # pid -> (name, cpu time in 100 ns)
_prev_cpu_ts = 0.0


def _nt_process_table() -> list[tuple[int, str, int, int]] | None:
    """Every process as (pid, name, cpu_time_100ns, memory_bytes) from ONE
    NtQuerySystemInformation call (~2 ms for the whole table).

    psutil's per-process cpu/memory getters take about a second per pass on
    Windows: for each protected system process (Defender, csrss, System…) they
    fall back to re-enumerating the entire process list. This reads the same
    kernel table once, and — unlike the fast psutil path — still sees those
    protected processes, which are often exactly what is eating the CPU."""
    global _nt_buf_size
    if sys.platform != "win32":
        return None
    ntdll = ctypes.WinDLL("ntdll")
    needed = ctypes.c_ulong(0)
    for _ in range(4):
        buf = ctypes.create_string_buffer(_nt_buf_size)
        status = ntdll.NtQuerySystemInformation(5, buf, _nt_buf_size, ctypes.byref(needed))
        if status == 0:
            break
        if status & 0xFFFFFFFF == 0xC0000004:      # STATUS_INFO_LENGTH_MISMATCH
            _nt_buf_size = max(_nt_buf_size * 2, needed.value + (1 << 16))
            continue
        return None
    else:
        return None
    rows: list[tuple[int, str, int, int]] = []
    base = ctypes.addressof(buf)
    offset = 0
    while True:
        info = _SystemProcessInformation.from_address(base + offset)
        pid = info.UniqueProcessId or 0
        name = ""
        if info.ImageName.Buffer and info.ImageName.Length:
            name = ctypes.wstring_at(info.ImageName.Buffer, info.ImageName.Length // 2)
        mem = info.WorkingSetPrivateSize or info.WorkingSetSize
        rows.append((int(pid), name, int(info.UserTime + info.KernelTime), int(mem)))
        if not info.NextEntryOffset:
            break
        offset += info.NextEntryOffset
    return rows


def _read_processes(ncpu: int, top: int = 6) -> dict[str, list[dict[str, Any]]]:
    """Heaviest processes, grouped by name (30 chrome.exe rows are one line).
    cpu is the share of the WHOLE machine, so it reads like Task Manager."""
    global _prev_cpu, _prev_cpu_ts
    now = time.time()
    try:
        table = _nt_process_table()
    except Exception:
        table = None
    if table is None:
        return {"cpu": [], "mem": []}
    dt = now - _prev_cpu_ts if _prev_cpu_ts else 0.0
    agg: dict[str, dict[str, Any]] = {}
    cur: dict[int, tuple[str, int]] = {}
    for pid, name, cpu_time, mem in table:
        if pid == 0 or name.lower() in _SKIP_PROCS:
            continue
        cur[pid] = (name, cpu_time)
        row = agg.setdefault(name, {"name": name, "cpu": 0.0, "mem": 0, "n": 0})
        row["mem"] += mem
        row["n"] += 1
        prev = _prev_cpu.get(pid)
        if dt > 0 and prev is not None and prev[0] == name and cpu_time >= prev[1]:
            # 100 ns ticks -> percent of all cores over the interval
            row["cpu"] += (cpu_time - prev[1]) / 1e7 / dt * 100.0 / ncpu
    _prev_cpu, _prev_cpu_ts = cur, now
    rows = []
    for r in agg.values():
        label = r["name"][:-4] if r["name"].lower().endswith(".exe") else r["name"]
        rows.append({"name": label, "cpu": round(r["cpu"], 1),
                     "mem_gb": round(r["mem"] / 2**30, 2), "n": r["n"]})
    by_cpu = sorted(rows, key=lambda r: r["cpu"], reverse=True)[:top]
    by_mem = sorted(rows, key=lambda r: r["mem_gb"], reverse=True)[:top]
    return {"cpu": [r for r in by_cpu if r["cpu"] >= 0.1], "mem": by_mem}


sampler = Sampler()

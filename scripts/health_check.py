#!/usr/bin/env python3
"""
OptimusGrime — health_check.py
Collects CPU, memory pressure, disk I/O, disk space, and top processes.
Supports macOS (darwin) and Windows (win32) — auto-detected at runtime.

Usage:
  python3 health_check.py                    # pretty output
  python3 health_check.py --format json      # compact JSON
  python3 health_check.py --warnings-only    # only show problems
"""
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

PLATFORM  = sys.platform          # 'darwin' or 'win32'
PAGE_SIZE = 16384                  # bytes — Apple Silicon page size (darwin only)


def run(cmd, timeout=12):
    """Run a command list and return stdout."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def run_ps(expression: str, timeout=15) -> str:
    """Run a PowerShell expression and return stdout. Windows only."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", expression],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout
    except Exception:
        return ""


# ── CPU ───────────────────────────────────────────────────────────────────────

def _get_cpu_darwin():
    """Two-sample top for accurate current CPU %. Takes ~1s."""
    out = run(["top", "-l", "2", "-n", "0", "-s", "1"])
    lines = [l for l in out.splitlines() if l.startswith("CPU usage:")]
    line = lines[-1] if lines else ""
    m = re.search(r"([\d.]+)%\s+user.*?([\d.]+)%\s+sys.*?([\d.]+)%\s+idle", line)
    if not m:
        return None
    user, sys_, idle = float(m.group(1)), float(m.group(2)), float(m.group(3))
    return {"user": user, "sys": sys_, "idle": idle, "used": round(user + sys_, 1)}


def _get_cpu_win32():
    out = run_ps(
        "(Get-WmiObject Win32_Processor | "
        "Measure-Object -Property LoadPercentage -Average).Average"
    )
    try:
        load = float(out.strip())
        return {"user": load, "sys": 0.0, "idle": round(100 - load, 1), "used": round(load, 1)}
    except (ValueError, TypeError):
        return None


def get_cpu():
    if PLATFORM == "darwin": return _get_cpu_darwin()
    if PLATFORM == "win32":  return _get_cpu_win32()
    return None


# ── Memory ────────────────────────────────────────────────────────────────────

def _get_memory_darwin():
    """Parse vm_stat for memory breakdown and pressure level."""
    out = run(["vm_stat"])

    def pages(key):
        m = re.search(rf"{re.escape(key)}:\s+([\d]+)", out)
        return int(m.group(1)) if m else 0

    free       = pages("Pages free")
    active     = pages("Pages active")
    wired      = pages("Pages wired down")
    purgeable  = pages("Pages purgeable")
    compressed = pages("Pages stored in compressor")
    inactive   = pages("Pages inactive")

    mem_bytes = run(["sysctl", "-n", "hw.memsize"]).strip()
    total_pages = int(int(mem_bytes) / PAGE_SIZE) if mem_bytes.isdigit() else 0

    def to_gb(p):
        return round(p * PAGE_SIZE / 1024 ** 3, 1)

    used_pages = active + wired + compressed
    free_pages = free + purgeable + inactive

    if total_pages > 0:
        pct = free_pages / total_pages
        pressure = "low" if pct > 0.25 else ("medium" if pct > 0.10 else "high")
    else:
        pressure = "unknown"

    return {
        "used_gb":       to_gb(used_pages),
        "free_gb":       to_gb(free_pages),
        "wired_gb":      to_gb(wired),
        "compressed_gb": to_gb(compressed),
        "total_gb":      to_gb(total_pages) if total_pages else None,
        "pressure":      pressure,
    }


def _get_memory_win32():
    out = run_ps(
        "Get-WmiObject Win32_OperatingSystem | "
        "Select-Object FreePhysicalMemory,TotalVisibleMemorySize | "
        "ConvertTo-Json"
    )
    try:
        d = json.loads(out.strip())
        free_kb  = float(d["FreePhysicalMemory"])
        total_kb = float(d["TotalVisibleMemorySize"])
        used_kb  = total_kb - free_kb
        free_gb  = round(free_kb  / 1024 ** 2, 1)
        total_gb = round(total_kb / 1024 ** 2, 1)
        used_gb  = round(used_kb  / 1024 ** 2, 1)
        pct_free = free_kb / total_kb if total_kb else 1.0
        pressure = "low" if pct_free > 0.25 else ("medium" if pct_free > 0.10 else "high")
        return {
            "used_gb":       used_gb,
            "free_gb":       free_gb,
            "wired_gb":      0.0,
            "compressed_gb": 0.0,
            "total_gb":      total_gb,
            "pressure":      pressure,
        }
    except Exception:
        return None


def get_memory():
    if PLATFORM == "darwin": return _get_memory_darwin()
    if PLATFORM == "win32":  return _get_memory_win32()
    return None


# ── Disk I/O ──────────────────────────────────────────────────────────────────

def _get_disk_io_darwin():
    """Live disk activity via iostat second sample. Takes ~1s."""
    out = run(["iostat", "-d", "1", "2"])
    lines = out.strip().splitlines()
    data_rows = [l for l in lines if re.match(r"\s+[\d]", l)]
    if len(data_rows) >= 2:
        parts = data_rows[1].split()
        try:
            return {"mbps": float(parts[2]), "tps": int(float(parts[1]))}
        except (IndexError, ValueError):
            pass
    return {"mbps": 0.0, "tps": 0}


def _get_disk_io_win32():
    out = run_ps(
        r"Get-Counter '\PhysicalDisk(_Total)\Disk Read Bytes/sec',"
        r"'\PhysicalDisk(_Total)\Disk Write Bytes/sec' | "
        "Select-Object -ExpandProperty CounterSamples | "
        "Select-Object Path,CookedValue | ConvertTo-Json"
    )
    try:
        samples = json.loads(out.strip())
        if isinstance(samples, dict):
            samples = [samples]
        read_bps  = next((s["CookedValue"] for s in samples if "read"  in s["Path"].lower()), 0.0)
        write_bps = next((s["CookedValue"] for s in samples if "write" in s["Path"].lower()), 0.0)
        return {"mbps": round((read_bps + write_bps) / 1024 ** 2, 2), "tps": 0}
    except Exception:
        return {"mbps": 0.0, "tps": 0}


def get_disk_io():
    if PLATFORM == "darwin": return _get_disk_io_darwin()
    if PLATFORM == "win32":  return _get_disk_io_win32()
    return {"mbps": 0.0, "tps": 0}


# ── Disk Space ────────────────────────────────────────────────────────────────

def _get_disk_space_darwin():
    """Root volume usage from df."""
    out = run(["df", "-H", "/"])
    lines = out.strip().splitlines()
    if len(lines) >= 2:
        parts = lines[-1].split()
        if len(parts) >= 5:
            try:
                return {
                    "total":    parts[1],
                    "used":     parts[2],
                    "free":     parts[3],
                    "used_pct": int(parts[4].rstrip("%")),
                }
            except (ValueError, IndexError):
                pass
    return None


def _get_disk_space_win32():
    out = run_ps("Get-PSDrive C | Select-Object Used,Free | ConvertTo-Json")
    try:
        d = json.loads(out.strip())
        used  = float(d["Used"])
        free  = float(d["Free"])
        total = used + free
        def to_g(b): return f"{b / 1024**3:.1f}G"
        used_pct = int(used / total * 100) if total else 0
        return {"total": to_g(total), "used": to_g(used), "free": to_g(free), "used_pct": used_pct}
    except Exception:
        return None


def get_disk_space():
    if PLATFORM == "darwin": return _get_disk_space_darwin()
    if PLATFORM == "win32":  return _get_disk_space_win32()
    return None


# ── Top Processes ─────────────────────────────────────────────────────────────

def _get_top_processes_darwin(n=5):
    """Top n user processes by CPU, excluding system daemons."""
    out = run(["ps", "aux", "-r"])
    procs = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        user, pid, cpu_s, mem_s = parts[0], parts[1], parts[2], parts[3]
        rss_kb = parts[5] if len(parts) > 5 else "0"
        raw_cmd = parts[10]
        if user in ("root",) or user.startswith("_"):
            continue
        try:
            cpu_f  = float(cpu_s)
            mem_f  = float(mem_s)
            rss_mb = round(int(rss_kb) / 1024, 1)
        except ValueError:
            continue
        cmd_path = raw_cmd.split()[0]
        name = cmd_path.split("/")[-1]
        if not name or name in (".", ""):
            name = cmd_path[:28]
        name = name[:28]
        procs.append({"pid": int(pid), "name": name, "cpu": cpu_f, "mem": mem_f, "rss_mb": rss_mb})
    return sorted(procs, key=lambda x: x["cpu"], reverse=True)[:n]


def _get_top_processes_win32(n=5):
    out = run_ps(
        f"Get-Process | Sort-Object CPU -Descending | "
        f"Select-Object -First 15 Name,Id,CPU,WorkingSet | ConvertTo-Json"
    )
    try:
        raw = json.loads(out.strip())
        if isinstance(raw, dict):
            raw = [raw]
        procs = []
        for p in raw:
            name   = (p.get("Name") or "")[:28]
            pid    = p.get("Id", 0)
            cpu    = round(float(p.get("CPU") or 0), 1)
            ws     = float(p.get("WorkingSet") or 0)
            rss_mb = round(ws / 1024 ** 2, 1)
            if not name or pid == 0:
                continue
            procs.append({"pid": pid, "name": name, "cpu": cpu, "mem": 0.0, "rss_mb": rss_mb})
        return sorted(procs, key=lambda x: x["cpu"], reverse=True)[:n]
    except Exception:
        return []


def get_top_processes(n=5):
    if PLATFORM == "darwin": return _get_top_processes_darwin(n)
    if PLATFORM == "win32":  return _get_top_processes_win32(n)
    return []


# ── Thermal ───────────────────────────────────────────────────────────────────

def _check_thermal_darwin():
    """True if kernel_task CPU > 20% — indicates thermal throttling."""
    out = run(["ps", "-A", "-o", "%cpu,comm"])
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and "kernel_task" in parts[1]:
            try:
                return float(parts[0]) > 20.0
            except ValueError:
                pass
    return False


def _check_thermal_win32():
    # No equivalent concept on Windows — thermal throttling is handled silently by the OS.
    return False


def check_thermal():
    if PLATFORM == "darwin": return _check_thermal_darwin()
    if PLATFORM == "win32":  return _check_thermal_win32()
    return False


# ── Collect ───────────────────────────────────────────────────────────────────

def collect():
    """Collect all metrics in parallel."""
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_cpu   = ex.submit(get_cpu)
        f_mem   = ex.submit(get_memory)
        f_io    = ex.submit(get_disk_io)
        f_disk  = ex.submit(get_disk_space)
        f_procs = ex.submit(get_top_processes)
        f_therm = ex.submit(check_thermal)

    return {
        "timestamp":          datetime.now().isoformat(),
        "cpu":                f_cpu.result(),
        "memory":             f_mem.result(),
        "disk_io":            f_io.result(),
        "disk_space":         f_disk.result(),
        "processes":          f_procs.result(),
        "thermal_throttling": f_therm.result(),
    }


# ── Formatting ────────────────────────────────────────────────────────────────

def bar(value, max_val=100, width=20):
    filled = int(min(value, max_val) / max_val * width)
    return "█" * filled + "░" * (width - filled)


def format_pretty(d):
    out = []
    out.append("╔═ OptimusGrime ═══════════════════════════════════╗")

    if d.get("thermal_throttling"):
        out.append("│ ⚠  THERMAL THROTTLE — system is cooling itself down │")

    cpu = d.get("cpu")
    if cpu:
        b = bar(cpu["used"])
        out.append(f"│ CPU  {cpu['used']:5.1f}%  [{b}]  idle {cpu['idle']:.0f}%  │")

    mem = d.get("memory")
    if mem:
        picon = {"low": "●", "medium": "◐", "high": "○"}.get(mem["pressure"], "?")
        out.append(f"│ MEM  {mem['used_gb']}G used  {mem['free_gb']}G free  {picon} {mem['pressure'].upper():<6}      │")

    disk = d.get("disk_space")
    if disk:
        b = bar(disk["used_pct"])
        out.append(f"│ DISK {disk['used']:>5}/{disk['total']:<5} ({disk['used_pct']:2d}%)  [{b}]  │")

    io = d.get("disk_io")
    if io:
        out.append(f"│ I/O  {io['mbps']:.1f} MB/s  {io['tps']} tps                          │")

    out.append("├─ Top Processes ──────────────────────────────────┤")
    for p in d.get("processes", []):
        out.append(f"│  {p['pid']:5}  {p['name']:<22}  {p['cpu']:5.1f}%  {p['rss_mb']:5.0f}M │")

    out.append("╚══════════════════════════════════════════════════╝")
    return "\n".join(out)


def get_warnings(d):
    w = []
    cpu = d.get("cpu")
    if cpu and cpu["used"] > 80:
        w.append(f"CPU high: {cpu['used']:.1f}%")
    mem = d.get("memory")
    if mem and mem["pressure"] in ("medium", "high"):
        w.append(f"Memory pressure: {mem['pressure'].upper()} ({mem['free_gb']}G free)")
    disk = d.get("disk_space")
    if disk and disk["used_pct"] > 80:
        w.append(f"Disk: {disk['used_pct']}% used")
    if d.get("thermal_throttling"):
        w.append("Thermal throttling active")
    return w


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    fmt = "pretty"
    warnings_only = "--warnings-only" in args

    for i, a in enumerate(args):
        if a == "--format" and i + 1 < len(args):
            fmt = args[i + 1]
        elif a.startswith("--format="):
            fmt = a.split("=", 1)[1]

    d = collect()

    if fmt == "json":
        print(json.dumps(d, separators=(",", ":")))
    elif warnings_only:
        w = get_warnings(d)
        print("\n".join(w) if w else "✓ All systems nominal")
    else:
        print(format_pretty(d))


if __name__ == "__main__":
    main()

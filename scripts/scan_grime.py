#!/usr/bin/env python3
"""
OptimusGrime — scan_grime.py
Categorised junk scanner: shows what's safe to delete and why.
Inspired by CleanMyMac's named-category approach.

Usage:
  python3 scan_grime.py              # pretty summary
  python3 scan_grime.py --json       # JSON output for dashboard
  python3 scan_grime.py --clean all  # delete all safe categories (with confirmation)
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()

# ── Safe categories ──────────────────────────────────────────────────────────

CATEGORIES = [
    {
        "id":       "app_caches",
        "label":    "App Caches",
        "reason":   "Apps regenerate these automatically",
        "path":     HOME / "Library" / "Caches",
        "mode":     "dir_contents",   # scan subdirs, exclude com.apple.*
        "age_days": 0,   # show all non-Apple caches; age check only applied at deletion
        "safe":     True,
    },
    {
        "id":       "xcode_derived",
        "label":    "Xcode Build Artifacts",
        "reason":   "Fully regenerated on next Xcode build",
        "path":     HOME / "Library" / "Developer" / "Xcode" / "DerivedData",
        "mode":     "dir_contents",
        "age_days": 0,   # any age is safe
        "safe":     True,
    },
    {
        "id":       "system_tmp",
        "label":    "System Temp Files",
        "reason":   "Cleared on reboot; stragglers from crashed apps",
        "path":     Path("/private/tmp"),
        "mode":     "dir_contents",
        "age_days": 7,
        "safe":     True,
    },
    {
        "id":       "trash",
        "label":    "Trash",
        "reason":   "Files you already deleted",
        "path":     HOME / ".Trash",
        "mode":     "dir_total",
        "age_days": 0,
        "safe":     True,
    },
    {
        "id":       "broken_agents",
        "label":    "Broken Login Items",
        "reason":   "LaunchAgent plists pointing to apps that no longer exist",
        "path":     HOME / "Library" / "LaunchAgents",
        "mode":     "broken_plists",
        "age_days": 0,
        "safe":     True,
    },
]

# Prefixes that should never be touched
SYSTEM_PREFIXES = ("com.apple.", "com.AppleInternal.", "com.crashlytics.")


# ── Helpers ──────────────────────────────────────────────────────────────────

def dir_size_bytes(path: Path) -> int:
    """Recursively sum file sizes under path."""
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
            except (PermissionError, OSError):
                pass
    except (PermissionError, OSError):
        pass
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def is_open(path: Path) -> bool:
    """True if any file under path is held open by a process."""
    try:
        r = subprocess.run(
            ["lsof", "+D", str(path)],
            capture_output=True, text=True, timeout=5
        )
        return bool(r.stdout.strip())
    except Exception:
        return False


def age_days(path: Path) -> float:
    """Days since last modification."""
    import time
    try:
        return (time.time() - path.stat().st_mtime) / 86400
    except OSError:
        return 0


# ── Scanning logic ────────────────────────────────────────────────────────────

def scan_dir_contents(cat: dict) -> dict:
    """Scan immediate subdirs/files in path, respecting age_days and exclusions."""
    base = cat["path"]
    age_threshold = cat["age_days"]
    items = []
    total_bytes = 0

    if not base.exists():
        return {"bytes": 0, "count": 0, "items": [], "accessible": False}

    try:
        entries = list(base.iterdir())
    except PermissionError:
        return {"bytes": 0, "count": 0, "items": [], "accessible": False}

    for entry in entries:
        # Skip Apple system caches
        if any(entry.name.startswith(p) for p in SYSTEM_PREFIXES):
            continue
        # Skip hidden control files
        if entry.name.startswith("."):
            continue
        # Age filter (0 = no filter)
        if age_threshold > 0 and age_days(entry) < age_threshold:
            continue
        try:
            size = dir_size_bytes(entry) if entry.is_dir() else entry.stat().st_size
        except OSError:
            continue
        if size > 0:
            items.append({"path": str(entry), "name": entry.name, "bytes": size})
            total_bytes += size

    return {
        "bytes": total_bytes,
        "count": len(items),
        "items": sorted(items, key=lambda x: x["bytes"], reverse=True)[:10],
        "accessible": True,
    }


def scan_dir_total(cat: dict) -> dict:
    """Total size of the directory (Trash etc.)."""
    base = cat["path"]
    if not base.exists():
        return {"bytes": 0, "count": 0, "items": [], "accessible": True}
    try:
        entries = [e for e in base.iterdir() if not e.name.startswith(".")]
    except PermissionError:
        # Fall back to `du -sh` for directories we can't iterate (e.g. ~/.Trash)
        try:
            r = subprocess.run(
                ["du", "-sk", str(base)],
                capture_output=True, text=True, timeout=10
            )
            line = r.stdout.strip().split("\t")[0]
            kb = int(line) if line.isdigit() else 0
            return {"bytes": kb * 1024, "count": -1, "items": [], "accessible": True}
        except Exception:
            return {"bytes": 0, "count": 0, "items": [], "accessible": False}

    total = 0
    for e in entries:
        try:
            total += dir_size_bytes(e) if e.is_dir() else e.stat().st_size
        except OSError:
            pass
    return {
        "bytes": total,
        "count": len(entries),
        "items": [],
        "accessible": True,
    }


def scan_broken_plists(cat: dict) -> dict:
    """Find LaunchAgent plists that point to missing executables."""
    base = cat["path"]
    if not base.exists():
        return {"bytes": 0, "count": 0, "items": [], "accessible": True}

    broken = []
    try:
        for plist in base.glob("*.plist"):
            try:
                content = plist.read_text(errors="replace")
                # Look for ProgramArguments paths
                paths = re.findall(r"<string>(/[^<]+)</string>", content)
                for p in paths:
                    exe = Path(p.split()[0])
                    if not exe.exists():
                        broken.append({
                            "path": str(plist),
                            "name": plist.name,
                            "bytes": plist.stat().st_size,
                            "missing": str(exe),
                        })
                        break
            except (OSError, PermissionError):
                pass
    except (OSError, PermissionError):
        return {"bytes": 0, "count": 0, "items": [], "accessible": False}

    total = sum(i["bytes"] for i in broken)
    return {"bytes": total, "count": len(broken), "items": broken, "accessible": True}


def scan_category(cat: dict) -> dict:
    mode = cat["mode"]
    if mode == "dir_contents":
        result = scan_dir_contents(cat)
    elif mode == "dir_total":
        result = scan_dir_total(cat)
    elif mode == "broken_plists":
        result = scan_broken_plists(cat)
    else:
        result = {"bytes": 0, "count": 0, "items": [], "accessible": False}

    return {
        "id":          cat["id"],
        "label":       cat["label"],
        "reason":      cat["reason"],
        "safe":        cat["safe"],
        "bytes":       result["bytes"],
        "size":        human(result["bytes"]),
        "count":       result["count"],
        "items":       result.get("items", []),
        "accessible":  result.get("accessible", True),
    }


def scan_all():
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(scan_category, CATEGORIES))
    return results


# ── Deletion ─────────────────────────────────────────────────────────────────

def delete_category(cat_id: str, results: list, dry_run=False) -> int:
    """Delete files for a category. Returns bytes freed."""
    cat_result = next((r for r in results if r["id"] == cat_id), None)
    if not cat_result:
        return 0

    freed = 0
    for item in cat_result["items"]:
        p = Path(item["path"])
        if not p.exists():
            continue
        if is_open(p):
            print(f"  skipping (in use): {p.name}")
            continue
        if not dry_run:
            try:
                import shutil
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                freed += item["bytes"]
                print(f"  deleted: {p.name} ({human(item['bytes'])})")
            except OSError as e:
                print(f"  error: {p.name} — {e}")
        else:
            freed += item["bytes"]
            print(f"  would delete: {p.name} ({human(item['bytes'])})")

    return freed


# ── Output formatting ─────────────────────────────────────────────────────────

def format_pretty(results: list) -> str:
    lines = ["╔═ OptimusGrime Grime Scan ══════════════════════════╗"]
    total = sum(r["bytes"] for r in results)
    for r in results:
        bar_w = 20
        pct = r["bytes"] / total if total else 0
        filled = int(pct * bar_w)
        b = "█" * filled + "░" * (bar_w - filled)
        acc = "" if r["accessible"] else " (no access)"
        lines.append(f"│  {r['label']:<24} {r['size']:>8}  [{b}]{acc}")
        lines.append(f"│    ↳ {r['reason']}")
    lines.append(f"├────────────────────────────────────────────────────┤")
    lines.append(f"│  Total recoverable: {human(total):>10}                  │")
    lines.append("╚════════════════════════════════════════════════════╝")
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    use_json = "--json" in args

    results = scan_all()

    if use_json:
        # Strip large items list for compactness
        compact = [{k: v for k, v in r.items() if k != "items"} for r in results]
        print(json.dumps(compact, separators=(",", ":")))
    else:
        print(format_pretty(results))


if __name__ == "__main__":
    main()

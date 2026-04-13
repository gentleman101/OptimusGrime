#!/usr/bin/env python3
"""
OptimusGrime — get_tabs.py
Cross-platform browser tab lister.

- macOS:   delegates to get_tabs.scpt via osascript (live Chrome + Safari tabs)
- Windows: reads Chrome History SQLite (recently visited URLs)

Output: JSON — list of tab objects on macOS, or {windows_mode, history} on Windows.

Usage:
  python3 scripts/get_tabs.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLATFORM = sys.platform
HOME     = Path.home()
CHROME_EPOCH_OFFSET = 11644473600  # seconds between 1601-01-01 and 1970-01-01


# ── macOS ─────────────────────────────────────────────────────────────────────

def _get_tabs_darwin() -> list:
    """Run get_tabs.scpt via osascript. Returns tab list or permission dict."""
    script = Path(__file__).parent / "get_tabs.scpt"
    try:
        r = subprocess.run(
            ["osascript", str(script)],
            capture_output=True, text=True, timeout=10
        )
        stderr = r.stderr.lower()
        if "-1743" in stderr or "not authorized" in stderr or "not allowed" in stderr:
            return {"permission": "denied"}
        raw = r.stdout.strip()
        if not raw:
            return []
        return json.loads(raw)
    except Exception:
        return []


# ── Windows ───────────────────────────────────────────────────────────────────

def _chrome_history_path_win32() -> Path | None:
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    candidates = [
        Path(local)   / "Google/Chrome/User Data/Default/History",
        Path(local)   / "Google/Chrome/User Data/Profile 1/History",
        Path(roaming) / "Google/Chrome/User Data/Default/History",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _get_tabs_win32(limit=50) -> list:
    """Return recently visited Chrome URLs from History SQLite."""
    hist = _chrome_history_path_win32()
    if not hist:
        return []
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        shutil.copy2(hist, tmp.name)
        conn = sqlite3.connect(tmp.name)
        cur = conn.cursor()
        cur.execute(
            "SELECT url, title, last_visit_time, visit_count "
            "FROM urls ORDER BY last_visit_time DESC LIMIT ?",
            (limit,)
        )
        rows = cur.fetchall()
        conn.close()
        os.unlink(tmp.name)
    except Exception:
        return []

    now = time.time()
    tabs = []
    for url, title, lvt, visit_count in rows:
        epoch     = lvt / 1_000_000 - CHROME_EPOCH_OFFSET if lvt else 0
        hours_ago = (now - epoch) / 3600 if epoch else 9999
        tabs.append({
            "browser":     "Chrome",
            "source":      "history",
            "title":       title or url,
            "url":         url,
            "window":      0,
            "index":       0,
            "hours_ago":   round(hours_ago, 1),
            "visit_count": visit_count,
        })
    return tabs


# ── Dispatcher ────────────────────────────────────────────────────────────────

def get_tabs():
    if PLATFORM == "darwin":
        return _get_tabs_darwin()
    if PLATFORM == "win32":
        return {"windows_mode": True, "history": _get_tabs_win32()}
    return []


if __name__ == "__main__":
    print(json.dumps(get_tabs(), separators=(",", ":")))

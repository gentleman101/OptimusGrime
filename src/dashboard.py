#!/usr/bin/env python3
"""
OptimusGrime — dashboard.py
Local browser dashboard. Starts HTTP server on localhost:7777.

Usage:
  python3 src/dashboard.py           # start server + open browser
  python3 src/dashboard.py --no-open # start server only
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Add scripts/ to path so we can reuse health_check + scan_grime logic
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import health_check as hc
import scan_grime as sg

HOME = Path.home()
PORT = 7777

# ── Tab intelligence ──────────────────────────────────────────────────────────

SOCIAL_DOMAINS = {
    "reddit.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "tiktok.com", "linkedin.com", "news.ycombinator.com", "lobste.rs",
    "producthunt.com", "discord.com", "slack.com", "youtube.com",
}

GENERIC_TITLES = {"new tab", "untitled", "blank page", ""}


def chrome_history_path():
    candidates = [
        HOME / "Library/Application Support/Google/Chrome/Default/History",
        HOME / "Library/Application Support/Chromium/Default/History",
        HOME / "Library/Application Support/Google/Chrome/Profile 1/History",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def get_chrome_last_visits(urls: list) -> dict:
    """Query Chrome History.db for last_visit_time per URL. Returns {url: epoch_seconds}."""
    hist = chrome_history_path()
    if not hist:
        return {}
    # Copy DB to temp to avoid locking issues
    import shutil, tempfile
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        shutil.copy2(hist, tmp.name)
        conn = sqlite3.connect(tmp.name)
        cur = conn.cursor()
        # Chrome stores time as microseconds since 1601-01-01
        CHROME_EPOCH_OFFSET = 11644473600  # seconds between 1601 and 1970
        result = {}
        for url in urls:
            try:
                cur.execute(
                    "SELECT last_visit_time FROM urls WHERE url=? ORDER BY last_visit_time DESC LIMIT 1",
                    (url,)
                )
                row = cur.fetchone()
                if row and row[0]:
                    epoch = row[0] / 1_000_000 - CHROME_EPOCH_OFFSET
                    result[url] = epoch
            except Exception:
                pass
        conn.close()
        os.unlink(tmp.name)
        return result
    except Exception:
        return {}


def score_tab(tab: dict, last_visits: dict) -> dict:
    """Add inactivity score and suggestion flag to a tab dict."""
    url = tab.get("url", "")
    title = tab.get("title", "").strip().lower()
    browser = tab.get("browser", "")

    score = 0
    reasons = []

    # Signal 1: last visit time (Chrome only, high weight)
    lv = last_visits.get(url)
    if lv:
        hours_ago = (time.time() - lv) / 3600
        if hours_ago > 2:
            score += 3
            reasons.append(f"last visited {hours_ago:.0f}h ago")
        elif hours_ago > 0.5:
            score += 1

    # Signal 2: generic title (medium weight)
    if title in GENERIC_TITLES or title.startswith("loading"):
        score += 2
        reasons.append("generic title")

    # Signal 3: social/news domain (low weight)
    try:
        domain = urlparse(url).netloc.lstrip("www.")
    except Exception:
        domain = ""
    if any(domain.endswith(s) for s in SOCIAL_DOMAINS):
        score += 1
        reasons.append("social/news site")

    tab["score"] = score
    tab["suggest_close"] = score >= 2
    tab["reasons"] = reasons
    return tab


def check_automation_permission() -> str:
    """
    Returns 'granted', 'denied', or 'unknown'.
    Runs a harmless AppleScript that requires Automation permission.
    Error -1743 = not authorised; error -600 = app not running (permission exists but app closed).
    """
    test = 'tell application "System Events" to return name of first process whose frontmost is true'
    try:
        r = subprocess.run(
            ["osascript", "-e", test],
            capture_output=True, text=True, timeout=5
        )
        stderr = r.stderr.lower()
        if "-1743" in stderr or "not authorized" in stderr or "not allowed" in stderr:
            return "denied"
        return "granted"
    except Exception:
        return "unknown"


def get_tabs():
    """Get tabs from running browsers, scored for inactivity.
    Returns list of tab dicts, or {"permission": "denied"} if Automation is blocked.
    """
    script = Path(__file__).parent.parent / "scripts" / "get_tabs.scpt"
    try:
        r = subprocess.run(
            ["osascript", str(script)],
            capture_output=True, text=True, timeout=10
        )
        stderr = r.stderr.lower()

        # Detect Automation permission denied (error -1743)
        if "-1743" in stderr or "not authorized" in stderr or "not allowed" in stderr:
            return {"permission": "denied"}

        raw = r.stdout.strip()
        if not raw:
            # Check if permission is the silent cause
            if check_automation_permission() == "denied":
                return {"permission": "denied"}
            return []

        tabs = json.loads(raw)
    except Exception:
        return []

    # Enrich Chrome tabs with history
    chrome_urls = [t["url"] for t in tabs if t.get("browser") == "Chrome"]
    last_visits = get_chrome_last_visits(chrome_urls) if chrome_urls else {}

    return [score_tab(t, last_visits) for t in tabs]


# ── Process hog detection ────────────────────────────────────────────────────

BROWSER_NAMES = {"safari", "chrome", "google chrome", "chromium", "firefox", "arc", "opera"}
SYSTEM_NAMES  = {"windowserver", "kernel_task", "launchd", "mds", "mds_stores", "mdworker",
                 "spotlight", "coreaudiod", "loginwindow", "distnoted", "cfprefsd"}


def get_hog_processes(n=3):
    """Top n background processes eating CPU that user didn't intentionally open."""
    procs = hc.get_top_processes(10)
    hogs = []
    for p in procs:
        name_lower = p["name"].lower()
        if any(b in name_lower for b in BROWSER_NAMES):
            continue
        if any(s in name_lower for s in SYSTEM_NAMES):
            continue
        if p["cpu"] < 1.0:
            continue
        hogs.append(p)
        if len(hogs) >= n:
            break
    return hogs


# ── Snapshot for "Ask Claude" ─────────────────────────────────────────────────

def build_snapshot_prompt(stats, tabs, grime):
    lines = [
        "## OptimusGrime System Snapshot",
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "### System Health",
    ]
    cpu = stats.get("cpu") or {}
    mem = stats.get("memory") or {}
    disk = stats.get("disk_space") or {}
    io = stats.get("disk_io") or {}

    lines.append(f"- CPU: {cpu.get('used', '?')}% (user {cpu.get('user','?')}%, sys {cpu.get('sys','?')}%)")
    lines.append(f"- Memory: {mem.get('used_gb','?')}G used / {mem.get('free_gb','?')}G free — pressure: {mem.get('pressure','?')}")
    lines.append(f"- Disk: {disk.get('used_pct','?')}% used ({disk.get('used','?')} of {disk.get('total','?')})")
    lines.append(f"- Disk I/O: {io.get('mbps','?')} MB/s")
    if stats.get("thermal_throttling"):
        lines.append("- ⚠ THERMAL THROTTLING ACTIVE")

    procs = stats.get("processes", [])
    if procs:
        lines += ["", "### Top Processes"]
        for p in procs:
            lines.append(f"- {p['name']} (PID {p['pid']}): {p['cpu']}% CPU, {p['rss_mb']}MB RAM")

    hogs = get_hog_processes()
    if hogs:
        lines += ["", "### Background CPU Hogs (non-browser, non-system)"]
        for p in hogs:
            lines.append(f"- {p['name']} (PID {p['pid']}): {p['cpu']}% CPU — safe to kill?")

    if tabs:
        lines += ["", "### Open Browser Tabs"]
        suggested = [t for t in tabs if t.get("suggest_close")]
        lines.append(f"Total: {len(tabs)} tabs. Suggested to close: {len(suggested)}")
        for t in suggested[:5]:
            lines.append(f"- [{t['browser']}] {t['title'][:60]} — {', '.join(t.get('reasons', []))}")

    if grime:
        total = sum(g["bytes"] for g in grime)
        lines += ["", f"### Disk Grime (total: {sg.human(total)})"]
        for g in grime:
            if g["bytes"] > 0:
                lines.append(f"- {g['label']}: {g['size']} — {g['reason']}")

    lines += ["", "---", "Please analyze this snapshot and tell me:",
              "1. What is slowing my Mac right now?",
              "2. Which processes are safe to kill?",
              "3. What should I clean up first?"]

    return "\n".join(lines)


# ── HTTP API ──────────────────────────────────────────────────────────────────

_stats_cache = {"data": None, "ts": 0}
_grime_cache = {"data": None, "ts": 0}


def api_stats():
    now = time.time()
    if now - _stats_cache["ts"] < 3:
        return _stats_cache["data"]
    try:
        data = hc.collect()
        data["hogs"] = get_hog_processes()
        _stats_cache.update({"data": data, "ts": now})
        return data
    except Exception as e:
        return {"error": str(e)}


def api_tabs():
    try:
        return get_tabs()
    except Exception as e:
        return {"error": str(e)}


def api_grime():
    now = time.time()
    if now - _grime_cache["ts"] < 60:
        return _grime_cache["data"]
    try:
        results = sg.scan_all()
        compact = [{k: v for k, v in r.items() if k != "items"} for r in results]
        _grime_cache.update({"data": compact, "ts": now})
        return compact
    except Exception as e:
        return {"error": str(e)}


def api_snapshot():
    stats = api_stats()
    tabs  = api_tabs()
    grime = api_grime()
    if isinstance(tabs, dict):  tabs = []
    if isinstance(grime, dict): grime = []
    return {"prompt": build_snapshot_prompt(stats, tabs, grime)}


def api_kill(pid: int):
    try:
        os.kill(pid, 15)  # SIGTERM
        time.sleep(0.5)
        try:
            os.kill(pid, 0)   # check still alive
            os.kill(pid, 9)   # SIGKILL if needed
        except ProcessLookupError:
            pass
        return {"ok": True, "pid": pid}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def api_close_tabs(browser: str):
    """Close all suggested tabs in browser via AppleScript."""
    try:
        tabs = get_tabs()
        to_close = [t for t in tabs if t.get("suggest_close") and t.get("browser") == browser]
        if not to_close:
            return {"ok": True, "closed": 0}

        if browser == "Chrome":
            for t in reversed(to_close):
                script = f'''
                tell application "Google Chrome"
                    set w to window {t["window"]}
                    close tab {t["index"]} of w
                end tell
                '''
                subprocess.run(["osascript", "-e", script], timeout=5, capture_output=True)
        elif browser == "Safari":
            for t in reversed(to_close):
                script = f'''
                tell application "Safari"
                    set w to window {t["window"]}
                    close tab {t["index"]} of w
                end tell
                '''
                subprocess.run(["osascript", "-e", script], timeout=5, capture_output=True)

        return {"ok": True, "closed": len(to_close)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def api_clean(cat_id: str):
    """Delete files in a grime category."""
    try:
        cat = next((c for c in sg.CATEGORIES if c["id"] == cat_id), None)
        if not cat:
            return {"ok": False, "error": "unknown category"}
        results = sg.scan_all()
        freed = sg.delete_category(cat_id, results, dry_run=False)
        _grime_cache["ts"] = 0  # invalidate cache
        return {"ok": True, "freed": freed, "freed_human": sg.human(freed)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── HTML Frontend ─────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OptimusGrime</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:       #0d0d0d;
    --surface:  #161616;
    --border:   #2a2a2a;
    --text:     #f0f0f0;
    --muted:    #888;
    --blue:     #3b82f6;
    --green:    #22c55e;
    --amber:    #f59e0b;
    --red:      #ef4444;
    --radius:   12px;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 13px;
    min-height: 100vh;
    padding: 24px;
  }

  /* ── Header ── */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 24px;
  }
  .logo { font-size: 18px; font-weight: 700; letter-spacing: -0.5px; }
  .logo span { color: var(--blue); }
  .live-dot {
    display: inline-block; width: 8px; height: 8px;
    background: var(--green); border-radius: 50%; margin-right: 6px;
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%,100% { opacity: 1; box-shadow: 0 0 0 0 rgba(34,197,94,.4); }
    50%      { opacity: .8; box-shadow: 0 0 0 5px rgba(34,197,94,0); }
  }
  .status-bar { color: var(--muted); font-size: 11px; }

  /* ── Alert banner ── */
  .alert-banner {
    display: none;
    background: rgba(239,68,68,.1); border: 1px solid rgba(239,68,68,.3);
    border-radius: var(--radius); padding: 10px 16px; margin-bottom: 16px;
    color: var(--red); font-size: 12px;
  }
  .alert-banner.show { display: flex; align-items: center; gap: 8px; }

  /* ── Cards ── */
  .card {
    background: rgba(22,22,22,.9); backdrop-filter: blur(12px);
    border: 1px solid var(--border); border-radius: var(--radius);
    padding: 16px 20px; transition: transform .2s, box-shadow .2s;
    position: relative; overflow: hidden;
  }
  .card:hover { transform: translateY(-2px); box-shadow: 0 8px 32px rgba(0,0,0,.4); }
  .card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0;
    height: 3px; border-radius: var(--radius) var(--radius) 0 0;
    background: var(--card-accent, #3b82f6);
    opacity: 0.7;
  }
  .card-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
  .card-label { font-size: 11px; color: var(--muted); text-transform: uppercase;
                letter-spacing: .8px; }
  .card-icon  { font-size: 16px; opacity: .7; }
  .card-value { font-family: "SF Mono", "Fira Code", monospace; font-size: 32px;
                font-weight: 700; line-height: 1; margin-bottom: 2px; }
  .card-sub   { font-size: 11px; color: var(--muted); margin-top: 4px; }

  /* ── Top bar grid ── */
  .top-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin-bottom: 24px;
  }

  /* ── Sparkline ── */
  .sparkline { width: 100%; height: 50px; margin-top: 8px; }
  .sparkline-svg { width: 100%; height: 100%; overflow: visible; }

  /* ── Gauge ── */
  .gauge-wrap { display: flex; justify-content: center; margin-top: 8px; }
  .gauge { width: 80px; height: 44px; }

  /* ── Pressure badge ── */
  .pressure {
    display: inline-block; padding: 2px 8px; border-radius: 20px;
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .5px;
    margin-top: 6px;
  }
  .pressure.low    { background: rgba(34,197,94,.15);  color: var(--green); }
  .pressure.medium { background: rgba(245,158,11,.15); color: var(--amber); }
  .pressure.high   { background: rgba(239,68,68,.15);  color: var(--red);   }
  .pressure.unknown{ background: rgba(136,136,136,.15);color: var(--muted); }

  /* ── Bottom 3-col grid ── */
  .bottom-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
  }
  @media (max-width: 900px) {
    .top-grid    { grid-template-columns: repeat(2, 1fr); }
    .bottom-grid { grid-template-columns: 1fr; }
  }

  /* ── Section headers ── */
  .section-title {
    font-size: 12px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1px;
    margin-bottom: 16px; padding: 8px 12px;
    border-radius: 6px; display: flex; align-items: center; gap: 8px;
  }
  .section-title.red   { color: #ff6b6b; background: rgba(239,68,68,.08); border-left: 3px solid #ef4444; }
  .section-title.blue  { color: #60a5fa; background: rgba(59,130,246,.08); border-left: 3px solid #3b82f6; }
  .section-title.amber { color: #fbbf24; background: rgba(245,158,11,.08); border-left: 3px solid #f59e0b; }

  /* ── Process rows ── */
  .proc-row {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 0; border-bottom: 1px solid rgba(42,42,42,.5);
  }
  .proc-row:last-child { border-bottom: none; }
  .proc-info   { flex: 1; min-width: 0; }
  .proc-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
  .proc-name   { font-size: 12px; font-weight: 500; font-family: "SF Mono","Fira Code",monospace;
                 white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 160px; }
  .proc-stats  { font-size: 11px; color: var(--muted); white-space: nowrap; }
  .proc-bars   { display: flex; flex-direction: column; gap: 3px; }
  .bar-track   { height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }
  .bar-fill    { height: 100%; border-radius: 2px; transition: width .4s ease; }
  .bar-cpu     { background: var(--blue); }
  .bar-mem     { background: rgba(139,92,246,.7); }

  .kill-btn {
    flex-shrink: 0;
    background: rgba(239,68,68,.12); border: 1px solid rgba(239,68,68,.4);
    color: #ff6b6b; font-size: 12px; font-weight: 600; padding: 6px 16px;
    border-radius: 8px; cursor: pointer; transition: background .15s, box-shadow .15s, transform .1s;
    letter-spacing: .3px; white-space: nowrap;
  }
  .kill-btn:hover {
    background: rgba(239,68,68,.25); box-shadow: 0 0 14px rgba(239,68,68,.4);
    transform: scale(1.05);
  }

  /* ── Tab rows ── */
  .tab-summary { display: flex; gap: 8px; margin-bottom: 12px; }
  .pill {
    padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 500;
    background: var(--border);
  }
  .tab-row {
    padding: 7px 0; border-bottom: 1px solid rgba(42,42,42,.5);
    position: relative; cursor: default;
  }
  .tab-row:last-child { border-bottom: none; }
  .tab-browser { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; }
  .tab-title   { font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
                 max-width: calc(100% - 70px); margin: 2px 0; }
  .tab-tag {
    display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 4px;
    background: rgba(245,158,11,.15); color: var(--amber); margin-top: 2px;
  }
  .close-btn {
    display: block; position: absolute; right: 0; top: 50%; transform: translateY(-50%);
    background: rgba(239,68,68,.12); border: 1px solid rgba(239,68,68,.4);
    color: #ff6b6b; font-size: 12px; font-weight: 600; padding: 5px 14px;
    border-radius: 8px; cursor: pointer; transition: background .15s, box-shadow .15s, transform .1s;
    letter-spacing: .3px;
  }
  .close-btn:hover {
    background: rgba(239,68,68,.25); box-shadow: 0 0 14px rgba(239,68,68,.4);
    transform: translateY(-50%) scale(1.05);
  }

  .action-bar { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
  .btn {
    padding: 6px 14px; border-radius: 8px; font-size: 12px; font-weight: 500;
    cursor: pointer; transition: background .15s, box-shadow .15s; border: none;
  }
  .btn-danger {
    background: rgba(239,68,68,.1); border: 1px solid rgba(239,68,68,.3); color: var(--red);
  }
  .btn-danger:hover { background: rgba(239,68,68,.2); box-shadow: 0 0 10px rgba(239,68,68,.25); }
  .btn-muted {
    background: var(--surface); border: 1px solid var(--border); color: var(--muted);
  }
  .btn-muted:hover { border-color: var(--text); color: var(--text); }

  /* ── Grime rows ── */
  .grime-row { padding: 8px 0; border-bottom: 1px solid rgba(42,42,42,.5); }
  .grime-row:last-child { border-bottom: none; }
  .grime-header { display: flex; justify-content: space-between; align-items: center; }
  .grime-label  { font-size: 12px; font-weight: 500; }
  .grime-size   { font-family: "SF Mono","Fira Code",monospace; font-size: 12px; color: var(--muted); }
  .grime-reason { font-size: 11px; color: var(--muted); margin: 3px 0 5px; }
  .grime-bar-track { height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }
  .grime-bar-fill  { height: 100%; border-radius: 2px; transition: width .5s ease;
                     background: linear-gradient(90deg, var(--amber), var(--red)); }
  .clean-btn {
    font-size: 11px; padding: 2px 8px; border-radius: 6px; cursor: pointer;
    background: rgba(245,158,11,.1); border: 1px solid rgba(245,158,11,.3); color: var(--amber);
    transition: background .15s;
  }
  .clean-btn:hover { background: rgba(245,158,11,.2); }

  /* ── Vibe error tile ── */
  .vibe-tile {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    min-height: 120px; gap: 10px; opacity: .6; cursor: pointer;
  }
  .vibe-tile:hover { opacity: .9; }
  .vibe-emoji { font-size: 28px; }
  .vibe-text  { font-size: 12px; color: var(--muted); text-align: center; }

  /* ── Toast ── */
  .toast {
    position: fixed; bottom: 80px; right: 24px;
    background: var(--surface); border: 1px solid var(--border);
    padding: 10px 18px; border-radius: 10px; font-size: 12px;
    opacity: 0; transform: translateY(10px);
    transition: opacity .3s, transform .3s; pointer-events: none; z-index: 100;
  }
  .toast.show { opacity: 1; transform: translateY(0); }

  /* ── Modal ── */
  .modal-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,.7); backdrop-filter: blur(4px);
    z-index: 50; align-items: center; justify-content: center;
  }
  .modal-overlay.show { display: flex; }
  .modal {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 16px; padding: 24px; max-width: 400px; width: 90%;
    animation: modalIn .2s ease;
  }
  @keyframes modalIn { from { opacity:0; transform:scale(.95); } to { opacity:1; transform:scale(1); } }
  .modal-title { font-size: 15px; font-weight: 600; margin-bottom: 8px; }
  .modal-body  { font-size: 13px; color: var(--muted); margin-bottom: 20px; line-height: 1.5; }
  .modal-actions { display: flex; gap: 10px; justify-content: flex-end; }

  /* ── Ask Claude ── */
  .ask-claude {
    position: fixed; bottom: 24px; right: 24px; z-index: 40;
    display: flex; align-items: center; gap: 8px;
    background: var(--surface); border: 1px solid var(--border);
    padding: 10px 18px; border-radius: 40px; cursor: pointer;
    font-size: 13px; font-weight: 500;
    transition: border-color .2s, box-shadow .2s;
  }
  .ask-claude:hover { border-color: var(--blue); box-shadow: 0 0 20px rgba(59,130,246,.2); }
  .ask-claude-icon { font-size: 16px; }
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="logo">Optimus<span>Grime</span></div>
  <div class="status-bar">
    <span class="live-dot"></span>
    <span id="status-text">Connecting…</span>
  </div>
</div>

<!-- Thermal alert -->
<div class="alert-banner" id="thermal-alert">
  <span>⚠</span>
  <span>Thermal throttling active — your Mac is slowing itself down to cool off</span>
</div>

<!-- Top 4 stat cards -->
<div class="top-grid">
  <!-- CPU -->
  <div class="card" id="card-cpu" style="--card-accent:#3b82f6">
    <div class="card-header">
      <span class="card-label">CPU</span>
      <span class="card-icon">⚡</span>
    </div>
    <div class="card-value" id="cpu-val">—</div>
    <div class="card-sub" id="cpu-sub">user · sys · idle</div>
    <svg class="sparkline sparkline-svg" id="spark-cpu" viewBox="0 0 200 50" preserveAspectRatio="none">
      <defs>
        <linearGradient id="grad-cpu" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#3b82f6" stop-opacity=".5"/>
          <stop offset="100%" stop-color="#3b82f6" stop-opacity="0"/>
        </linearGradient>
      </defs>
    </svg>
  </div>

  <!-- Memory -->
  <div class="card" id="card-mem" style="--card-accent:#a855f7">
    <div class="card-header">
      <span class="card-label">Memory</span>
      <span class="card-icon">🧠</span>
    </div>
    <div class="card-value" id="mem-val">—</div>
    <div class="card-sub" id="mem-sub">used · free</div>
    <span class="pressure unknown" id="mem-pressure">—</span>
    <svg class="sparkline sparkline-svg" id="spark-mem" viewBox="0 0 200 50" preserveAspectRatio="none">
      <defs>
        <linearGradient id="grad-mem" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#a855f7" stop-opacity=".5"/>
          <stop offset="100%" stop-color="#a855f7" stop-opacity="0"/>
        </linearGradient>
      </defs>
    </svg>
  </div>

  <!-- Disk I/O -->
  <div class="card" id="card-io" style="--card-accent:#22c55e">
    <div class="card-header">
      <span class="card-label">Disk I/O</span>
      <span class="card-icon">💾</span>
    </div>
    <div class="card-value" id="io-val">—</div>
    <div class="card-sub">MB/s activity</div>
    <svg class="sparkline sparkline-svg" id="spark-io" viewBox="0 0 200 50" preserveAspectRatio="none">
      <defs>
        <linearGradient id="grad-io" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#22c55e" stop-opacity=".5"/>
          <stop offset="100%" stop-color="#22c55e" stop-opacity="0"/>
        </linearGradient>
      </defs>
    </svg>
  </div>

  <!-- Disk Space -->
  <div class="card" id="card-disk" style="--card-accent:#f59e0b">
    <div class="card-header">
      <span class="card-label">Disk Space</span>
      <span class="card-icon">🗄</span>
    </div>
    <div class="card-value" id="disk-val">—</div>
    <div class="card-sub" id="disk-sub">used</div>
    <div class="gauge-wrap">
      <svg class="gauge" viewBox="0 0 80 44">
        <path d="M10,40 A30,30 0 0,1 70,40" fill="none" stroke="#2a2a2a" stroke-width="7" stroke-linecap="round"/>
        <path id="disk-arc" d="M10,40 A30,30 0 0,1 70,40" fill="none" stroke="#3b82f6"
              stroke-width="7" stroke-linecap="round"
              stroke-dasharray="94.2" stroke-dashoffset="94.2"
              style="transition:stroke-dashoffset .6s ease,stroke .3s"/>
        <text x="40" y="38" text-anchor="middle" fill="#f0f0f0"
              font-size="11" font-family="SF Mono,Fira Code,monospace" id="disk-arc-label">0%</text>
      </svg>
    </div>
  </div>
</div>

<!-- Bottom 3 sections -->
<div class="bottom-grid">

  <!-- Section 1: CPU Hogs -->
  <div class="card" id="section-hogs">
    <div class="section-title red">⚡ Background CPU Hogs</div>
    <div id="hogs-content">
      <div class="vibe-tile" onclick="retrySection('hogs')">
        <div class="vibe-emoji">🤙</div>
        <div class="vibe-text">Vibe coding didn't vibe much</div>
      </div>
    </div>
  </div>

  <!-- Section 2: Browser Tabs -->
  <div class="card" id="section-tabs">
    <div class="section-title blue">🌐 Browser Tabs</div>
    <div id="tabs-content">
      <div class="vibe-tile" onclick="retrySection('tabs')">
        <div class="vibe-emoji">🤙</div>
        <div class="vibe-text">Vibe coding didn't vibe much</div>
      </div>
    </div>
  </div>

  <!-- Section 3: Smart Cleanup -->
  <div class="card" id="section-grime">
    <div class="section-title amber">🧹 Smart Cleanup</div>
    <div id="grime-content">
      <div class="vibe-tile" onclick="retrySection('grime')">
        <div class="vibe-emoji">🤙</div>
        <div class="vibe-text">Vibe coding didn't vibe much</div>
      </div>
    </div>
  </div>

</div>

<!-- Ask Claude button -->
<div class="ask-claude" onclick="askClaude()">
  <span class="ask-claude-icon">✦</span>
  Ask Claude
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<!-- Confirm Modal -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <div class="modal-title" id="modal-title">Confirm</div>
    <div class="modal-body"  id="modal-body">Are you sure?</div>
    <div class="modal-actions">
      <button class="btn btn-muted" onclick="closeModal()">Cancel</button>
      <button class="btn btn-danger" id="modal-confirm">Confirm</button>
    </div>
  </div>
</div>

<script>
// ── Sparkline data buffers ───────────────────────────────────────────────────
const MAX_PTS = 60;
const buffers = { cpu: [], mem: [], io: [] };

function pushBuf(key, val) {
  buffers[key].push(val);
  if (buffers[key].length > MAX_PTS) buffers[key].shift();
}

function polyline(buf, svgId, gradId, maxVal) {
  const svg = document.getElementById(svgId);
  if (!svg) return;
  // Remove old elements
  svg.querySelectorAll('.spark-area,.spark-line').forEach(e => e.remove());
  if (buf.length < 2) return;

  const W = 200, H = 50, pad = 2;
  const n = buf.length;
  const max = maxVal || Math.max(...buf, 1);

  const pts = buf.map((v, i) => {
    const x = (i / (MAX_PTS - 1)) * (W - pad * 2) + pad;
    const y = H - pad - (v / max) * (H - pad * 2);
    return `${x},${y}`;
  });

  const closedPts = pts.join(' ') +
    ` ${(n-1)/(MAX_PTS-1)*(W-pad*2)+pad},${H} ${pad},${H}`;

  const area = document.createElementNS('http://www.w3.org/2000/svg','polygon');
  area.setAttribute('points', closedPts);
  area.setAttribute('fill', `url(#${gradId})`);
  area.setAttribute('class', 'spark-area');

  const line = document.createElementNS('http://www.w3.org/2000/svg','polyline');
  line.setAttribute('points', pts.join(' '));
  line.setAttribute('fill', 'none');
  line.setAttribute('stroke', gradId === 'grad-cpu' ? '#3b82f6' :
                              gradId === 'grad-mem' ? '#a855f7' : '#22c55e');
  line.setAttribute('stroke-width', '1.5');
  line.setAttribute('class', 'spark-line');

  svg.appendChild(area);
  svg.appendChild(line);
}

function updateArc(pct) {
  const arc = document.getElementById('disk-arc');
  const lbl = document.getElementById('disk-arc-label');
  if (!arc) return;
  // Arc circumference of the half-circle is ~94.2
  const circ = 94.2;
  const offset = circ - (pct / 100) * circ;
  arc.style.strokeDashoffset = offset;
  arc.style.stroke = pct > 90 ? '#ef4444' : pct > 70 ? '#f59e0b' : '#3b82f6';
  if (lbl) lbl.textContent = pct + '%';
}

// ── Colour helpers ───────────────────────────────────────────────────────────
function cpuColor(v) { return v > 90 ? '#ef4444' : v > 70 ? '#f59e0b' : '#3b82f6'; }
function memColor(p) { return p === 'high' ? '#ef4444' : p === 'medium' ? '#f59e0b' : '#22c55e'; }

// ── Stats polling ────────────────────────────────────────────────────────────
let lastUpdate = 0;

async function fetchStats() {
  try {
    const r = await fetch('/api/stats');
    if (!r.ok) throw new Error('bad response');
    const d = await r.json();

    // CPU
    const cpuUsed = d.cpu?.used ?? 0;
    pushBuf('cpu', cpuUsed);
    document.getElementById('cpu-val').textContent = cpuUsed.toFixed(1) + '%';
    document.getElementById('cpu-val').style.color = cpuColor(cpuUsed);
    document.getElementById('cpu-sub').textContent =
      `${(d.cpu?.user??0).toFixed(1)}% usr · ${(d.cpu?.sys??0).toFixed(1)}% sys`;
    polyline(buffers.cpu, 'spark-cpu', 'grad-cpu', 100);

    // Memory
    const memUsed = d.memory?.used_gb ?? 0;
    const memFree = d.memory?.free_gb ?? 0;
    const pressure = d.memory?.pressure ?? 'unknown';
    pushBuf('mem', memUsed);
    document.getElementById('mem-val').textContent = memUsed.toFixed(1) + 'G';
    document.getElementById('mem-sub').textContent = `${memFree.toFixed(1)}G free · ${(d.memory?.total_gb??16)}G total`;
    const mp = document.getElementById('mem-pressure');
    mp.textContent = pressure.toUpperCase();
    mp.className = `pressure ${pressure}`;
    polyline(buffers.mem, 'spark-mem', 'grad-mem', d.memory?.total_gb ?? 16);

    // Disk I/O
    const mbps = d.disk_io?.mbps ?? 0;
    pushBuf('io', mbps);
    document.getElementById('io-val').textContent = mbps.toFixed(1) + ' MB/s';
    polyline(buffers.io, 'spark-io', 'grad-io', null);

    // Disk space
    const dpct = d.disk_space?.used_pct ?? 0;
    document.getElementById('disk-val').textContent = d.disk_space?.used ?? '—';
    document.getElementById('disk-sub').textContent =
      `of ${d.disk_space?.total ?? '?'} (${dpct}% used)`;
    updateArc(dpct);

    // Thermal throttle banner
    document.getElementById('thermal-alert').classList.toggle('show', !!d.thermal_throttling);

    // Hogs
    renderHogs(d.hogs ?? []);

    lastUpdate = Date.now();
    document.getElementById('status-text').textContent = 'Live';

  } catch (e) {
    document.getElementById('status-text').textContent = 'Error — retrying…';
  }
}

// ── Hogs section ─────────────────────────────────────────────────────────────
function renderHogs(hogs) {
  const el = document.getElementById('hogs-content');
  if (!hogs || hogs.length === 0) {
    el.innerHTML = `<div style="color:var(--muted);font-size:12px;padding:12px 0;text-align:center">
      <div style="font-size:20px;margin-bottom:6px">✓</div>No background hogs detected</div>`;
    return;
  }
  const maxCpu = Math.max(...hogs.map(p => p.cpu), 1);
  el.innerHTML = hogs.map(p => `
    <div class="proc-row" data-pid="${p.pid}">
      <div class="proc-info">
        <div class="proc-header">
          <span class="proc-name">${esc(p.name)}</span>
          <span class="proc-stats">${p.cpu.toFixed(1)}% · ${p.rss_mb}M</span>
        </div>
        <div class="proc-bars">
          <div class="bar-track"><div class="bar-fill bar-cpu" style="width:${(p.cpu/maxCpu*100).toFixed(1)}%"></div></div>
          <div class="bar-track"><div class="bar-fill bar-mem" style="width:${Math.min(p.mem,100).toFixed(1)}%"></div></div>
        </div>
      </div>
      <button class="kill-btn" onclick="confirmKill(${p.pid},'${esc(p.name)}')">Kill</button>
    </div>`).join('');
}

// ── Tabs section ──────────────────────────────────────────────────────────────
let tabsLoaded = false;
async function fetchTabs() {
  try {
    const r = await fetch('/api/tabs');
    if (!r.ok) throw new Error();
    const tabs = await r.json();
    renderTabs(tabs);
    tabsLoaded = true;
  } catch {
    // keep vibe tile
  }
}

function renderTabs(tabs) {
  const el = document.getElementById('tabs-content');

  // Permission denied — show step-by-step fix card
  if (tabs && tabs.permission === 'denied') {
    el.innerHTML = `
      <div style="padding:4px 0">
        <div style="font-size:13px;font-weight:600;margin-bottom:10px;color:var(--amber)">
          ⚠ Automation Permission Required
        </div>
        <div style="font-size:12px;color:var(--muted);line-height:1.7;margin-bottom:14px">
          macOS is blocking Safari/browser access.<br>
          Grant permission in <strong style="color:var(--text)">System Settings</strong>:
        </div>
        <ol style="font-size:12px;color:var(--muted);padding-left:18px;line-height:2">
          <li>Open <strong style="color:var(--text)">System Settings</strong></li>
          <li>Go to <strong style="color:var(--text)">Privacy &amp; Security → Automation</strong></li>
          <li>Find <strong style="color:var(--text)">Terminal</strong> (or Python) in the list</li>
          <li>Enable the toggle next to <strong style="color:var(--text)">Safari</strong></li>
        </ol>
        <button class="btn btn-muted" style="margin-top:14px;font-size:12px" onclick="openSystemSettings()">
          Open System Settings
        </button>
        <button class="btn btn-muted" style="margin-top:14px;margin-left:8px;font-size:12px" onclick="retrySection('tabs')">
          ↻ Retry
        </button>
      </div>`;
    return;
  }

  if (!tabs || tabs.length === 0) {
    el.innerHTML = `<div style="color:var(--muted);font-size:12px;padding:12px 0;text-align:center">
      <div style="font-size:20px;margin-bottom:6px">🌐</div>No browser tabs found<br>
      <span style="font-size:11px">(Chrome or Safari not running)</span></div>`;
    return;
  }

  const suggested = tabs.filter(t => t.suggest_close);
  const byChromeCount = tabs.filter(t => t.browser === 'Chrome').length;
  const bySafariCount = tabs.filter(t => t.browser === 'Safari').length;

  const pills = [
    byChromeCount && `<span class="pill">Chrome ${byChromeCount}</span>`,
    bySafariCount && `<span class="pill">Safari ${bySafariCount}</span>`,
    suggested.length && `<span class="pill" style="color:var(--amber)">⚠ ${suggested.length} to close</span>`,
  ].filter(Boolean).join('');

  const tabRows = suggested.slice(0, 6).map(t => `
    <div class="tab-row">
      <div class="tab-browser">${esc(t.browser)}</div>
      <div class="tab-title">${esc(t.title || t.url)}</div>
      ${t.reasons?.length ? `<span class="tab-tag">${esc(t.reasons[0])}</span>` : ''}
      <button class="close-btn" onclick="closeSingleTab('${esc(t.browser)}',${t.window},${t.index})">Close</button>
    </div>`).join('');

  const actionBar = suggested.length ? `
    <div class="action-bar">
      ${byChromeCount && suggested.some(t=>t.browser==='Chrome') ?
        `<button class="btn btn-danger" onclick="confirmCloseTabs('Chrome')">Close Chrome suggestions</button>` : ''}
      ${bySafariCount && suggested.some(t=>t.browser==='Safari') ?
        `<button class="btn btn-danger" onclick="confirmCloseTabs('Safari')">Close Safari suggestions</button>` : ''}
    </div>` : '';

  el.innerHTML = `
    <div class="tab-summary">${pills}</div>
    ${tabRows}
    ${actionBar}`;
}

// ── Grime section ─────────────────────────────────────────────────────────────
let grimeLoaded = false;
async function fetchGrime() {
  try {
    const r = await fetch('/api/grime');
    if (!r.ok) throw new Error();
    const grime = await r.json();
    renderGrime(grime);
    grimeLoaded = true;
  } catch {
    // keep vibe tile
  }
}

function renderGrime(grime) {
  const el = document.getElementById('grime-content');
  if (!grime || grime.length === 0) {
    el.innerHTML = `<div class="vibe-tile"><div class="vibe-emoji">🤙</div>
      <div class="vibe-text">Vibe coding didn't vibe much</div></div>`;
    return;
  }

  const totalBytes = grime.reduce((a, g) => a + g.bytes, 0);
  const rows = grime.map(g => {
    const pct = totalBytes ? (g.bytes / totalBytes * 100) : 0;
    const accessible = g.accessible !== false;
    const hasData = g.bytes > 0;
    return `
      <div class="grime-row">
        <div class="grime-header">
          <span class="grime-label">${esc(g.label)}</span>
          <span class="grime-size">${esc(g.size)}</span>
        </div>
        <div class="grime-reason">${esc(g.reason)}</div>
        <div class="grime-bar-track">
          <div class="grime-bar-fill" style="width:${pct.toFixed(1)}%"></div>
        </div>
        ${hasData && accessible ? `
          <button class="clean-btn" style="margin-top:6px"
            onclick="confirmClean('${g.id}','${esc(g.label)}','${esc(g.size)}')">
            Clean ${esc(g.size)}
          </button>` : ''}
        ${!accessible ? `<span style="font-size:10px;color:var(--muted)">No access</span>` : ''}
      </div>`;
  }).join('');

  el.innerHTML = rows + `
    <div class="action-bar" style="margin-top:4px">
      <button class="btn btn-muted" onclick="fetchGrime()">↻ Rescan</button>
    </div>`;
}

// ── Retry vibe tiles ──────────────────────────────────────────────────────────
function retrySection(section) {
  if (section === 'tabs')  fetchTabs();
  if (section === 'grime') fetchGrime();
  if (section === 'hogs')  fetchStats();
}

// ── Modal helpers ─────────────────────────────────────────────────────────────
let _modalAction = null;
function showModal(title, body, action) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').textContent  = body;
  document.getElementById('modal').classList.add('show');
  _modalAction = action;
  document.getElementById('modal-confirm').onclick = () => { closeModal(); action(); };
}
function closeModal() {
  document.getElementById('modal').classList.remove('show');
  _modalAction = null;
}
document.getElementById('modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});

function confirmKill(pid, name) {
  showModal(`Kill ${name}?`,
    `This will terminate PID ${pid} (${name}). The process will close immediately.`,
    () => doKill(pid));
}

async function doKill(pid) {
  try {
    const r = await fetch('/api/kill', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({pid})
    });
    const d = await r.json();
    showToast(d.ok ? `Killed PID ${pid}` : `Failed: ${d.error}`);
    setTimeout(fetchStats, 1000);
  } catch { showToast('Kill failed'); }
}

function confirmCloseTabs(browser) {
  showModal(`Close ${browser} suggestions?`,
    `This will close all suggested tabs in ${browser}.`,
    () => doCloseTabs(browser));
}

async function doCloseTabs(browser) {
  try {
    const r = await fetch('/api/close-tabs', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({browser})
    });
    const d = await r.json();
    showToast(d.ok ? `Closed ${d.closed} tab(s)` : `Failed: ${d.error}`);
    setTimeout(fetchTabs, 1500);
  } catch { showToast('Failed to close tabs'); }
}

async function closeSingleTab(browser, win, idx) {
  try {
    await fetch('/api/close-tabs', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({browser, window: win, index: idx, single: true})
    });
    showToast('Tab closed');
    setTimeout(fetchTabs, 1000);
  } catch { showToast('Failed'); }
}

function confirmClean(id, label, size) {
  showModal(`Clean ${label}?`,
    `This will permanently delete ${size} of ${label.toLowerCase()}. ${
      id === 'trash' ? 'Items in Trash will be gone forever.' :
      'These files will be regenerated by apps when needed.'
    }`,
    () => doClean(id, label));
}

async function doClean(id, label) {
  showToast(`Cleaning ${label}…`);
  try {
    const r = await fetch('/api/clean', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({cat_id: id})
    });
    const d = await r.json();
    showToast(d.ok ? `Freed ${d.freed_human}` : `Failed: ${d.error}`);
    setTimeout(fetchGrime, 1000);
  } catch { showToast('Clean failed'); }
}

// ── Ask Claude ────────────────────────────────────────────────────────────────
async function askClaude() {
  try {
    const r = await fetch('/api/snapshot');
    const d = await r.json();
    await navigator.clipboard.writeText(d.prompt);
    showToast('✓ Copied to clipboard — paste into Claude Code');
  } catch {
    showToast('Clipboard access denied — try from a browser tab');
  }
}

// ── Toast ─────────────────────────────────────────────────────────────────────
let _toastTimer;
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove('show'), 3000);
}

// ── System Settings shortcut ─────────────────────────────────────────────────
function openSystemSettings() {
  fetch('/api/open-settings', {method:'POST'}).catch(()=>{});
  showToast('Opening System Settings…');
}

// ── Escape helper ─────────────────────────────────────────────────────────────
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
                  .replace(/"/g,'&quot;').replace(/'/g,'&#039;');
}

// ── Init ──────────────────────────────────────────────────────────────────────
fetchStats();
fetchTabs();
fetchGrime();
setInterval(fetchStats, 3000);
setInterval(fetchTabs,  30000);
setInterval(fetchGrime, 60000);

// Status counter
setInterval(() => {
  if (lastUpdate) {
    const secs = Math.round((Date.now() - lastUpdate) / 1000);
    if (secs > 5)
      document.getElementById('status-text').textContent = `Updated ${secs}s ago`;
  }
}, 1000);
</script>
</body>
</html>
"""

# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress access logs

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/stats":
            self.send_json(api_stats())

        elif path == "/api/tabs":
            self.send_json(api_tabs())

        elif path == "/api/grime":
            self.send_json(api_grime())

        elif path == "/api/snapshot":
            self.send_json(api_snapshot())

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if path == "/api/kill":
            pid = data.get("pid")
            if isinstance(pid, int):
                self.send_json(api_kill(pid))
            else:
                self.send_json({"ok": False, "error": "pid required"}, 400)

        elif path == "/api/close-tabs":
            browser = data.get("browser", "")
            self.send_json(api_close_tabs(browser))

        elif path == "/api/clean":
            cat_id = data.get("cat_id", "")
            self.send_json(api_clean(cat_id))

        elif path == "/api/open-settings":
            subprocess.Popen([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
            ])
            self.send_json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    no_open = "--no-open" in sys.argv
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"OptimusGrime dashboard → {url}")
    print("Press Ctrl-C to stop.")
    if not no_open:
        subprocess.Popen(["open", url])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

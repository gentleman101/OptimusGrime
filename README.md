# OptimusGrime
**Transform your grime.**

A macOS system monitoring toolkit with a live browser dashboard and Claude Code AI skills. No dependencies — pure Python stdlib + native macOS tools.

---

## What it does

| Feature | Description |
|---------|-------------|
| **Live Dashboard** | Real-time CPU, memory, disk I/O, and disk space sparklines at `localhost:7777` |
| **Background Hog Detection** | Surfaces non-system processes burning CPU with one-click Kill |
| **Browser Tab Intelligence** | Lists open Safari + Chrome tabs, scores inactivity, suggests which to close |
| **Smart Cleanup** | Scans app caches, Xcode artifacts, temp files, and broken login items with Clean buttons |
| **Intelligence Analysis** | Rule-based AI summary with health score, observations, and recommended actions |
| **Claude Code Skills** | Invoke any feature directly from the Claude Code CLI |

---

## Requirements

- macOS 12 Monterey or later
- Python 3.9+ (ships with macOS — verify with `python3 --version`)
- [Claude Code](https://claude.ai/code) CLI (for AI skills)
- Safari and/or Google Chrome (for tab intelligence)

No `pip install` needed — everything uses the Python standard library and built-in macOS tools (`top`, `vm_stat`, `iostat`, `ps`, `df`, `osascript`).

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/gentleman101/OptimusGrime.git
cd OptimusGrime

# 2. Launch the dashboard
python3 src/dashboard.py
```

The dashboard opens automatically at **http://localhost:7777**.

To expose it on your local network (view from phone, tablet, or another machine):

```bash
python3 src/dashboard.py --remote
# Prints: Remote access → http://<your-ip>:7777
```

---

## macOS Permissions

The first time you run the dashboard, macOS will prompt for **Automation permission** to read browser tabs.

- Click **Allow** when prompted, or go to:
  `System Settings → Privacy & Security → Automation`
  and enable the toggle for **Terminal** → **Safari** (and Chrome if installed).

This is a one-time step. No other special permissions are required.

---

## Claude Code Skills

If you have [Claude Code](https://claude.ai/code) installed, open this repo as your working directory and the skills are automatically available:

```
/get-health          — CPU, memory, disk, top processes (pretty output)
/scan-grime          — Categorised junk scan with sizes and clean reasons
/list-tabs           — Browser tab list with inactivity scoring
/dashboard           — Launch the live dashboard
```

Skills live in `.claude/skills/` — Claude Code picks them up automatically when the repo is your working directory.

---

## Running Scripts Directly

```bash
# System health (human-readable)
python3 scripts/health_check.py

# System health (compact JSON — for programmatic use)
python3 scripts/health_check.py --format json

# Junk scan
python3 scripts/scan_grime.py

# Browser tabs (raw JSON)
osascript scripts/get_tabs.scpt

# Dashboard — local only
python3 src/dashboard.py

# Dashboard — accessible on LAN
python3 src/dashboard.py --remote

# Dashboard — no auto-open browser
python3 src/dashboard.py --no-open
```

---

## Project Structure

```
OptimusGrime/
├── scripts/
│   ├── health_check.py      # CPU, memory, disk, process metrics
│   ├── scan_grime.py        # Junk file scanner and cleaner
│   └── get_tabs.scpt        # AppleScript: list Safari + Chrome tabs
├── src/
│   └── dashboard.py         # HTTP server + inline HTML/CSS/JS dashboard
├── .claude/
│   └── skills/              # Claude Code skill definitions
│       ├── get-health/
│       ├── scan-grime/
│       ├── list-tabs/
│       └── dashboard/
├── docs/
│   └── best_practices.md
└── SKILLS.md                # Skill reference table
```

---

## Ask Claude

The dashboard has an **Ask Claude** button (bottom-right). Clicking it copies a full system snapshot to your clipboard — paste it into Claude Code for AI-powered diagnosis:

- Why is a process eating CPU?
- Which tabs are worth closing?
- What junk is safe to delete?

---

## License

MIT

# OptimusGrime Custom Skills

| Skill | Command | Description |
|-------|---------|-------------|
| `get-health` | `python3 ~/OptimusGrime/scripts/health_check.py` | CPU, memory pressure, disk I/O, disk space, top processes |
| `get-health-json` | `python3 ~/OptimusGrime/scripts/health_check.py --format json` | Same as above in compact JSON (token-efficient) |
| `get-health-warnings` | `python3 ~/OptimusGrime/scripts/health_check.py --warnings-only` | Only show metrics that exceed thresholds |
| `list-tabs` | `python3 ~/OptimusGrime/scripts/get_tabs.py` | Open browser tabs — macOS: live Chrome + Safari; Windows: Chrome history |
| `scan-grime` | `python3 ~/OptimusGrime/scripts/scan_grime.py` | Categorised junk scan: caches, Xcode artifacts, temp files, broken login items |
| `scan-grime-json` | `python3 ~/OptimusGrime/scripts/scan_grime.py --json` | Grime scan in compact JSON |
| `dashboard` | `python3 ~/OptimusGrime/src/dashboard.py` | Launch live browser dashboard at http://localhost:7777 |

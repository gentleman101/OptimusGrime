---
name: get-health
description: Check macOS system health — CPU usage, memory pressure, disk I/O, disk space, and top processes. Use when the user asks about system performance, slowness, resource usage, or what's consuming CPU/memory.
argument-hint: "[--format json|pretty] [--warnings-only]"
allowed-tools: Bash
---

Run the OptimusGrime health check and show the result:

```bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
python3 "$PROJECT_ROOT/scripts/health_check.py" $ARGUMENTS
```

Report the output to the user. If `--format json` was used, parse the JSON and summarise the key findings in plain language. If there are warnings (high CPU, memory pressure, disk near full, thermal throttling), highlight them clearly.

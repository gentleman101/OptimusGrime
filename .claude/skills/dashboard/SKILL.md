---
name: dashboard
description: Launch the OptimusGrime live browser dashboard at http://localhost:7777. Shows real-time CPU/memory/disk sparklines, background process hogs with kill buttons, browser tab intelligence, and smart cleanup controls.
argument-hint: "[--no-open]"
allowed-tools: Bash
disable-model-invocation: true
---

Start the OptimusGrime dashboard server and open it in the browser:

```bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
python3 "$PROJECT_ROOT/src/dashboard.py" $ARGUMENTS
```

Tell the user the dashboard is running at http://localhost:7777 and describe what they'll see:
- **Top bar**: Live sparkline charts for CPU, Memory, Disk I/O, and Disk Space
- **Section 1**: Background CPU hogs with Kill buttons (hover to reveal)
- **Section 2**: Browser tab count with inactivity suggestions and Close buttons
- **Section 3**: Smart cleanup by category with Clean buttons
- **Ask Claude button** (bottom-right): copies a full system snapshot prompt to clipboard — paste it here for AI analysis

Press Ctrl-C in the terminal to stop the server.

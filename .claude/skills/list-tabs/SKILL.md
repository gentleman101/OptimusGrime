---
name: list-tabs
description: List open browser tabs from Chrome and Safari, with inactivity scoring. Shows which tabs are suggested for closing based on last visit time and tab title heuristics.
argument-hint: ""
allowed-tools: Bash
disable-model-invocation: true
---

List open browser tabs from Chrome and Safari:

```bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
osascript "$PROJECT_ROOT/scripts/get_tabs.scpt"
```

Parse the JSON output and display it as a readable list grouped by browser. For each tab marked `suggest_close: true`, note the reasons (e.g. "last visited 4h ago", "social/news site"). Give the user a summary of how many tabs are open total and how many are suggested for closing.

Note: macOS will prompt for Automation permission the first time this runs — the user should click Allow.

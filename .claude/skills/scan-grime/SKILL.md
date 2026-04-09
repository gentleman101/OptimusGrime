---
name: scan-grime
description: Scan for junk files that are safe to delete — app caches, Xcode build artifacts, system temp files, trash, and broken login items. Shows size per category and why each is safe to clean.
argument-hint: "[--json]"
allowed-tools: Bash
---

Run the OptimusGrime junk scanner:

```bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
python3 "$PROJECT_ROOT/scripts/scan_grime.py" $ARGUMENTS
```

Show the output to the user. If there are categories with significant size (> 100 MB), highlight them and explain what they are and whether it's safe to delete them. If everything is clean, confirm that the system looks tidy.

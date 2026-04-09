# optimusGrime Best Practices
## 1. Modularity
- OS-level data extraction (AppleScript, shell) goes in `/scripts`.
- Business logic and UI code go in `/src`.
## 2. macOS Integration
- Use AppleScript (`osascript`) for browser tabs and idle states.
- Use `ps`, `top`, and `du` for resource monitoring.
## 3. Claude Code Optimization
- Update `knowledge_graph.json` when adding new modules.
- Maintain `SKILLS.md` for reusable terminal commands.

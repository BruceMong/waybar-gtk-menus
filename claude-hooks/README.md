# Claude Code hooks

State feeding the `custom/claude` Waybar module. Each hook writes a small JSON
file per session under `$XDG_RUNTIME_DIR/claude-sessions/`; the module reads
them. Nothing leaves the machine, and no transcript content is stored — only
session id, working directory, status and the Hyprland window address used to
jump to the right terminal.

Install:

```bash
cp *.sh ~/.claude/hooks/ && chmod +x ~/.claude/hooks/*.sh
```

Then register them in `~/.claude/settings.json` — see the main README.

| Hook | Event | Role |
|------|-------|------|
| `claude-session-start.sh` | SessionStart | registers the session |
| `claude-session-end.sh` | SessionEnd | removes it |
| `claude-prompt-state.sh` | UserPromptSubmit | marks it *running* |
| `claude-stop-notify.sh` | Stop | marks it *done* + notifies |
| `claude-notify-focus.sh` | Notification | marks it *waiting* + notifies |
| `claude-session-lib.sh` | — | shared library, sourced by the others |

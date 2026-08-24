#!/usr/bin/env bash
# Hook SessionEnd de Claude Code : la session se termine.
#
# reason = clear | logout | prompt_input_exit | other
#
# On efface son fichier d'état pour que le menu Waybar cesse de l'afficher
# sans attendre la mort du processus (sur /clear, il survit).

source "$HOME/.claude/hooks/claude-session-lib.sh"

payload="$(cat)"
sid="$(printf '%s' "$payload" | jq -r '.session_id // empty')"
[ -n "$sid" ] && rm -f "$CS_DIR/$sid.json"
cs_refresh_waybar

exit 0

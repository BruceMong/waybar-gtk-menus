#!/usr/bin/env bash
# Hook SessionStart de Claude Code : une session vient de (re)démarrer.
#
# source = startup  (lancement de `claude`)
#        | resume   (reprise d'une conversation)
#        | clear    (/clear : nouvelle session_id, même processus)
#        | compact  (compactage : nouvelle session_id, même processus)
#
# Sans ce hook, `/clear` laissait orphelin le fichier d'état de la session
# précédente : son pid restant vivant, rien ne le purgeait et le menu Waybar
# continuait d'afficher une conversation qui n'existe plus. cs_write purge les
# autres états du même pid, ce qui suffit à faire disparaître l'ancienne.

source "$HOME/.claude/hooks/claude-session-lib.sh"

payload="$(cat)"
cs_resolve
cs_write idle "$payload" "Session prête"

exit 0

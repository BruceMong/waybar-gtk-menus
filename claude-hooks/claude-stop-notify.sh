#!/usr/bin/env bash
# Hook Stop de Claude Code : la réponse vient de se terminer.
#
# Marque la session comme « terminée » pour le module Waybar custom/claude et
# envoie une notification cliquable — mais seulement si la fenêtre n'est pas
# déjà à l'écran. Quand on discute activement avec une session, on la regarde
# déjà : notifier à chaque tour serait du bruit. L'intérêt est de prévenir
# pour les sessions laissées sur un autre workspace.
#
# Même interrupteur que le hook Notification (fichier-drapeau Waybar) ; le
# suivi Waybar reste actif dans tous les cas.

source "$HOME/.claude/hooks/claude-session-lib.sh"

payload="$(cat)"
cwd="$(printf '%s' "$payload" | jq -r '.cwd // empty')"
[ -n "$cwd" ] && dir="$(basename "$cwd")" || dir="Claude Code"

cs_resolve
addr="$CS_ADDR"
cs_write done "$payload" "Réponse terminée"

if [ -f "$HOME/.config/waybar/claude-notify-focus.disabled" ]; then
    exit 0
fi

# Fenêtre déjà au premier plan : rien à signaler.
active="$(hyprctl activewindow -j 2>/dev/null | jq -r '.address // empty')"
[ -n "$addr" ] && [ "$addr" = "$active" ] && exit 0

export CNF_DIR="$dir" CNF_ADDR="$addr"
if [ -n "$addr" ]; then
    setsid -f bash -c '
        action=$(notify-send -a "Claude Code" -u low -t 8000 \
            -A "default=Aller à la fenêtre" \
            "Claude Code — $CNF_DIR" "Tâche terminée")
        if [ "$action" = "default" ]; then
            hyprctl dispatch "hl.dsp.focus({ window = \"address:$CNF_ADDR\" })"
        fi
    ' >/dev/null 2>&1
else
    notify-send -a "Claude Code" -u low "Claude Code — $CNF_DIR" "Tâche terminée"
fi

exit 0

#!/usr/bin/env bash
# Hook Notification de Claude Code.
# Envoie une notification cliquable : un clic ramène le focus sur la fenêtre
# (terminal) où tourne cette session Claude Code, en basculant de workspace
# si nécessaire (Hyprland).
#
# Enregistre aussi la session comme « en attente » pour le module Waybar
# custom/claude (voir claude-session-lib.sh).
#
# Activable/désactivable via le menu notification de Waybar (clic droit sur la
# cloche) : la présence du fichier-drapeau ci-dessous coupe la notification.
# Le suivi Waybar, lui, reste actif pour ne pas rendre la barre aveugle.

source "$HOME/.claude/hooks/claude-session-lib.sh"

# --- Données du hook (JSON sur stdin) ---
payload="$(cat)"
msg="$(printf '%s' "$payload" | jq -r '.message // "En attente de tes instructions"')"
cwd="$(printf '%s' "$payload" | jq -r '.cwd // empty')"
[ -n "$cwd" ] && dir="$(basename "$cwd")" || dir="Claude Code"

# --- Fenêtre Hyprland + suivi de session ---
cs_resolve
addr="$CS_ADDR"
cs_write waiting "$payload" "$msg"

# --- Interrupteur (drapeau posé par le menu Waybar) ---
if [ -f "$HOME/.config/waybar/claude-notify-focus.disabled" ]; then
    exit 0
fi

# --- Notification ---
# Détachée du hook (setsid -f) pour ne jamais bloquer Claude Code.
export CNF_MSG="$msg" CNF_DIR="$dir" CNF_ADDR="$addr"
if [ -n "$addr" ]; then
    setsid -f bash -c '
        action=$(notify-send -a "Claude Code" -u normal -t 0 \
            -A "default=Aller à la fenêtre" \
            "Claude Code — $CNF_DIR" "$CNF_MSG")
        if [ "$action" = "default" ]; then
            hyprctl dispatch focuswindow "address:$CNF_ADDR"
        fi
    ' >/dev/null 2>&1
else
    # Fenêtre introuvable : notification simple sans action.
    notify-send -a "Claude Code" -u normal "Claude Code — $CNF_DIR" "$CNF_MSG"
fi

exit 0

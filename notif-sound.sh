#!/bin/bash
# Script « run-on: receive » de swaync. Deux rôles, car swaync ne déclenche
# qu'UN SEUL script par notification : impossible d'en ajouter un second.
#   1. signaler le nouveau popup au bouton flottant « Tout effacer »
#      (notif-clear-button.py), qui compte les popups réellement à l'écran ;
#   2. jouer un son si l'utilisateur l'a activé.
# Rien ne se produit si Ne pas déranger est actif : aucun popup n'apparaît.

FLAG="$HOME/.config/waybar/notif-sound.enabled"
SOUND="/usr/share/sounds/freedesktop/stereo/message.oga"
CLEAR_PIDFILE="/tmp/notif-clear-button.pid"
CLEAR_QUEUE="/tmp/notif-clear-button.queue"

[ "$(swaync-client --get-dnd 2>/dev/null)" = "true" ] && exit 0

# -- 1. Compteur de popups --
if CLEAR_PID=$(cat "$CLEAR_PIDFILE" 2>/dev/null) && [ -n "$CLEAR_PID" ]; then
    printf '%s %s\n' "${SWAYNC_ID:-$RANDOM}" "${SWAYNC_URGENCY:-Normal}" \
        >> "$CLEAR_QUEUE" 2>/dev/null
    kill -USR1 "$CLEAR_PID" 2>/dev/null
fi

# -- 2. Son --
[ -f "$FLAG" ] || exit 0
paplay "$SOUND" 2>/dev/null &
exit 0

#!/bin/bash
# Mode Pro des notifications — interrupteur du menu notifications de la barre.
#
# Usage : notif-pro.sh on|off|toggle|apply|status|regex
#
# Actif, seules les applications listées dans notif-pro-allow s'affichent ;
# le reste (WhatsApp, Discord, mails, to-do, santé…) est rangé en silence
# dans le centre swaync (« muted » : pas de popup, pas de son, mais compté
# dans le badge de la cloche, donc retrouvable en sortant du mode). Une
# notification critique passe toujours : batterie, panne.
#
# La règle elle-même est écrite par notif-compact.sh, seul auteur de
# config-active.json ; ce script ne tient que le drapeau, le son de ZapZap
# (que l'application joue elle-même, hors swaync : même coupe-son que Ne pas
# déranger) et le rafraîchissement de la cloche.

DIR="$HOME/.config/waybar"
FLAG="$DIR/notif-pro.enabled"
ALLOW="$DIR/notif-pro-allow"
WATCHER="$DIR/dnd-mute-watcher.sh"
PID_FILE="${XDG_RUNTIME_DIR:-/tmp}/dnd-mute-watcher.pid"

# Les lignes utiles de la liste, jointes en une alternance : a|b|c
regex() {
    grep -vE '^[[:space:]]*(#|$)' "$ALLOW" 2>/dev/null \
        | sed 's/[[:space:]]*$//' | paste -sd'|'
}

watcher_on() {
    local pid
    pid=$(cat "$PID_FILE" 2>/dev/null)
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && return
    setsid -f "$WATCHER" >/dev/null 2>&1 </dev/null
}

apply() {
    "$DIR/notif-compact.sh" rules || { echo "notif-pro : règle non appliquée" >&2; return 1; }
    if [ -f "$FLAG" ]; then
        watcher_on
    elif [ "$(swaync-client --get-dnd 2>/dev/null)" != "true" ]; then
        # Ne pas déranger tient le même coupe-son : on ne le lui retire pas.
        "$DIR/dnd-toggle.sh" release-sound
    fi
    "$DIR/notification-refresh.sh"
}

case "${1:-toggle}" in
    on)     [ -n "$(regex)" ] || { echo "notif-pro : $ALLOW vide ou absent" >&2; exit 1; }
            touch "$FLAG"; apply ;;
    off)    rm -f "$FLAG"; apply ;;
    toggle) if [ -f "$FLAG" ]; then exec "$0" off; else exec "$0" on; fi ;;
    apply)  apply ;;
    status) [ -f "$FLAG" ] && echo on || echo off ;;
    regex)  regex ;;
    *)      echo "usage: notif-pro.sh on|off|toggle|apply|status|regex" >&2; exit 1 ;;
esac

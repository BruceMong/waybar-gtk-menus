#!/bin/bash
# Active/désactive Do Not Disturb (swaync) + coupe le son des notifs zapzap.
# Usage : dnd-toggle.sh [on|off|toggle]   (défaut : toggle)

WATCHER="$HOME/.config/waybar/dnd-mute-watcher.sh"
PID_FILE="${XDG_RUNTIME_DIR:-/tmp}/dnd-mute-watcher.pid"
SNOOZE_PID="${XDG_RUNTIME_DIR:-/tmp}/dnd-snooze.pid"

stop_watcher() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
        fi
        rm -f "$PID_FILE"
    fi
}

unmute_zapzap_sinks() {
    local pids
    pids=" $(pgrep -f '/usr/bin/zapzap' | tr '\n' ' ')"
    [ "$pids" = " " ] && return

    pactl list sink-inputs 2>/dev/null | awk -v pids="$pids" '
        /^Sink Input #/ {
            id = $3
            sub(/^#/, "", id)
        }
        /application\.process\.id = "/ {
            match($0, /"[0-9]+"/)
            pid = substr($0, RSTART+1, RLENGTH-2)
            if (index(pids, " " pid " ") > 0) print id
        }
    ' | while read -r id; do
        pactl set-sink-input-mute "$id" 0 2>/dev/null
    done
}

cancel_snooze() {
    # Annule une minuterie « Ne pas déranger » en attente, le cas échéant.
    if [ -f "$SNOOZE_PID" ]; then
        kill "$(cat "$SNOOZE_PID")" 2>/dev/null
        rm -f "$SNOOZE_PID"
    fi
}

action="${1:-toggle}"
current="$(swaync-client --get-dnd)"
case "$action" in
    on)  [ "$current" = "true" ]  || swaync-client --dnd-on  >/dev/null ;;
    off) [ "$current" = "false" ] || swaync-client --dnd-off >/dev/null ;;
    *)   swaync-client --toggle-dnd >/dev/null ;;
esac

if [ "$(swaync-client --get-dnd)" = "true" ]; then
    stop_watcher
    setsid -f "$WATCHER" >/dev/null 2>&1 < /dev/null
    # Le watcher ecrit lui-meme son PID dans $PID_FILE
else
    cancel_snooze
    stop_watcher
    unmute_zapzap_sinks
fi

# Rafraichit l'icone Waybar
~/.config/waybar/notification-refresh.sh

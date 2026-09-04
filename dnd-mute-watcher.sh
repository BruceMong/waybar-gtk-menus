#!/bin/bash
# Surveille les nouveaux sink-inputs PipeWire et mute ceux appartenant a zapzap.
# Lance par dnd-toggle.sh quand le mode DND est active.

# Ecrit son propre PID pour que dnd-toggle.sh puisse le tuer proprement
echo $$ > "${XDG_RUNTIME_DIR:-/tmp}/dnd-mute-watcher.pid"

mute_zapzap_sinks() {
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
        pactl set-sink-input-mute "$id" 1 2>/dev/null
    done
}

# Mute les flux deja existants au demarrage
mute_zapzap_sinks

# Puis suit les nouveaux flux en temps reel
pactl subscribe 2>/dev/null | while read -r line; do
    case "$line" in
        *"'new'"*"sink-input"*|*"'change'"*"sink-input"*)
            mute_zapzap_sinks
            ;;
    esac
done

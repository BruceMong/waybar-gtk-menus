#!/bin/bash
# Version continue de notification-status.sh (module custom/dnd).
# Émet l'état :
#   - au démarrage
#   - à chaque événement swaync (subscribe -> temps réel, zéro polling)
#   - sur SIGRTMIN+9, envoyé directement à ce processus (via son PIDFILE) par
#     notification-refresh.sh (bascule des flags claude/son)
#
# Le module custom/dnd ne doit PAS déclarer "signal" côté waybar : avec une
# clé "signal", waybar attend la fin du script avant de lire sa sortie — ce
# script étant continu, le module resterait vide et disparaîtrait de la barre.
STATUS="$HOME/.config/waybar/notification-status.sh"
PIDFILE="/tmp/waybar-dnd-watch.pid"

emit() { "$STATUS"; }

kill_tree() {
    local pid=$1 child
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        kill_tree "$child"
    done
    kill "$pid" 2>/dev/null
}

# Instance unique : le reload SIGUSR2 de waybar relance ce script sans
# toujours terminer l'ancien ; on remplace l'instance précédente et tout
# son arbre (boucle de reprise + swaync-client).
if OLD=$(cat "$PIDFILE" 2>/dev/null) && [ -n "$OLD" ] && [ "$OLD" != "$$" ]; then
    kill_tree "$OLD"
fi
echo $$ > "$PIDFILE"

FEEDER=""
cleanup() {
    [ -n "$FEEDER" ] && kill_tree "$FEEDER"
    rm -f "$PIDFILE"
    exit 0
}
trap cleanup TERM INT HUP
trap 'emit' RTMIN+9 USR1

emit

# Flux d'événements swaync : une ligne JSON par changement d'état.
# Boucle de reprise si swaync n'est pas encore lancé ou redémarre.
while true; do
    swaync-client -s 2>/dev/null | while read -r _; do emit; done
    sleep 5
done &
FEEDER=$!

# Bloque en attendant les signaux ; les traps ré-émettent ou nettoient.
while true; do
    wait
done

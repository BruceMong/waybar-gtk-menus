#!/bin/bash
# Force le module custom/dnd a re-emettre son etat.
#
# Le module tourne en flux continu (notification-status-watch.sh) : il n'a
# donc PAS de cle "signal" dans la config waybar. Ajouter "signal" ferait
# basculer waybar en mode « lance le script et attend sa fin » — le script
# ne se terminant jamais, le module reste vide et disparait de la barre.
#
# Waybar ne relaie pas les signaux a ses scripts enfants : on vise donc
# directement le processus du watcher, via son fichier PID.
PIDFILE="/tmp/waybar-dnd-watch.pid"
PID="$(cat "$PIDFILE" 2>/dev/null)"
[ -n "$PID" ] && kill -RTMIN+9 "$PID" 2>/dev/null
exit 0

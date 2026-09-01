#!/bin/bash
# Indicateur d'enregistrement d'écran (module waybar custom/recorder).
#
# Émet une ligne JSON par seconde : icône + durée tant que wf-recorder tourne,
# texte vide sinon — waybar masque alors le module, la barre reste propre.
#
# Script continu ("exec" sans "interval" côté waybar) : le module ne doit donc
# PAS déclarer de clé "signal", sinon waybar attendrait la fin du script pour
# lire sa sortie (cf. notification-status-watch.sh).
PIDFILE="/tmp/waybar-recorder-watch.pid"

# Instance unique : le reload SIGUSR2 de waybar relance ce script sans
# toujours terminer l'ancien.
if OLD=$(cat "$PIDFILE" 2>/dev/null) && [ -n "$OLD" ] && [ "$OLD" != "$$" ]; then
    kill "$OLD" 2>/dev/null
fi
echo $$ > "$PIDFILE"
# Le pidfile n'est effacé que s'il porte encore NOTRE pid : l'ancienne
# instance, bloquée dans son `sleep 1`, ne traite son signal qu'après
# que la nouvelle y a écrit le sien — un `rm` inconditionnel effacerait
# donc le pid du remplaçant, et le lancement suivant ne tuerait plus
# rien : deux boucles écriraient dans le même module.
trap '[ "$(cat "$PIDFILE" 2>/dev/null)" = "$$" ] && rm -f "$PIDFILE"; exit 0' TERM INT HUP

json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

while true; do
    read -r pid secs <<< "$(ps -C wf-recorder -o pid=,etimes= --no-headers 2>/dev/null | head -1)"
    if [ -n "$pid" ]; then
        # Le chemin de sortie est le dernier argument (`-f <fichier>`).
        file=$(tr '\0' '\n' < "/proc/$pid/cmdline" 2>/dev/null | tail -1)
        if [ "$secs" -ge 3600 ]; then
            dur=$(printf '%d:%02d:%02d' $((secs / 3600)) $((secs % 3600 / 60)) $((secs % 60)))
        else
            dur=$(printf '%02d:%02d' $((secs / 60)) $((secs % 60)))
        fi
        printf '{"text":" %s","class":"recording","tooltip":"Enregistrement en cours → %s\\nclic : arrêter (ou SUPER+SHIFT+R)"}\n' \
            "$dur" "$(json_escape "${file##*/}")"
    else
        printf '{"text":"","class":"idle","tooltip":""}\n'
    fi
    sleep 1
done

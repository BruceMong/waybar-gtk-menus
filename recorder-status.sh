#!/bin/bash
# Indicateur d'enregistrement d'écran (module waybar custom/recorder).
#
# Émet une ligne JSON : icône + durée tant que wf-recorder tourne, texte vide
# sinon — waybar masque alors le module, la barre reste propre.
#
# Script continu ("exec" sans "interval" côté waybar) : le module ne doit donc
# PAS déclarer de clé "signal", sinon waybar attendrait la fin du script pour
# lire sa sortie (cf. notification-status-watch.sh).
#
# Cadence : une seconde pendant l'enregistrement (le compteur doit avancer),
# IDLE_NAP au repos. La boucle à la seconde 24 h/24 coûtait ~90 s de CPU par
# jour — et un `ps` forké à chaque tour — uniquement pour constater qu'il n'y
# a rien à afficher.
#
# Pourquoi 3 s et non la sieste longue de voice-status.sh : l'enregistrement
# d'écran démarre depuis un bind Hyprland qui lance wf-recorder directement,
# sans passer par un script qui pourrait nous réveiller. La sieste doit donc
# rester assez courte pour que l'indicateur apparaisse aussitôt la sélection
# slurp validée. SIGRTMIN+13 est malgré tout accepté, pour le jour où le
# lancement passera par un wrapper.
PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/waybar-recorder-watch.pid"
IDLE_NAP=3

# Instance unique : le reload SIGUSR2 de waybar relance ce script sans
# toujours terminer l'ancien.
if OLD=$(cat "$PIDFILE" 2>/dev/null) && [ -n "$OLD" ] && [ "$OLD" != "$$" ]; then
    kill "$OLD" 2>/dev/null
fi
echo $$ > "$PIDFILE"
# Le pidfile n'est effacé que s'il porte encore NOTRE pid : l'ancienne
# instance, bloquée dans sa sieste, ne traite son signal qu'après que la
# nouvelle y a écrit le sien — un `rm` inconditionnel effacerait donc le pid
# du remplaçant, et le lancement suivant ne tuerait plus rien : deux boucles
# écriraient dans le même module.
trap '[ "$(cat "$PIDFILE" 2>/dev/null)" = "$$" ] && rm -f "$PIDFILE"; exit 0' TERM INT HUP

# Sieste interruptible : `sleep` en avant-plan retarderait le trap jusqu'à son
# terme, ce qui rendrait le signal de réveil inutile.
NAP_PID=""
trap '[ -n "$NAP_PID" ] && kill "$NAP_PID" 2>/dev/null' RTMIN+13 USR1
nap() {
    sleep "$1" &
    NAP_PID=$!
    wait "$NAP_PID" 2>/dev/null
    NAP_PID=""
}

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
        printf '{"text":" %s","class":"recording","tooltip":"Enregistrement en cours → %s\\nclic : arrêter (ou SUPER+SHIFT+R)"}\n' \
            "$dur" "$(json_escape "${file##*/}")"
        nap 1
    else
        printf '{"text":"","class":"idle","tooltip":""}\n'
        nap "$IDLE_NAP"
    fi
done

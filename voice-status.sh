#!/bin/bash
# Indicateur d'enregistrement vocal (module waybar custom/voice-rec).
#
# Émet une ligne JSON : icône + durée tant que l'enregistrement tourne, texte
# vide sinon — waybar masque alors le module.
#
# Script continu ("exec" sans "interval") : le module ne doit donc PAS
# déclarer de clé "signal", sinon waybar attendrait la fin du script pour
# lire sa sortie (cf. recorder-status.sh).
#
# Cadence : une seconde pendant l'enregistrement (le compteur doit avancer),
# IDLE_NAP au repos. Une boucle à la seconde 24 h/24 pour n'afficher que du
# vide coûtait ~90 s de CPU par jour en pure perte, et autant de réveils qui
# empêchent le processeur de descendre dans ses états de veille profonds.
# Le repos n'en devient pas moins réactif : voice-recorder.sh envoie
# SIGRTMIN+12 à ce processus (via WATCHPID) au démarrage comme à l'arrêt,
# ce qui interrompt la sieste et réaffiche immédiatement.
PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/waybar-voicerec.pid"
PATHFILE="${XDG_RUNTIME_DIR:-/tmp}/waybar-voicerec.path"
WATCHPID="${XDG_RUNTIME_DIR:-/tmp}/waybar-voicerec-watch.pid"
IDLE_NAP=30

# Instance unique : le reload SIGUSR2 de waybar relance ce script sans
# toujours terminer l'ancien.
if OLD=$(cat "$WATCHPID" 2>/dev/null) && [ -n "$OLD" ] && [ "$OLD" != "$$" ]; then
    kill "$OLD" 2>/dev/null
fi
echo $$ > "$WATCHPID"
# Le pidfile n'est effacé que s'il porte encore NOTRE pid : l'ancienne
# instance, bloquée dans sa sieste, ne traite son signal qu'après que la
# nouvelle y a écrit le sien — un `rm` inconditionnel effacerait donc le pid
# du remplaçant, et le lancement suivant ne tuerait plus rien : deux boucles
# écriraient dans le même module.
trap '[ "$(cat "$WATCHPID" 2>/dev/null)" = "$$" ] && rm -f "$WATCHPID"; exit 0' TERM INT HUP

# Sieste interruptible : `sleep` en avant-plan retarderait le trap jusqu'à son
# terme, ce qui rendrait le signal de réveil inutile.
NAP_PID=""
trap '[ -n "$NAP_PID" ] && kill "$NAP_PID" 2>/dev/null' RTMIN+12 USR1
nap() {
    sleep "$1" &
    NAP_PID=$!
    wait "$NAP_PID" 2>/dev/null
    NAP_PID=""
}

json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

while true; do
    pid=$(cat "$PIDFILE" 2>/dev/null)
    secs=""
    if [ -n "$pid" ] && [ -d "/proc/$pid" ]; then
        secs=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')
    fi
    if [ -n "$secs" ]; then
        file=$(cat "$PATHFILE" 2>/dev/null)
        if [ "$secs" -ge 3600 ]; then
            dur=$(printf '%d:%02d:%02d' $((secs / 3600)) $((secs % 3600 / 60)) $((secs % 60)))
        else
            dur=$(printf '%02d:%02d' $((secs / 60)) $((secs % 60)))
        fi
        printf '{"text":" %s","class":"recording","tooltip":"Enregistrement vocal → %s\\nclic : arrêter | clic droit : ouvrir le dossier"}\n' \
            "$dur" "$(json_escape "${file##*/}")"
        nap 1
    else
        printf '{"text":"","class":"idle","tooltip":""}\n'
        nap "$IDLE_NAP"
    fi
done

#!/bin/bash
# Indicateur d'enregistrement vocal (module waybar custom/voice-rec).
#
# Émet une ligne JSON par seconde : icône + durée tant que l'enregistrement
# tourne, texte vide sinon — waybar masque alors le module.
#
# Script continu ("exec" sans "interval") : le module ne doit donc PAS
# déclarer de clé "signal", sinon waybar attendrait la fin du script pour
# lire sa sortie (cf. recorder-status.sh).
PIDFILE="/tmp/waybar-voicerec.pid"
PATHFILE="/tmp/waybar-voicerec.path"
WATCHPID="/tmp/waybar-voicerec-watch.pid"

# Instance unique : le reload SIGUSR2 de waybar relance ce script sans
# toujours terminer l'ancien.
if OLD=$(cat "$WATCHPID" 2>/dev/null) && [ -n "$OLD" ] && [ "$OLD" != "$$" ]; then
    kill "$OLD" 2>/dev/null
fi
echo $$ > "$WATCHPID"
# Le pidfile n'est effacé que s'il porte encore NOTRE pid : l'ancienne
# instance, bloquée dans son `sleep 1`, ne traite son signal qu'après
# que la nouvelle y a écrit le sien — un `rm` inconditionnel effacerait
# donc le pid du remplaçant, et le lancement suivant ne tuerait plus
# rien : deux boucles écriraient dans le même module.
trap '[ "$(cat "$WATCHPID" 2>/dev/null)" = "$$" ] && rm -f "$WATCHPID"; exit 0' TERM INT HUP

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
        printf '{"text":" %s","class":"recording","tooltip":"Enregistrement vocal → %s\\nclic : arrêter | clic droit : ouvrir le dossier"}\n' \
            "$dur" "$(json_escape "${file##*/}")"
    else
        printf '{"text":"","class":"idle","tooltip":""}\n'
    fi
    sleep 1
done

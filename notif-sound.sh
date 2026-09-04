#!/bin/bash
# Script « run-on: receive » de swaync. Deux rôles, car swaync ne déclenche
# qu'UN SEUL script par notification : impossible d'en ajouter un second.
#   1. signaler le nouveau popup au bouton flottant « Tout effacer »
#      (notif-clear-button.py), qui compte les popups réellement à l'écran ;
#   2. jouer un son si l'utilisateur l'a activé.
# Rien ne se produit si Ne pas déranger est actif : aucun popup n'apparaît.

FLAG="$HOME/.config/waybar/notif-sound.enabled"

# Pointait sur /usr/share/sounds/freedesktop/stereo/message.oga, qui n'existe
# pas sur cette machine : le paquet sound-theme-freedesktop n'est pas
# installé, et /usr/share/sounds est vide. Le son était donc muet même flag
# activé — paplay échouait en silence, redirigé vers /dev/null.
#
# Le fichier est maintenant généré localement (~/.config/sounds/generate.sh) :
# deux notes montantes, Sol5 puis Do6. Voir ce script pour le raisonnement.
SOUND="$HOME/.config/sounds/notification.wav"
CLEAR_PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/notif-clear-button.pid"
CLEAR_QUEUE="${XDG_RUNTIME_DIR:-/tmp}/notif-clear-button.queue"

[ "$(swaync-client --get-dnd 2>/dev/null)" = "true" ] && exit 0

# -- 1. Compteur de popups --
if CLEAR_PID=$(cat "$CLEAR_PIDFILE" 2>/dev/null) && [ -n "$CLEAR_PID" ]; then
    printf '%s %s\n' "${SWAYNC_ID:-$RANDOM}" "${SWAYNC_URGENCY:-Normal}" \
        >> "$CLEAR_QUEUE" 2>/dev/null
    kill -USR1 "$CLEAR_PID" 2>/dev/null
fi

# -- 2. Son --
# pw-play plutôt que paplay : il accepte --volume, ce qui permet de poser le
# niveau du son système indépendamment du volume de la session. Une
# notification qui arrive pendant un appel ne doit pas couvrir l'appel.
[ -f "$FLAG" ] || exit 0
[ -f "$SOUND" ] || exit 0
pw-play --volume=0.4 "$SOUND" >/dev/null 2>&1 &
exit 0

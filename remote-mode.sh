#!/bin/bash
# Toggle mode remote : lock + pas de suspend + luminosité min + barre rouge
#
# « Pas de suspend » couvre aussi le capot rabattu : l'inhibiteur porte
# `handle-lid-switch`, sans quoi logind suspendrait la machine dès la fermeture
# de l'écran — les deux autres verrous (`idle`, `sleep`) ne portent que sur
# l'inactivité et sur les demandes de veille, pas sur l'événement capot, que
# logind traite par un chemin séparé. C'est ce qui permet de laisser tourner une
# session Claude Code portable fermé, dans un sac.
#
# `handle-power-key` est de la même famille : portable fermé et remué, le bouton
# d'alimentation — logé dans le clavier — peut être enfoncé par les touches
# d'en face, et son action par défaut est l'extinction immédiate.
#
# L'extinction du panneau à la fermeture n'est PAS faite ici : elle est confiée
# à un bind Hyprland sur le capot (scripts/lid-remote.sh), pour ne pas laisser
# l'écran allumé lorsque le mode est activé portable ouvert, sur le bureau.

# État de session : $XDG_RUNTIME_DIR est privé à l'utilisateur et vidé à la
# déconnexion, là où /tmp est partagé entre comptes et survit à la session —
# un mode remote interrompu brutalement y laissait son témoin « actif » alors
# que l'inhibiteur, lui, était mort.
STATE_DIR="${XDG_RUNTIME_DIR:-/tmp}"
STATE_FILE="$STATE_DIR/remote-mode-active"
BRIGHTNESS_SAVE="$STATE_DIR/remote-mode-brightness"
INHIBIT_PID="$STATE_DIR/remote-mode-inhibit-pid"
DND_SAVE="$STATE_DIR/remote-mode-dnd"
STYLE_DIR="$HOME/.config/waybar"

# style.css est un lien vers style-normal.css ou style-remote.css, et ce lien
# appartient au dépôt (Stow ne fait que le refléter dans ~/.config). On le
# bascule donc là où il vit réellement : un `ln -sf` dans $STYLE_DIR
# écraserait le lien de Stow par un lien absolu, et la config cesserait
# d'être suivie. Le dossier réel se déduit d'un fichier voisin non ambigu.
REAL_DIR="$(dirname "$(readlink -f "$STYLE_DIR/style-normal.css")")"

set_style() {  # set_style <fichier de style>
    ln -sfn "$1" "$REAL_DIR/style.css"
}

if [ -f "$STATE_FILE" ]; then
    # === Désactiver ===
    if [ -f "$BRIGHTNESS_SAVE" ]; then
        brightnessctl set "$(cat "$BRIGHTNESS_SAVE")"
        rm "$BRIGHTNESS_SAVE"
    fi
    kill "$(cat "$INHIBIT_PID" 2>/dev/null)" 2>/dev/null
    rm -f "$INHIBIT_PID"
    rm "$STATE_FILE"

    # Restaurer l'état Ne pas déranger d'avant le mode remote
    if [ "$(cat "$DND_SAVE" 2>/dev/null)" != "true" ]; then
        "$STYLE_DIR/dnd-toggle.sh" off
    fi
    rm -f "$DND_SAVE"

    # Revenir au style normal
    set_style style-normal.css
else
    # === Activer ===
    brightnessctl get > "$BRIGHTNESS_SAVE"
    brightnessctl set 1

    systemd-inhibit --what=idle:sleep:handle-lid-switch:handle-power-key --who="remote-mode" --why="Mode remote actif" --mode=block sleep infinity &
    echo $! > "$INHIBIT_PID"

    touch "$STATE_FILE"

    # Activer Ne pas déranger (en mémorisant l'état précédent pour le restaurer)
    swaync-client --get-dnd > "$DND_SAVE" 2>/dev/null
    "$STYLE_DIR/dnd-toggle.sh" on

    # Passer au style remote (barre rouge)
    set_style style-remote.css

    loginctl lock-session
fi

# Recharger waybar pour appliquer le nouveau style
killall -SIGUSR2 waybar

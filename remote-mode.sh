#!/bin/bash
# Toggle mode remote : lock + pas de suspend + luminosité min + barre rouge

STATE_FILE="/tmp/remote-mode-active"
BRIGHTNESS_SAVE="/tmp/remote-mode-brightness"
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
    kill "$(cat /tmp/remote-mode-inhibit-pid 2>/dev/null)" 2>/dev/null
    rm -f /tmp/remote-mode-inhibit-pid
    rm "$STATE_FILE"

    # Restaurer l'état Ne pas déranger d'avant le mode remote
    if [ "$(cat /tmp/remote-mode-dnd 2>/dev/null)" != "true" ]; then
        "$STYLE_DIR/dnd-toggle.sh" off
    fi
    rm -f /tmp/remote-mode-dnd

    # Revenir au style normal
    set_style style-normal.css
else
    # === Activer ===
    brightnessctl get > "$BRIGHTNESS_SAVE"
    brightnessctl set 1

    systemd-inhibit --what=idle:sleep --who="remote-mode" --why="Mode remote actif" --mode=block sleep infinity &
    echo $! > /tmp/remote-mode-inhibit-pid

    touch "$STATE_FILE"

    # Activer Ne pas déranger (en mémorisant l'état précédent pour le restaurer)
    swaync-client --get-dnd > /tmp/remote-mode-dnd 2>/dev/null
    "$STYLE_DIR/dnd-toggle.sh" on

    # Passer au style remote (barre rouge)
    set_style style-remote.css

    loginctl lock-session
fi

# Recharger waybar pour appliquer le nouveau style
killall -SIGUSR2 waybar

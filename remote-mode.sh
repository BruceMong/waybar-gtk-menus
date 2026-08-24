#!/bin/bash
# Toggle mode remote : lock + pas de suspend + luminosité min + barre rouge

STATE_FILE="/tmp/remote-mode-active"
BRIGHTNESS_SAVE="/tmp/remote-mode-brightness"
STYLE_DIR="$HOME/.config/waybar"

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
    ln -sf "$STYLE_DIR/style-normal.css" "$STYLE_DIR/style.css"
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
    ln -sf "$STYLE_DIR/style-remote.css" "$STYLE_DIR/style.css"

    loginctl lock-session
fi

# Recharger waybar pour appliquer le nouveau style
killall -SIGUSR2 waybar

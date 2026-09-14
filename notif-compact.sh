#!/bin/bash
# Notifications compactes (swaync) — interrupteur « Notifications discrètes »
# du menu notifications de Waybar.
#
# Usage : notif-compact.sh on|off|toggle|apply|start
#
# La largeur d'une notification flottante est une clé de la config swaync
# (notification-window-width), pas du CSS : la surface layer-shell elle-même
# fait cette largeur. Or ~/.config/swaync/config.json est un lien Stow vers le
# dépôt : le réécrire à chaque bascule salirait `git status`. swaync lit donc
# une config GÉNÉRÉE, config-active.json (ignorée par git, comme le
# config-active de waybar), produite à partir de config.json avec la largeur
# qui correspond à l'état du drapeau. `hyprland.lua` lance swaync par
# `notif-compact.sh start`, qui génère puis exécute `swaync -c`.
#
# Le drapeau vit à côté des autres interrupteurs du menu (notif-sound.enabled,
# claude-notify-focus.disabled) : présent = compact.

FLAG="$HOME/.config/waybar/notif-compact.enabled"
SRC="$HOME/.config/swaync/config.json"
OUT="$HOME/.config/swaync/config-active.json"

# 380 px en normal (valeur de config.json). Le corps d'image suit, sinon une
# capture de 200 px déborderait d'une carte de 280.
COMPACT_WIDTH=280
COMPACT_IMAGE_WIDTH=140

generate() {
    if [ -f "$FLAG" ]; then
        jq --argjson w "$COMPACT_WIDTH" --argjson i "$COMPACT_IMAGE_WIDTH" \
           '."notification-window-width" = $w
            | ."notification-body-image-width" = $i' "$SRC" > "$OUT.tmp"
    else
        cat "$SRC" > "$OUT.tmp"
    fi
    mv "$OUT.tmp" "$OUT"
}

apply() {
    generate
    # `swaync-client --reload-config` relit bien le fichier, mais la largeur
    # n'est appliquée qu'à la création de la fenêtre des notifications
    # (vérifié sur swaync 0.12.6 : après reload, la carte reste à 380). Il
    # faut donc redémarrer swaync — c'est instantané, et les abonnés
    # (custom/dnd, bouton « Tout effacer ») se reconnectent d'eux-mêmes.
    # Ce redémarrage rattrape aussi un swaync lancé sans -c.
    pkill -x swaync
    setsid -f "$0" start >/dev/null 2>&1 </dev/null
    # Attendre qu'il soit de retour sur le bus, pour que la notification de
    # confirmation envoyée juste après ne parte pas dans le vide.
    for _ in $(seq 20); do
        pgrep -x swaync >/dev/null && swaync-client --count >/dev/null 2>&1 && break
        sleep 0.1
    done
}

case "${1:-toggle}" in
    on)     touch "$FLAG"; apply ;;
    off)    rm -f "$FLAG"; apply ;;
    toggle) if [ -f "$FLAG" ]; then rm -f "$FLAG"; else touch "$FLAG"; fi; apply ;;
    apply)  apply ;;
    start)  generate; exec swaync -c "$OUT" ;;
    *)      echo "usage: notif-compact.sh on|off|toggle|apply|start" >&2; exit 1 ;;
esac

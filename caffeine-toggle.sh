#!/bin/bash
# Caféine : bloque/rétablit la veille en arrêtant/relançant hypridle.
# Utilisé par la grille de boutons du centre de notifications swaync.
if pgrep -x hypridle >/dev/null; then
    pkill -x hypridle
    notify-send -a "Caféine" -u low "󰅶 Caféine activée" "Veille et verrouillage bloqués"
else
    setsid -f hypridle >/dev/null 2>&1
    notify-send -a "Caféine" -u low "󰾪 Caféine désactivée" "Veille normale rétablie"
fi

#!/usr/bin/env bash
# Mode discret de la waybar : masque toute la barre sauf un petit œil.
# Cliquer sur l'œil (via eye.sh) ressort la barre complète.
# Usage : stealth.sh [on|off|toggle]   (défaut : toggle)
set -uo pipefail

CONFIG_DIR="$HOME/.config/waybar"
FLAG="/tmp/waybar-stealth"

case "${1:-toggle}" in
    on)  touch "$FLAG" ;;
    off) rm -f "$FLAG" ;;
    *)   if [ -f "$FLAG" ]; then rm -f "$FLAG"; else touch "$FLAG"; fi ;;
esac

python3 "$CONFIG_DIR/generate-config.py"
ln -sf "$CONFIG_DIR/config-active" "$CONFIG_DIR/config"
pkill -SIGUSR2 waybar 2>/dev/null || true

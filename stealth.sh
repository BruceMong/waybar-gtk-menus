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
# Le lien `config -> config-active` fait partie du dépôt (posé par Stow), il
# n'a pas à être recréé ici : un `ln -sf` sur le chemin de ~/.config le
# remplacerait par un lien absolu et sortirait le fichier de la gestion Stow.
pkill -SIGUSR2 waybar 2>/dev/null || true

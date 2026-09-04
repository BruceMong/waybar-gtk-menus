#!/bin/bash
# Bascule le mode "dizaines" (+10) des workspaces.
# - Mode unités   : touches Super+1..0 -> workspaces 1..10, waybar affiche 1-10
# - Mode dizaines : touches Super+1..0 -> workspaces 11..20, waybar affiche 11-20
#                   (chiffres teintés en mauve pour repère visuel)
#
# Régénère config-active (qui adapte filtre + couleur selon l'état) puis
# recharge waybar via SIGUSR2 (rechargement à chaud, pas de redémarrage).

STATE="${XDG_RUNTIME_DIR:-/tmp}/waybar-ws-tens"
WAYBAR_DIR="$HOME/.config/waybar"

if [ -f "$STATE" ]; then
    rm -f "$STATE"
else
    touch "$STATE"
fi

python3 "$WAYBAR_DIR/generate-config.py"
pkill -SIGUSR2 waybar

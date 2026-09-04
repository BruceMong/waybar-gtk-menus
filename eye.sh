#!/usr/bin/env bash
# Clic sur l'œil de la waybar.
# - Mode discret actif -> ressort la barre complète.
# - Sinon              -> ouvre le menu de visibilité des modules.
if [ -f "${XDG_RUNTIME_DIR:-/tmp}/waybar-stealth" ]; then
    exec "$HOME/.config/waybar/stealth.sh" off
fi
exec setsid -f python3 "$HOME/.config/waybar/module-menu.py" >/dev/null 2>&1

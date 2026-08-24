#!/usr/bin/env bash
# Bascule la vidéo Chrome de l'onglet actif en Picture-in-Picture.
# Déclenché par le clic droit du module mpris de Waybar.
#
# Prérequis (une seule fois, côté Chrome) :
#   1. Installer l'extension « Picture-in-Picture Extension (by Google) »
#   2. chrome://extensions/shortcuts → lui assigner Alt+P, portée « Dans Chrome »
#
# Le clic Waybar n'étant pas un geste utilisateur valide pour la page,
# on donne le focus à Chrome puis on envoie le raccourci de l'extension.

CHROME_CLASS='^(google-chrome)$'

# Mémorise la fenêtre actuellement au premier plan pour y revenir ensuite.
prev=$(hyprctl activewindow -j 2>/dev/null | jq -r '.address // empty')

# Donne le focus à Chrome ; s'il n'est pas ouvert, on ne fait rien.
if ! hyprctl dispatch focuswindow "class:${CHROME_CLASS}" >/dev/null 2>&1; then
    exit 0
fi

# Laisse le focus clavier s'établir avant d'envoyer la touche.
sleep 0.15

# Raccourci de l'extension PiP (Alt+P).
wtype -M alt -k p -m alt

# Revient à la fenêtre d'origine si ce n'était pas Chrome (PiP reste flottant).
if [ -n "$prev" ]; then
    sleep 0.05
    hyprctl dispatch focuswindow "address:${prev}" >/dev/null 2>&1
fi

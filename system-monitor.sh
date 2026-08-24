#!/bin/bash
# Ouvre btop dans une fenêtre Kitty flottante.
# Si déjà ouverte, la ferme (toggle).

CLASS="system-monitor"

if pgrep -f "kitty --class $CLASS" >/dev/null; then
    pkill -f "kitty --class $CLASS"
    exit 0
fi

setsid -f kitty --class "$CLASS" -e btop >/dev/null 2>&1

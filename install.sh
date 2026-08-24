#!/usr/bin/env bash
# Installe/active cette config Waybar : vérifie les dépendances, recrée les
# symlinks attendus par Waybar, génère config-active puis (re)démarre la barre.
#
# Idempotent : relançable à volonté, ne touche à rien en dehors de ce dossier.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
DIR="$PWD"

info() { printf '\033[1;34m::\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1"; }

# --- Dépendances -----------------------------------------------------------
# Un binaire manquant ne bloque pas l'install : le module concerné se contente
# de ne rien afficher. On prévient, on continue.
REQUIRED=(waybar python3 hyprctl)
OPTIONAL=(nmcli wpctl pactl brightnessctl playerctl swaync-client
          systemctl checkupdates hyprsunset hypridle kitty notify-send)

missing=()
for bin in "${REQUIRED[@]}"; do
    command -v "$bin" >/dev/null || missing+=("$bin")
done
if ((${#missing[@]})); then
    warn "dépendances obligatoires manquantes : ${missing[*]}"
    exit 1
fi

if ! python3 -c 'import gi; gi.require_version("Gtk", "3.0")' 2>/dev/null; then
    warn "python-gobject / gtk3 manquant : les menus ne s'ouvriront pas"
    warn "  sudo pacman -S python-gobject gtk3 gtk-layer-shell"
fi

absent=()
for bin in "${OPTIONAL[@]}"; do
    command -v "$bin" >/dev/null || absent+=("$bin")
done
((${#absent[@]})) && warn "optionnels absents (modules inactifs) : ${absent[*]}"

# --- Symlinks --------------------------------------------------------------
# Waybar lit `config` et `style.css` ; ce sont des alias relatifs vers la
# variante active, pour pouvoir basculer de thème sans rien réécrire.
info "symlinks config / style.css"
ln -sfn config-active config
ln -sfn style-normal.css style.css

# --- Exécutables -----------------------------------------------------------
info "droits d'exécution"
chmod +x ./*.py ./*.sh

# --- Génération ------------------------------------------------------------
info "génération de config-active"
./generate-config.py
python3 -c 'import json,sys; json.load(open("config-active"))' \
    || { warn "config-active invalide, abandon"; exit 1; }

# --- Démarrage -------------------------------------------------------------
if pgrep -x waybar >/dev/null; then
    info "rechargement de Waybar"
    pkill -SIGUSR2 waybar
else
    info "démarrage de Waybar"
    waybar >/dev/null 2>&1 &
    disown
fi

info "terminé — config installée depuis $DIR"
echo
echo "Pour le module Claude Code (optionnel) :"
echo "  cp claude-hooks/*.sh ~/.claude/hooks/  # puis voir le README"

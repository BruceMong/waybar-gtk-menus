#!/usr/bin/env bash
# Molette sur « ⋮ » : fait défiler les modules quand la barre déborde.
#   down -> cache le prochain module selon ORDER (du moins important au plus)
#   up   -> ressort le dernier module caché par CE script
#
# Les modules cachés restent accessibles via le popup ⋮.
# La pile $XDG_RUNTIME_DIR/waybar-scroll-stack ne contient que les masquages faits ici :
# les modules cachés manuellement (menu de l'œil) ne sont jamais ressortis
# par la molette.

set -euo pipefail

HIDDEN="$HOME/.config/waybar/modules-hidden"
STACK="${XDG_RUNTIME_DIR:-/tmp}/waybar-scroll-stack"
LOCK="${XDG_RUNTIME_DIR:-/tmp}/waybar-scroll.lock"
GEN="$HOME/.config/waybar/generate-config.py"

# Étapes de masquage, de la première à cacher à la dernière : lues depuis
# modules-priority, partagé avec autofit.py (une seule source de vérité).
# Une étape peut grouper plusieurs ids (cachés/ressortis ensemble).
mapfile -t ORDER < <(grep -v '^\s*#' "$HOME/.config/waybar/modules-priority" | grep .)

# Anti-rafale : une molette rapide déclenche plusieurs événements,
# on n'en traite qu'un à la fois.
exec 9>"$LOCK"
flock -n 9 || exit 0

touch "$HIDDEN" "$STACK"

is_hidden() { grep -qxF "$1" "$HIDDEN"; }

changed=0
case "${1:-}" in
    down)
        for step in "${ORDER[@]}"; do
            read -ra ids <<< "$step"
            if ! is_hidden "${ids[0]}"; then
                printf '%s\n' "${ids[@]}" >> "$HIDDEN"
                printf '%s\n' "$step" >> "$STACK"
                changed=1
                break
            fi
        done
        ;;
    up)
        step="$(tail -n 1 "$STACK" 2>/dev/null || true)"
        [ -z "$step" ] && exit 0
        sed -i '$d' "$STACK"
        read -ra ids <<< "$step"
        for id in "${ids[@]}"; do
            grep -vxF "$id" "$HIDDEN" > "$HIDDEN.tmp" || true
            mv "$HIDDEN.tmp" "$HIDDEN"
        done
        changed=1
        ;;
    *)
        echo "usage: $0 up|down" >&2
        exit 1
        ;;
esac

if [ "$changed" -eq 1 ]; then
    python3 "$GEN"
    pkill -SIGUSR2 waybar
fi

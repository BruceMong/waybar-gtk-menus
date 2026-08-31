#!/usr/bin/env bash
# Module custom/updates : nombre de mises à jour en attente (dépôts + AUR).
#
#   sans argument : émet le JSON du module (text/tooltip/class)
#   --menu        : ouvre le popup GTK « Mises à jour » (updates-menu.py)
#   --refresh     : recompte et rafraîchit immédiatement le module dans la barre
#
# Le module disparaît de la barre quand il n'y a rien à mettre à jour
# (text vide -> waybar masque le module).

set -uo pipefail

CACHE="/tmp/waybar-updates.cache"
DIR="$(dirname "$(readlink -f "$0")")"

collect() {
    # checkupdates (pacman-contrib) : synchro dans une base temporaire,
    # ne touche pas /var/lib/pacman — sans droits root.
    local repo aur
    repo="$(checkupdates 2>/dev/null || true)"
    aur="$(yay -Qua 2>/dev/null || true)"
    printf '%s\n---\n%s\n' "$repo" "$aur" > "$CACHE"
    printf '%s\n---\n%s\n' "$repo" "$aur"
}

count_lines() { grep -c . <<< "${1:-}" || true; }

case "${1:-}" in
    --menu)
        # Le popup lit le cache ; on le crée s'il manque pour éviter d'ouvrir
        # une fenêtre vide au premier clic.
        [ -f "$CACHE" ] || collect > /dev/null
        exec python3 "$DIR/updates-menu.py"
        ;;
    --refresh)
        # Recompte puis demande à waybar de réexécuter le module (signal 10),
        # sinon l'affichage reste figé jusqu'au prochain intervalle (30 min).
        collect > /dev/null
        pkill -RTMIN+10 waybar 2>/dev/null
        exit 0
        ;;
esac

out="$(collect)"
repo_list="$(sed -n '1,/^---$/p' <<< "$out" | grep -v '^---$')"
aur_list="$(sed -n '/^---$/,$p'  <<< "$out" | grep -v '^---$')"

n_repo="$(count_lines "$repo_list")"
n_aur="$(count_lines "$aur_list")"
total=$(( n_repo + n_aur ))

if [ "$total" -eq 0 ]; then
    echo '{"text": "", "tooltip": "Système à jour"}'
    exit 0
fi

# Tooltip : résumé lisible, puis les 10 premiers paquets.
tooltip="$total mise(s) à jour — $n_repo dépôts, $n_aur AUR"
# Noms exacts (un espace suit) : linux-api-headers n'impose pas de reboot.
if grep -qE '^((linux|linux-zen|linux-lts|linux-firmware|systemd|glibc|mesa|amd-ucode|intel-ucode) |nvidia)' <<< "$repo_list"; then
    tooltip+=$'\n''⚠ noyau ou pilotes concernés : redémarrage nécessaire ensuite'
fi
preview="$(printf '%s\n%s\n' "$repo_list" "$aur_list" | grep . | head -10)"
[ -n "$preview" ] && tooltip+=$'\n\n'"$preview"
[ "$total" -gt 10 ] && tooltip+=$'\n'"… et $(( total - 10 )) autres"
tooltip+=$'\n\n''clic : détail par catégorie et mise à jour'

class="pending"
[ "$total" -ge 30 ] && class="many"

python3 -c '
import json, sys
print(json.dumps({
    "text": sys.argv[1],
    "tooltip": sys.argv[2],
    "class": sys.argv[3],
    "alt": sys.argv[3],
}, ensure_ascii=False))
' "󰚰 $total" "$tooltip" "$class"

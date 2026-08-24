#!/usr/bin/env bash
# Module custom/updates : nombre de mises à jour en attente (dépôts + AUR).
#
#   sans argument : émet le JSON du module (text/tooltip/class)
#   --menu        : ouvre un terminal flottant listant les MAJ, avec option
#                   pour lancer `yay -Syu`
#
# Le module disparaît de la barre quand il n'y a rien à mettre à jour
# (text vide -> waybar masque le module).

set -uo pipefail

CACHE="/tmp/waybar-updates.cache"

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

if [ "${1:-}" = "--menu" ]; then
    [ -f "$CACHE" ] || collect > /dev/null
    exec kitty --class waybar.modules -T "Mises à jour" \
        bash -c '
            printf "\033[1;35m── Dépôts officiels ──\033[0m\n"
            sed -n "1,/^---$/p" '"$CACHE"' | grep -v "^---$" | grep . || echo "  (aucune)"
            printf "\n\033[1;35m── AUR ──\033[0m\n"
            sed -n "/^---$/,\$p" '"$CACHE"' | grep -v "^---$" | grep . || echo "  (aucune)"
            printf "\n\033[1mLancer la mise à jour complète (yay -Syu) ? [o/N] \033[0m"
            read -r rep
            case "$rep" in
                [oOyY]) yay -Syu ;;
                *) exit 0 ;;
            esac
            printf "\nTerminé — Entrée pour fermer."
            read -r _
        '
fi

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

# Tooltip : les 15 premiers paquets, pour rester lisible.
tooltip="$total mise(s) à jour — $n_repo dépôts, $n_aur AUR"
preview="$(printf '%s\n%s\n' "$repo_list" "$aur_list" | grep . | head -15)"
[ -n "$preview" ] && tooltip+=$'\n\n'"$preview"
[ "$total" -gt 15 ] && tooltip+=$'\n'"… et $(( total - 15 )) autres"
tooltip+=$'\n\n''clic : voir la liste / mettre à jour'

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

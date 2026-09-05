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

CACHE="${XDG_RUNTIME_DIR:-/tmp}/waybar-updates.cache"
META="${XDG_RUNTIME_DIR:-/tmp}/waybar-updates.meta"
DIR="$(dirname "$(readlink -f "$0")")"

# Métadonnées du popup : taille restant à télécharger, part des paquets
# installés explicitement, date du dernier `pacman -Syu`. Elles coûtaient
# ~450 ms — dont 370 pour le seul `pacman -Sp` — et étaient calculées au clic,
# fenêtre non encore dessinée. Le module tourne de toute façon toutes les
# 30 min : autant les payer là, en arrière-plan.
#
# `sig` scelle le fichier au contenu du cache dont il est tiré : le popup
# recalcule cette empreinte et retombe sur son propre calcul si elle diffère
# (meta absent, périmé, écrit par une version antérieure).
write_meta() {
    local repo="$1" aur="$2"
    local db="${CHECKUPDATES_DB:-/tmp/checkup-db-$(id -u)}"
    local names sig size last apps

    names="$(awk 'NF {print $1}' <<< "$repo")"
    sig="$(printf '%s\n---\n%s\n' "$repo" "$aur" | sha1sum | cut -d' ' -f1)"

    size=0
    if [ -n "$names" ] && [ -d "$db" ]; then
        # shellcheck disable=SC2086 — découpage voulu : un argument par paquet.
        size="$(pacman -Sp --dbpath "$db" --print-format '%s' $names 2>/dev/null \
                | awk '{s += $1} END {print s + 0}')"
    fi

    # Horodatage brut : « il y a 2 jours » se périme, pas l'epoch.
    last="$(tac /var/log/pacman.log 2>/dev/null \
            | grep -m1 -F 'starting full system upgrade' \
            | sed -n 's/^\[\([^]]*\)\].*/\1/p')"
    last="$(date -d "$last" +%s 2>/dev/null || true)"

    apps=""
    [ -n "$names" ] && apps="$(comm -12 <(sort -u <<< "$names") \
                                        <(pacman -Qqe 2>/dev/null | sort) | tr '\n' ' ')"

    # Écriture atomique : le popup ne doit jamais lire un fichier à moitié écrit.
    { printf 'sig %s\n' "$sig"
      printf 'size %s\n' "$size"
      printf 'last %s\n' "$last"
      printf 'apps %s\n' "$apps"
    } > "$META.tmp" && mv -f "$META.tmp" "$META"
}

collect() {
    # checkupdates (pacman-contrib) : synchro dans une base temporaire,
    # ne touche pas /var/lib/pacman — sans droits root.
    local repo aur
    repo="$(checkupdates 2>/dev/null || true)"
    aur="$(yay -Qua 2>/dev/null || true)"
    printf '%s\n---\n%s\n' "$repo" "$aur" > "$CACHE"
    write_meta "$repo" "$aur" >/dev/null 2>&1
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
' " $total" "$tooltip" "$class"

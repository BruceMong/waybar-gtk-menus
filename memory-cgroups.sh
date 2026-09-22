#!/usr/bin/env bash
# Qui consomme la mémoire, par cgroup — RAM résidente ET swap.
#
# Pourquoi pas `ps` : son RSS compte les bibliothèques partagées une fois par
# processus, ce qui gonfle les arbres larges (1 515 curl orphelins d'un
# healthcheck Docker y affichaient 12 Go pour 0,5 Go réels), et il ignore
# purement et simplement ce qui est parti en swap. Un cgroup, lui, facture une
# page une fois et suit les deux.
#
# C'est ce relevé qui a désigné le scope du Kitty herdr le 2026-09-16, puis
# celui du compositeur le 2026-09-22 — 7,7 Go de vif et 8,8 Go de swap pour
# 142 processus, avant que l'autostart ne passe par `uwsm app`.
set -uo pipefail

cd /sys/fs/cgroup || exit 1

printf '%8s %8s %8s  %s\n' "TOTAL" "VIF" "SWAP" "CGROUP"
printf '%8s %8s %8s  %s\n' "──────" "──────" "──────" "──────────────────────────"

for d in user.slice/user-$(id -u).slice/user@$(id -u).service/*/*/ system.slice/*/; do
    [ -f "$d/memory.current" ] || continue
    vif=$(cat "$d/memory.current" 2>/dev/null || echo 0)
    swap=$(cat "$d/memory.swap.current" 2>/dev/null || echo 0)
    tot=$(( (vif + swap) / 1048576 ))
    [ "$tot" -lt 50 ] && continue   # sous 50 Mo, c'est du bruit
    printf '%6s Mo %6s Mo %6s Mo  %s\n' \
        "$tot" "$(( vif / 1048576 ))" "$(( swap / 1048576 ))" \
        "$(basename "$d")"
done | sort -rn | head -20

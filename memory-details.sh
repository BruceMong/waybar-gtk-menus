#!/bin/bash
# Affiche les top 10 processus consommant le plus de RAM via dunst

details=$(ps axo rss,comm --no-headers | awk '{mem[$2]+=$1} END {for(p in mem) printf "%d %s\n", mem[p]/1024, p}' | sort -rn | head -10 | awk '{printf "%-6s Mo  %s\n", $1, $2}')

notify-send -u normal -t 10000 "RAM - Top 10 processus" "$details"

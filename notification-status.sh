#!/bin/bash
# État de la cloche Waybar (module custom/dnd) au format JSON :
#   - icône cloche / cloche barrée selon Ne pas déranger
#   - badge compteur de notifications (centre swaync), plafonné à 99+
#   - classe "claude-off" si l'effet « Notif Claude → fenêtre » est désactivé

CLAUDE_FLAG="$HOME/.config/waybar/claude-notify-focus.disabled"
SOUND_FLAG="$HOME/.config/waybar/notif-sound.enabled"

dnd="$(swaync-client --get-dnd 2>/dev/null)"
count="$(swaync-client -c 2>/dev/null)"
[ -z "$count" ] && count=0

classes=()

if [ "$dnd" = "true" ]; then
    icon=""
    classes+=("active")
    dnd_txt="Notifications : Ne pas déranger"
else
    icon=""
    classes+=("inactive")
    dnd_txt="Notifications : actives"
fi

badge=""
if [ "$count" -gt 0 ] 2>/dev/null; then
    if [ "$count" -gt 99 ]; then badge=" 99+"; else badge=" $count"; fi
    classes+=("has-notifs")
fi
text="$icon$badge"

if [ -f "$CLAUDE_FLAG" ]; then
    classes+=("claude-off")
    claude_txt="Notif Claude → fenêtre : DÉSACTIVÉE"
else
    claude_txt="Notif Claude → fenêtre : activée"
fi

[ -f "$SOUND_FLAG" ] && sound_txt="Son : activé" || sound_txt="Son : coupé"

tooltip="$(printf '%s\n%s\n%s\nclic : centre de notifications · clic droit : réglages' \
    "$dnd_txt" "$claude_txt" "$sound_txt")"

class_json="$(printf '%s\n' "${classes[@]}" | jq -R . | jq -cs .)"
jq -cn --arg text "$text" --arg tooltip "$tooltip" --argjson class "$class_json" \
    '{text: $text, class: $class, tooltip: $tooltip}'

#!/bin/bash
# Apparence des notifications (swaync) — piloté par le menu notifications de
# Waybar : interrupteur « Notifications discrètes » et curseur « Opacité ».
#
# Usage : notif-compact.sh on|off|toggle|opacity <0-100>|apply|start
#
# Deux réglages, deux supports :
#
#   - la LARGEUR d'une notification flottante est une clé de la config swaync
#     (notification-window-width), pas du CSS : la surface layer-shell
#     elle-même fait cette largeur ;
#   - la TAILLE DU TEXTE et l'OPACITÉ sont du CSS.
#
# Or ~/.config/swaync/config.json et style.css sont des liens Stow vers le
# dépôt : les réécrire à chaque bascule salirait `git status`. swaync lit donc
# des fichiers GÉNÉRÉS, config-active.json et style-active.css (ignorés par
# git, comme le config-active de waybar), produits à partir des sources avec
# les valeurs qui correspondent à l'état du drapeau et du curseur.
# `hyprland.lua` lance swaync par `notif-compact.sh start`, qui génère puis
# exécute `swaync -c … -s …`.
#
# Le drapeau et la valeur d'opacité vivent à côté des autres interrupteurs du
# menu (notif-sound.enabled, claude-notify-focus.disabled) : drapeau présent =
# compact ; fichier d'opacité absent = 72 %, la valeur d'origine de style.css.

FLAG="$HOME/.config/waybar/notif-compact.enabled"
OPACITY_FILE="$HOME/.config/waybar/notif-opacity"
SRC="$HOME/.config/swaync/config.json"
OUT="$HOME/.config/swaync/config-active.json"
STYLE_SRC="$HOME/.config/swaync/style.css"
STYLE_OUT="$HOME/.config/swaync/style-active.css"

# 380 px en normal (valeur de config.json). Le corps d'image suit, sinon une
# capture de 200 px déborderait d'une carte de 280.
COMPACT_WIDTH=280
COMPACT_IMAGE_WIDTH=140
COMPACT_IMAGE_HEIGHT=90

DEFAULT_OPACITY=72   # l'alpha écrit en dur dans style.css (@base, @crust)

read_opacity() {
    local v
    v=$(cat "$OPACITY_FILE" 2>/dev/null)
    case "$v" in
        ''|*[!0-9]*) v=$DEFAULT_OPACITY ;;
    esac
    [ "$v" -ge 10 ] 2>/dev/null || v=10
    [ "$v" -le 100 ] 2>/dev/null || v=100
    printf '%s' "$v"
}

generate_config() {
    if [ -f "$FLAG" ]; then
        jq --argjson w "$COMPACT_WIDTH" --argjson i "$COMPACT_IMAGE_WIDTH" \
           --argjson h "$COMPACT_IMAGE_HEIGHT" \
           '."notification-window-width" = $w
            | ."notification-body-image-width" = $i
            | ."notification-body-image-height" = $h' "$SRC" > "$OUT.tmp"
    else
        cat "$SRC" > "$OUT.tmp"
    fi
    mv "$OUT.tmp" "$OUT"
}

generate_css() {
    local pct alpha
    pct=$(read_opacity)
    alpha=$(awk -v p="$pct" 'BEGIN { printf "%.2f", p / 100 }')

    {
        cat "$STYLE_SRC"

        # GTK résout @define-color à la lecture de chaque règle : redéfinir
        # @base ici ne changerait rien aux règles écrites plus haut. Ce sont
        # donc les règles elles-mêmes qui sont réécrites, avec la couleur
        # littérale — mêmes teintes que dans style.css, seul l'alpha varie.
        cat <<EOF

/* ═══ Bloc généré par notif-compact.sh — ne pas éditer ═══
   Réécrit à chaque bascule du menu notifications de la barre. Seul
   style.css est suivi par le dépôt ; ce fichier-ci est jetable. */

/* Opacité des surfaces, curseur « Opacité » du menu notifications. */
.floating-notifications.background .notification-row .notification-background {
    background-color: rgba(30, 30, 32, $alpha);
}

.control-center {
    background-color: rgba(28, 28, 30, $alpha);
}
EOF

        # Mode discret : la largeur vient de config-active.json, tout le reste
        # est ici. Une carte étroite au texte de 14 px reste aussi haute
        # qu'avant et ne gagne que de la coupure de ligne : c'est le corps du
        # texte et les marges qui font la place occupée.
        [ -f "$FLAG" ] && cat <<'EOF'

/* ── Notifications discrètes ── */
:root {
    --notification-icon-size: 28px;
    --notification-app-icon-size: 14px;
    --notification-group-icon-size: 16px;
}

.floating-notifications.background .notification-row .notification-background {
    margin: 4px;
    border-radius: 10px;
}

.floating-notifications.background .notification-row .notification-background .notification {
    border-radius: 10px;
    padding: 0;
}

.floating-notifications.background .notification-content {
    padding: 5px 7px;
}

.floating-notifications.background .notification .summary {
    font-size: 12px;
}

.floating-notifications.background .notification .body {
    font-size: 11px;
}

.floating-notifications.background .notification .time {
    font-size: 10px;
}

.floating-notifications.background .notification-content .image {
    margin: 0 6px 0 2px;
}

.floating-notifications.background .notification-action {
    font-size: 11px;
    margin: 2px;
    padding: 1px 6px;
}

.floating-notifications.background .close-button {
    min-width: 16px;
    min-height: 16px;
    margin: 4px;
    padding: 0;
}
EOF
    } > "$STYLE_OUT.tmp"
    mv "$STYLE_OUT.tmp" "$STYLE_OUT"
}

generate() {
    generate_config
    generate_css
}

# Le CSS se recharge à chaud, la largeur non : deux chemins distincts, pour
# que déplacer le curseur d'opacité ne fasse pas clignoter toute la pile.
reload_css() {
    generate_css
    # « Location change requires restart » : swaync ne relit que le fichier
    # passé au lancement. Un swaync démarré sans -s (session d'avant ce
    # réglage, ou lancement nu) relirait style.css et ignorerait le bloc
    # généré — dans ce cas seul un redémarrage applique la valeur.
    if pgrep -x swaync >/dev/null \
       && tr '\0' '\n' < "/proc/$(pgrep -x swaync | head -1)/cmdline" \
          | grep -qx "$STYLE_OUT"; then
        swaync-client --reload-css >/dev/null 2>&1
    else
        apply
    fi
}

apply() {
    generate
    # `swaync-client --reload-config` relit bien le fichier, mais la largeur
    # n'est appliquée qu'à la création de la fenêtre des notifications
    # (vérifié sur swaync 0.12.6 : après reload, la carte reste à 380). Il
    # faut donc redémarrer swaync — c'est instantané, et les abonnés
    # (custom/dnd, bouton « Tout effacer ») se reconnectent d'eux-mêmes.
    # Ce redémarrage rattrape aussi un swaync lancé sans -c/-s.
    pkill -x swaync
    setsid -f "$0" start >/dev/null 2>&1 </dev/null
    # Attendre qu'il soit de retour sur le bus, pour que la notification de
    # confirmation envoyée juste après ne parte pas dans le vide.
    for _ in $(seq 20); do
        pgrep -x swaync >/dev/null && swaync-client --count >/dev/null 2>&1 && break
        sleep 0.1
    done
}

case "${1:-toggle}" in
    on)     touch "$FLAG"; apply ;;
    off)    rm -f "$FLAG"; apply ;;
    toggle) if [ -f "$FLAG" ]; then rm -f "$FLAG"; else touch "$FLAG"; fi; apply ;;
    opacity)
            case "$2" in
                ''|*[!0-9]*) echo "usage: notif-compact.sh opacity <10-100>" >&2; exit 1 ;;
            esac
            printf '%s\n' "$2" > "$OPACITY_FILE"
            reload_css ;;
    get-opacity) read_opacity; echo ;;
    apply)  apply ;;
    start)  generate; exec swaync -c "$OUT" -s "$STYLE_OUT" ;;
    *)      echo "usage: notif-compact.sh on|off|toggle|opacity <10-100>|apply|start" >&2; exit 1 ;;
esac

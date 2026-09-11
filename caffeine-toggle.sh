#!/bin/bash
# Caféine : bloque / rétablit la veille.
#
#   caffeine-toggle.sh          bascule
#   caffeine-toggle.sh on|off   force l'état (interrupteur du menu batterie)
#
# Mécanisme : une unité systemd transitoire (caffeine.service, posée par
# systemd-run) qui tient un inhibiteur logind « idle » en mode block. hypridle
# l'honore (ignore_systemd_inhibit = false par défaut) et reste vivant : le
# verrouillage avant suspend (capot fermé) et le réveil de l'écran continuent
# de marcher. L'ancienne version tuait hypridle, ce qui laissait fermer le
# capot sans verrouiller. L'état se lit d'un `systemctl --user is-active
# caffeine`, sans pidfile, et survit aux rechargements de waybar.
#
# Appelé par le bind SUPER+SHIFT+A, le menu batterie, le menu ⋮ et le clic
# sur l'icône custom/caffeine — qui n'est dans la barre que quand la caféine
# est active (caffeine-status.sh).
UNIT=caffeine
SIGNAL=15   # "signal" de custom/caffeine dans config-full

case "${1:-}" in
    on)  want=1 ;;
    off) want=0 ;;
    "")  systemctl --user -q is-active "$UNIT" && want=0 || want=1 ;;
    *)   echo "usage: $0 [on|off]" >&2; exit 2 ;;
esac

if [ "$want" = 1 ]; then
    systemctl --user -q is-active "$UNIT" || systemd-run --user -q --unit="$UNIT" \
        --description="Caféine — inhibiteur idle logind, veille bloquée" \
        systemd-inhibit --what=idle --who=Caféine \
            --why="Caféine activée depuis la barre" --mode=block sleep infinity || {
        notify-send -a "Caféine" -u critical "Caféine" "Impossible de poser l'inhibiteur ($UNIT.service)"
        exit 1
    }
    notify-send -a "Caféine" -u low " Caféine activée" "Veille et verrouillage bloqués"
else
    systemctl --user -q is-active "$UNIT" && systemctl --user stop "$UNIT"
    notify-send -a "Caféine" -u low " Caféine désactivée" "Veille normale rétablie"
fi

pkill -RTMIN+$SIGNAL waybar 2>/dev/null
exit 0

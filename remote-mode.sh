#!/bin/bash
# Toggle mode remote : lock + pas de veille + luminosité min + barre rouge
#
# « Pas de veille » couvre aussi le capot rabattu : l'inhibiteur porte
# `handle-lid-switch`, sans quoi logind suspendrait la machine dès la fermeture
# de l'écran — les deux autres verrous (`idle`, `sleep`) ne portent que sur
# l'inactivité et sur les demandes de veille, pas sur l'événement capot, que
# logind traite par un chemin séparé. C'est ce qui permet de laisser tourner une
# session Claude Code portable fermé, dans un sac.
#
# `handle-power-key` est de la même famille : portable fermé et remué, le bouton
# d'alimentation — logé dans le clavier — peut être enfoncé par les touches
# d'en face, et son action par défaut est l'extinction immédiate.
#
# L'extinction du panneau à la fermeture n'est PAS faite ici : elle est confiée
# à un bind Hyprland sur le capot (hypr/scripts/lid-remote.sh), pour ne pas
# éteindre l'écran quand le mode est activé portable ouvert, sur le bureau.
#
# ── Pourquoi une unité systemd et non un `systemd-inhibit &` ───────────────
#
# Le 2026-09-06, la machine a dormi capot fermé alors que le mode était affiché
# actif : barre rouge, interrupteur du menu Power sur « activé ». Le journal est
# sans appel — `systemctl suspend` accepté à 21:10, « Lid closed » suivi d'une
# veille à 04:44 : il n'y avait plus aucun inhibiteur. Le mode mentait.
#
# Il mentait parce que son état était un FICHIER, et l'inhibiteur un processus
# d'arrière-plan lancé depuis le popup Waybar — donc un enfant de
# `waybar.service`, qui porte `Restart=always` et dont le binaire a produit dix
# vidages mémoire en deux jours. Le jour où systemd recycle ce cgroup, le
# `sleep infinity` part avec, le fichier reste, et plus rien ne le dit.
#
# D'où les trois changements :
#   1. l'inhibiteur vit dans une unité transitoire à lui, hors du cgroup Waybar ;
#   2. l'état affiché est DÉRIVÉ de cette unité, plus d'un fichier témoin ;
#   3. si l'unité tombe sans qu'on l'ait demandé, `ExecStopPost` referme le mode
#      proprement et le fait savoir — la barre redevient normale, ce qui est le
#      signal le plus fiable, une notification pouvant être avalée par le DND
#      que ce mode active justement.
#
# Contrôle après coup, côté logind, qui est seul juge :
#   busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
#          org.freedesktop.login1.Manager BlockInhibited

set -u

# État de session : $XDG_RUNTIME_DIR est privé à l'utilisateur et vidé à la
# déconnexion, là où /tmp est partagé entre comptes et survit à la session —
# un mode remote interrompu brutalement y laissait son témoin « actif » alors
# que l'inhibiteur, lui, était mort.
STATE_DIR="${XDG_RUNTIME_DIR:-/tmp}"
STATE_FILE="$STATE_DIR/remote-mode-active"
BRIGHTNESS_SAVE="$STATE_DIR/remote-mode-brightness"
DND_SAVE="$STATE_DIR/remote-mode-dnd"
STYLE_DIR="$HOME/.config/waybar"
UNIT="remote-mode-inhibit"

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

log() { logger -t remote-mode -- "$*"; }

# style.css est un lien vers style-normal.css ou style-remote.css, et ce lien
# appartient au dépôt (Stow ne fait que le refléter dans ~/.config). On le
# bascule donc là où il vit réellement : un `ln -sf` dans $STYLE_DIR
# écraserait le lien de Stow par un lien absolu, et la config cesserait
# d'être suivie. Le dossier réel se déduit d'un fichier voisin non ambigu.
REAL_DIR="$(dirname "$(readlink -f "$STYLE_DIR/style-normal.css")")"

set_style() {  # set_style <fichier de style>
    ln -sfn "$1" "$REAL_DIR/style.css"
    killall -SIGUSR2 waybar 2>/dev/null
}

# L'inhibiteur est-il RÉELLEMENT posé ? On interroge l'unité, pas un fichier.
is_active() { systemctl --user is-active --quiet "$UNIT.service"; }

# Ce que logind déclare bloquer en cet instant.
blocked() {
    busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
        org.freedesktop.login1.Manager BlockInhibited 2>/dev/null
}

restore_session() {
    if [ -f "$BRIGHTNESS_SAVE" ]; then
        brightnessctl set "$(cat "$BRIGHTNESS_SAVE")" -q
        rm -f "$BRIGHTNESS_SAVE"
    fi
    # Restaurer l'état Ne pas déranger d'avant le mode remote
    if [ "$(cat "$DND_SAVE" 2>/dev/null)" != "true" ]; then
        "$STYLE_DIR/dnd-toggle.sh" off
    fi
    rm -f "$DND_SAVE"
    set_style style-normal.css
}

deactivate() {
    # Le témoin part AVANT l'unité : c'est à son absence qu'ExecStopPost
    # reconnaît un arrêt voulu et se tait.
    rm -f "$STATE_FILE"
    systemctl --user stop "$UNIT.service" 2>/dev/null
    systemctl --user reset-failed "$UNIT.service" 2>/dev/null
    restore_session
    log "désactivé"
}

activate() {
    brightnessctl get > "$BRIGHTNESS_SAVE"
    brightnessctl set 1 -q

    touch "$STATE_FILE"

    # Une unité laissée en échec par une chute précédente refuserait de
    # redémarrer sous le même nom.
    systemctl --user reset-failed "$UNIT.service" 2>/dev/null

    if ! systemd-run --user --quiet --unit="$UNIT" \
            --description="Mode remote : ni veille, ni capot, ni bouton d'alimentation" \
            --property="ExecStopPost=$SELF --inhibit-exit" \
            systemd-inhibit \
                --what=idle:sleep:handle-lid-switch:handle-power-key \
                --who="remote-mode" --why="Mode remote actif" --mode=block \
                sleep infinity; then
        log "ÉCHEC : impossible de lancer $UNIT"
        notify-send -u critical "Mode remote" \
            "Échec : l'inhibiteur n'a pas pu être posé. La machine dormira capot fermé."
        rm -f "$STATE_FILE"
        restore_session
        return 1
    fi

    # Ne pas se fier au code de retour de systemd-run : il dit que l'unité est
    # lancée, pas que logind a enregistré les quatre verrous. C'est cette
    # confusion-là qui a coûté une nuit de veille — on demande donc à logind.
    for _ in 1 2 3 4 5; do
        case "$(blocked)" in *handle-lid-switch*) break ;; esac
        sleep 0.2
    done
    case "$(blocked)" in
        *handle-lid-switch*)
            log "activé — logind bloque : $(blocked)"
            ;;
        *)
            log "ÉCHEC : logind ne bloque pas handle-lid-switch ($(blocked))"
            notify-send -u critical "Mode remote" \
                "Le capot n'est pas inhibé : la machine dormira si tu la fermes."
            ;;
    esac

    # Activer Ne pas déranger (en mémorisant l'état précédent pour le restaurer)
    swaync-client --get-dnd > "$DND_SAVE" 2>/dev/null
    "$STYLE_DIR/dnd-toggle.sh" on

    set_style style-remote.css
    loginctl lock-session
}

case "${1:-toggle}" in
    # Appelé par ExecStopPost de l'unité, à chaque arrêt. Un arrêt voulu est
    # passé par deactivate(), qui a déjà retiré le témoin : il n'y a alors rien
    # à faire. Si le témoin est encore là, l'inhibiteur est tombé tout seul —
    # cgroup recyclé, OOM, session qui se termine — et le mode doit se refermer
    # au lieu de rester affiché actif sans rien inhiber.
    --inhibit-exit)
        [ -f "$STATE_FILE" ] || exit 0
        rm -f "$STATE_FILE"
        restore_session
        log "CHUTE : l'inhibiteur s'est arrêté seul, mode refermé"
        notify-send -u critical "Mode remote interrompu" \
            "L'inhibiteur est tombé. La machine se mettra en veille capot fermé."
        ;;
    on)     is_active || activate ;;
    off)    is_active && deactivate ;;
    status) is_active && echo "actif" || echo "inactif" ;;
    toggle) if is_active; then deactivate; else activate; fi ;;
    *)      echo "usage: $(basename "$0") [toggle|on|off|status]" >&2; exit 1 ;;
esac

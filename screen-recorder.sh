#!/bin/bash
# Enregistreur d'écran (popup screen-menu.py, module custom/recorder,
# SUPER+SHIFT+R, palette de commandes).
#
# Enveloppe wf-recorder avec ce qui lui manque :
#   - trois zones : région (slurp), fenêtre (un clic sur une fenêtre visible),
#     écran entier (le moniteur qui a le focus) ;
#   - le son : aucun, système, micro, ou les deux mixés — wf-recorder n'accepte
#     qu'UNE source, d'où un sink nul où deux boucles déversent micro et
#     sortie, et dont on enregistre le moniteur ;
#   - compte à rebours, Ne pas déranger le temps de l'enregistrement, export GIF.
#
# Les réglages vivent dans $STATE/prefs (écrits par le popup) : le raccourci
# clavier reprend donc la dernière configuration choisie dans le menu.
#
# Chaque enregistrement tourne dans une session détachée (`_session`) qui
# attend wf-recorder puis range derrière lui : modules audio, Ne pas déranger,
# conversion GIF, notification. Ce rangement ne dépend donc pas de la manière
# dont wf-recorder s'arrête — un `pkill wf-recorder` le déclenche aussi.
#
# Usage : screen-recorder.sh {start [libellé] | stop | toggle [libellé] | status | dir | open-last}

set -u

DIR="${SCREEN_REC_DIR:-$HOME/Videos}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/screen-recorder"
PREFS="$STATE/prefs"
RUN="${XDG_RUNTIME_DIR:-/tmp}/screen-recorder"
PIDFILE="$RUN/pid"          # pid de wf-recorder
PATHFILE="$RUN/path"        # fichier final (le .gif en mode GIF)
LOGFILE="$RUN/log"
MIXFILE="$RUN/mix"          # modules audio chargés pour le mode « les deux »
LASTFILE="$STATE/last"
MIX_SINK="screenrec_mix"
RENDER_NODE="/dev/dri/renderD128"

mkdir -p "$STATE" "$RUN"

# Réglages par défaut, écrasés par ceux du popup.
ZONE=region      # region | window | screen
AUDIO=none       # none | system | mic | both
DELAY=0          # secondes de compte à rebours
FORMAT=mp4       # mp4 | gif
DND=0            # 1 = Ne pas déranger pendant l'enregistrement
# shellcheck source=/dev/null
[ -f "$PREFS" ] && . "$PREFS"

notify() { command -v notify-send >/dev/null && notify-send -a "Enregistreur d'écran" "$@"; }

# Réveille l'indicateur de la barre (recorder-status.sh), qui somnole entre
# deux relevés au repos.
wake_watcher() {
    local pid
    pid=$(cat "${XDG_RUNTIME_DIR:-/tmp}/waybar-recorder-watch.pid" 2>/dev/null) || return 0
    [ -n "$pid" ] && kill -RTMIN+13 "$pid" 2>/dev/null
    return 0
}

current_pid() {
    local pid
    pid=$(cat "$PIDFILE" 2>/dev/null) || return 1
    [ -n "$pid" ] && [ "$(cat "/proc/$pid/comm" 2>/dev/null)" = "wf-recorder" ] \
        || { rm -f "$PIDFILE"; return 1; }
    printf '%s' "$pid"
}

# ── Zone ────────────────────────────────────────────────────────────────────

# Affiche les arguments de géométrie de wf-recorder, un par ligne ; renvoie 1
# si la sélection a été annulée (Échap dans slurp).
pick_geometry() {
    local g
    case "$ZONE" in
        screen)
            g=$(hyprctl monitors -j 2>/dev/null | jq -r '.[] | select(.focused) | .name')
            [ -n "$g" ] && printf -- '-o\n%s\n' "$g"
            ;;
        window)
            # Les fenêtres des bureaux affichés, proposées à slurp comme autant
            # de rectangles : un clic en choisit une. Les coordonnées de
            # Hyprland sont logiques, comme celles qu'attend wf-recorder.
            g=$(hyprctl -j monitors 2>/dev/null | jq -r '[.[].activeWorkspace.id] | @json' \
                | { read -r ws; hyprctl -j clients 2>/dev/null | jq -r --argjson ws "$ws" '
                    .[] | select(.mapped and (.hidden | not) and (.workspace.id as $w | $ws | index($w)))
                        | "\(.at[0]),\(.at[1]) \(.size[0])x\(.size[1])"'; } \
                | slurp -r 2>/dev/null) || return 1
            [ -n "$g" ] && printf -- '-g\n%s\n' "$g"
            ;;
        *)
            # REGION_PICKER : même convention que ocr.sh et qr-scan.sh, pour
            # choisir la zone au clavier avec region-select.
            g=$(${REGION_PICKER:-slurp} 2>/dev/null) || return 1
            [ -n "$g" ] && printf -- '-g\n%s\n' "$g"
            ;;
    esac
    [ -n "$g" ]
}

# ── Son ─────────────────────────────────────────────────────────────────────

mix_stop() {
    local id
    [ -f "$MIXFILE" ] || return 0
    # Ordre inverse du chargement : les boucles avant le sink qu'elles nourrissent.
    for id in $(tac "$MIXFILE"); do
        pactl unload-module "$id" 2>/dev/null
    done
    rm -f "$MIXFILE"
}

mix_start() {
    local mic="$1" monitor="$2" id
    mix_stop
    id=$(pactl load-module module-null-sink sink_name="$MIX_SINK" \
            sink_properties=device.description="Enregistrement-écran" 2>/dev/null) || return 1
    echo "$id" >> "$MIXFILE"
    for src in "$mic" "$monitor"; do
        id=$(pactl load-module module-loopback source="$src" sink="$MIX_SINK" \
                latency_msec=20 source_dont_move=true sink_dont_move=true 2>/dev/null) \
            || { mix_stop; return 1; }
        echo "$id" >> "$MIXFILE"
    done
}

# Affiche le périphérique à passer à `-a`, ou rien pour une vidéo muette.
audio_device() {
    local sink mic
    [ "$FORMAT" = gif ] && return 0
    sink=$(pactl get-default-sink 2>/dev/null)
    mic=$(pactl get-default-source 2>/dev/null)
    case "$AUDIO" in
        system) [ -n "$sink" ] && printf '%s.monitor' "$sink" ;;
        mic)    [ -n "$mic" ] && printf '%s' "$mic" ;;
        both)   if [ -n "$sink" ] && [ -n "$mic" ] && mix_start "$mic" "$sink.monitor"; then
                    printf '%s.monitor' "$MIX_SINK"
                elif [ -n "$mic" ]; then
                    printf '%s' "$mic"      # repli : au moins la voix
                fi ;;
    esac
}

# ── Démarrage ───────────────────────────────────────────────────────────────

start() {
    if current_pid >/dev/null; then
        notify "Enregistrement déjà en cours"
        return 0
    fi
    local geom label stamp final
    geom=$(pick_geometry) || return 0          # sélection annulée : rien à dire

    mkdir -p "$DIR" || return 1
    stamp=$(date +%Y%m%d-%H%M%S)
    label=$(printf '%s' "${1:-}" | iconv -f utf-8 -t ascii//TRANSLIT 2>/dev/null \
            || printf '%s' "${1:-}")
    label=$(printf '%s' "$label" | tr -c 'A-Za-z0-9' '-' | tr -s '-' | sed 's/^-//; s/-$//')
    final="$DIR/recording-${stamp}${label:+_$label}.$FORMAT"

    # Session détachée (setsid) : waybar tue le groupe de processus de ce
    # qu'il a lancé à chaque rechargement de la barre, ce que provoque
    # justement l'apparition de l'indicateur (cf. voice-recorder.sh).
    setsid -f "$0" _session "$final" "$geom" >/dev/null 2>&1 < /dev/null
}

session() {
    local final="$1" video dev mute_dnd=0 pid rc codec
    local -a geom
    mapfile -t geom <<< "$2"
    video="$final"
    [ "$FORMAT" = gif ] && video="$DIR/.recording-tmp-$$.mp4"
    printf '%s\n' "$final" > "$PATHFILE"

    # Compte à rebours sur l'OSD de swayosd et non en notification : en Ne pas
    # déranger, swaync n'affiche rien, et le décompte serait muet.
    if [ "$DELAY" -gt 0 ] 2>/dev/null; then
        local i
        for ((i = DELAY; i > 0; i--)); do
            swayosd-client --custom-icon media-record \
                --custom-message "Enregistrement dans $i…" >/dev/null 2>&1
            sleep 1
        done
    fi
    # Laisse au popup qui vient de se fermer le temps de quitter l'écran :
    # en mode écran entier, rien ne sépare son clic de la première image.
    sleep 0.3

    if [ "$DND" = 1 ] && [ "$(swaync-client -D 2>/dev/null)" = false ]; then
        "$HOME/.config/waybar/dnd-toggle.sh" on >/dev/null 2>&1 && mute_dnd=1
    fi

    dev=$(audio_device)

    # Encodage matériel d'abord (VAAPI : quasi rien sur le CPU), logiciel en
    # repli si le pilote refuse — wf-recorder meurt alors dans la seconde.
    for codec in "vaapi" "cpu"; do
        local args=()
        [ "$codec" = vaapi ] && [ -e "$RENDER_NODE" ] \
            && args+=(-c h264_vaapi -d "$RENDER_NODE")
        [ "$codec" = vaapi ] && [ ! -e "$RENDER_NODE" ] && continue
        [ -n "$dev" ] && args+=("--audio=$dev")
        # -f en dernier : recorder-status.sh lit le fichier dans le dernier argument.
        wf-recorder "${args[@]}" "${geom[@]}" -f "$video" >"$LOGFILE" 2>&1 &
        pid=$!
        echo "$pid" > "$PIDFILE"
        wake_watcher
        sleep 1
        kill -0 "$pid" 2>/dev/null && break
        wait "$pid"
        rm -f "$video"
    done

    if kill -0 "$pid" 2>/dev/null; then
        [ "$codec" = cpu ] && notify -t 3000 "Encodage logiciel" "VAAPI indisponible — le CPU prend le relais."
        wait "$pid"
    fi
    rc=$?

    rm -f "$PIDFILE"
    wake_watcher
    mix_stop
    [ "$mute_dnd" = 1 ] && "$HOME/.config/waybar/dnd-toggle.sh" off >/dev/null 2>&1

    if [ ! -s "$video" ]; then
        rm -f "$video" "$PATHFILE"
        notify -u critical "Échec de l'enregistrement" "$(tail -3 "$LOGFILE" 2>/dev/null)"
        return 1
    fi

    if [ "$FORMAT" = gif ]; then
        notify -t 2000 "Conversion en GIF…"
        # Palette calculée sur la vidéo : sans elle, ffmpeg tombe sur une
        # palette générique et le texte bave. 15 i/s et 960 px de large au
        # plus : au-delà, un GIF de trente secondes dépasse vite 20 Mo.
        if ! ffmpeg -nostdin -loglevel error -y -i "$video" -vf \
"fps=15,scale='min(960,iw)':-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5" \
                "$final" 2>>"$LOGFILE"; then
            mv -f "$video" "${final%.gif}.mp4"
            final="${final%.gif}.mp4"
            notify -u critical "Conversion GIF ratée" "La vidéo est gardée en MP4."
        fi
        rm -f "$video"
    fi
    rm -f "$PATHFILE"
    printf '%s\n' "$final" > "$LASTFILE"

    # Notification à actions : elle bloque jusqu'à la réponse, ce qui ne gêne
    # personne ici — la session n'a plus rien d'autre à faire.
    local choice
    choice=$(notify-send -a "Enregistreur d'écran" -t 15000 \
                -A open=Ouvrir -A copy="Copier le fichier" -A dir=Dossier \
                "Enregistrement terminé" "$(basename "$final") — $(du -h "$final" | cut -f1)")
    case "$choice" in
        open) xdg-open "$final" >/dev/null 2>&1 ;;
        # text/uri-list : c'est ce que colle un navigateur ou Slack comme
        # pièce jointe, là où le chemin nu ne serait que du texte.
        copy) printf 'file://%s\n' "$final" | wl-copy -t text/uri-list ;;
        dir)  open_dir ;;
    esac
    return "$rc"
}

stop() {
    local pid
    pid=$(current_pid) || { notify "Aucun enregistrement en cours"; return 0; }
    # SIGINT : wf-recorder finalise le conteneur. La session détachée prend
    # le relais pour tout le reste.
    kill -INT "$pid" 2>/dev/null
}

open_dir() {
    mkdir -p "$DIR"
    # Pas xdg-open : le handler inode/directory pointe sur kitty (cf.
    # voice-recorder.sh).
    if command -v thunar >/dev/null; then
        setsid -f thunar "$DIR" >/dev/null 2>&1
    else
        setsid -f xdg-open "$DIR" >/dev/null 2>&1
    fi
}

case "${1:-toggle}" in
    start)     shift; start "${1:-}" ;;
    stop)      stop ;;
    toggle)    shift
               if current_pid >/dev/null; then stop; else start "${1:-}"; fi ;;
    status)    if pid=$(current_pid); then
                   echo "recording $pid $(cat "$PATHFILE" 2>/dev/null)"
               else
                   echo "idle"
               fi ;;
    dir)       open_dir ;;
    open-last) f=$(cat "$LASTFILE" 2>/dev/null)
               [ -f "$f" ] && setsid -f xdg-open "$f" >/dev/null 2>&1 ;;
    _session)  shift; session "$@" ;;
    *)         echo "usage: $0 {start [libellé]|stop|toggle [libellé]|status|dir|open-last}" >&2; exit 2 ;;
esac

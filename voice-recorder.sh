#!/bin/bash
# Enregistreur vocal de réunion (module waybar custom/voice-rec + menu Son).
#
# Capture simultanément le micro et la sortie audio dans UN fichier stéréo :
#   canal gauche = micro (moi)   |   canal droit = sortie (les autres)
# La séparation des canaux est ce qui permet, à la transcription, de savoir
# qui parle sans diarisation : c'est la raison du `join` plutôt qu'un `amix`.
#
# Usage : voice-recorder.sh {start [libellé] | stop | toggle [libellé] | status | dir}

set -u

DIR="${VOICE_REC_DIR:-$HOME/Recordings/meetings}"
PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/waybar-voicerec.pid"
PATHFILE="${XDG_RUNTIME_DIR:-/tmp}/waybar-voicerec.path"
LOGFILE="${XDG_RUNTIME_DIR:-/tmp}/waybar-voicerec.log"
BITRATE="48k"

notify() { command -v notify-send >/dev/null && notify-send -a "Enregistreur" "$@"; }

# Réveille l'indicateur de la barre (voice-status.sh). Au repos il sommeille
# une trentaine de secondes entre deux affichages plutôt que de tourner à la
# seconde pour ne rien dire ; sans ce signal, l'icône mettrait donc jusqu'à
# une demi-minute à apparaître ou à disparaître.
wake_watcher() {
    local pid
    pid=$(cat "${XDG_RUNTIME_DIR:-/tmp}/waybar-voicerec-watch.pid" 2>/dev/null) || return 0
    [ -n "$pid" ] && kill -RTMIN+12 "$pid" 2>/dev/null
    return 0
}

# Affiche le PID de l'enregistrement en cours, ou renvoie 1. Un pidfile
# périmé pointerait sur un process recyclé : on exige que ce soit bien un
# ffmpeg écrivant dans notre dossier.
current_pid() {
    local pid cmd
    pid=$(cat "$PIDFILE" 2>/dev/null) || return 1
    [ -n "$pid" ] || return 1
    cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null) || cmd=""
    case "$cmd" in
        *ffmpeg*"$DIR"*) printf '%s' "$pid" ;;
        *) rm -f "$PIDFILE" "$PATHFILE"; return 1 ;;
    esac
}

start() {
    if current_pid >/dev/null; then
        notify "Enregistrement déjà en cours"
        return 0
    fi

    local mic sink monitor label file stamp pid
    mic=$(pactl get-default-source 2>/dev/null)
    sink=$(pactl get-default-sink 2>/dev/null)
    [ -n "$mic" ] || { notify -u critical "Aucun micro par défaut"; return 1; }

    # Le monitor du sink capte ce que les autres disent. S'il est absent — ou
    # si le micro par défaut est lui-même un monitor — on retombe sur micro seul.
    monitor="${sink}.monitor"
    case "$mic" in *.monitor) monitor="" ;; esac
    [ -n "$monitor" ] && ! pactl list short sources 2>/dev/null \
        | awk '{print $2}' | grep -qx "$monitor" && monitor=""

    mkdir -p "$DIR" || return 1
    stamp=$(date +%Y-%m-%d_%H-%M)
    # Libellé libre saisi dans le menu Son, normalisé en nom de fichier sain.
    label=$(printf '%s' "${1:-}" | iconv -f utf-8 -t ascii//TRANSLIT 2>/dev/null \
            || printf '%s' "${1:-}")
    label=$(printf '%s' "$label" | tr -c 'A-Za-z0-9' '-' | tr -s '-' | sed 's/^-//; s/-$//')
    file="$DIR/${stamp}${label:+_$label}.ogg"

    # Micro démuté d'office : rien de plus rageant qu'une réunion enregistrée
    # muette parce que le micro était coupé depuis la veille.
    wpctl set-mute @DEFAULT_AUDIO_SOURCE@ 0 2>/dev/null

    # `set -m` place le job d'arrière-plan dans son propre groupe de process.
    # Sans lui, ffmpeg hérite du groupe du menu Son qui l'a lancé — or waybar
    # tue ce groupe entier quand il recharge sa barre (SIGUSR2), ce que fait
    # justement autofit dès que l'indicateur d'enregistrement apparaît. La
    # réunion s'arrêtait donc toute seule au bout de quelques secondes.
    set -m
    if [ -n "$monitor" ]; then
        nohup ffmpeg -nostdin -hide_banner -loglevel error -y \
            -f pulse -i "$mic" \
            -f pulse -i "$monitor" \
            -filter_complex \
"[0:a]aresample=async=1:first_pts=0,aformat=channel_layouts=mono[mic];\
[1:a]aresample=async=1:first_pts=0,aformat=channel_layouts=mono[sys];\
[mic][sys]join=inputs=2:channel_layout=stereo[out]" \
            -map "[out]" -c:a libopus -b:a "$BITRATE" -vbr on \
            "$file" >/dev/null 2>"$LOGFILE" &
    else
        nohup ffmpeg -nostdin -hide_banner -loglevel error -y \
            -f pulse -i "$mic" \
            -af "aresample=async=1:first_pts=0,aformat=channel_layouts=mono" \
            -c:a libopus -b:a "$BITRATE" -vbr on \
            "$file" >/dev/null 2>"$LOGFILE" &
    fi
    pid=$!
    set +m
    disown 2>/dev/null

    # ffmpeg peut mourir aussitôt (source occupée, codec absent) : on vérifie
    # qu'il vit encore avant d'annoncer un enregistrement en cours.
    sleep 0.6
    if ! kill -0 "$pid" 2>/dev/null; then
        notify -u critical "Échec du démarrage" "$(tail -3 "$LOGFILE" 2>/dev/null)"
        return 1
    fi

    echo "$pid" > "$PIDFILE"
    printf '%s\n' "$file" > "$PATHFILE"
    wake_watcher
    notify "Enregistrement démarré" \
        "$(basename "$file")$([ -n "$monitor" ] && echo ' — micro + sortie audio')"
}

stop() {
    local pid file
    pid=$(current_pid) || { notify "Aucun enregistrement en cours"; return 0; }
    file=$(cat "$PATHFILE" 2>/dev/null)
    # SIGINT et non KILL : ffmpeg doit finaliser l'en-tête Ogg, sans quoi le
    # fichier est illisible.
    kill -INT "$pid" 2>/dev/null
    for _ in $(seq 1 30); do
        [ -d "/proc/$pid" ] || break
        sleep 0.1
    done
    [ -d "/proc/$pid" ] && kill -TERM "$pid" 2>/dev/null
    rm -f "$PIDFILE" "$PATHFILE"
    wake_watcher
    if [ -n "$file" ] && [ -f "$file" ]; then
        notify "Enregistrement terminé" "$(basename "$file") — $(du -h "$file" | cut -f1)"
    else
        notify "Enregistrement terminé"
    fi
}

case "${1:-toggle}" in
    start)  shift; start "${1:-}" ;;
    stop)   stop ;;
    toggle) shift
            if current_pid >/dev/null; then stop; else start "${1:-}"; fi ;;
    status) if pid=$(current_pid); then
                echo "recording $pid $(cat "$PATHFILE" 2>/dev/null)"
            else
                echo "idle"
            fi ;;
    dir)    mkdir -p "$DIR"
            # Pas xdg-open : le handler inode/directory de la session pointe
            # sur kitty, le dossier s'ouvrait donc dans un terminal. Thunar est
            # le gestionnaire de fichiers du bureau, on l'appelle directement —
            # avec xdg-open en repli sur une machine où il manquerait.
            if command -v thunar >/dev/null; then
                setsid -f thunar "$DIR" >/dev/null 2>&1
            else
                setsid -f xdg-open "$DIR" >/dev/null 2>&1
            fi ;;
    *)      echo "usage: $0 {start [libellé]|stop|toggle|status|dir}" >&2; exit 2 ;;
esac

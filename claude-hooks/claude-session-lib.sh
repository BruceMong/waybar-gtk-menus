#!/usr/bin/env bash
# Bibliothèque commune aux hooks Claude Code (à sourcer, pas à exécuter).
#
# Tient à jour un fichier d'état par session dans $CS_DIR, que le module
# Waybar « custom/claude » lit pour afficher qui travaille et qui attend.
#
# Chaque session écrit un JSON :
#   { session_id, pid, dir, cwd, status, addr, ts, msg }
#
#   status = running  (une réponse est en cours)
#          | waiting  (Claude attend une action de l'utilisateur)
#          | done     (réponse terminée)
#
# `addr` est l'adresse de la fenêtre Hyprland du terminal : elle permet au
# menu Waybar de sauter directement sur la bonne session.

CS_DIR="${XDG_RUNTIME_DIR:-/tmp}/claude-sessions"

# --- Remontée de l'arbre des processus -------------------------------------
# Une seule passe donne les deux informations dont on a besoin :
#   CS_PID  : le processus `claude` de cette session (test de vie)
#   CS_ADDR : la fenêtre Hyprland qui le contient (saut de focus)
#
# On part du parent du hook et on remonte : le premier ancêtre dont le
# cmdline mentionne claude est le bon (le terminal, plus haut, peut aussi
# le mentionner s'il a été lancé avec `kitty claude`).
cs_resolve() {
    local clients pid ppid
    clients="$(hyprctl clients -j 2>/dev/null)"
    CS_PID=""
    CS_ADDR=""

    pid="$PPID"
    while [ -n "$pid" ] && [ "$pid" -gt 1 ] 2>/dev/null; do
        if [ -z "$CS_PID" ] &&
           tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q 'claude'; then
            CS_PID="$pid"
        fi
        if [ -z "$CS_ADDR" ]; then
            CS_ADDR="$(printf '%s' "$clients" | jq -r --argjson p "$pid" \
                'first(.[] | select(.pid == $p) | .address) // empty')"
            # La fenêtre est le dernier maillon utile : une fois trouvée, et
            # le pid claude avec, plus rien à remonter.
            [ -n "$CS_ADDR" ] && [ -n "$CS_PID" ] && break
        fi
        ppid="$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null)"
        [ "$ppid" = "$pid" ] && break
        pid="$ppid"
    done

    # Repli : sans ancêtre `claude` identifié, le parent direct fait un test
    # de vie acceptable.
    [ -z "$CS_PID" ] && CS_PID="$PPID"
}

# --- Écriture de l'état ----------------------------------------------------
# cs_write <status> <payload_json> [message]
cs_write() {
    local status="$1" payload="$2" msg="${3:-}"
    local sid cwd dir

    mkdir -p "$CS_DIR" 2>/dev/null || return 0

    sid="$(printf '%s' "$payload" | jq -r '.session_id // empty')"
    cwd="$(printf '%s' "$payload" | jq -r '.cwd // empty')"
    [ -n "$cwd" ] && dir="$(basename "$cwd")" || dir="Claude Code"
    # Sans session_id (hook appelé hors contexte), le pid sert d'identité.
    [ -z "$sid" ] && sid="pid-$CS_PID"

    # Écriture atomique : le module Waybar peut lire à tout moment.
    local tmp="$CS_DIR/.$sid.tmp"
    jq -n --arg sid "$sid" --arg pid "$CS_PID" --arg dir "$dir" \
          --arg cwd "$cwd" --arg status "$status" --arg addr "$CS_ADDR" \
          --arg msg "$msg" --arg ts "$(date +%s)" \
        '{session_id:$sid, pid:($pid|tonumber?), dir:$dir, cwd:$cwd,
          status:$status, addr:$addr, msg:$msg, ts:($ts|tonumber?)}' \
        > "$tmp" 2>/dev/null && mv -f "$tmp" "$CS_DIR/$sid.json" 2>/dev/null

    cs_prune_stale "$sid"
    cs_refresh_waybar
}

# --- Nettoyage des états périmés -------------------------------------------
# Un même processus `claude` enchaîne plusieurs session_id au fil du temps
# (/clear, reprise d'une conversation, compactage). Les fichiers des sessions
# précédentes n'ont plus lieu d'être : leur pid étant toujours vivant, rien ne
# les purgeait et la barre finissait par compter des sessions refermées.
cs_prune_stale() {
    local keep="$1" f pid
    [ -n "$CS_PID" ] || return 0
    for f in "$CS_DIR"/*.json; do
        [ -e "$f" ] || continue
        [ "$(basename "$f" .json)" = "$keep" ] && continue
        pid="$(jq -r '.pid // empty' "$f" 2>/dev/null)"
        [ "$pid" = "$CS_PID" ] && rm -f "$f"
    done
    return 0
}

# --- Rafraîchissement immédiat de la barre ---------------------------------
# SIGRTMIN+11 : signal dédié au module custom/claude (voir config-active).
cs_refresh_waybar() {
    pkill -RTMIN+11 waybar 2>/dev/null
    return 0
}

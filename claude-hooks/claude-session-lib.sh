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
#
# `herdr_pane` : sous herdr (multiplexeur), le pty appartient au serveur
# herdr et non à une fenêtre — `addr` reste vide, mais HERDR_PANE_ID est dans
# l'environnement de Claude. C'est lui que le menu emploie alors pour sauter
# sur le pane, y écrire une réponse, ou le refermer. Vide hors herdr.

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
          --arg pane "${HERDR_PANE_ID:-}" \
          --arg msg "$msg" --arg ts "$(date +%s)" \
        '{session_id:$sid, pid:($pid|tonumber?), dir:$dir, cwd:$cwd,
          status:$status, addr:$addr, herdr_pane:$pane, msg:$msg,
          ts:($ts|tonumber?)}' \
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

# --- Un scope systemd par session -------------------------------------------
# cgroup v2 facture la mémoire au cgroup où elle est allouée, et ne la suit
# pas quand on déplace le processus. Pour qu'une session puisse être gelée et
# poussée en swap (herdr-freeze, ALT+F : cgroup.freeze + memory.reclaim), il
# faut donc qu'elle soit dans son scope AVANT d'allouer — d'où ce déplacement
# au SessionStart, Claude et ses enfants déjà lancés compris, les MCP suivants
# héritant du cgroup. Ce qui a été alloué avant ce hook (~150 Mo de base)
# reste compté dans le scope du terminal : tant pis, l'essentiel — contexte,
# MCP — vient après.
#
# Nom : claude-<pane herdr>.scope (w6:p2 → claude-w6-p2.scope), ou
# claude-pid-<pid>.scope hors herdr. systemd-run exige un processus : un
# `tail --pid` qui s'éteint avec Claude tient le scope, et l'emporte avec lui
# — un `sleep infinity` laissait un scope et un sleep orphelins à chaque
# `claude -p` de script. Idempotent : /clear, /compact et resume relancent le
# hook sur un processus déjà placé.
#
# Rappelé aussi à chaque UserPromptSubmit : Claude lance ses MCP en parallèle
# du SessionStart, et ceux qui naissent après le hook restent dans le scope du
# terminal (constaté le 2026-09-17 : quatre @stripe/mcp de 200 Mo facturés au
# scope kitty d'herdr, que memory.reclaim ne pouvait donc pas évacuer au gel).
# Quand Claude est déjà dans son scope, on ne fait que rapatrier l'arbre.
cs_scope() {
    local unit dir p
    [ -n "$CS_PID" ] && [ -d "/proc/$CS_PID" ] || return 0
    command -v systemd-run >/dev/null 2>&1 || return 0

    unit="$(sed -n 's|.*/\(claude-[^/]*\)\.scope$|\1|p' "/proc/$CS_PID/cgroup" 2>/dev/null | head -1)"
    if [ -z "$unit" ]; then
        if [ -n "${HERDR_PANE_ID:-}" ]; then
            unit="claude-$(printf '%s' "$HERDR_PANE_ID" | tr ':' '-')"
        else
            unit="claude-pid-$CS_PID"
        fi
    fi
    dir="$(cs_scope_dir "$unit")"
    if [ -z "$dir" ]; then
        systemd-run --user --scope --unit "$unit" --quiet \
            tail --pid="$CS_PID" -s 10 -f /dev/null >/dev/null 2>&1 &
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            dir="$(cs_scope_dir "$unit")"; [ -n "$dir" ] && break; sleep 0.2
        done
        [ -n "$dir" ] || return 0
    fi
    for p in $(cs_tree "$CS_PID"); do
        grep -q "/$unit\.scope$" "/proc/$p/cgroup" 2>/dev/null && continue
        echo "$p" > "$dir/cgroup.procs" 2>/dev/null
    done
    return 0
}

cs_scope_dir() {
    find "/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service" \
         -maxdepth 2 -type d -name "$1.scope" 2>/dev/null | head -1
}

cs_tree() {
    local c
    echo "$1"
    for c in $(pgrep -P "$1" 2>/dev/null); do cs_tree "$c"; done
}

# --- Rafraîchissement immédiat de la barre ---------------------------------
# SIGRTMIN+11 : signal dédié au module custom/claude (voir config-active).
cs_refresh_waybar() {
    pkill -RTMIN+11 waybar 2>/dev/null
    return 0
}

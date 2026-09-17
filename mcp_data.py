#!/usr/bin/env python3
"""Données MCP pour le module custom/mcp et son popup.

Une session Claude Code voit trois couches de serveurs MCP, et chacune vit
dans un fichier différent :

  user     ~/.claude.json            .mcpServers            (tous les projets)
  project  <projet>/.mcp.json        .mcpServers            (versionné, à approuver)
  local    ~/.claude.json            .projects[cwd].mcpServers

Ce qu'un projet en garde se règle encore ailleurs :

  ~/.claude.json  .projects[cwd].disabledMcpServers       coupe un user/local
  <projet>/.claude/settings.local.json
      .enabledMcpjsonServers / .disabledMcpjsonServers    approuve un .mcp.json
      .enableAllProjectMcpServers                         approuve tout

Ce module lit tout cela et rend, par projet, la liste résolue. Il sait aussi
compter ce que ça coûte réellement : un serveur `stdio` est relancé par
CHAQUE session (15 sessions → 15 processus 1password), et c'est ce fan-out
qui a rempli la RAM le 2026-09-16. Les processus sont retrouvés sous le pid
de chaque session, et rattachés à leur serveur par leur ligne de commande.

L'état résolu (« connecté », « auth requise », « désactivé pour ce projet »)
vient de `claude mcp list`, lancé depuis le projet. C'est la vérité de Claude
Code, mais elle coûte ~5 s et DÉMARRE les serveurs stdio pour les sonder — un
projet dont le .mcp.json porte chrome-devtools verrait un Chrome headless se
lancer. Elle est donc mise en cache (~/.cache/waybar-mcp/) et n'est
rafraîchie d'office que pour les projets qui ont une session ouverte.
"""
import json
import os
import re
import subprocess
import time
from urllib.parse import urlparse

HOME = os.path.expanduser("~")
CLAUDE_JSON = os.path.join(HOME, ".claude.json")
SHARED_CONF = os.path.join(HOME, ".config/mcp-proxy/servers.json")
MCP_SHARED = os.path.join(HOME, ".local/bin/mcp-shared")
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME",
                                        os.path.join(HOME, ".cache")),
                         "waybar-mcp")
PROJECTS_ROOT = os.path.join(HOME, "projects")
# Passé ce délai, l'état résolu d'un projet ouvert est relu.
HEALTH_MAX_AGE = 600
# Un .mcp.json qui lance autant de serveurs stdio PAR SESSION mérite un
# avertissement : à trois sessions, c'est déjà quinze processus node.
STDIO_WARN = 5

# ---------------------------------------------------------------------------
# Lecture des fichiers
# ---------------------------------------------------------------------------


def _read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_json(path, data, mode=None):
    """Écriture atomique : Claude Code relit ~/.claude.json à tout moment, et
    un fichier à moitié écrit le ferait repartir d'une config vide."""
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    if mode is not None:
        os.chmod(tmp, mode)
    else:
        try:
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
        except OSError:
            pass
    os.replace(tmp, path)


def user_config():
    return _read_json(CLAUDE_JSON)


def shared_config():
    return _read_json(SHARED_CONF) if os.path.isfile(SHARED_CONF) else {}


def project_settings_path(cwd):
    return os.path.join(cwd, ".claude", "settings.local.json")


def transport_of(entry):
    t = entry.get("type")
    if t:
        return t
    return "http" if entry.get("url") else "stdio"


def is_local_url(url):
    host = urlparse(url or "").hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def shared_name_of(entry):
    """Nom du serveur mcp-shared derrière une URL locale, ou None."""
    url = entry.get("url") or ""
    if not is_local_url(url):
        return None
    m = re.search(r"/servers/([^/]+)/mcp", url)
    return m.group(1) if m else None


def _stdio_tokens(entry):
    """Ce qui, dans la ligne de commande d'un processus, trahit ce serveur.

    `npx -y @stripe/mcp` finit en `node …/node_modules/@stripe/mcp/…` ;
    `/opt/1Password/1password-mcp` s'exécute tel quel. On garde le nom du
    binaire et les arguments qui ressemblent à un paquet, jamais les options
    ni les valeurs courtes (`-y`, `all`) qui matcheraient n'importe quoi.
    """
    tokens = []
    cmd = os.path.basename(entry.get("command") or "")
    if cmd and cmd not in ("npx", "node", "npm", "uvx", "uv", "python",
                           "python3", "bash", "sh", "docker", "bunx"):
        tokens.append(cmd)
    for a in entry.get("args") or []:
        if a.startswith("-") or len(a) < 4 or "=" in a:
            continue
        if a in ("exec", "run", "start"):
            continue
        # `@scope/pkg@1.2` → `@scope/pkg`
        tokens.append(re.sub(r"(?<=.)@[\d.^~*a-z-]+$", "", a))
    return [t for t in tokens if t]


# ---------------------------------------------------------------------------
# Résolution par projet
# ---------------------------------------------------------------------------


def _mcpjson_approval(cwd, ucfg):
    """(tout approuvé ?, approuvés, refusés) pour le .mcp.json d'un projet.

    Les deux fichiers portent ces listes : Claude Code range son propre
    dialogue d'approbation dans ~/.claude.json, et `settings.local.json` les
    accepte aussi. On les unit.
    """
    proj = (ucfg.get("projects") or {}).get(cwd) or {}
    local = _read_json(project_settings_path(cwd))
    all_ok = bool(local.get("enableAllProjectMcpServers")
                  or proj.get("enableAllProjectMcpServers"))
    enabled = set(local.get("enabledMcpjsonServers") or []) \
        | set(proj.get("enabledMcpjsonServers") or [])
    disabled = set(local.get("disabledMcpjsonServers") or []) \
        | set(proj.get("disabledMcpjsonServers") or [])
    return all_ok, enabled, disabled


def project_servers(cwd, ucfg=None):
    """Liste résolue des serveurs d'un projet, ordre : local, project, user.

    Chaque entrée : name, scope, transport, enabled, why (si désactivé),
    target (URL ou binaire, sans arguments : un .mcp.json met sa clé d'API
    dans les args), shared (nom mcp-shared si c'est un partagé local),
    tokens (pour retrouver ses processus), origin (fichier).
    """
    ucfg = ucfg if ucfg is not None else user_config()
    proj = (ucfg.get("projects") or {}).get(cwd) or {}
    disabled_user = set(proj.get("disabledMcpServers") or [])
    all_ok, en_json, dis_json = _mcpjson_approval(cwd, ucfg)

    out, seen = [], {}

    def add(name, entry, scope, origin, enabled, why=""):
        prev = seen.get(name)
        if prev is not None:
            # Un homonyme de .mcp.json refusé pour le projet ne masque pas le
            # global : c'est le cas d'un stdio passé en partagé (cf.
            # share_server), où le global http est celui qui sert.
            if not (prev["scope"] == "project" and not prev["enabled"] and enabled):
                return
            out.remove(prev)
        tr = transport_of(entry)
        target = entry.get("url") if tr != "stdio" else (entry.get("command") or "")
        seen[name] = {
            "name": name, "scope": scope, "transport": tr, "enabled": enabled,
            "why": why, "target": target or "", "origin": origin,
            "shared": shared_name_of(entry) if tr != "stdio" else None,
            "tokens": _stdio_tokens(entry) if tr == "stdio" else [],
            "entry": entry,
        }
        out.append(seen[name])

    for name, entry in (proj.get("mcpServers") or {}).items():
        off = name in disabled_user
        add(name, entry, "local", CLAUDE_JSON, not off,
            "désactivé pour ce projet" if off else "")

    mcpjson = os.path.join(cwd, ".mcp.json")
    for name, entry in (_read_json(mcpjson).get("mcpServers") or {}).items():
        if name in dis_json:
            add(name, entry, "project", mcpjson, False, "refusé pour ce projet")
        elif all_ok or name in en_json:
            add(name, entry, "project", mcpjson, True)
        else:
            add(name, entry, "project", mcpjson, False, "pas encore approuvé")

    for name, entry in (ucfg.get("mcpServers") or {}).items():
        off = name in disabled_user
        add(name, entry, "user", CLAUDE_JSON, not off,
            "désactivé pour ce projet" if off else "")
    return out


def candidate_projects(session_cwds=(), ucfg=None):
    """Projets qui ont quelque chose à dire côté MCP.

    Ceux qui ont une session ouverte, d'abord ; puis ceux qui ont un
    .mcp.json, des serveurs locaux ou un serveur global coupé. Un projet qui
    n'a que la configuration globale n'apporte rien : il n'apparaît pas.
    """
    ucfg = ucfg if ucfg is not None else user_config()
    found = {}
    for cwd in session_cwds:
        if cwd:
            found[cwd] = True
    for cwd, proj in (ucfg.get("projects") or {}).items():
        if not os.path.isdir(cwd) or cwd == HOME:
            continue
        if proj.get("mcpServers") or proj.get("disabledMcpServers") \
                or proj.get("disabledMcpjsonServers"):
            found.setdefault(cwd, False)
    if os.path.isdir(PROJECTS_ROOT):
        for name in os.listdir(PROJECTS_ROOT):
            cwd = os.path.join(PROJECTS_ROOT, name)
            if os.path.isfile(os.path.join(cwd, ".mcp.json")):
                found.setdefault(cwd, False)
    return found


# ---------------------------------------------------------------------------
# Processus réels
# ---------------------------------------------------------------------------


def _process_table():
    """{pid: (ppid, rss_ko, cmdline)} pour tous les processus de l'utilisateur."""
    try:
        out = subprocess.run(
            ["ps", "-u", str(os.getuid()), "-o", "pid=,ppid=,rss=,args=",
             "--no-headers", "-ww"],
            capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            table[int(parts[0])] = (int(parts[1]), int(parts[2]), parts[3])
        except ValueError:
            continue
    return table


def _descendants(table, root):
    kids = {}
    for pid, (ppid, _, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    out, stack = [], list(kids.get(root, []))
    while stack:
        p = stack.pop()
        out.append(p)
        stack.extend(kids.get(p, []))
    return out, kids


def stdio_processes(sessions, servers_by_cwd):
    """Processus MCP stdio par session : {pid_session: {nom: [(pid, rss_ko)]}}.

    Un processus est attribué au premier serveur dont un jeton apparaît dans
    sa ligne de commande ; sa mémoire inclut ses propres descendants (le
    Chrome de chrome-devtools) sans qu'ils soient comptés une seconde fois.
    """
    table = _process_table()
    result = {}
    for s in sessions:
        pid, cwd = s.get("pid"), s.get("cwd")
        if not pid or pid not in table:
            continue
        servers = [e for e in servers_by_cwd.get(cwd, []) if e["tokens"]]
        if not servers:
            continue
        desc, kids = _descendants(table, pid)
        claimed = set()
        per = {}
        for p in desc:
            if p in claimed:
                continue
            # Seuls les premiers mots comptent : un `bash -c` dont le script
            # cite « 1password-mcp » n'est pas un serveur 1password.
            cmd = " ".join(table[p][2].split()[:4])
            for e in servers:
                if any(t in cmd for t in e["tokens"]):
                    sub, _ = _descendants(table, p)
                    rss = table[p][1] + sum(table[q][1] for q in sub if q in table)
                    claimed.update(sub)
                    claimed.add(p)
                    per.setdefault(e["name"], []).append((p, rss))
                    break
        if per:
            result[pid] = per
    return result


# ---------------------------------------------------------------------------
# État résolu : `claude mcp list`
# ---------------------------------------------------------------------------

_LINE = re.compile(r"^(?P<name>[^\s:][^:]*?(?::[^\s:][^:]*?)*?): (?P<target>.*?)"
                   r"(?: \((?P<kind>HTTP|SSE)\))? - (?P<mark>[✔✘!⊘⚠]) ?(?P<detail>.*)$")
_MARKS = {"✔": "ok", "✘": "failed", "!": "auth", "⊘": "disabled", "⚠": "warn"}


def _cache_path(cwd):
    return os.path.join(CACHE_DIR, cwd.strip("/").replace("/", "-") + ".json")


def health(cwd):
    """Dernier état résolu connu : {"ts": …, "servers": {nom: {state, detail}}}
    ou None si jamais vérifié."""
    data = _read_json(_cache_path(cwd))
    return data if data.get("servers") is not None else None


def health_is_stale(cwd, max_age=HEALTH_MAX_AGE):
    h = health(cwd)
    return h is None or time.time() - h.get("ts", 0) > max_age


def refresh_health(cwd, timeout=90):
    """Lance `claude mcp list` dans le projet et met le cache à jour.

    Bloquant (5 à 30 s) : à appeler dans un fil. Rend le dictionnaire écrit,
    ou None si claude manque ou ne répond pas — le cache précédent est alors
    laissé en place.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    try:
        out = subprocess.run(["claude", "mcp", "list"], cwd=cwd, env=env,
                             capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    servers = {}
    for line in (out.stdout + "\n" + out.stderr).splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        servers[m.group("name")] = {
            "state": _MARKS.get(m.group("mark"), "warn"),
            "detail": m.group("detail").strip(),
            "kind": (m.group("kind") or "stdio").lower(),
        }
    if not servers and out.returncode != 0:
        return None
    data = {"ts": time.time(), "servers": servers}
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        _write_json(_cache_path(cwd), data, mode=0o600)
    except OSError:
        pass
    return data


# ---------------------------------------------------------------------------
# Serveurs partagés (mcp-shared)
# ---------------------------------------------------------------------------


def shared_status(timeout=25):
    """`mcp-shared status --json`, ou [] si le montage n'existe pas."""
    if not os.access(MCP_SHARED, os.X_OK) or not os.path.isfile(SHARED_CONF):
        return []
    try:
        out = subprocess.run([MCP_SHARED, "status", "--json"], text=True,
                             capture_output=True, timeout=timeout).stdout
        return json.loads(out or "[]")
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Écritures
# ---------------------------------------------------------------------------


def set_user_server_disabled(cwd, name, disabled):
    """Coupe (ou rétablit) un serveur user/local pour un projet :
    ~/.claude.json .projects[cwd].disabledMcpServers."""
    cfg = user_config()
    proj = cfg.setdefault("projects", {}).setdefault(cwd, {})
    lst = [n for n in proj.get("disabledMcpServers") or [] if n != name]
    if disabled:
        lst.append(name)
    proj["disabledMcpServers"] = lst
    _write_json(CLAUDE_JSON, cfg)


def set_mcpjson_enabled(cwd, name, enabled):
    """Approuve (ou refuse) un serveur du .mcp.json d'un projet :
    <projet>/.claude/settings.local.json."""
    path = project_settings_path(cwd)
    cfg = _read_json(path)
    en = [n for n in cfg.get("enabledMcpjsonServers") or [] if n != name]
    dis = [n for n in cfg.get("disabledMcpjsonServers") or [] if n != name]
    (en if enabled else dis).append(name)
    cfg["enabledMcpjsonServers"] = en
    if dis or "disabledMcpjsonServers" in cfg:
        cfg["disabledMcpjsonServers"] = dis
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_json(path, cfg, mode=0o644 if not os.path.exists(path) else None)
    # L'approbation vit aussi dans ~/.claude.json : sans la retirer de là, un
    # serveur refusé ici resterait approuvé par le dialogue de Claude Code.
    ucfg = user_config()
    proj = (ucfg.get("projects") or {}).get(cwd)
    if proj and name in (proj.get("enabledMcpjsonServers") or []) and not enabled:
        proj["enabledMcpjsonServers"] = [
            n for n in proj["enabledMcpjsonServers"] if n != name]
        _write_json(CLAUDE_JSON, ucfg)


def next_shared_port():
    ports = [int(v.get("port", 0)) for v in shared_config().values()
             if isinstance(v, dict)]
    return max(ports + [8100]) + 1


def share_server(cwd, server):
    """Fait d'un serveur stdio un serveur partagé mcp-shared.

    1. l'ajoute à servers.json (600) sur le prochain port libre ;
    2. active et démarre son unité ;
    3. rebranche la config Claude : l'entrée d'origine devient http
       (scope user ou local), ou, pour un .mcp.json qu'on ne veut pas
       réécrire (versionné), une entrée http en scope user plus un refus
       du stdio pour ce projet — le scope project l'emporterait sinon.
    Rend (ok, message).
    """
    name, entry = server["name"], server["entry"]
    if server["transport"] != "stdio":
        return False, "déjà en http"
    shared = shared_config()
    if name in shared:
        return False, "déjà partagé"
    if not os.access(MCP_SHARED, os.X_OK):
        return False, "mcp-shared absent"
    port = next_shared_port()
    shared[name] = {"port": port, "command": entry.get("command"),
                    "args": list(entry.get("args") or [])}
    if entry.get("env"):
        shared[name]["env"] = dict(entry["env"])
    os.makedirs(os.path.dirname(SHARED_CONF), mode=0o700, exist_ok=True)
    try:
        _write_json(SHARED_CONF, shared, mode=0o600)
    except OSError as exc:
        return False, str(exc)
    try:
        subprocess.run([MCP_SHARED, "enable"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False, "mcp-shared enable a échoué"

    http_entry = {"type": "http",
                  "url": f"http://127.0.0.1:{port}/servers/{name}/mcp"}
    cfg = user_config()
    if server["scope"] == "local":
        cfg.setdefault("projects", {}).setdefault(cwd, {}) \
           .setdefault("mcpServers", {})[name] = http_entry
    else:
        cfg.setdefault("mcpServers", {})[name] = http_entry
    _write_json(CLAUDE_JSON, cfg)
    if server["scope"] == "project":
        set_mcpjson_enabled(cwd, name, False)
    return True, f"{name} partagé sur :{port}"


# ---------------------------------------------------------------------------
# Vue d'ensemble, pour la barre
# ---------------------------------------------------------------------------


def overview(sessions):
    """Ce que la barre affiche : partagés, fan-out stdio, auth manquante."""
    ucfg = user_config()
    servers_by_cwd = {}
    for s in sessions:
        cwd = s.get("cwd")
        if cwd and cwd not in servers_by_cwd:
            servers_by_cwd[cwd] = project_servers(cwd, ucfg)
    procs = stdio_processes(sessions, servers_by_cwd)
    by_name, total_rss, count = {}, 0, 0
    for per in procs.values():
        for name, lst in per.items():
            by_name[name] = by_name.get(name, 0) + len(lst)
            count += len(lst)
            total_rss += sum(r for _, r in lst)
    auth = set()
    for cwd in servers_by_cwd:
        h = health(cwd) or {}
        for name, st in (h.get("servers") or {}).items():
            if st.get("state") == "auth":
                auth.add(name)
    return {"stdio": count, "stdio_rss_ko": total_rss, "stdio_by_name": by_name,
            "auth": sorted(auth), "sessions": len(sessions)}

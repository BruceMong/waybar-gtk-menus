#!/usr/bin/env python3
"""Lecture des états de sessions Claude Code (base commune).

Les hooks Claude Code (~/.claude/hooks/claude-session-lib.sh) déposent un
fichier JSON par session dans $XDG_RUNTIME_DIR/claude-sessions. Ce module les
relit pour le module Waybar (claude-sessions.py) et pour le popup
(claude-menu.py).

Deux niveaux de lecture :

  load_sessions()               — état brut des hooks, aucune I/O lourde.
                                  C'est ce que Waybar appelle toutes les 10 s.
  load_sessions(enrich=True)    — y ajoute le titre de session, le dernier
                                  prompt, le contexte consommé, le modèle, le
                                  workspace Hyprland et l'état git du projet.
                                  Réservé au popup, ouvert à la demande.

Trois entretiens sont faits à chaque lecture :
  - purge des sessions dont le processus `claude` est mort ;
  - déduplication par pid : un même processus enchaîne plusieurs session_id
    (/clear, reprise, compactage) et seule la plus récente compte ;
  - les sessions « terminées » dont la fenêtre est à l'écran passent en
    « idle » : si Bruce la regarde, la réponse est considérée comme lue et
    elle cesse de réclamer son attention.
"""
import json
import os
import subprocess

STATE_DIR = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "claude-sessions"
)
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
# Cache des données de transcript, pour ne pas relire à chaque tick Waybar.
CACHE_FILE = os.path.join(STATE_DIR, ".transcripts.cache")

# Ordre d'affichage : le plus urgent en premier.
ORDER = {"waiting": 0, "done": 1, "running": 2, "idle": 3}
# Ces états réclament une intervention -> comptés dans le badge Waybar.
NEEDS_ATTENTION = ("waiting", "done")

# Les transcripts montent à plusieurs dizaines de Mo : on les lit à l'envers,
# par blocs, et on s'arrête dès qu'on a trouvé ce qu'on cherche.
TAIL_STEPS = (256 * 1024, 2 * 1024 * 1024)

# Aucun champ du transcript ne donne la taille de la fenêtre de contexte : on
# la déduit du nom de modèle, et l'affichage rappelle la limite retenue.
CONTEXT_LIMITS = (
    ("[1m]", 1_000_000),
    ("haiku", 200_000),
    ("sonnet", 200_000),
    ("opus", 200_000),
)
DEFAULT_CONTEXT_LIMIT = 200_000


# --------------------------------------------------------------------------
# État des hooks
# --------------------------------------------------------------------------
def alive(pid):
    """Le processus claude de cette session tourne-t-il encore ?"""
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _hypr(*args):
    try:
        out = subprocess.run(["hyprctl", *args, "-j"],
                             capture_output=True, text=True, timeout=2).stdout
        return json.loads(out)
    except Exception:
        return None


def active_window():
    data = _hypr("activewindow")
    return (data or {}).get("address", "")


def _drop(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _dedupe_by_pid(entries):
    """Une fenêtre `claude` vivante = une seule session affichée.

    Un même processus enchaîne plusieurs session_id au fil du temps (/clear,
    reprise d'une conversation, compactage) et chacun laisse son fichier
    d'état. Comme le pid, lui, reste vivant, rien ne les purgeait : la barre
    comptait des sessions depuis longtemps refermées. On ne garde donc que le
    fichier le plus récent par pid et on efface les autres.
    """
    latest = {}
    for path, data in entries:
        # Sans pid exploitable, le fichier reste sa propre identité.
        key = data.get("pid") or path
        kept = latest.get(key)
        if kept is None:
            latest[key] = (path, data)
        elif data.get("ts", 0) >= kept[1].get("ts", 0):
            _drop(kept[0])
            latest[key] = (path, data)
        else:
            _drop(path)
    return list(latest.values())


def load_sessions(enrich=False):
    sessions = []
    if not os.path.isdir(STATE_DIR):
        return sessions

    entries = []
    for name in sorted(os.listdir(STATE_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(STATE_DIR, name)
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            # Fichier illisible ou écriture en cours : ignoré ce tour-ci.
            continue

        if not alive(data.get("pid")):
            _drop(path)
            continue

        entries.append((path, data))

    focused = active_window()
    for path, data in _dedupe_by_pid(entries):
        # Réponse terminée sur une fenêtre sous les yeux : elle est lue.
        if (data.get("status") == "done" and focused
                and data.get("addr") == focused):
            data["status"] = "idle"
            try:
                tmp = path + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(data, fh)
                os.replace(tmp, path)
            except OSError:
                pass

        sessions.append(data)

    if enrich:
        _enrich_all(sessions)

    sessions.sort(key=lambda d: (ORDER.get(d.get("status"), 9),
                                 d.get("dir", "")))
    return sessions


# --------------------------------------------------------------------------
# Lecture des transcripts
# --------------------------------------------------------------------------
def transcript_path(session):
    """Chemin du .jsonl correspondant à une session.

    Claude Code range les transcripts sous ~/.claude/projects/<cwd encodé>/
    <session_id>.jsonl — l'encodage remplace les séparateurs par des tirets.
    """
    cwd = session.get("cwd") or ""
    sid = session.get("session_id") or ""
    if not cwd or not sid:
        return None
    encoded = cwd.replace("/", "-")
    path = os.path.join(PROJECTS_DIR, encoded, f"{sid}.jsonl")
    return path if os.path.exists(path) else None


def _tail_lines(path, max_bytes):
    """Dernières lignes complètes d'un fichier, sans le charger en entier."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            start = max(0, size - max_bytes)
            fh.seek(start)
            chunk = fh.read()
    except OSError:
        return []
    text = chunk.decode("utf-8", errors="replace")
    lines = text.split("\n")
    # La première ligne est probablement tronquée si on n'est pas au début.
    if start > 0:
        lines = lines[1:]
    return [ln for ln in lines if ln.strip()]


def read_transcript(path):
    """Extrait titre, dernier prompt, contexte, modèle et mode d'un transcript.

    On remonte le fichier à l'envers et on s'arrête dès que tout est trouvé.
    Un premier passage sur la fin suffit presque toujours ; le second, plus
    large, ne sert qu'aux sessions dont les tours sont très volumineux.

    Le titre a deux sources : `custom-title` quand la session a été renommée à
    la main — c'est le nom le plus parlant — et `ai-title` sinon.
    """
    found = {}
    for max_bytes in TAIL_STEPS:
        lines = _tail_lines(path, max_bytes)
        for raw in reversed(lines):
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            t = d.get("type")

            if t == "custom-title" and not found.get("title_custom"):
                found["title"] = d.get("customTitle")
                found["title_custom"] = True
            elif t == "ai-title" and "title" not in found:
                found["title"] = d.get("aiTitle")
            elif t == "last-prompt" and "last_prompt" not in found:
                found["last_prompt"] = d.get("lastPrompt")
            elif t == "permission-mode" and "permission_mode" not in found:
                found["permission_mode"] = d.get("permissionMode")
            elif t == "mode" and "mode" not in found:
                found["mode"] = d.get("mode")
            elif t == "assistant" and "context" not in found:
                msg = d.get("message") or {}
                usage = msg.get("usage") or {}
                total = (usage.get("input_tokens", 0)
                         + usage.get("cache_read_input_tokens", 0)
                         + usage.get("cache_creation_input_tokens", 0))
                if total:
                    found["context"] = total
                    found["model"] = msg.get("model")
                    found["output_tokens"] = usage.get("output_tokens", 0)
            if "branch" not in found and d.get("gitBranch"):
                found["branch"] = d.get("gitBranch")

            if found.get("title_custom") and "context" in found \
                    and "last_prompt" in found and "mode" in found:
                return found
        if "title" in found and "context" in found:
            break
    return found


def context_limit(model, used=0):
    """Taille de fenêtre retenue pour le calcul du pourcentage.

    Aucun champ du transcript ne la donne. On la déduit du nom de modèle, puis
    on corrige : un contexte qui dépasse la limite supposée prouve que la
    session tourne sur une fenêtre étendue.
    """
    m = (model or "").lower()
    limit = DEFAULT_CONTEXT_LIMIT
    for marker, value in CONTEXT_LIMITS:
        if marker in m:
            limit = value
            break
    if used > limit:
        limit = 1_000_000
    return limit


# --------------------------------------------------------------------------
# Contexte système : workspaces Hyprland et état git
# --------------------------------------------------------------------------
def window_workspaces():
    """Adresse de fenêtre -> nom du workspace."""
    clients = _hypr("clients") or []
    return {c.get("address"): (c.get("workspace") or {}).get("name", "?")
            for c in clients}


def git_state(cwd):
    """Branche courante et nombre de fichiers modifiés d'un projet."""
    if not cwd or not os.path.isdir(cwd):
        return {}
    try:
        branch = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        if not branch:
            return {}
        porcelain = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        changes = len([l for l in porcelain.splitlines() if l.strip()])
        return {"branch": branch, "changes": changes}
    except Exception:
        return {}


def _enrich_all(sessions):
    workspaces = window_workspaces()
    git_cache = {}
    for s in sessions:
        s["workspace"] = workspaces.get(s.get("addr"), "")

        cwd = s.get("cwd")
        if cwd not in git_cache:
            git_cache[cwd] = git_state(cwd)
        s.update({f"git_{k}": v for k, v in git_cache[cwd].items()})

        path = transcript_path(s)
        if not path:
            continue
        info = read_transcript(path)
        s["title"] = info.get("title") or ""
        s["last_prompt"] = info.get("last_prompt") or ""
        s["model"] = info.get("model") or ""
        s["permission_mode"] = info.get("permission_mode") or info.get("mode") or ""
        s["context"] = info.get("context") or 0
        s["context_limit"] = context_limit(info.get("model"), s["context"])


# --------------------------------------------------------------------------
# Cache des titres (pour le tooltip Waybar, appelé toutes les 10 s)
# --------------------------------------------------------------------------
def cached_titles(sessions, max_age=60):
    """Titres de session, relus au plus une fois par minute.

    Le tooltip Waybar veut les titres, mais relire huit transcripts à chaque
    tick serait du gâchis : un titre change rarement.
    """
    import time
    now = time.time()
    try:
        with open(CACHE_FILE) as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}

    dirty = False
    for s in sessions:
        sid = s.get("session_id")
        entry = cache.get(sid)
        if entry and now - entry.get("ts", 0) < max_age:
            s["title"] = entry.get("title", "")
            continue
        path = transcript_path(s)
        title = read_transcript(path).get("title", "") if path else ""
        s["title"] = title or ""
        cache[sid] = {"title": s["title"], "ts": now}
        dirty = True

    if dirty:
        # On ne garde que les sessions encore vivantes.
        live = {s.get("session_id") for s in sessions}
        cache = {k: v for k, v in cache.items() if k in live}
        try:
            tmp = CACHE_FILE + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(cache, fh)
            os.replace(tmp, CACHE_FILE)
        except OSError:
            pass
    return sessions


# --------------------------------------------------------------------------
# Formatage
# --------------------------------------------------------------------------
def humanize(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}min"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}"


def human_tokens(n):
    n = int(n or 0)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.0f}k"
    return f"{n / 1_000_000:.1f}M"


def short_model(model):
    """`claude-opus-4-8` -> `Opus 4.8`, pour tenir dans une ligne."""
    m = (model or "").lower()
    for family in ("opus", "sonnet", "haiku", "fable"):
        if family in m:
            digits = [p for p in m.split("-") if p.isdigit()]
            version = ".".join(digits[:2]) if digits else ""
            return f"{family.capitalize()} {version}".strip()
    return model or ""

#!/usr/bin/env python3
"""Replie automatiquement les modules waybar quand la barre déborde.

Problème résolu : sur un écran 1920x1200 en scale 1.5, waybar ne dispose que
de 1280 px logiques. Passé un certain nombre de modules, ceux de droite
sortent de l'écran et deviennent inaccessibles — sans aucun signe visible.

Principe (pattern « overflow menu » : Android, GNOME, tray Windows) :
  1. mesure la largeur réellement occupée par les modules visibles (Pango,
     avec la police et la taille du CSS) ;
  2. si ça dépasse, cache les modules du moins important au plus important,
     selon modules-priority ;
  3. si la place revient, les ressort dans l'ordre inverse.

Les modules repliés ici sont écrits dans modules-hidden-auto — jamais dans
modules-hidden, qui reste la propriété de l'utilisateur (menu de l'œil).
Ils restent tous accessibles en un clic via le menu « ⋮ », qui affiche un
compteur tant qu'il en reste de repliés.

Usage :
    autofit.py            calcule une fois et applique
    autofit.py --watch    idem toutes les INTERVAL secondes
    autofit.py --reset    oublie tous les replis automatiques
    autofit.py --debug    affiche le détail du calcul sans rien modifier
"""
import json
import os
import subprocess
import sys
import time

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(CONFIG_DIR, "config-full")
HIDDEN_FILE = os.path.join(CONFIG_DIR, "modules-hidden")
AUTO_HIDDEN_FILE = os.path.join(CONFIG_DIR, "modules-hidden-auto")
PRIORITY_FILE = os.path.join(CONFIG_DIR, "modules-priority")
GENERATE = os.path.join(CONFIG_DIR, "generate-config.py")
STEALTH_FLAG = "/tmp/waybar-stealth"
TENS_FLAG = "/tmp/waybar-ws-tens"

INTERVAL = 30           # tick de secours en mode --watch (secondes)
DEBOUNCE = 0.4          # calme à attendre après une rafale d'événements Hyprland
SAFETY = 48             # marge de sécurité (px logiques) : couvre l'imprécision
HYSTERESIS = 32         # place à regagner en plus avant de ressortir un module
FONT = "JetBrainsMono Nerd Font Propo"
FONT_PX = 13
SPACING = 4             # "spacing" de la barre, entre modules
MARGIN = 4              # margin: 4px 2px -> 2 + 2 horizontalement

ZONES = ("modules-left", "modules-center", "modules-right")

# Gabarit de mesure par module : (texte représentatif, padding horizontal,
# taille de police). Le texte est le pire cas courant, pas le pire absolu.
# padding/police repris de style-normal.css.
SPECS = {
    "custom/ws-tens":          ("+10", 16, 13),
    "custom/chrome":           ("\U000f00af", 20, 15),
    "clock#time":              ("\U000f0954 00:00", 18, 13),
    "clock#date":              ("\U000f00ed mer 30 sept", 18, 13),
    "custom/tray-handle":      ("\U000f0141", 20, 13),
    "custom/dots":             ("⋮ 9", 20, 15),
    "custom/toggle-info":      ("\U000f0208", 20, 15),
    "custom/keybinds":         ("\U000f030c", 20, 15),
    "custom/dnd":              ("\U000f009a", 20, 15),
    "cpu":                     ("\U000f07e0 100%", 18, 13),
    "temperature":             ("\U000f10c3 100°C", 18, 13),
    "memory":                  ("\U000f035b 100%", 18, 13),
    "disk":                    ("\U000f028b 100%", 18, 13),
    "network":                 ("\U000f05a9", 18, 13),
    "bluetooth":               ("\U000f00b1 AirPods P", 18, 13),
    "pulseaudio#icon":         ("\U000f057e", 13, 13),
    "pulseaudio#percentage":   ("100%", 13, 13),
    "backlight":               ("\U000f00e0 100%", 18, 13),
    "idle_inhibitor":          ("\U000f0fba", 18, 15),
    "power-profiles-daemon":   ("\U000f0442", 18, 15),
    "battery":                 ("\U000f0082 100%", 18, 13),
    "custom/power":            ("\U000f0425", 24, 15),
    "privacy":                 ("", 16, 13),
    "systemd-failed-units":    ("", 18, 13),
    "custom/updates":          ("", 18, 13),
    "hyprland/window":         ("", 18, 13),
    "mpris":                   ("", 18, 13),
    "hyprland/workspaces":     ("", 18, 13),
}

_FALLBACK_CHAR_PX = 8.2  # si Pango indisponible


# ── Mesure de texte ───────────────────────────────────────────────────────

def _make_measurer():
    """Retourne une fonction (texte, taille_px) -> largeur en px logiques."""
    try:
        import cairo
        import gi
        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Pango, PangoCairo

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        ctx = cairo.Context(surface)
        layout = PangoCairo.create_layout(ctx)
        cache = {}

        def measure(text, size_px):
            if not text:
                return 0
            key = (text, size_px)
            if key not in cache:
                desc = Pango.FontDescription(FONT)
                desc.set_absolute_size(size_px * Pango.SCALE)
                layout.set_font_description(desc)
                layout.set_text(text, -1)
                cache[key] = layout.get_pixel_size().width
            return cache[key]

        return measure
    except Exception:
        def measure(text, size_px):
            return int(len(text or "") * _FALLBACK_CHAR_PX * size_px / FONT_PX)
        return measure


measure_text = _make_measurer()


# ── État du système ───────────────────────────────────────────────────────

def run(cmd, default=""):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=3).stdout.strip()
    except Exception:
        return default


def screen_width():
    """Largeur logique (post-scaling) du moniteur portant la barre."""
    try:
        mons = json.loads(run(["hyprctl", "monitors", "-j"], "[]"))
    except json.JSONDecodeError:
        return 1920
    if not mons:
        return 1920
    focused = next((m for m in mons if m.get("focused")), mons[0])
    scale = focused.get("scale") or 1
    width = focused.get("width", 1920)
    # Un moniteur pivoté (transform 1/3) échange largeur et hauteur.
    if focused.get("transform", 0) in (1, 3, 5, 7):
        width = focused.get("height", width)
    return int(width / scale)


def workspaces_width():
    """Largeur du module workspaces : dépend du nombre d'espaces existants."""
    try:
        wss = json.loads(run(["hyprctl", "workspaces", "-j"], "[]"))
    except json.JSONDecodeError:
        return 60
    tens = os.path.exists(TENS_FLAG)
    shown = [w for w in wss
             if isinstance(w.get("id"), int)
             and (11 <= w["id"] <= 20 if tens else 1 <= w["id"] <= 10)]
    n = max(len(shown), 1)
    # Chaque bouton : chiffre + padding 6px de chaque côté.
    per_button = measure_text("10", 13) + 12
    return n * per_button + 18


def mpris_width():
    players = run(["playerctl", "-l"])
    if not players:
        return 0
    # format "{icon} {dynamic}" avec dynamic-len 20, borné par max-length 28.
    return measure_text("\U000f0230 " + "M" * 20, 13) + 18


def window_width():
    try:
        win = json.loads(run(["hyprctl", "activewindow", "-j"], "{}"))
    except json.JSONDecodeError:
        return 0
    title = (win.get("title") or "")[:25]
    return measure_text(title, 13) + 18 if title else 0


def updates_width():
    """Le module ne s'affiche que s'il y a des MAJ en attente."""
    cache = "/tmp/waybar-updates.cache"
    try:
        with open(cache, encoding="utf-8") as f:
            n = sum(1 for line in f if line.strip() and line.strip() != "---")
    except OSError:
        return 0
    return measure_text("\U000f0590 %d" % n, 13) + 18 if n else 0


def failed_units_width():
    out = run(["systemctl", "--failed", "--no-legend", "--plain"])
    return measure_text("\U000f002a 1", 13) + 18 if out else 0


def privacy_width():
    """Micro / partage d'écran actifs : le module n'existe qu'à ce moment-là."""
    out = run(["pactl", "list", "source-outputs", "short"])
    return 34 if out else 0


DYNAMIC = {
    "hyprland/workspaces": workspaces_width,
    "mpris": mpris_width,
    "hyprland/window": window_width,
    "custom/updates": updates_width,
    "systemd-failed-units": failed_units_width,
    "privacy": privacy_width,
}


# ── Calcul de largeur ─────────────────────────────────────────────────────

def module_width(mid):
    if mid in DYNAMIC:
        w = DYNAMIC[mid]()
        return w + MARGIN if w else 0
    text, pad, size = SPECS.get(mid, ("MMMM", 18, 13))
    return measure_text(text, size) + pad + MARGIN


def group_width(cfg, gid, hidden):
    """Un groupe drawer n'occupe que la largeur de son premier module visible.

    Les suivants ne sont révélés qu'au survol : les cacher ne libère rien,
    sauf à vider le groupe entier.
    """
    mods = [m for m in cfg.get(gid, {}).get("modules", []) if m not in hidden]
    if not mods:
        return 0
    if "drawer" in cfg.get(gid, {}):
        return module_width(mods[0])
    return sum(module_width(m) for m in mods)


def total_width(cfg, hidden):
    total = 0
    count = 0
    for zone in ZONES:
        for mid in cfg.get(zone, []):
            if mid in hidden:
                continue
            w = group_width(cfg, mid, hidden) if mid.startswith("group/") \
                else module_width(mid)
            if w:
                total += w
                count += 1
    return total + SPACING * max(count - 1, 0)


# ── Fichiers d'état ───────────────────────────────────────────────────────

def read_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []


def load_priority():
    steps = []
    for line in read_lines(PRIORITY_FILE):
        if not line.startswith("#"):
            steps.append(line.split())
    return steps


def apply(auto_hidden):
    with open(AUTO_HIDDEN_FILE, "w", encoding="utf-8") as f:
        for mid in auto_hidden:
            f.write(mid + "\n")
    subprocess.run(["python3", GENERATE], check=False)
    subprocess.run(["pkill", "-SIGUSR2", "waybar"], check=False)


# ── Boucle de décision ────────────────────────────────────────────────────

def compute(debug=False):
    """Retourne la liste des modules à replier, dans l'ordre de sacrifice."""
    with open(TEMPLATE, encoding="utf-8") as f:
        cfg = json.load(f)

    manual = set(read_lines(HIDDEN_FILE))
    prev = set(read_lines(AUTO_HIDDEN_FILE))
    steps = load_priority()
    capacity = screen_width() - SAFETY

    # On repart toujours de zéro : la décision ne dépend que de l'état courant,
    # ce qui évite les dérives où un module resterait replié sans raison.
    folded = []
    hidden = set(manual)
    width = total_width(cfg, hidden)

    if debug:
        print("largeur écran (logique) : %d px" % screen_width())
        print("capacité (marge %d)      : %d px" % (SAFETY, capacity))
        print("occupation actuelle      : %d px" % width)

    for step in steps:
        if all(m in hidden for m in step):
            continue
        # Hystérésis : ressortir un module déjà replié demande un peu plus de
        # place que ce qu'il occupe, sinon il clignote autour du seuil.
        limit = capacity - (HYSTERESIS if all(m in prev for m in step) else 0)
        if width <= limit:
            break
        hidden.update(step)
        folded.extend(step)
        width = total_width(cfg, hidden)
        if debug:
            print("  replie %-28s -> %d px" % (" ".join(step), width))

    if debug:
        print("final : %d px, replié = %s"
              % (total_width(cfg, hidden), folded or "rien"))
    return folded


def tick(debug=False):
    folded = compute(debug)
    if debug:
        return
    if folded != read_lines(AUTO_HIDDEN_FILE):
        apply(folded)


def safe_tick():
    if os.path.exists(STEALTH_FLAG):
        return
    try:
        tick()
    except Exception as exc:  # une mesure ratée ne doit rien casser
        print("autofit: %s" % exc, file=sys.stderr)


def event_socket():
    """Socket d'événements d'Hyprland, ou None s'il n'est pas joignable."""
    his = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    runtime = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    if not his:
        return None
    path = os.path.join(runtime, "hypr", his, ".socket2.sock")
    try:
        import socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(path)
        return sock
    except OSError:
        return None


def watch():
    """Réagit aux événements Hyprland, avec un tick lent en filet de sécurité.

    Les changements de largeur viennent surtout des workspaces et du moniteur :
    les écouter évite d'interroger le système en boucle. Le reste (lecteur
    média qui apparaît, mises à jour, service en échec) est rattrapé par le
    tick périodique.
    """
    import select

    TRIGGERS = ("workspace", "createworkspace", "destroyworkspace",
                "monitoradded", "monitorremoved", "configreloaded")

    safe_tick()
    sock = event_socket()
    buf = b""
    pending = False

    while True:
        if sock is None:
            time.sleep(INTERVAL)
            safe_tick()
            sock = event_socket()
            continue

        # Un événement déclenche un recalcul après un court délai de calme :
        # changer de workspace en émet plusieurs d'affilée.
        timeout = DEBOUNCE if pending else INTERVAL
        ready, _, _ = select.select([sock], [], [], timeout)
        if not ready:
            safe_tick()
            pending = False
            continue

        chunk = sock.recv(4096)
        if not chunk:                      # Hyprland est parti : on retente
            sock.close()
            sock = None
            continue
        buf += chunk
        lines, _, buf = buf.rpartition(b"\n")
        for line in lines.decode(errors="replace").splitlines():
            if line.split(">>", 1)[0] in TRIGGERS:
                pending = True


def main():
    args = sys.argv[1:]
    if "--reset" in args:
        apply([])
        return
    if "--debug" in args:
        compute(debug=True)
        return
    if "--watch" in args:
        watch()
    else:
        tick()


if __name__ == "__main__":
    main()

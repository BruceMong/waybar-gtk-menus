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
# État de session : $XDG_RUNTIME_DIR est privé à l'utilisateur et vidé à la
# déconnexion, là où /tmp est partagé entre comptes et survit à la session.
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
STEALTH_FLAG = os.path.join(RUNTIME_DIR, "waybar-stealth")
TENS_FLAG = os.path.join(RUNTIME_DIR, "waybar-ws-tens")

INTERVAL = 30           # tick de secours en mode --watch (secondes)
DEBOUNCE = 0.4          # calme à attendre après une rafale d'événements Hyprland
SAFETY = 48             # marge de sécurité (px logiques) : couvre l'imprécision
HYSTERESIS = 32         # place à regagner en plus avant de ressortir un module
RELOAD_GAP = 5          # délai minimal entre deux rechargements de waybar
FONT = "SF Pro Text"    # police de la barre (cf. style-normal.css)
FONT_PX = 13
SPACING = 2             # "spacing" de la barre, entre modules

# Gabarits de padding, relevés dans style-normal.css. Le nom dit la règle CSS
# d'origine : quand le style bouge, c'est ici qu'il faut répercuter.
PAD_MODULE = 10 + 2     # padding: 0 5px + margin: 3px 1px
PAD_IN_GROUP = 14       # modules dans une pilule de groupe : padding: 0 7px
PAD_GROUP = 2           # la pilule elle-même : padding: 0 1px
PAD_ICON = 10           # cellule d'icône dans un groupe : padding ramené à 5px
PAD_STATUS = 20 + 2     # zone état : padding: 0 10px + margin: 3px 1px

# Largeur de cellule des modules qui n'affichent qu'un glyphe (`min-width`
# dans le bloc « La rangée d'icônes » du CSS). Sans ce plancher, un glyphe
# étroit — la batterie fait 6px — serait mesuré à sa chasse alors qu'il
# occupe la cellule entière, et la barre se replierait trop tard.
MIN_ICON = 22

# Espaces de « rythme » ajoutés à quatre modules pour séparer les zones
# fonctionnelles de la barre (margin-left dans le CSS).
RHYTHM = {
    "group/systray": 24,
    "group/status": 24,
    "group/system": 24,
    "custom/power": 24 + 5,   # + margin-right
    "hyprland/window": 12,    # coupure entre les workspaces et le nom de l'app
}

ZONES = ("modules-left", "modules-center", "modules-right")

# Modules à cellule fixe : leur largeur est max(chasse du glyphe, MIN_ICON).
# La liste suit celle du bloc « La rangée d'icônes » de style-normal.css.
ICON_CELL = {
    "cpu", "network", "pulseaudio#icon", "custom/tray-handle",
    "custom/toggle-info", "custom/keybinds", "backlight", "battery",
    "idle_inhibitor", "power-profiles-daemon", "custom/chrome",
    "custom/power",
}

# Gabarit de mesure par module : (texte affiché, padding horizontal total,
# taille de police). Le texte est celui que le module rend vraiment — un
# `format` réduit à "{icon}" ne vaut qu'un glyphe, pas « icône + 100% » — et le
# padding vient de style-normal.css (PAD_* ci-dessus), marge comprise. Un
# gabarit trop large replie des modules alors que la barre est à moitié vide.
# Les modules qui n'apparaissent que dans un état particulier sont mesurés à
# chaud dans DYNAMIC, pas ici.
SPECS = {
    "custom/ws-tens":          ("+10", PAD_MODULE, 13),
    "custom/chrome":           ("\U000f00af", PAD_MODULE, 14),
    "clock#time":              ("00:00", PAD_MODULE, 13),
    "clock#date":              ("mar. 01 sept.", PAD_MODULE, 13),
    "custom/tray-handle":      ("\U000f0141", PAD_ICON, 14),
    "custom/dots":             ("⋮ 9", PAD_IN_GROUP, 14),
    "custom/toggle-info":      ("\U000f0208", PAD_ICON, 14),
    "custom/keybinds":         ("\U000f030c", PAD_ICON, 14),
    "custom/dnd":              ("\U000f009a 99+", PAD_STATUS, 13),
    "custom/claude":           ("\U000f051f 00/00", PAD_STATUS, 13),
    "cpu":                     ("\U000f0ee0", PAD_ICON, 14),
    "temperature":             ("\U000f10c3 100°C", PAD_IN_GROUP, 13),
    "memory":                  ("\U000f035b 100%", PAD_IN_GROUP, 13),
    "disk":                    ("\U000f028b 100%", PAD_IN_GROUP, 13),
    "network":                 ("\U000f05a9", PAD_ICON, 14),
    "bluetooth":               ("\U000f00b1 AirPods P", PAD_IN_GROUP, 13),
    "pulseaudio#icon":         ("\U000f057e", PAD_ICON, 14),
    "pulseaudio#percentage":   ("100%", 11, 13),
    "backlight":               ("\U000f00e0", PAD_MODULE, 14),
    "idle_inhibitor":          ("\U000f0fba", PAD_MODULE, 14),
    "power-profiles-daemon":   ("\U000f0442", PAD_MODULE, 14),
    "battery":                 ("\U000f0082", PAD_MODULE, 14),
    "custom/power":            ("\U000f0425", PAD_MODULE, 14),
    "privacy":                 ("", PAD_MODULE, 13),
    "systemd-failed-units":    ("", PAD_MODULE, 13),
    "custom/updates":          ("", PAD_MODULE, 13),
    "custom/voice-rec":        ("", PAD_MODULE, 13),
    "custom/recorder":         ("", PAD_MODULE, 13),
    "hyprland/window":         ("", PAD_MODULE, 13),
    "mpris":                   ("", PAD_MODULE, 13),
    "hyprland/workspaces":     ("", PAD_MODULE, 13),
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
    # Un bouton = un chiffre + padding: 0 8px + margin: 0 1px (style-normal.css).
    per_button = measure_text("8", 13) + 16 + 2
    return n * per_button + 4 + PAD_MODULE   # #workspaces padding: 0 2px


def mpris_width():
    players = run(["playerctl", "-l"])
    if not players:
        return 0
    # format "{icon} {dynamic}" avec dynamic-len 20, borné par max-length 28.
    return measure_text("\U000f0230 " + "M" * 20, 13) + PAD_MODULE


_WINDOW_WIDTH = None


def window_width():
    """Largeur du module « nom de l'app » — constante, et c'est voulu.

    Le module rend `{initialClass}` passé par les rewrites de config-full
    (« Kitty », « Google Chrome », « Bureau »…), pas le titre de la fenêtre.
    Mesurer la fenêtre focalisée faisait varier la largeur à chaque changement
    de bureau : autofit repliait un module puis le ressortait, et chaque
    bascule recharge waybar (SIGUSR2) — la barre disparaît le temps du
    rechargement et les tuiles se redimensionnent. On mesure donc le pire cas
    une fois pour toutes : la décision de repli ne dépend plus du bureau
    courant.
    """
    global _WINDOW_WIDTH
    if _WINDOW_WIDTH is None:
        try:
            with open(TEMPLATE, encoding="utf-8") as f:
                mod = json.load(f).get("hyprland/window", {})
        except (OSError, json.JSONDecodeError):
            mod = {}
        limit = mod.get("max-length", 28)
        labels = [str(v)[:limit] for v in mod.get("rewrite", {}).values()]
        _WINDOW_WIDTH = max(measure_text(t, 13)
                            for t in labels or ["Bureau"]) + PAD_MODULE
    return _WINDOW_WIDTH


def updates_width():
    """Le module ne s'affiche que s'il y a des MAJ en attente."""
    cache = os.path.join(RUNTIME_DIR, "waybar-updates.cache")
    try:
        with open(cache, encoding="utf-8") as f:
            n = sum(1 for line in f if line.strip() and line.strip() != "---")
    except OSError:
        return 0
    return measure_text("\U000f0590 %d" % n, 13) + PAD_STATUS if n else 0


def failed_units_width():
    out = run(["systemctl", "--failed", "--no-legend", "--plain"])
    return measure_text("\U000f002a 1", 13) + PAD_STATUS if out else 0


def privacy_width():
    """Micro / partage d'écran actifs : le module n'existe qu'à ce moment-là."""
    out = run(["pactl", "list", "source-outputs", "short"])
    return measure_text("\U000f036c", 13) + 6 + PAD_MODULE if out else 0


def voice_rec_width():
    """Enregistrement vocal en cours : sinon voice-status.sh n'émet rien.

    Le module disparaît alors de la barre, et lui compter une largeur revenait
    à replier un module visible pour loger un module absent.
    """
    try:
        with open(os.path.join(RUNTIME_DIR, "waybar-voicerec.pid"),
                  encoding="utf-8") as fh:
            pid = fh.read().strip()
    except OSError:
        return 0
    if not pid.isdigit() or not os.path.exists("/proc/" + pid):
        return 0
    return measure_text("\U000f036c 00:00", 13) + PAD_MODULE


def recorder_width():
    """Capture d'écran en cours (wf-recorder) : même logique que ci-dessus."""
    if not run(["pgrep", "-x", "wf-recorder"]):
        return 0
    return measure_text("\U000f0567 00:00", 13) + PAD_MODULE


DYNAMIC = {
    "hyprland/workspaces": workspaces_width,
    "mpris": mpris_width,
    "hyprland/window": window_width,
    "custom/updates": updates_width,
    "systemd-failed-units": failed_units_width,
    "privacy": privacy_width,
    "custom/voice-rec": voice_rec_width,
    "custom/recorder": recorder_width,
}


# ── Calcul de largeur ─────────────────────────────────────────────────────

def module_width(mid):
    """Largeur occupée par un module, marges comprises (0 s'il est absent)."""
    if mid in DYNAMIC:
        w = DYNAMIC[mid]()
        return w + RHYTHM.get(mid, 0) if w else 0
    text, pad, size = SPECS.get(mid, ("MMMM", PAD_MODULE, 13))
    w = measure_text(text, size)
    if mid in ICON_CELL:
        w = max(w, MIN_ICON)
    return w + pad + RHYTHM.get(mid, 0)


def group_width(cfg, gid, hidden):
    """Un groupe drawer n'occupe que la largeur de son premier module visible.

    Les suivants ne sont révélés qu'au survol : les cacher ne libère rien,
    sauf à vider le groupe entier.
    """
    mods = [m for m in cfg.get(gid, {}).get("modules", []) if m not in hidden]
    if not mods:
        return 0
    extra = PAD_GROUP + RHYTHM.get(gid, 0)
    if "drawer" in cfg.get(gid, {}):
        return module_width(mods[0]) + extra
    return sum(module_width(m) for m in mods) + extra


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


_last_reload = 0.0


def apply(auto_hidden):
    global _last_reload
    with open(AUTO_HIDDEN_FILE, "w", encoding="utf-8") as f:
        for mid in auto_hidden:
            f.write(mid + "\n")
    subprocess.run(["python3", GENERATE], check=False)
    # Un SIGUSR2 détruit et recrée la barre : visible à l'écran (les fenêtres
    # reprennent l'espace le temps du rechargement) et coûteux (waybar relance
    # tous ses modules custom). On espace donc deux rechargements.
    wait = RELOAD_GAP - (time.monotonic() - _last_reload)
    if wait > 0:
        time.sleep(wait)
    subprocess.run(["pkill", "-SIGUSR2", "waybar"], check=False)
    _last_reload = time.monotonic()


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

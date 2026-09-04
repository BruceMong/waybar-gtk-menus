#!/usr/bin/env python3
"""Module custom/calendar : le prochain rendez-vous Google Agenda.

Quatre rôles dans un seul fichier, séparés par le premier argument :

  --auth     autorisation OAuth (une fois, ouvre le navigateur)
  --sync     interroge l'API et réécrit le cache
  --tick     appelé chaque minute par le timer systemd : resynchronise si le
             cache est périmé, puis émet les rappels T-10 / T-2
  --status   JSON du module waybar (comportement par défaut, sans argument)
  --menu     ouvre le popup GTK

Ce découpage n'est pas cosmétique. `--status` s'exécute deux fois par minute
dans la boucle de waybar : il ne doit ni toucher au réseau, ni importer les
bibliothèques Google — il se contente de relire un fichier JSON déjà écrit.
Le réseau vit dans `--sync`, sous timer, où une coupure ou une lenteur de
l'API ne peut pas figer la barre.

Les secrets (identifiants OAuth et jeton) sont délibérément rangés hors du
dépôt, dans ~/.config/waybar-calendar/ : ce dossier-ci est publié tel quel
dans waybar-gtk-menus, et un jeton de rafraîchissement Google y aurait été
poussé au premier `git add`.
"""

import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

# ── Emplacements ──────────────────────────────────────────────────────────
# Configuration et secrets : hors du dépôt (cf. docstring).
CONF_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "waybar-calendar")
CLIENT_SECRET = os.path.join(CONF_DIR, "client_secret.json")
TOKEN = os.path.join(CONF_DIR, "token.json")
SETTINGS = os.path.join(CONF_DIR, "settings.json")

# Cache des événements : ~/.cache plutôt que $XDG_RUNTIME_DIR, qui est vidé à
# la déconnexion — la barre resterait muette entre l'ouverture de session et
# la première synchronisation.
CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "waybar-calendar")
CACHE = os.path.join(CACHE_DIR, "events.json")

# Marqueurs « déjà notifié » : eux relèvent bien de la session. Les garder
# d'un jour sur l'autre ferait taire le rappel d'un événement récurrent qui
# porte le même identifiant d'instance après un simple redémarrage.
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
NOTIFIED_DIR = os.path.join(RUNTIME_DIR, "waybar-calendar-notified")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Réglages par défaut (surchargeables par settings.json) ─────────────────
DEFAULTS = {
    # Au-delà de cet horizon la barre n'affiche rien : le module disparaît.
    # C'est tout l'intérêt du dispositif — la barre ne parle que lorsqu'elle
    # a quelque chose à dire.
    "horizon_min": 120,
    # En deçà, le titre de l'événement rejoint le décompte dans la barre.
    # Cinq minutes, et pas quinze : le titre coûte une centaine de pixels sur
    # une barre déjà pleine, et autofit replie d'autant plus de modules qu'il
    # est là. Le seuil coïncide avec le passage à l'orange — une seule
    # bascule à lire, au moment où il faut se lever.
    "title_from_min": 5,
    # Rappels, en minutes avant le début. Vider la liste les supprime tous.
    "reminders": [10, 2],
    # Âge maximal du cache avant que --tick ne rappelle l'API.
    "sync_every_sec": 300,
    # Fenêtre interrogée côté API, en jours : de quoi remplir le popup.
    "lookahead_days": 8,
    # Agendas à ignorer, par fragment d'identifiant. Les jours fériés et les
    # anniversaires sont des agendas comme les autres pour l'API, mais un
    # « 1 h 05 » dans la barre pour la fête nationale n'apprend rien.
    "skip_calendars": ["holiday", "#contacts", "#weather"],
    # Agendas à suivre, par identifiant exact. Vide = tous ceux que Google
    # marque comme sélectionnés, moins skip_calendars.
    "calendars": [],
}

# Noms de jours et de mois écrits ici plutôt que via strftime : le module
# tourne sous systemd et sous waybar, deux environnements dont la locale n'est
# pas la même que celle du terminal — le tooltip sortait « Sat 05 » à côté
# d'une barre en français.
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi",
         "dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre")

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
WAYBAR_SIGNAL = 14  # doit correspondre à "signal" dans config-full
ICON = "\ue878"  # « event » — Material Symbols, comme le reste de la barre
# (les popups, eux, puisent dans la Nerd Font : voir calendar-menu.py)


def settings():
    conf = dict(DEFAULTS)
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            conf.update(json.load(f))
    except (OSError, ValueError):
        pass
    return conf


# ── Cache ─────────────────────────────────────────────────────────────────

def read_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"fetched": None, "error": "no-cache", "events": []}


def write_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    # Remplacement atomique : --status lit ce fichier deux fois par minute et
    # tomberait autrement sur un JSON tronqué.
    os.replace(tmp, CACHE)


def parse(stamp):
    """ISO 8601 -> datetime conscient du fuseau."""
    dt = datetime.fromisoformat(stamp)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


# ── Synchronisation (réseau) ──────────────────────────────────────────────

def credentials():
    """Jeton OAuth valide, rafraîchi si besoin. None si rien n'est configuré."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not os.path.exists(TOKEN):
        return None
    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_token(creds)
    return creds


def save_token(creds):
    os.makedirs(CONF_DIR, exist_ok=True)
    # Le jeton porte de quoi lire l'agenda sans mot de passe : il ne doit être
    # lisible que par son propriétaire, y compris sur une machine à un seul
    # compte — les sauvegardes, elles, voyagent.
    fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def auth():
    """Flot OAuth « application de bureau », à lancer une seule fois."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not os.path.exists(CLIENT_SECRET):
        print("Identifiants OAuth absents : %s" % CLIENT_SECRET,
              file=sys.stderr)
        print("Voir la marche à suivre dans README.md (section Agenda).",
              file=sys.stderr)
        return 1
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent",
                                  authorization_prompt_message="")
    save_token(creds)
    print("Autorisation enregistrée dans %s" % TOKEN)
    return sync()


def meet_link(event):
    """Lien de visioconférence, quel que soit l'endroit où Google l'a rangé."""
    if event.get("hangoutLink"):
        return event["hangoutLink"]
    for entry in event.get("conferenceData", {}).get("entryPoints", []):
        if entry.get("entryPointType") == "video" and entry.get("uri"):
            return entry["uri"]
    return None


def declined(event):
    """Vrai si l'utilisateur a répondu « non » à cette invitation."""
    for att in event.get("attendees", []):
        if att.get("self") and att.get("responseStatus") == "declined":
            return True
    return False


def sync():
    """Interroge l'API et réécrit le cache. Renvoie un code de sortie."""
    conf = settings()
    try:
        from googleapiclient.discovery import build
        creds = credentials()
        if creds is None:
            write_cache({"fetched": None, "error": "no-token", "events": []})
            return 2
        service = build("calendar", "v3", credentials=creds,
                        cache_discovery=False)

        wanted = conf["calendars"]
        if wanted:
            ids = list(wanted)
        else:
            ids = []
            for cal in service.calendarList().list().execute().get("items", []):
                cid = cal.get("id", "")
                if cal.get("selected") is False:
                    continue
                if any(frag in cid for frag in conf["skip_calendars"]):
                    continue
                ids.append(cid)

        now = datetime.now(timezone.utc)
        t_min = now.isoformat()
        t_max = (now + timedelta(days=conf["lookahead_days"])).isoformat()

        events = []
        for cid in ids:
            resp = service.events().list(
                calendarId=cid, timeMin=t_min, timeMax=t_max,
                singleEvents=True, orderBy="startTime", maxResults=50,
            ).execute()
            for ev in resp.get("items", []):
                if ev.get("status") == "cancelled" or declined(ev):
                    continue
                start, end = ev.get("start", {}), ev.get("end", {})
                all_day = "date" in start
                events.append({
                    "id": ev.get("id", ""),
                    "summary": ev.get("summary") or "(sans titre)",
                    "start": start.get("dateTime") or start.get("date"),
                    "end": end.get("dateTime") or end.get("date"),
                    "all_day": all_day,
                    "location": ev.get("location"),
                    "link": ev.get("htmlLink"),
                    "meet": meet_link(ev),
                    "calendar": cid,
                })

        # Tri sur l'instant réel, pas sur la chaîne : deux agendas dans des
        # fuseaux différents rendent « 2026-09-05T09:00:00+02:00 » et
        # « 2026-09-05T08:30:00Z », dont l'ordre alphabétique est l'inverse de
        # l'ordre chronologique. Les journées entières n'ont pas d'heure : on
        # les place au début de leur jour, ce qui est leur sens.
        def _instant(ev):
            try:
                return parse(ev["start"])
            except ValueError:
                return datetime.max.replace(tzinfo=timezone.utc)
        events.sort(key=lambda e: (_instant(e), e["summary"]))
        write_cache({"fetched": datetime.now().astimezone().isoformat(),
                     "error": None, "events": events})
        return 0
    except Exception as exc:                                # noqa: BLE001
        # Une coupure réseau ne doit pas vider la barre : on garde les
        # événements déjà connus et on note seulement que la lecture a échoué.
        old = read_cache()
        old["error"] = str(exc)[:200]
        write_cache(old)
        return 1


def refresh_bar():
    subprocess.run(["pkill", "-RTMIN+%d" % WAYBAR_SIGNAL, "waybar"],
                   check=False, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


# ── Lecture (sans réseau) ─────────────────────────────────────────────────

def upcoming(cache, now=None):
    """Événements horodatés encore à venir ou en cours, les plus proches d'abord."""
    now = now or datetime.now().astimezone()
    out = []
    for ev in cache.get("events", []):
        if ev["all_day"]:
            continue
        try:
            start, end = parse(ev["start"]), parse(ev["end"])
        except ValueError:
            continue
        if end <= now:
            continue
        out.append(dict(ev, _start=start, _end=end,
                        _in=(start - now).total_seconds() / 60))
    # `choose` prend ahead[0] pour le prochain rendez-vous : l'ordre doit être
    # garanti ici, pas hérité de celui du cache.
    out.sort(key=lambda e: e["_start"])
    return out


def relative(minutes):
    """« 3 min », « 48 min », « 1 h 05 » — jamais plus de six caractères.

    Arrondi au supérieur : à onze minutes et quarante secondes d'un rendez-vous
    on ne dispose pas de onze minutes. Un décompte tronqué avance d'une minute
    sur la réalité, ce qui est précisément le sens où l'erreur coûte.
    """
    minutes = math.ceil(minutes)
    if minutes < 1:
        return "maintenant"
    if minutes < 60:
        return "%d min" % minutes
    return "%d h %02d" % divmod(minutes, 60)


def truncate(text, size):
    return text if len(text) <= size else text[:size - 1].rstrip() + "…"


def choose(events, conf):
    """L'événement que la barre doit montrer, et s'il a déjà commencé.

    Ce qui est sur le point de commencer prime sur ce qui est déjà en cours :
    à trois minutes du suivant, c'est lui qu'on a besoin de voir.
    """
    ahead = [e for e in events if e["_in"] > 0]
    current = next((e for e in events if e["_in"] <= 0), None)
    nxt = ahead[0] if ahead else None

    if nxt and nxt["_in"] <= conf["title_from_min"]:
        return nxt, False
    if current:
        return current, True
    if nxt and nxt["_in"] <= conf["horizon_min"]:
        return nxt, False
    return None, False


def status():
    """JSON du module. Texte vide = module absent de la barre."""
    conf = settings()
    cache = read_cache()
    now = datetime.now().astimezone()
    events = upcoming(cache, now)
    target, running = choose(events, conf)

    if target is None:
        return {"text": "", "tooltip": tooltip(cache, events)}

    if running:
        text = "%s %s" % (ICON, truncate(target["summary"], 18))
        cls = "now"
    else:
        left = relative(target["_in"])
        if target["_in"] <= conf["title_from_min"]:
            text = "%s %s · %s" % (ICON, left, truncate(target["summary"], 14))
        else:
            text = "%s %s" % (ICON, left)
        cls = ("imminent" if target["_in"] <= 5
               else "soon" if target["_in"] <= 30 else "upcoming")

    return {"text": text, "class": cls, "tooltip": tooltip(cache, events)}


def width_hint():
    """Le texte le plus large que le module portera pour l'événement en tête.

    autofit.py mesure ceci plutôt que le texte du moment. Le décompte rétrécit
    de minute en minute et le titre s'y ajoute en cours de route : mesurer la
    largeur réelle ferait osciller le repli automatique, un module chassé de
    la barre puis rappelé toutes les minutes. Réserver dès le départ la place
    du pire cas coûte une trentaine de pixels et ne bouge plus.
    """
    conf = settings()
    target, running = choose(upcoming(read_cache()), conf)
    if target is None:
        return ""
    if running:
        return "%s %s" % (ICON, truncate(target["summary"], 18))
    # La place du titre est réservée cinq minutes avant qu'il n'apparaisse :
    # assez tôt pour qu'autofit ait replié ce qu'il fallait quand il arrive,
    # assez tard pour ne pas amputer la barre pendant les deux heures où le
    # module ne montre qu'un décompte.
    if target["_in"] <= conf["title_from_min"] + 5:
        return "%s 00 min · %s" % (ICON, truncate(target["summary"], 14))
    return "%s 00 min" % ICON


def tooltip(cache, events):
    """Les prochaines heures en clair, plus l'état de la synchronisation."""
    lines = []
    now = datetime.now().astimezone()
    today = now.date()

    all_day = [e for e in cache.get("events", [])
               if e["all_day"] and e["end"] > today.isoformat()]
    for ev in all_day[:2]:
        try:
            if parse(ev["start"]).date() <= today:
                lines.append("<b>%s</b>  toute la journée" % esc(ev["summary"]))
        except ValueError:
            continue

    for ev in events[:4]:
        when = ev["_start"].strftime("%H:%M")
        if ev["_start"].date() != today:
            when = "%s %d · %s" % (JOURS[ev["_start"].weekday()][:3],
                                   ev["_start"].day, when)
        mark = "● " if ev["_in"] <= 0 else ""
        lines.append("%s<b>%s</b>  %s" % (mark, when, esc(ev["summary"])))

    if not lines:
        lines.append("Aucun rendez-vous à venir")

    err = cache.get("error")
    if err == "no-token":
        lines.append("\n<i>Agenda non autorisé — voir README</i>")
    elif err == "no-cache":
        lines.append("\n<i>en attente de la première synchronisation</i>")
    elif err:
        age = cache.get("fetched")
        if age:
            try:
                mins = int((now - parse(age)).total_seconds() / 60)
                lines.append("\n<i>hors ligne — données d'il y a %s</i>"
                             % relative(mins))
            except ValueError:
                lines.append("\n<i>synchronisation en échec</i>")
        else:
            lines.append("\n<i>synchronisation en échec</i>")
    return "\n".join(lines)


def esc(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))


# ── Rappels ───────────────────────────────────────────────────────────────

def notify(event, threshold):
    """Notification swaync, avec bouton « Rejoindre » si l'événement a un lien."""
    when = parse(event["start"]).strftime("%H:%M")
    body = when
    if event.get("location"):
        body += " · " + event["location"]

    # « Dans 10 minutes » à T-10, « Ça commence » au dernier rappel : le
    # second n'a pas à être lu, sa seule présence dit l'heure qu'il est.
    title = "Ça commence" if threshold <= 2 else "Dans %d minutes" % threshold
    urgency = "critical" if threshold <= 2 else "normal"

    link = event.get("meet") or event.get("link")
    args = ["notify-send", "-a", "Agenda", "-u", urgency,
            "-i", "x-office-calendar"]
    if link:
        args += ["-A", "join=Rejoindre"]
    args += ["%s — %s" % (title, event["summary"]), body]

    if link:
        # notify-send -A n'a la main que lorsque l'utilisateur a cliqué : lancé
        # tel quel il figerait le tick pendant toute la durée d'affichage. On
        # le détache donc, avec un shell qui ouvre le lien si le bouton est
        # pressé (notify-send imprime alors la clé de l'action).
        script = ('out=$("$@"); [ "$out" = join ] && '
                  'exec setsid -f google-chrome-stable %s' % shquote(link))
        args = ["bash", "-c", script, "bash"] + args
    subprocess.Popen(args, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def shquote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def marker(event, threshold):
    safe = "".join(c if c.isalnum() else "_"
                   for c in "%s@%s" % (event["id"], event["start"]))
    return os.path.join(NOTIFIED_DIR, "%s.%d" % (safe[:120], threshold))


def remind(conf):
    """Émet les rappels dus, une seule fois chacun."""
    if not conf["reminders"]:
        return
    os.makedirs(NOTIFIED_DIR, exist_ok=True)
    events = upcoming(read_cache())
    for ev in events:
        for threshold in conf["reminders"]:
            # Borne basse : au démarrage de la session, les marqueurs ont
            # disparu avec $XDG_RUNTIME_DIR. Sans elle, un rendez-vous
            # commencé il y a une heure déclencherait ses deux rappels d'un
            # coup au premier tick.
            if not 0 < ev["_in"] <= threshold:
                continue
            path = marker(ev, threshold)
            if os.path.exists(path):
                continue
            open(path, "a").close()
            notify(ev, threshold)
    prune_markers()


def prune_markers():
    cutoff = datetime.now().timestamp() - 86400
    try:
        for name in os.listdir(NOTIFIED_DIR):
            path = os.path.join(NOTIFIED_DIR, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def tick():
    """Une minute de travail : resynchroniser si besoin, puis rappeler."""
    conf = settings()
    cache = read_cache()
    stale = True
    if cache.get("fetched") and not cache.get("error"):
        try:
            age = (datetime.now().astimezone()
                   - parse(cache["fetched"])).total_seconds()
            stale = age >= conf["sync_every_sec"]
        except ValueError:
            pass
    if stale:
        sync()
        refresh_bar()
    remind(conf)
    return 0


# ── Entrée ────────────────────────────────────────────────────────────────

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "--status"
    if arg == "--auth":
        return auth()
    if arg == "--sync":
        code = sync()
        refresh_bar()
        return code
    if arg == "--tick":
        return tick()
    if arg == "--menu":
        os.execv(sys.executable,
                 [sys.executable, os.path.join(SCRIPT_DIR, "calendar-menu.py")])
    print(json.dumps(status(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

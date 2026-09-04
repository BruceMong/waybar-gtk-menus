#!/usr/bin/env python3
"""Popup Agenda pour Waybar (style menu luminosité).

Les rendez-vous des prochains jours, groupés par journée, une carte par jour.
Cliquer une ligne ouvre la visioconférence quand il y en a une, l'événement
dans Google Agenda sinon — c'est le geste qu'on fait vraiment quand on
regarde son agenda à trois minutes d'une réunion.

Deux points d'entrée dans la barre : le module `custom/calendar`, et la date
de l'horloge — qui ouvrait jusqu'ici Google Agenda dans Chrome. Un même popup
pour les deux plutôt que deux menus jumeaux : ce qu'on veut en cliquant sur la
date, c'est justement savoir ce qu'il y a dans la journée, et le navigateur
reste à un clic dans la carte du bas.

L'argument optionnel donne la marge droite d'ancrage, la barre n'ayant pas
les deux modules au même endroit.

Le popup ne parle jamais au réseau : il lit le cache écrit par le timer
(calendar_agenda.py --sync). « Actualiser » est le seul chemin vers l'API, et
il est explicite.
"""
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

from menu_common import LayerPopup, run_popup, caption_label  # noqa: E402

import calendar_agenda as ca

DEVNULL = subprocess.DEVNULL
WB = os.path.dirname(os.path.abspath(__file__))
GENERATE = os.path.join(WB, "generate-config.py")
# Drapeau lu par generate-config.py : présent = heure sur 12 h (AM/PM).
CLOCK_12H = os.path.join(WB, "clock-12h")
# Mêmes tables que le module de la barre, pour la même raison : la locale de
# l'environnement où tourne le popup n'est pas garantie.
JOURS, MOIS = ca.JOURS, ca.MOIS


class CalendarPopup(LayerPopup):
    """Une carte par journée, plus une carte d'actions.

    La limite de dix lignes n'est pas une contrainte technique : au-delà, le
    popup cesse d'être un coup d'œil et devient une liste qu'il faut lire —
    or Google Agenda existe déjà pour ça, et la dernière carte y mène.
    """

    IC_EVENT = "\U000f00ed"    # calendrier
    IC_VIDEO = "\U000f0567"    # caméra : l'événement porte un lien de visio
    IC_OPEN = "\U000f03cc"     # ouvrir dans une fenêtre extérieure
    IC_SYNC = "\U000f0450"     # flèches circulaires
    IC_KEY = "\U000f0306"      # clé : autorisation manquante
    IC_NEW = "\U000f0415"      # plus
    IC_COPY = "\U000f018f"     # copier
    IC_TODAY = "\U000f00f0"    # calendrier + horloge
    IC_CLOCK = "\U000f0954"    # horloge

    MAX_ROWS = 10

    def __init__(self, margin_right=200):
        super().__init__("Agenda", width=360, margin_right=margin_right)

        cache = ca.read_cache()
        if cache.get("error") == "no-token":
            self._build_unauthorized()
            return

        self._build_today()
        self._build_days(cache)
        self._build_format()
        self._build_actions(cache)

    # ---- Contenu ----

    def _build_unauthorized(self):
        card = self.add_card()
        card.info(self.IC_KEY, "Agenda non autorisé")
        self.box.pack_start(
            caption_label("Aucun jeton Google. Lancer l'autorisation ouvre "
                          "le navigateur une seule fois ; le jeton reste "
                          "ensuite dans ~/.config/waybar-calendar."),
            False, False, 0)
        actions = self.add_card()
        actions.action(self.IC_KEY, "Autoriser l'accès", chevron=True,
                       on_click=self._authorize)
        self.box.show_all()

    def _build_today(self):
        """L'heure et la date sur une seule ligne, plus le numéro de semaine.

        La barre les sépare en deux moitiés parce qu'elle doit pouvoir replier
        la date quand la place manque ; ici rien ne l'impose, et ce qu'on lit
        est bien une seule information — le moment présent. Le survol de
        l'horloge donne déjà la grille du mois : cette ligne dit ce que la
        grille ne dit pas, le jour écrit en français et la semaine ISO, qu'on
        cherche à chaque devis et chaque planning.
        """
        today = date.today()
        card = self.add_card()
        row = card.info(self.IC_TODAY, self._now(),
                        subtitle="%s %d %s"
                        % (JOURS[today.weekday()].capitalize(), today.day,
                           MOIS[today.month - 1]),
                        value="sem. %d" % today.isocalendar().week)
        # Une horloge qui ne tourne pas est pire qu'une absence d'horloge : le
        # popup reste volontiers ouvert le temps de lire l'agenda, et l'heure
        # de l'ouverture y serait encore affichée deux minutes plus tard.
        self._clock_label = row.title_label
        GLib.timeout_add_seconds(20, self._tick_clock)

    def _tick_clock(self):
        if self._clock_label.get_parent() is None:      # popup fermé
            return False
        self._clock_label.set_text(self._now())
        return True

    def _now(self):
        """L'heure courante, exactement comme la barre l'écrit.

        Zéro initial compris : le formateur de waybar ne connaît pas `%-I`, et
        montrer « 6:45 » ici pour « 06:45 » là-bas ferait douter du réglage.
        Le suffixe est posé à la main plutôt que par `%p`, qui ne rend rien en
        locale française.
        """
        now = datetime.now()
        if not self._is_12h():
            return now.strftime("%H:%M")
        return now.strftime("%I:%M ") + ("AM" if now.hour < 12 else "PM")

    @staticmethod
    def _is_12h():
        return os.path.exists(CLOCK_12H)

    def _build_format(self):
        """Le choix 24 h / AM-PM, avec son exemple à droite.

        Un interrupteur aurait demandé de deviner ce que « 12 heures » donne à
        l'écran ; deux lignes montrent les deux résultats et disent laquelle
        est active.
        """
        card = self.add_card("Format de l'heure")
        twelve = self._is_12h()
        card.action(self.IC_CLOCK, "24 heures", value="13:45",
                    selected=not twelve,
                    on_click=lambda *_: self._set_format(False))
        card.action(self.IC_CLOCK, "AM / PM", value="01:45 PM",
                    selected=twelve,
                    on_click=lambda *_: self._set_format(True))

    def _build_days(self, cache):
        events = self._collect(cache)
        if not events:
            card = self.add_card()
            card.info(self.IC_EVENT, "Aucun rendez-vous",
                      subtitle="rien dans les huit prochains jours")
            return

        current_day = None
        card = None
        for ev in events:
            day = ev["day"]
            if day != current_day:
                current_day = day
                card = self.add_card(self._day_label(day))
            icon = self.IC_VIDEO if ev["meet"] else self.IC_EVENT
            if ev["open"]:
                card.action(icon, ev["summary"], subtitle=ev["subtitle"],
                            value=ev["when"], chevron=True,
                            on_click=self._opener(ev["open"],
                                                  ev.get("calendar")))
            else:
                # Une ligne d'action sans destination se présenterait comme
                # cliquable et ne ferait rien : un événement sans lien est un
                # constat, pas une action.
                card.info(icon, ev["summary"], subtitle=ev["subtitle"],
                          value=ev["when"])

    def _build_actions(self, cache):
        card = self.add_card()
        card.action(self.IC_NEW, "Nouvel événement", chevron=True,
                    on_click=self._new_event)
        card.action(self.IC_OPEN, "Ouvrir Google Agenda", chevron=True,
                    on_click=self._open_web)
        card.action(self.IC_COPY, "Copier la date",
                    subtitle=date.today().isoformat(), on_click=self._copy_date)
        card.action(self.IC_SYNC, "Actualiser",
                    subtitle=self._freshness(cache),
                    on_click=self._sync)

    # ---- Données ----

    def _collect(self, cache):
        """Aplatit le cache en lignes prêtes à afficher, journées comprises."""
        now = datetime.now().astimezone()
        rows = []
        for ev in cache.get("events", []):
            try:
                start = ca.parse(ev["start"])
                end = ca.parse(ev["end"])
            except ValueError:
                continue
            if ev["all_day"]:
                # `end` est exclusif côté Google : un événement d'un seul jour
                # se termine « le lendemain ». Comparer à la date du jour sans
                # ce décalage ferait disparaître l'événement dès son matin.
                if end.date() <= now.date():
                    continue
                day, when, subtitle = start.date(), "", "toute la journée"
                if day < now.date():
                    day = now.date()
            else:
                if end <= now:
                    continue
                day = start.date()
                when = start.strftime("%H:%M")
                subtitle = self._subtitle(ev, start, end, now)
            rows.append({
                "day": day, "when": when, "summary": ev["summary"],
                "subtitle": subtitle, "meet": ev.get("meet"),
                "open": ev.get("meet") or ev.get("link"),
                # L'identifiant de l'agenda est l'adresse mail de son
                # propriétaire : c'est elle qui dit dans quel profil Chrome le
                # lien s'ouvrira vraiment.
                "calendar": ev.get("calendar"),
                "sort": (day, start.time() if not ev["all_day"] else
                         datetime.min.time()),
            })
        rows.sort(key=lambda r: r["sort"])
        return rows[:self.MAX_ROWS]

    def _subtitle(self, ev, start, end, now):
        parts = []
        if start <= now < end:
            parts.append("en cours · fin %s" % end.strftime("%H:%M"))
        else:
            parts.append("→ %s" % end.strftime("%H:%M"))
        if ev.get("location") and not str(ev["location"]).startswith("http"):
            parts.append(ev["location"])
        return " · ".join(parts)

    def _day_label(self, day):
        today = datetime.now().date()
        if day == today:
            return "Aujourd'hui"
        if day == today + timedelta(days=1):
            return "Demain"
        return "%s %d %s" % (JOURS[day.weekday()].capitalize(), day.day,
                             MOIS[day.month - 1])

    def _freshness(self, cache):
        if cache.get("error"):
            return "dernière lecture en échec"
        stamp = cache.get("fetched")
        if not stamp:
            return "jamais synchronisé"
        try:
            mins = int((datetime.now().astimezone()
                        - ca.parse(stamp)).total_seconds() / 60)
        except ValueError:
            return "date de synchro illisible"
        return "à jour" if mins < 2 else "il y a %s" % ca.relative(mins)

    # ---- Actions ----

    def _opener(self, url, calendar=None):
        """Ouvre la visio ou l'événement, dans le profil Chrome qui y a accès.

        Les agendas pro sont partagés vers le compte perso : ils s'affichent
        donc ici, mais leurs événements ne s'ouvrent que depuis le profil du
        compte propriétaire. `calendar_agenda.open_command` fait la traduction
        et retombe sur un lancement nu quand aucun profil ne correspond.
        """
        if not url:
            return None

        def handler(_btn):
            subprocess.Popen(ca.open_command(url, calendar),
                             stdout=DEVNULL, stderr=DEVNULL,
                             start_new_session=True)
            self.close()
        return handler

    def _set_format(self, twelve):
        """Pose ou retire le drapeau, régénère la config, recharge la barre.

        Le rechargement est détaché : waybar tue le groupe de processus de ses
        modules quand il se recrée, et ce popup en fait partie — il partirait
        avec, à mi-chemin.
        """
        if twelve == self._is_12h():
            self.close()
            return
        if twelve:
            open(CLOCK_12H, "a").close()
        else:
            try:
                os.remove(CLOCK_12H)
            except OSError:
                pass
        subprocess.Popen(
            ["bash", "-c", "python3 %s && pkill -SIGUSR2 waybar" % GENERATE],
            start_new_session=True, stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _new_event(self, _btn):
        subprocess.Popen(
            ["setsid", "-f", "google-chrome-stable",
             "https://calendar.google.com/calendar/u/0/r/eventedit"],
            stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _copy_date(self, _btn):
        """La date au format ISO dans le presse-papier — pour nommer un
        fichier, dater un devis, remplir un champ."""
        subprocess.Popen(["wl-copy", date.today().isoformat()],
                         stdout=DEVNULL, stderr=DEVNULL)
        subprocess.Popen(["notify-send", "-a", "Agenda", "Date copiée",
                          date.today().isoformat()],
                         stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _open_web(self, _btn):
        subprocess.Popen(["setsid", "-f", "google-chrome-stable",
                          "https://calendar.google.com"],
                         stdout=DEVNULL, stderr=DEVNULL)
        self.close()

    def _sync(self, _btn):
        subprocess.Popen([os.path.join(ca.SCRIPT_DIR, "calendar_agenda.py"),
                          "--sync"], stdout=DEVNULL, stderr=DEVNULL,
                         start_new_session=True)
        self.close()

    def _authorize(self, _btn):
        subprocess.Popen([os.path.join(ca.SCRIPT_DIR, "calendar_agenda.py"),
                          "--auth"], stdout=DEVNULL, stderr=DEVNULL,
                         start_new_session=True)
        self.close()


def main():
    try:
        margin = int(sys.argv[1])
    except (IndexError, ValueError):
        margin = 200
    run_popup(lambda: CalendarPopup(margin), "waybar-calendar-menu")


if __name__ == "__main__":
    main()

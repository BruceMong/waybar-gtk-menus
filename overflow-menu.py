#!/usr/bin/env python3
"""Popup « ⋮ » : lanceur des modules absents de la barre.

Deux raisons de ne pas voir un module, et le popup répond aux deux :

  - il est CACHÉ (œil, molette, repli automatique d'autofit) : la première
    carte le liste, cliquer lance son action normale — son menu, son on-click —
    comme s'il était dans la barre. Il reste caché ; seul son accès revient ;
  - il PEUT N'AVOIR RIEN À DIRE : `custom/updates` s'efface quand tout est à
    jour, `systemd-failed-units` quand aucune unité n'échoue, `custom/claude`
    et `custom/calendar` quand rien ne tourne ni n'approche. Ceux-là n'entrent
    pas dans modules-hidden et n'apparaissaient donc nulle part quand ils
    s'effaçaient : plus moyen de forcer une vérification des mises à jour tant
    qu'il n'y en avait aucune. Ils ont leur carte, listée en permanence — leur
    présence dans la barre dépend de l'instant, pas leur accessibilité.

L'inventaire vient de modules_registry.py, partagé avec le menu de l'œil. Deux
listes séparées avaient fini par diverger : `custom/ws-tens`, masquable par
l'œil, ne figurait pas ici — une fois caché, il n'était plus joignable.

La grille d'emoji couleur a laissé place à une liste : trois tuiles par rangée
imposaient de lire les noms en zigzag, et les emoji — seuls éléments colorés de
toute l'interface — attiraient l'œil bien au-delà de leur importance.
"""
import os
import subprocess

# menu_common fixe la version de GTK et fournit les cartes : ce popup n'a plus
# aucun widget à assembler à la main.
from menu_common import LayerPopup, caption_label, run_popup
from modules_registry import MODULES

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
HIDDEN_FILE = os.path.join(CONFIG_DIR, "modules-hidden")
AUTO_HIDDEN_FILE = os.path.join(CONFIG_DIR, "modules-hidden-auto")


def load_hidden():
    """Modules cachés à la main (œil / molette) + repliés par autofit.py."""
    hidden = set()
    for path in (HIDDEN_FILE, AUTO_HIDDEN_FILE):
        try:
            with open(path, encoding="utf-8") as f:
                hidden |= {line.strip() for line in f if line.strip()}
        except FileNotFoundError:
            pass
    return hidden


class OverflowMenu(LayerPopup):
    def __init__(self):
        super().__init__("Modules cachés", width=300, margin_right=110)

        hidden = load_hidden()
        cached = [m for m in MODULES if any(i in hidden for i in m.ids)]
        # Un module à état déjà listé comme caché n'a pas à l'être deux fois.
        transient = [m for m in MODULES
                     if m.transient and m not in cached and m.action]

        if cached:
            card = self.add_card()
            for module in cached:
                self._add(card, module)
        else:
            self.add_card().custom(caption_label("Aucun module caché."))

        if transient:
            # Titre volontairement neutre : ces modules-là peuvent très bien
            # être visibles dans la barre au moment où l'on regarde (il y a
            # des mises à jour, une session tourne). « Rien à signaler »
            # aurait été faux une fois sur deux ; ce qui est vrai dans tous
            # les cas, c'est que leur menu s'ouvre d'ici même quand leur icône
            # s'est effacée.
            card = self.add_card("Autres menus")
            for module in transient:
                self._add(card, module)
            self.box.pack_start(
                caption_label("Ces modules disparaissent de la barre quand "
                              "ils n'ont rien à dire."), False, False, 0)

    def _add(self, card, module):
        # Un module sans action (Tray, Disque…) n'est pas lançable : la ligne
        # reste, grisée, plutôt que de disparaître — sa présence dans la liste
        # dit qu'il est caché, ce qui est déjà une réponse.
        action = module.action
        row = card.action(
            module.icon, module.label, chevron=bool(action),
            on_click=(lambda _b, a=action: self._on_launch(_b, a))
            if action else None)
        if not action:
            row.set_sensitive(False)
            row.set_tooltip_text("Ce module n'a pas d'action à lancer")
        return row

    def _on_launch(self, _btn, action):
        subprocess.Popen(action, shell=True, start_new_session=True,
                         cwd=CONFIG_DIR)
        self.close()


def main():
    run_popup(OverflowMenu, "waybar-overflow-menu")


if __name__ == "__main__":
    main()

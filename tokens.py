#!/usr/bin/env python3
"""Palette unique des surfaces Waybar — source de vérité des couleurs.

Pourquoi ce fichier existe
──────────────────────────
La palette est cohérente — audit fait : le bleu système est `#0a84ff` partout,
et les valeurs voisines qu'on croise ont chacune leur raison (les gris de
claude-menu.py sont les équivalents OPAQUES d'un blanc translucide, parce que
le markup Pango ne connaît pas rgba() ; `#f5f5f7` contre `#ebebf0` est la
hiérarchie titre / corps). Rien à réparer, donc.

Ce qui manquait était le NOM. Quatorze scripts recopiaient les mêmes hex sans
qu'aucun ne puisse dire lequel est « l'encre primaire » : la palette tenait
par la discipline de celui qui écrivait, pas par la structure. Ce module la
nomme une fois, pour que le prochain menu l'importe au lieu de la deviner.

Les valeurs ci-dessous sont celles déjà en place, à l'identique — ce fichier
n'a jamais eu vocation à changer un pixel.

Règles d'usage (celles de style-normal.css, reprises telles quelles)
───────────────────────────────────────────────────────────────────
1. La hiérarchie se fait par l'OPACITÉ de l'encre (INK / INK2 / INK3),
   jamais par la teinte.
2. Les couleurs système ne signalent qu'un état ANORMAL : batterie critique,
   surchauffe, enregistrement, unité systemd en échec. Si tout est coloré,
   plus rien n'alerte.
3. Deux tons par accent : le ton plein sert de FOND (bouton, interrupteur),
   le ton éclairci sert de TEXTE. `#0a84ff` en texte sur fond sombre passe
   sous le seuil de contraste — c'est la raison d'être de `BLUE_TEXT`, et la
   même règle qu'appliquent gtk-3.0/gtk.css et gtk-4.0/gtk.css.

Ce module ne dépend de rien : il est importable depuis n'importe quel script
de menu, et `generate-tokens.py` s'en sert pour écrire la feuille CSS que
consomment la barre et les popups.
"""
import re

# ── Encre : hiérarchie par opacité, jamais par teinte ──────────────────────
INK = "#ebebf0"   # primaire   — valeurs, icônes actives, libellés de ligne
INK2 = "#9a9aa2"  # secondaire — sous-titres, contexte, unités
INK3 = "#68686f"  # tertiaire  — inactif, désactivé, séparateurs de texte

# Blanc pur : réservé au texte posé SUR un aplat d'accent (bouton bleu, ligne
# sélectionnée). Ce n'est pas un quatrième niveau d'encre.
ON_ACCENT = "#ffffff"

# ── Accents système Apple (dark mode) — réservés aux anomalies ─────────────
BLUE = "#0a84ff"         # accent : fond de bouton, interrupteur actif
BLUE_TEXT = "#5e9cff"    # accent en TEXTE (cf. règle 3)
BLUE_HOVER = "#409cff"   # accent survolé — éclairci d'un cran

RED = "#ff453a"          # anomalie : échec, arrêt, enregistrement
RED_HOVER = "#ff6961"
GREEN = "#32d74b"        # état sain, tâche terminée
ORANGE = "#ff9f0a"       # état volontaire qui se paie (DND, mode dégradé)
YELLOW = "#ffd60a"
PURPLE = "#bf5af2"

# ── Matériaux ──────────────────────────────────────────────────────────────
# Le flou vient du compositeur (blocs `layerrule` de la config Hyprland), jamais
# du CSS : GTK3 n'a pas de backdrop-filter. Ces valeurs ne sont que la teinte
# posée par-dessus.
#
# DEUX surfaces, et deux seulement, depuis le 2026-09-23 :
#
#   · `surface` — tout ce qui se POSE sur le contenu : popups de la barre,
#     cartes et panneau de swaync, HUD, lanceur, palette de commandes ;
#   · `barBg` — la barre elle-même, plus légère parce qu'elle BORDE l'écran
#     au lieu de se poser dessus. Un élément posé par-dessus doit être plus
#     dense que celui qui longe le bord, sinon on ne sait plus lequel est au
#     premier plan.
#
# Il y en avait cinq, nées chacune dans sa feuille : 0.72 pour les cartes
# swaync, le HUD et le lanceur, 0.74 pour les popups de la barre, 0.76 pour la
# palette, sur deux teintes voisines (28,28,30 et 30,30,32) que l'œil ne
# distingue pas. La valeur retenue est la majoritaire.
#
# Le fond du centre de notifications et les cartes qui s'y posent partagent
# désormais cette surface : leur écart n'était que de deux points de teinte,
# donc le relief ne venait de toute façon pas de là mais des remplissages
# blancs ci-dessous.
SURFACE = "rgba(30, 30, 32, 0.72)"
BAR_BG = "rgba(30, 30, 32, 0.52)"

# ── Remplissages ───────────────────────────────────────────────────────────
# Quatre rôles, une valeur chacun. Il y avait neuf valeurs pour ces quatre
# rôles — 0.06 et 0.07 pour le même liseré, 0.09 et 0.10 pour le même survol,
# 0.16, 0.17 et 0.22 pour la même sélection — parce que chaque feuille avait
# choisi la sienne sans voir les autres.
FILL_FAINT = "rgba(255, 255, 255, 0.07)"   # liseré, séparateur, ligne au repos
FILL = "rgba(255, 255, 255, 0.10)"         # survol
FILL_MID = "rgba(255, 255, 255, 0.14)"     # bordure d'une surface
FILL_HARD = "rgba(255, 255, 255, 0.17)"    # appui, sélection
FILL_STRONG = "rgba(255, 255, 255, 0.28)"  # poignée d'ascenseur : une commande
                                           # qu'on saisit, pas une surface

HAIRLINE = "rgba(255, 255, 255, 0.13)"  # bordure spéculaire d'un pixel
TOOLTIP_BG = "rgba(38, 38, 40, 0.98)"   # infobulle : opaque, elle n'est pas
                                        # floutée par le compositeur
ACCENT_DIM = "rgba(10, 132, 255, 0.20)"    # aplat d'accent très dilué
RED_WASH = "rgba(255, 69, 58, 0.28)"       # aplat d'alerte (batterie critique)
RED_WASH_SOFT = "rgba(255, 69, 58, 0.20)"

# ── Surfaces OPAQUES ───────────────────────────────────────────────────────
# Les surfaces ci-dessus sont translucides : le compositeur floute ce qui est
# derrière. Trois consommateurs n'ont pas cette chance — un terminal, un écran
# de verrouillage et le style Fusion de Qt composent sur de l'opaque. Il leur
# faut donc la même rampe, mais aplatie.
#
# Elle était jusqu'ici recopiée dans `kitty/current-theme.conf` et
# `qt6ct/colors/macos-dark.conf`, sans nom et sans lien avec le reste : c'est
# précisément ce que ce module existe pour empêcher.
#
# `BASE` est l'opaque de `SURFACE` — mêmes 30, 30, 32. `TERMINAL_BG` vaut
# 28, 28, 30 : c'est l'autre teinte, celle que le ménage du 2026-09-23 a
# écartée côté CSS sans toucher au terminal. Les deux sont gardées telles
# quelles pour que la centralisation ne change aucun pixel ; les unifier est
# désormais la suppression d'une ligne, et l'écart est invisible à l'œil.
SUNKEN = "#141416"       # creux : ombre portée, enfoncement
TERMINAL_BG = "#1c1c1e"  # fond du terminal
BASE = "#1e1e20"         # fond de fenêtre — l'opaque de SURFACE
RAISED = "#242426"       # ligne alternée d'une liste
RAISED_OFF = "#262628"   # la même, désactivée
FIELD = "#2c2c2e"        # champ de saisie, onglet actif, zone de texte
CONTROL = "#38383a"      # bouton, contrôle
CONTROL_HOVER = "#3a3a3c"
EDGE = "#48484a"         # bordure d'un élément actif
BLACK = "#000000"        # ombre portée pure — pas une encre

# ── Variantes vives ────────────────────────────────────────────────────────
# Un terminal a besoin de SEIZE couleurs, pas de six : chaque accent système
# a une variante vive. `RED_HOVER` et `BLUE_HOVER` en tiennent déjà lieu pour
# le rouge et le bleu — un survol est exactement « le même ton, un cran plus
# clair », donc la même valeur sert aux deux emplois.
GREEN_BRIGHT = "#30db5b"
YELLOW_BRIGHT = "#ffe14d"
PURPLE_BRIGHT = "#da8fff"
CYAN = "#5ac8f5"         # le cyan n'a pas d'emploi d'état : il n'existe que
CYAN_BRIGHT = "#70d7ff"  # parce que la table ANSI en réclame un
WHITE = "#d8d8dd"        # blanc ANSI — entre INK2 et INK, pas un quatrième
                         # niveau d'encre


def rgba_parts(v):
    """Rend (r, g, b, a) d'une chaîne `rgba(r, g, b, a)` ou `rgb(r, g, b)`."""
    nombres = re.findall(r"[0-9.]+", v)
    r, g, b = (int(n) for n in nombres[:3])
    a = float(nombres[3]) if len(nombres) > 3 else 1.0
    return r, g, b, a


def argb(v):
    """Rend `#aarrggbb` — la forme qu'attend qt6ct, alpha en tête."""
    if v.startswith("#"):
        return "#ff" + v[1:]
    r, g, b, a = rgba_parts(v)
    return "#%02x%02x%02x%02x" % (round(a * 255), r, g, b)


def css_vars():
    """Palette sous forme de déclarations `@define-color` GTK.

    GTK3 n'accepte pas les variables CSS (`var(--x)`) : `@define-color` est le
    seul mécanisme de nommage disponible, et il doit être déclaré avant tout
    usage dans la même feuille.
    """
    pairs = [
        ("ink", INK), ("ink2", INK2), ("ink3", INK3), ("onAccent", ON_ACCENT),
        ("sysBlue", BLUE), ("sysBlueText", BLUE_TEXT),
        ("sysBlueHover", BLUE_HOVER),
        ("sysRed", RED), ("sysRedHover", RED_HOVER),
        ("sysGreen", GREEN), ("sysOrange", ORANGE),
        ("sysYellow", YELLOW), ("sysPurple", PURPLE),
        ("surface", SURFACE), ("barBg", BAR_BG),
        ("hairline", HAIRLINE), ("tooltipBg", TOOLTIP_BG),
        ("fillFaint", FILL_FAINT), ("fill", FILL), ("fillMid", FILL_MID),
        ("fillHard", FILL_HARD), ("fillStrong", FILL_STRONG),
        ("accentDim", ACCENT_DIM),
        ("redWash", RED_WASH), ("redWashSoft", RED_WASH_SOFT),
    ]
    return "\n".join("@define-color %s %s;" % (n, v) for n, v in pairs)


def rgb_parts(v):
    """Rend (r, g, b) d'un `#rrggbb` comme d'un `rgba(...)`."""
    if v.startswith("#"):
        return tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
    return rgba_parts(v)[:3]


def rgba(v, a):
    """Reteinte : la couleur `v`, posée à l'opacité `a`.

    `a` est une CHAÎNE (« 0.9 », « 1.0 ») et non un flottant : les fichiers
    générés doivent rester comparables octet pour octet à ce qu'ils
    remplacent, et `0.9` ne s'écrit pas tout seul `0.90`.
    """
    return "rgba(%d, %d, %d, %s)" % (rgb_parts(v) + (a,))

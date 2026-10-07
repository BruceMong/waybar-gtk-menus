#!/usr/bin/env python3
"""Écrit la palette dans le format de chaque application qui la porte.

Pourquoi ce script existe
─────────────────────────
`tokens.py` est la source de vérité des couleurs. Elle ne servait d'abord
qu'aux feuilles CSS — barre, notifications, HUD, lanceur, palette de
commandes. Quatre consommateurs restaient dehors, chacun dans son format :
le terminal, l'écran de verrouillage, les applications Qt et l'invite du
shell. Changer d'accent demandait donc de retoucher quatre fichiers à la
main, et rien ne signalait l'oubli — c'était le dernier écart d'ergonomie
avec une distribution qui bascule de thème d'un seul geste.

Ce script les couvre tous. Il ne sait faire qu'une chose, déclinée en cinq
dialectes : rendre la palette dans la grammaire de celui qui la lit.

| Cible      | Forme                       | Mécanisme                     |
|------------|-----------------------------|-------------------------------|
| CSS (× 5)  | `@define-color`             | fichier voisin, importé       |
| kitty      | `background #…`             | fichier entier, `include`     |
| qt6ct      | `#aarrggbb` × 21 rôles      | fichier entier, `color_scheme_path` |
| hyprlock   | `$var = rgba(…)`            | bloc dans le fichier          |
| starship   | `[palettes.<nom>]`          | bloc dans le fichier          |

Les deux derniers n'ont **aucun mécanisme d'inclusion** : hyprlock n'implémente
pas le `source =` de Hyprland (vérifié — le mot n'existe ni dans son binaire
ni dans libhyprlang, où seules les variables sont gérées), et starship n'a
jamais eu d'`include`. Leur bloc est donc réécrit sur place, entre deux
marqueurs. Tout le reste du fichier — mise en page, commentaires, géométrie —
n'est jamais touché.

Ce que ce script ne change PAS
──────────────────────────────
Aucune valeur. Les fichiers qu'il produit sont, au premier passage,
identiques octet pour octet à ceux qu'ils remplacent. C'est la même règle que
le 2026-09-22 : centraliser et retoucher dans le même geste rendrait le
changement invisible à la relecture. Les écarts que la centralisation a mis
au jour sont notés en commentaire dans `tokens.py`, pas corrigés ici.

`gtk-3.0/gtk.css` et `gtk-4.0/gtk.css` restent dehors, sciemment : leurs noms
sont ceux de libadwaita, et ces feuilles sont lues par toutes les applications
GTK de la machine. Un import cassé y casserait l'apparence de tout le système,
pour trois couleurs.

Usage :
    generate-tokens.py            écrit les fichiers
    generate-tokens.py --check    vérifie qu'ils sont à jour (sortie 1 sinon)
"""
import os
import re
import sys

# Pas de bytecode : un `.pyc` n'est invalidé que sur (mtime À LA SECONDE,
# taille). Deux éditions de `tokens.py` dans la même seconde, de même longueur
# — remplacer un hex par un autre —, et l'import suivant relit l'ANCIENNE
# palette sans que rien ne le dise. Rencontré en testant ce script.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tokens as T  # noqa: E402

CONFIG = os.path.expanduser("~/.config")

BANDEAU = "FICHIER GÉNÉRÉ — ne pas éditer"
RAPPEL = ("Source : ~/.config/waybar/tokens.py\n"
          "Régénérer : ~/.config/waybar/generate-tokens.py\n"
          "Toute modification faite ici est perdue à la prochaine génération.")


def en_tete(ouvre, ferme, commente):
    corps = "\n".join(commente + l for l in RAPPEL.split("\n"))
    return "%s %s\n%s\n%s\n\n" % (ouvre, BANDEAU, corps, ferme)


def present(chemin, quoi):
    """Vrai si la cible est installée. Une cible absente est ignorée.

    `waybar/` est publié seul dans un dépôt public, et une restauration pose
    les packages un par un : le générateur doit tourner sur une machine où
    kitty, qt6ct, hyprlock ou starship ne sont pas encore là. Il le dit, il
    ne s'arrête pas.
    """
    if os.path.exists(chemin):
        return True
    print("%s absent, ignoré : %s" % (quoi, chemin), file=sys.stderr)
    return False


# ═══ 1. CSS — cinq feuilles ════════════════════════════════════════════════
#
# GTK résout `@import url("…")` relativement à la feuille qui importe, et un
# chemin absolu est exclu : `waybar/` est publié dans un dépôt public dont le
# garde-fou refuse tout `/home/<user>`. Chaque dossier reçoit donc sa copie.
#
# Chaque cible est désignée par une feuille EXISTANTE de son dossier. Le
# `tokens.css` est écrit à côté du fichier RÉEL vers lequel elle pointe, et non
# à côté du lien : sur cette machine les dossiers de `~/.config` sont de vrais
# dossiers remplis de liens par fichier, donc un fichier neuf écrit dans `~` y
# resterait — hors dépôt, invisible à `git status`, perdu à la restauration.
# Sur une machine sans Stow, `realpath` rend le fichier lui-même et tout se
# passe dans `~/.config` : le script n'a pas à savoir lequel des deux mondes
# il habite.
VOISINS_CSS = [
    "waybar/style-normal.css",
    "swaync/style.css",
    "swayosd/style.css",
    "walker/themes/macos/style.css",
    "walker/themes/menu/style.css",
]

EN_TETE_CSS = """/* ══ %s ══════════════════════════════════════
   %s
   ═══════════════════════════════════════════════════════════════════════ */

""" % (BANDEAU, RAPPEL.replace("\n", "\n   "))


def cible_css():
    contenu = EN_TETE_CSS + T.css_vars() + "\n"
    for voisin in VOISINS_CSS:
        chemin = os.path.join(CONFIG, voisin)
        if not os.path.exists(chemin):
            print("absent, ignoré : %s" % chemin, file=sys.stderr)
            continue
        yield (os.path.join(os.path.dirname(os.path.realpath(chemin)),
                            "tokens.css"), contenu)


# ═══ 2. kitty — fichier de thème entier ════════════════════════════════════
#
# `kitty.conf` fait `include current-theme.conf` depuis toujours : le point
# d'entrée existait déjà, c'est le fichier inclus qui était écrit à la main.
# Attention, `kitten themes` écrit au même endroit — le lancer écraserait ce
# fichier, et la prochaine génération le réécrirait par-dessus.
#
# Un terminal réclame SEIZE couleurs. Les huit normales sont les couleurs
# système d'Apple, les huit vives leurs variantes claires : le rouge d'une
# erreur de compilation est alors exactement le rouge d'une unité systemd en
# échec dans la barre juste au-dessus.
def cible_kitty():
    sortie = os.path.join(CONFIG, "kitty/current-theme.conf")
    if not present(os.path.join(CONFIG, "kitty/kitty.conf"), "kitty"):
        return
    ansi = [
        ("0", T.TERMINAL_BG), ("8", T.INK3),
        ("1", T.RED), ("9", T.RED_HOVER),
        ("2", T.GREEN), ("10", T.GREEN_BRIGHT),
        ("3", T.YELLOW), ("11", T.YELLOW_BRIGHT),
        ("4", T.BLUE), ("12", T.BLUE_HOVER),
        ("5", T.PURPLE), ("13", T.PURPLE_BRIGHT),
        ("6", T.CYAN), ("14", T.CYAN_BRIGHT),
        ("7", T.WHITE), ("15", T.INK),
    ]
    couples = "\n\n".join(
        "color%-2s %s\ncolor%-2s %s" % (ansi[i][0], ansi[i][1],
                                        ansi[i + 1][0], ansi[i + 1][1])
        for i in range(0, 16, 2))

    corps = """# vim:ft=kitty

## name:     macOS Dark
## blurb:    Palette système Apple (mode sombre), assortie au reste de la config.
##
## Remplace Catppuccin Mocha. Les seize couleurs ANSI sont les couleurs
## système d'Apple en mode sombre — les mêmes que celles employées par la
## waybar, swaync et hyprlock pour signaler un état. Un terminal qui parle la
## même langue chromatique que la barre juste au-dessus de lui.

# ── Surfaces et encre ──
background              {bg}
foreground              {ink}
cursor                  {ink}
cursor_text_color       {bg}

# Sélection : bleu système, encre blanche — comme partout ailleurs.
selection_background    {blue}
selection_foreground    {on_accent}

url_color               {blue}

# ── Bordures et onglets ──
active_border_color     {edge}
inactive_border_color   {field}
bell_border_color       {orange}

active_tab_foreground   {ink}
active_tab_background   {field}
inactive_tab_foreground {ink2}
inactive_tab_background {bg}
tab_bar_background      none

# ── Les seize couleurs ──
# Normales : les couleurs système Apple. Vives : leurs variantes claires.
{couples}
""".format(bg=T.TERMINAL_BG, ink=T.INK, ink2=T.INK2, blue=T.BLUE,
           on_accent=T.ON_ACCENT, edge=T.EDGE, field=T.FIELD,
           orange=T.ORANGE, couples=couples)

    yield (sortie, en_tete("#", "#" + "─" * 72, "# ") + corps)


# ═══ 3. qt6ct — palette QPalette entière ═══════════════════════════════════
#
# Fusion est le seul style Qt qui accepte une palette complète depuis un
# fichier, donc le seul qui puisse porter exactement les couleurs employées
# ailleurs. Sans lui, keepassxc, qbittorrent, OBS et Telegram sortent en gris
# clair au milieu d'un système entièrement sombre.
#
# Les 21 rôles sont POSITIONNELS : une virgule en trop décale toute la
# palette, et Qt ne dit rien. C'est la meilleure raison de les générer.
ROLES_QT = [
    "WindowText", "Button", "Light", "Midlight", "Dark", "Mid", "Text",
    "BrightText", "ButtonText", "Base", "Window", "Shadow", "Highlight",
    "HighlightedText", "Link", "LinkVisited", "AlternateBase", "NoRole",
    "ToolTipBase", "ToolTipText", "PlaceholderText",
]


def cible_qt6ct():
    if not present(os.path.join(CONFIG, "qt6ct/qt6ct.conf"), "qt6ct"):
        return

    def palette(encre, fond_champ, selection, texte_selection, liens,
                infobulle):
        return [
            encre,            # WindowText
            T.CONTROL,        # Button
            T.EDGE,           # Light
            T.CONTROL_HOVER,  # Midlight
            T.SUNKEN,         # Dark
            T.FIELD,          # Mid
            encre,            # Text
            T.ON_ACCENT,      # BrightText
            encre,            # ButtonText
            fond_champ,       # Base
            T.BASE,           # Window
            T.BLACK,          # Shadow
            selection,        # Highlight
            texte_selection,  # HighlightedText
            liens[0],         # Link
            liens[1],         # LinkVisited
            T.RAISED,         # AlternateBase
            T.ON_ACCENT,      # NoRole
            T.FIELD,          # ToolTipBase
            infobulle,        # ToolTipText
            T.rgba(encre, "0.5"),  # PlaceholderText
        ]

    # La fenêtre au premier plan : sélection bleue, encre pleine.
    actif = palette(T.INK, T.FIELD, T.BLUE, T.ON_ACCENT, (T.BLUE, T.PURPLE),
                    T.INK)
    # Au second plan : la sélection perd sa couleur — elle n'est plus un état
    # courant mais un souvenir de position.
    inactif = palette(T.INK, T.FIELD, T.CONTROL_HOVER, T.INK,
                      (T.BLUE, T.PURPLE), T.INK)
    # Désactivé : tout passe en encre tertiaire, y compris les liens — un lien
    # qu'on ne peut pas suivre ne doit pas s'annoncer comme cliquable.
    desactive = palette(T.INK3, T.RAISED_OFF, T.CONTROL_HOVER, T.INK2,
                        (T.INK3, T.INK3), T.INK2)

    lignes = []
    for nom, valeurs in (("active", actif), ("inactive", inactif),
                         ("disabled", desactive)):
        lignes.append("%s_colors=%s" % (
            nom, ", ".join(T.argb(v) for v in valeurs)))

    corps = """; Palette macOS (mode sombre) pour les applications Qt.
;
; Même encre et mêmes couleurs système que la waybar, swaync, walker et
; hyprlock. Sans ça, keepassxc, qbittorrent, OBS et Telegram sortent en gris
; clair Fusion au milieu d'un système entièrement sombre.
;
; Ordre des rôles QPalette (21), positionnel :
; %s
[ColorScheme]
%s
""" % ("\n; ".join(", ".join(ROLES_QT[i:i + 4]) + ","
                   for i in range(0, 21, 4)).rstrip(","),
       "\n".join(lignes))

    yield (os.path.join(CONFIG, "qt6ct/colors/macos-dark.conf"),
           en_tete(";", ";" + "─" * 72, "; ") + corps)


# ═══ 4. hyprlock — bloc de variables dans le fichier ═══════════════════════
#
# hyprlock n'a pas de `source` (vérifié : le mot n'apparaît ni dans son
# binaire ni dans libhyprlang, où seule l'expansion de variables existe). Le
# bloc est donc posé dans `hyprlock.conf` même, entre deux marqueurs, et les
# valeurs y sont appelées par `$nom`.
#
# Sept opacités d'encre là où le reste du système en a trois. Elles sont
# gardées telles quelles — ce script ne change aucune valeur — mais elles
# portent désormais un nom, ce qui rend l'écart visible. Deux d'entre elles
# méritent un arbitrage : `$inkContact` (0.58) est plus APPUYÉE que
# `$inkStatus` (0.55), alors que le commentaire du fichier annonce l'inverse.
MARQUEUR_DEBUT = "# ══ PALETTE GÉNÉRÉE — début ══"
MARQUEUR_FIN = "# ══ PALETTE GÉNÉRÉE — fin ══"

VARS_HYPRLOCK = [
    ("inkPrimary", T.INK, "1.0", "heure, texte saisi"),
    ("inkTitle", T.INK, "0.92", "le nom du compte"),
    ("inkDate", T.INK, "0.72", "la date, au-dessus de l'heure"),
    ("inkContact", T.INK, "0.58", "les coordonnées en cas de perte"),
    ("inkStatus", T.INK, "0.55", "batterie, empreinte, alimentation"),
    ("inkHint", T.INK, "0.45", "état du lecteur d'empreinte"),
    ("inkWhisper", T.INK, "0.34", "l'invitation à rendre la machine"),
    ("fieldOuter", T.ON_ACCENT, "0.16", "hairline du champ de saisie"),
    ("fieldInner", T.ON_ACCENT, "0.10", "intérieur du champ"),
    ("checking", T.BLUE, "0.9", "vérification en cours"),
    ("failed", T.RED, "0.9", "mot de passe refusé"),
    ("capslock", T.ORANGE, "0.9", "verrouillage majuscules"),
    ("shadow", T.BLACK, "0.45", "ombre portée de l'avatar"),
]

# Deux couleurs vivent dans du balisage Pango, à l'intérieur d'une chaîne, où
# hyprlang double le dièse pour qu'il ne soit pas lu comme un commentaire.
# Elles sont réécrites littéralement plutôt que remplacées par une variable :
# c'est un écran de verrouillage, on n'y prend pas de risque de parsing pour
# économiser deux lignes.
PANGO_HYPRLOCK = [
    ("placeholder_text", T.INK2),
    ("fail_text", T.RED),
]


def cible_hyprlock():
    chemin = os.path.join(CONFIG, "hypr/hyprlock.conf")
    if not present(chemin, "hyprlock"):
        return
    largeur = max(len(n) for n, _, _, _ in VARS_HYPRLOCK)
    lignes = [MARQUEUR_DEBUT,
              "# " + RAPPEL.replace("\n", "\n# "),
              "#",
              "# La hiérarchie se fait par l'OPACITÉ de l'encre, jamais par la",
              "# teinte : les trois seules couleurs de l'écran ne paraissent que",
              "# pendant une anomalie.", ""]
    for nom, base, alpha, role in VARS_HYPRLOCK:
        lignes.append("$%-*s = %-28s # %s"
                      % (largeur, nom, T.rgba(base, alpha), role))
    lignes += ["", MARQUEUR_FIN]

    texte = remplacer_bloc(chemin, "\n".join(lignes))
    for cle, couleur in PANGO_HYPRLOCK:
        texte = re.sub(r'(^\s*%s\s*=.*?foreground="##)[0-9a-fA-F]{6}' % cle,
                       r"\g<1>" + couleur[1:], texte, flags=re.M)
    yield (chemin, texte)


# ═══ 5. starship — bloc de palette dans le fichier ═════════════════════════
#
# starship n'a pas d'`include` : le bloc est réécrit sur place, comme pour
# hyprlock. Les NOMS restent ceux de Catppuccin — c'est le vocabulaire des
# 28 chaînes de style du fichier, et le même choix que swaync, qui garde
# `base` et `crust` en important nos valeurs.
#
# L'invite est une frise de segments : chaque bloc a son fond, et le texte
# posé dessus est presque noir. Ce sont donc les seuls endroits du système
# où une couleur ne signale pas une anomalie — d'où le passage par les
# variantes VIVES, qui portent sur un aplat là où les tons pleins
# s'assombriraient.
#
# Le bloc `[palettes.catppuccin_mocha]` d'origine reste dans le fichier, non
# généré : changer `palette =` d'un nom à l'autre bascule le thème de
# l'invite, et c'est le seul endroit où deux thèmes coexistent pour le
# montrer.
PALETTE_STARSHIP = "macos_dark"

COULEURS_STARSHIP = [
    ("rosewater", "WHITE"), ("flamingo", "WHITE"),
    ("pink", "PURPLE_BRIGHT"), ("mauve", "PURPLE"),
    ("red", "RED"), ("maroon", "RED_HOVER"),
    ("peach", "ORANGE"), ("yellow", "YELLOW"),
    ("green", "GREEN"), ("teal", "CYAN_BRIGHT"),
    ("sky", "CYAN"), ("sapphire", "BLUE_HOVER"),
    ("blue", "BLUE"), ("lavender", "PURPLE_BRIGHT"),
    ("text", "INK"), ("subtext1", "WHITE"), ("subtext0", "INK2"),
    ("overlay2", "INK2"), ("overlay1", "INK3"), ("overlay0", "INK3"),
    ("surface2", "EDGE"), ("surface1", "CONTROL"), ("surface0", "FIELD"),
    ("base", "BASE"), ("mantle", "SUNKEN"), ("crust", "SUNKEN"),
]


def cible_starship():
    chemin = os.path.join(CONFIG, "starship.toml")
    if not present(chemin, "starship"):
        return
    largeur = max(len(n) for n, _ in COULEURS_STARSHIP)
    lignes = [MARQUEUR_DEBUT,
              "# " + RAPPEL.replace("\n", "\n# "),
              "#",
              "# Les noms sont ceux de Catppuccin, les valeurs celles de la",
              "# palette système : les 28 chaînes de style du fichier n'ont pas",
              "# eu à changer.",
              "[palettes.%s]" % PALETTE_STARSHIP]
    for nom, token in COULEURS_STARSHIP:
        lignes.append('%-*s = "%s"  # %s'
                      % (largeur, nom, getattr(T, token), token))
    lignes += ["", MARQUEUR_FIN]
    yield (chemin, remplacer_bloc(chemin, "\n".join(lignes)))


# ═══ Mécanique commune ═════════════════════════════════════════════════════
def remplacer_bloc(chemin, bloc):
    """Rend le contenu de `chemin`, sa zone entre marqueurs remplacée.

    Les marqueurs doivent exister : les poser tout seul reviendrait à deviner
    où, dans un fichier écrit à la main, la palette doit atterrir — et un
    mauvais endroit dans un écran de verrouillage ne se voit qu'une fois
    verrouillé.
    """
    with open(chemin, encoding="utf-8") as f:
        texte = f.read()
    motif = re.compile("^%s$.*?^%s$" % (re.escape(MARQUEUR_DEBUT),
                                        re.escape(MARQUEUR_FIN)),
                       re.S | re.M)
    if not motif.search(texte):
        raise SystemExit(
            "marqueurs absents de %s — attendus :\n  %s\n  %s"
            % (chemin, MARQUEUR_DEBUT, MARQUEUR_FIN))
    return motif.sub(lambda _: bloc, texte, count=1)


CIBLES = [cible_css, cible_kitty, cible_qt6ct, cible_hyprlock, cible_starship]


def main():
    verifier = "--check" in sys.argv
    perimes = []

    for source in CIBLES:
        for chemin, attendu in source():
            actuel = None
            if os.path.exists(chemin):
                with open(chemin, encoding="utf-8") as f:
                    actuel = f.read()
            if actuel == attendu:
                continue
            if verifier:
                perimes.append(chemin)
                continue
            # Les fichiers générés sont VERSIONNÉS : sans eux, une restauration
            # partirait avec des feuilles important un fichier absent, donc
            # sans aucune couleur. Après un ajout, `stow` pose le lien dans `~`.
            with open(chemin, "w", encoding="utf-8") as f:
                f.write(attendu)
            print("écrit : %s" % chemin)

    if verifier and perimes:
        print("PÉRIMÉ (relancer generate-tokens.py) :", file=sys.stderr)
        for p in perimes:
            print("  " + p, file=sys.stderr)
        return 1
    if verifier:
        print("palette à jour dans les cinq formats")
    return 0


if __name__ == "__main__":
    sys.exit(main())

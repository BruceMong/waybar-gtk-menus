#!/usr/bin/env bash
# Bascule une vidéo du navigateur en Picture-in-Picture — Chrome ou Zen/Firefox.
#
#   mpris-pip.sh              « sors ce que je regarde »  (clic droit du module)
#   mpris-pip.sh --playing    « sors ce qui joue »        (media-menu.py)
#
# Deux navigateurs, deux canaux :
#   - Chrome n'a pas de raccourci PiP natif : il faut l'extension du dépôt et
#     son raccourci Alt+Shift+P (prérequis ci-dessous).
#   - Zen/Firefox a le sien, Ctrl+Shift+] (« key_togglePictureInPicture »),
#     qui vise la vidéo de l'onglet actif — la dernière touchée, sinon la plus
#     grande. Rien à installer. Il passe très bien par wtype sur AZERTY, bien
#     que « ] » y demande AltGr : wtype envoie le keysym, pas la touche.
#     Une vidéo laissée en arrière-plan n'est pas atteignable de l'extérieur,
#     mais Firefox la sort tout seul au changement d'onglet quand la pref
#     media.videocontrols.picture-in-picture.enable-when-switching-tabs.enabled
#     est posée — c'est le cas sur le profil Zen.
#
# Prérequis (une seule fois, côté Chrome) :
#   1. chrome://extensions → mode développeur → « Charger l'extension non
#      empaquetée » → ~/.config/waybar/pip-extension
#   2. chrome://extensions/shortcuts → lui assigner Alt+Shift+P, portée
#      « Dans Chrome ». Alt+P reste libre pour l'extension de Google, qui peut
#      donc rester installée : les deux se complètent au clavier.
#
# Pourquoi pas SUPER+P : Chrome n'accepte que Ctrl, Alt et Shift dans les
# raccourcis d'extension — pas Meta — et refuse Ctrl+Alt (AltGr). SUPER est de
# toute façon le mainMod d'Hyprland, qui intercepte avant Chrome.
#
# Le clic Waybar n'étant pas un geste utilisateur valide pour la page, on donne
# le focus à Chrome puis on envoie le raccourci de l'extension.
#
# ── Pourquoi une extension maison ────────────────────────────────────────────
# Le raccourci arrive dans la fenêtre focalisée ; rien, depuis l'extérieur, ne
# permet de désigner un ONGLET. L'extension de Google s'en tenait donc à
# l'onglet actif : vidéo passée en arrière-plan, le clic droit ne faisait rien,
# et rien ne le disait. Celle du dépôt (pip-extension/) cherche elle-même
# l'onglet qui porte une vidéo, dans toutes les fenêtres, et ne bascule dessus
# que si la page refuse le PiP depuis l'arrière-plan.
#
# Ce script garde malgré tout la charge de choisir la FENÊTRE : c'est elle qui
# reçoit Alt+P, et une page d'une fenêtre masquée est « hidden » pour Chrome,
# donc fondée à refuser le PiP. Viser juste évite ce détour.
#
# ── Ce que les deux modes veulent dire ───────────────────────────────────────
#   --playing  « sors ce qui joue » : la fenêtre dont le titre porte celui que
#              Chrome publie sur MPRIS. Faute de la trouver — l'onglet qui joue
#              n'est pas au premier plan de sa fenêtre — on retombe sur la
#              fenêtre la plus récente et on laisse l'extension viser l'onglet.
#              Chrome n'implémente pas Raise() (CanRaise = false) et n'expose
#              pas l'URL du morceau : il n'y a pas mieux de ce côté-ci.
#
#   (défaut)   « sors celle que j'ai sous les yeux » : la fenêtre la plus
#              récemment focalisée, sans autre condition. C'est ici que se
#              trouvait le bug d'origine : `focuswindow class:^(google-chrome)$`
#              prenait une fenêtre au hasard parmi les deux ouvertes.

CHROME_CLASS='google-chrome'
# Zen expose la classe « zen », Firefox « firefox ». Les deux parlent le même
# raccourci.
GECKO_CLASSES='["zen","firefox"]'

# Marques de direction que YouTube enrobe autour des noms de chaîne : elles
# sont dans le titre de la fenêtre mais pas dans celui de MPRIS, et feraient
# échouer la comparaison.
BIDI='[‎‏‪-‮⁦-⁩]'

# Laisse au focus le temps de s'établir avant d'envoyer la touche : sous ce
# seuil, Chrome reçoit le raccourci alors qu'il n'a pas encore la main.
FOCUS_DELAY=0.35

mode="watching"
[ "$1" = "--playing" ] && mode="playing"

clients=$(hyprctl clients -j 2>/dev/null) || exit 0

# Une fenêtre PiP est déjà là : on s'arrête. Le raccourci de l'extension est
# une bascule, donc l'envoyer maintenant refermerait le PiP — un clic droit
# « mets en PiP » qui retire le PiP est un piège, d'autant que la fenêtre porte
# déjà sa croix pour ça. Même motif que la règle `pip-float` de hyprland.lua :
# les deux désignent la même fenêtre, elles doivent le dire pareil.
if jq -e 'any(.[]; .title | test("picture.in.picture"; "i"))' >/dev/null <<<"$clients"; then
    exit 0
fi

# Classes de fenêtre candidates. Sans argument, les deux navigateurs sont en
# lice et c'est le focus qui tranche ; en mode --playing on restreint à la
# famille du lecteur MPRIS, sans quoi « sors ce qui joue » dans Zen viserait
# une fenêtre Chrome plus récemment touchée.
all_classes() { jq -cn --arg c "$CHROME_CLASS" --argjson g "$GECKO_CLASSES" '$g + [$c]'; }

# `min_by(.focusHistoryID)` = la fenêtre la plus récemment focalisée.
pick_focused() {
    jq -r --argjson cls "$1" '
        [ .[] | select(.class as $c | $cls | index($c)) ]
        | if length == 0 then empty else (min_by(.focusHistoryID) | .address) end
    ' <<<"$clients"
}

pick_by_title() {
    jq -r --argjson cls "$1" --arg want "$2" --arg bidi "$BIDI" '
        def clean: gsub($bidi; "");
        [ .[] | select(.class as $c | $cls | index($c))
              | select((.title | clean) | contains($want | clean)) ]
        | if length == 0 then empty else (min_by(.focusHistoryID) | .address) end
    ' <<<"$clients"
}

if [ "$mode" = "playing" ]; then
    # Zen se déclare « firefox » sur MPRIS : c'est le nom du moteur, pas de
    # l'application. Chrome se déclare « chromium ».
    player=$(playerctl -l 2>/dev/null | grep -m1 -E '^(chromium|chrome|firefox|zen)')
    title=""
    [ -n "$player" ] && title=$(playerctl -p "$player" metadata --format '{{title}}' 2>/dev/null)

    case "$player" in
        firefox*|zen*) classes=$GECKO_CLASSES ;;
        chrom*)        classes="[\"$CHROME_CLASS\"]" ;;
        *)             classes=$(all_classes) ;;
    esac

    if [ -z "$title" ]; then
        target=$(pick_focused "$classes")   # rien sur MPRIS : au moins viser l'écran
    else
        # Aucune fenêtre ne porte ce titre : l'onglet qui joue est en
        # arrière-plan. On vise la fenêtre la plus récente ; côté Chrome
        # l'extension ira chercher l'onglet — c'est exactement ce pour quoi
        # elle existe. Côté Zen, l'auto-PiP l'a normalement déjà sorti.
        target=$(pick_by_title "$classes" "$title")
        [ -n "$target" ] || target=$(pick_focused "$classes")
    fi
else
    target=$(pick_focused "$(all_classes)")
fi

# Aucun navigateur ouvert, ou aucune de ses fenêtres n'est joignable.
[ -n "$target" ] || exit 0

target_class=$(jq -r --arg a "$target" '.[] | select(.address == $a) | .class' <<<"$clients")

# Mémorise la fenêtre actuellement au premier plan pour y revenir ensuite.
prev=$(hyprctl activewindow -j 2>/dev/null | jq -r '.address // empty')

hyprctl dispatch "hl.dsp.focus({ window = \"address:$target\" })" >/dev/null 2>&1 || exit 0
sleep "$FOCUS_DELAY"

if [ "$target_class" = "$CHROME_CLASS" ]; then
    # Raccourci de l'extension du dépôt (Alt+Shift+P). Au-delà, c'est elle qui
    # choisit l'onglet, et qui notifie si aucun n'a de vidéo.
    wtype -M alt -M shift -k p -m shift -m alt
else
    # Raccourci natif de Firefox. C'est une bascule, comme côté Chrome — d'où
    # la sortie anticipée plus haut quand un PiP est déjà ouvert.
    wtype -M ctrl -M shift -k bracketright -m shift -m ctrl
fi

# Revient à la fenêtre d'origine si ce n'était pas celle-là (PiP reste flottant).
if [ -n "$prev" ] && [ "$prev" != "$target" ]; then
    sleep 0.05
    hyprctl dispatch "hl.dsp.focus({ window = \"address:$prev\" })" >/dev/null 2>&1
fi

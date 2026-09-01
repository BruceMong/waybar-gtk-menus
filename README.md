# waybar-gtk-menus

A Waybar setup for Hyprland where every module opens a **native GTK popup**
instead of shelling out to `rofi`/`wofi` menus — Wi-Fi picker, volume mixer,
brightness, battery, notifications, systemd units, keybindings, power.

Plus a few things Waybar can't do on its own:

- **Auto-fit** — when the bar runs out of room, the least important modules fold
  into a `⋮` overflow menu instead of overlapping. Priority is configurable.
- **Generated config** — `config-full` is the single source of truth;
  `generate-config.py` produces the config Waybar actually reads, applying
  hidden modules, shared defaults and hardware detection.
- **Stealth mode** — collapse the whole bar to a single eye icon.
- **Claude Code session tracker** — see which of your terminal AI sessions are
  running, done, or waiting on you, and jump to the right window (optional).

> ⚠️ **Language**: the popups' UI strings are currently **French only**.
> The code is otherwise generic — i18n contributions welcome (see *Contributing*).

## Screenshots

![bar](screenshots/bar.png)

| Audio | Keybindings | Overflow |
|-------|-------------|----------|
| ![audio](screenshots/audio.png) | ![keybindings](screenshots/keybinds.png) | ![overflow](screenshots/overflow.png) |

Every module opens a popup like these — anchored under the module, closing on
click-away or `Esc`, styled from the same CSS as the bar.

## Requirements

Arch Linux + Hyprland. Everything else is optional but modules degrade to
"hidden" rather than breaking the bar.

```bash
sudo pacman -S waybar python-gobject gtk3 gtk-layer-shell \
               inter-font ttf-material-symbols-variable \
               networkmanager pipewire-pulse wireplumber brightnessctl \
               playerctl pacman-contrib
# optional, per module:
sudo pacman -S swaync hyprsunset hypridle bluez-utils
```

### Icon font

Icons come from **Material Symbols Rounded** — a single family with a uniform
stroke, rather than a mix of outlined and solid glyphs. Application logos
(Chrome, Firefox, Spotify) stay in Nerd Font, which Material Symbols does not
cover; a brand mark has no reason to follow the system icon grammar anyway.

One catch: Inter and Adwaita Sans both ship ~745 glyphs in the private use
area U+E000–U+F8FF, the same range Material Symbols uses. Being first in the
font stack, they win the lookup and render their own glyphs instead — the
speaker icon comes out as a vertical bar. Reordering the stack is not an
option, since Material Symbols contains A–Z and would take over uppercase
letters in ordinary text.

The fix is to drop that range from the two text fonts. Save as
`~/.config/fontconfig/conf.d/99-icon-pua.conf` and run `fc-cache -f`:

```xml
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <match target="scan">
    <test name="family"><string>Inter</string></test>
    <edit name="charset" mode="assign">
      <minus>
        <name>charset</name>
        <charset><range><int>0xE000</int><int>0xF8FF</int></range></charset>
      </minus>
    </edit>
  </match>
  <!-- repeat the same block for "Adwaita Sans" -->
</fontconfig>
```

They keep all their text coverage; they simply stop offering themselves for
icons.

### Translucent material

The bar and the popups are drawn on a translucent background. GTK3 has no
`backdrop-filter`, so the blur behind them can only come from the compositor.
Without these two rules the background is merely see-through — windows show
through in full detail and the text becomes unreadable. The CSS and the rules
below are two halves of the same thing; do not ship one without the other.

Add to `~/.config/hypr/hyprland.conf` (Hyprland 0.53+ block syntax):

```
layerrule {
    name = waybar-material
    match:namespace = ^(waybar)$
    blur = on
    ignore_alpha = 0.1
}

# The popups set this namespace themselves (menu_common.py). Matching the
# generic `gtk-layer-shell` namespace instead would also catch the fullscreen
# click-away surface, and blur the entire screen whenever a menu opens.
layerrule {
    name = waybar-popup-material
    match:namespace = ^(waybar-popup)$
    blur = on
    ignore_alpha = 0.1
}
```

On Hyprland 0.52 and earlier, the equivalent one-liners are
`layerrule = blur, waybar` and `layerrule = ignorealpha 0.1, waybar`.

A stronger blur than the Hyprland default suits the material — around
`size = 8`, `passes = 3` in the `decoration { blur { ... } }` block. With
opaque windows (`active_opacity = 1.0`) this only affects translucent
surfaces, so the cost is limited to the bar and the popups.

| Module | Needs |
|--------|-------|
| network | `nmcli` (NetworkManager), optionally `nm-connection-editor`, `nmtui` |
| audio | `wpctl` (WirePlumber), `pactl` |
| brightness | `brightnessctl`, `hyprsunset` (night light), `hypridle` |
| notifications / dnd | `swaync` |
| updates | `checkupdates` (pacman-contrib), `yay` for AUR counts |
| media | `playerctl`, Waybar's `mpris` module |
| systemd | `systemctl --user` / system units |
| keybindings | Hyprland config in `~/.config/hypr` |
| claude | Claude Code + the hooks in `claude-hooks/` |

Terminal-launching actions assume **kitty**; change the `kitty` calls in
`*-menu.py` if you use something else.

## Install

```bash
git clone https://github.com/USER/waybar-gtk-menus ~/.config/waybar
cd ~/.config/waybar && ./install.sh
```

`install.sh` checks dependencies, recreates the `config` / `style.css`
symlinks, generates `config-active` and restarts Waybar. It never overwrites an
existing `~/.config/waybar` — back yours up first.

## How the config works

```
config-full          source of truth: every module definition + full order
      |
      |  generate-config.py   <- modules-hidden, modules-hidden-auto,
      v                          hardware detection, stealth/tens flags
config-active        what Waybar reads (via the `config` symlink)
```

Never edit `config-active` — it is regenerated. Edit `config-full`, then run
`./generate-config.py && pkill -SIGUSR2 waybar`.

- `modules-hidden` — one module id per line, hidden permanently (set from the
  `⋮` menu).
- `modules-priority` — order in which `autofit.py` folds modules away when the
  bar overflows; last line goes first.
- `style-normal.css` / `style-remote.css` — themes, swapped by `remote-mode.sh`.

Hardware paths are **detected, not hardcoded**: `"hwmon-path-abs": "auto"` in
`config-full` is resolved at generation time to whatever CPU sensor the machine
exposes (`k10temp`, `coretemp`, …), and dropped if none matches.

## The keybindings module — read this

`keybinds-menu.py` lists your Hyprland binds and lets you **reassign one by
capturing a new combo**. It rewrites the matching line in your own
`~/.config/hypr/hyprland.conf` (or `plugins.conf`), then reloads Hyprland.

Before writing it saves two backups next to the file:

- `hyprland.conf.orig` — state before the *first* ever modification, never
  touched again;
- `hyprland.conf.bak` — state before the *last* modification.

Writes are atomic (temp file + `rename`). Still: this touches your window
manager config. If that makes you uncomfortable, drop `custom/keybinds` from
`group/tools` in `config-full`.

## Claude Code module (optional)

`custom/claude` shows how many [Claude Code](https://claude.com/claude-code)
sessions are running and which ones are waiting on you. It reads state files
written by hooks — without them the module simply renders empty and Waybar
hides it.

```bash
cp claude-hooks/*.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/*.sh
```

Then register them in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart":      [{ "hooks": [{ "type": "command", "command": "$HOME/.claude/hooks/claude-session-start.sh" }] }],
    "SessionEnd":        [{ "hooks": [{ "type": "command", "command": "$HOME/.claude/hooks/claude-session-end.sh" }] }],
    "UserPromptSubmit":  [{ "hooks": [{ "type": "command", "command": "$HOME/.claude/hooks/claude-prompt-state.sh" }] }],
    "Stop":              [{ "hooks": [{ "type": "command", "command": "$HOME/.claude/hooks/claude-stop-notify.sh" }] }],
    "Notification":      [{ "hooks": [{ "type": "command", "command": "$HOME/.claude/hooks/claude-notify-focus.sh" }] }]
  }
}
```

Don't want it? Remove `custom/claude` from `modules-right` in `config-full`.

## Layout

| File | Role |
|------|------|
| `menu_common.py` | shared GTK layer-shell popup base — all menus build on it |
| `generate-config.py` | `config-full` → `config-active` |
| `autofit.py` | folds modules away when the bar overflows |
| `*-menu.py` | one popup per module |
| `*.sh` | small stateless helpers (toggles, watchers, status JSON) |

## Contributing

This is a personal config, published as-is: **Arch + Hyprland only**, no
support guaranteed. PRs are welcome, especially:

- **i18n** — extracting the French UI strings would be the single most useful
  contribution;
- portability to other compositors (the Hyprland coupling is limited to
  `hyprctl` calls and the workspaces module).

Open an issue before a large PR so we don't duplicate work.

## License

MIT — see [LICENSE](LICENSE).

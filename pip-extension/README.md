# Waybar PiP — Chrome extension

Pops the playing video into Picture-in-Picture **even when its tab is not the
one on screen**.

## Why it exists

Waybar's `mpris` right click runs `mpris-pip.sh`, which focuses a Chrome window
and sends `Alt+P`. That keystroke is the only channel available: nothing from
outside the browser can address a *tab*. Google's own PiP extension therefore
acts on the active tab only — move the video to a background tab and the click
does nothing, silently.

This extension keeps the same kind of trigger (a shortcut bound to
`_execute_action`, the one path that carries user activation into the page) and
picks the tab itself:

1. the active tab of the frontmost window — the common case, nothing moves;
2. otherwise the tab that holds a video (playing first, then largest), with PiP
   attempted **without** leaving the current tab;
3. only if the page refuses does it switch to that tab, pop the video, and
   switch straight back.

It also notifies when no tab holds a poppable video, instead of failing mute.

## Install

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   select `~/.config/waybar/pip-extension`
2. `chrome://extensions/shortcuts` → assign **Alt+Shift+P**, scope **In Chrome**

Google's own PiP extension can stay on `Alt+P`: one pops out the tab you are
looking at, the other finds the tab that plays. Chrome only accepts Ctrl, Alt
and Shift here — no Meta/Super — and rejects Ctrl+Alt, which collides with
AltGr.

Unpacked extensions survive restarts but Chrome forgets them if the folder
moves. Reload it from `chrome://extensions` after editing `background.js`.

## Permissions

`scripting` + `<all_urls>` to look for `<video>` in every frame of every tab,
`notifications` to report failures. No network access, no storage, no data
leaves the browser.

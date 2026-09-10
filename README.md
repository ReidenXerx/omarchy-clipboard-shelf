# Clipboard shelf

An [Omarchy](https://omarchy.org) bar widget that **pins the snippets you paste all day**
and keeps them one keystroke away, alongside your clipboard history.

Omarchy's built-in clipboard is a search-only overlay: everything ages out of it, so the
command you paste ten times a day is buried under the ten things you copied since. The
shelf adds a place to keep things — and a bar slot, type-aware rows, and a preview for
images.

![bar widget](https://img.shields.io/badge/omarchy-bar--widget-blue)

## Install

```bash
omarchy plugin add https://github.com/ReidenXerx/omarchy-clipboard-shelf.git --enable
```

Optionally add its entries to the Omarchy menu:

```bash
~/.config/omarchy/plugins/reidenxerx.clipboard-shelf/bin/clip-shelf-menu-install
```

Requires `wl-copy` / `wl-paste` (Omarchy default set) and the built-in `omarchy.clipboard`
service left enabled — see below.

## Use

| action | result |
|---|---|
| click the bar icon | open the shelf |
| type | filter |
| `↑` `↓` | move |
| `Enter` | copy the selected entry |
| `Alt` + `1`–`9` | copy that row straight away |
| `Ctrl` + `P` | pin or unpin the selected entry |
| right-click a row | pin or unpin it |
| hover an image row | preview it, floated to the left |
| `Esc` | close |

`Alt`+digit rather than a bare digit, because the search box has to stay typeable — a bare
`1` should filter for "1", not paste something.

Pinned entries sit at the top and never age out. They live in
`~/.config/omarchy/clipboard-shelf.json` — plain JSON, hand-editable, watched live.

## It captures nothing

The shelf reads Omarchy's own history at
`~/.local/state/omarchy/clipboard-history.json` and never starts a watcher of its own.
`omarchy.clipboard` already runs two persistent `wl-paste --watch` processes, so a second
set would mean double capture for no gain. Two consequences worth knowing:

- **Leave `omarchy.clipboard` enabled.** Disable it and history stops being recorded, so
  the shelf shows only your pins. Pins keep working.
- **Password managers are already filtered.** That capture skips entries marked
  `x-kde-passwordManagerHint` or copied while `CLIPBOARD_STATE=sensitive`, so those never
  reach the history the shelf reads.

## Rows tell you what they hold

Each row is marked for its kind — link, colour, path, email, code, image — and a colour
renders a swatch of itself rather than a glyph. Handy when you are hunting the hex you
copied out of a theme file three minutes ago.

## From the command line

```bash
clip-shelf list                # pinned snippets
clip-shelf pin "ssh alien-win" # pin some text
clip-shelf pin --clipboard     # pin whatever is on the clipboard
clip-shelf copy 2              # put pin 2 back on the clipboard
clip-shelf unpin 2
clip-shelf clear
```

## Menu entries

A plugin cannot register menu routes itself — Omarchy builds its menu from its own file
plus one user file. Opt in with:

```bash
bin/clip-shelf-menu-install          # add them
bin/clip-shelf-menu-install remove   # take them out
bin/clip-shelf-menu-install print    # just show the snippet
```

It writes only between its own marker comments in
`~/.config/omarchy/extensions/omarchy-menu.jsonc`, leaves the rest of that file byte for
byte, is safe to re-run, and rolls back to a backup rather than leaving the file
unparseable — a malformed menu file silently disables **every** user entry.

## Remove

```bash
bin/clip-shelf-menu-install remove
omarchy plugin remove reidenxerx.clipboard-shelf
```

Pins stay in `~/.config/omarchy/clipboard-shelf.json`; delete it if you want them gone.
Nothing else is left behind — the shelf owns no daemon and no capture process.

## License

MIT

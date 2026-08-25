"""Relay Control — a curses dashboard for a running relay.

    python3 scripts/relay_control/__main__.py [relay-dir]
    python3 -m relay_control [relay-dir]

The package is split so that its views can be built in parallel:

    app.py        the event loop and the view registry
    chrome.py     header, status bar, keybar, panes and rules
    theme.py      colour pairs, glyphs, and the tokens views draw with
    attention.py  the attention band above the panes
    overview.py   the four-pane Overview
    legs.py runners.py models.py contract.py    one module per full-screen view

Every fact any of them draws comes from `relay_model.build()`, called once per
repaint in `app.py` (ACC-TUI-007). No module here opens a relay file, reads a
baton, or shells out to git; `.relay/` is read by one module in this repository
and this is not it. The conventions the view modules follow are written down in
`.relay/skills/pane-conventions.md`.

`relay_model` lives beside this package rather than inside it — it is shared
with the coach's own tooling — so `scripts/` is put on the path here, once, at
import. This is the only path manipulation in the package.
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

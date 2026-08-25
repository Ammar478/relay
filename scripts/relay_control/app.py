"""The event loop and the view registry.

One repaint is one `relay_model.build()` and one pass over the chrome. That is
the rule the whole dashboard hangs on: the HTML dashboard this replaces read
four sources and reconciled none of them, and two panes built from two
`build()` calls would be the same defect in a new costume. `activeLeg` and
`activeRunner` are the *same object* in one model (ACC-DATA-003) and stop being
so the moment the model is copied, so the model is passed to the views in
process and never through JSON.

The registry
------------
Five views, each a module exposing the same three names:

    TITLE      str                            what the view calls itself
    BINDINGS   ((key, label), ...)            what the keybar shows for it
    draw(canvas, model, state)                paint into the rectangle given
    handle(key, state, model) -> bool         optional; True if the key was used

A view's `handle` is offered the key *first*, so a view leg can bind whatever it
needs without editing this file. It must never consume `q`: quitting is not a
view's decision. Everything a view is allowed to assume is written down in
`.relay/skills/pane-conventions.md`.
"""

import argparse
import curses
import os
import pathlib
import sys

import relay_model

from . import attention, chrome, contract, legs, models, overview, runners
from . import theme as theme_tokens

#: Tab order. Overview first: it is where `Esc` goes back to.
VIEWS = ("overview", "legs", "runners", "models", "contract")

_MODULES = {
    "overview": overview,
    "legs": legs,
    "runners": runners,
    "models": models,
    "contract": contract,
}

#: The single-key jumps of ACC-NAV-001, in both cases.
_SWITCH = {}
for _key, _view in (("f", "legs"), ("w", "runners"),
                    ("m", "models"), ("c", "contract")):
    _SWITCH[ord(_key)] = _view
    _SWITCH[ord(_key.upper())] = _view

_QUIT = (ord("q"), ord("Q"))
_TAB = 9
_ESC = 27


class State:
    """What the operator has done, as opposed to what the relay has done.

    Kept here rather than in the view modules so that it survives switching
    away and back, and so that a view leg has one documented place to put a
    selection or a filter without inventing a store of its own.
    """

    def __init__(self):
        self.view = "overview"
        self.selection = {name: 0 for name in VIEWS}
        self.filter = {name: 0 for name in VIEWS}
        self.detail = None

    @property
    def module(self):
        return _MODULES[self.view]

    def go(self, view):
        if view in _MODULES and view != self.view:
            self.view = view
            self.detail = None

    def next_view(self):
        self.go(VIEWS[(VIEWS.index(self.view) + 1) % len(VIEWS)])


# --------------------------------------------------------------------------
# painting
# --------------------------------------------------------------------------


def paint(stdscr, theme, model, state):
    """One whole screen, measured from the live terminal size.

    Every rectangle below is arithmetic on `getmaxyx()`; there is no constant
    here that is a width or a height. A terminal too short for a region simply
    does not get that region — the header goes last, the keybar next, and the
    view's canvas is whatever is left over.
    """
    stdscr.erase()
    rows, cols = stdscr.getmaxyx()
    if rows < 1 or cols < 2:
        stdscr.refresh()
        return

    chrome.draw_header(chrome.Canvas(stdscr, theme, 0, 0, 1, cols), model)
    if rows >= 2:
        chrome.draw_status_bar(chrome.Canvas(stdscr, theme, 1, 0, 1, cols), model)

    module = state.module
    keybar_row = rows - 1
    top, bottom = 2, keybar_row - 1
    if bottom >= top:
        band = 0
        if state.view == "overview":
            band = min(attention.height(model, cols), max(0, bottom - top))
            if band:
                attention.draw(
                    chrome.Canvas(stdscr, theme, top, 0, band, cols), model, state)
        view_top = top + band
        if bottom >= view_top:
            module.draw(
                chrome.Canvas(stdscr, theme, view_top, 0, bottom - view_top + 1,
                              cols),
                model, state)
    if rows >= 3:
        chrome.draw_keybar(
            chrome.Canvas(stdscr, theme, keybar_row, 0, 1, cols), module.BINDINGS)
    stdscr.refresh()


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------


def _run(stdscr, relay_dir):
    theme = theme_tokens.setup()
    try:
        curses.curs_set(0)
    except curses.error:  # pragma: no cover - terminal without a cursor mode
        pass
    stdscr.keypad(True)
    state = State()
    while True:
        # Once per repaint, never once per pane: every pane on one screen must
        # be drawn from one snapshot of the relay.
        model = relay_model.build(relay_dir)
        paint(stdscr, theme, model, state)
        key = stdscr.getch()
        if key in (-1, curses.KEY_RESIZE):
            continue
        handler = getattr(state.module, "handle", None)
        if handler is not None and handler(key, state, model):
            continue
        if key in _QUIT:
            return 0
        if key == _TAB:
            state.next_view()
        elif key == _ESC:
            state.go("overview")
        elif key in _SWITCH:
            state.go(_SWITCH[key])


# --------------------------------------------------------------------------
# entry
# --------------------------------------------------------------------------


def find_relay(start=None):
    """The relay directory to open when the caller named none.

    Resolution only: this asks whether a directory exists, and reads nothing.
    Every fact still comes from `build()`.
    """
    here = pathlib.Path(start or pathlib.Path.cwd()).resolve()
    for directory in [here] + list(here.parents):
        candidate = directory / ".relay"
        if candidate.is_dir():
            return str(candidate)
    return str(here / ".relay")


def main(argv=None):
    """Parse the arguments, refuse politely what cannot work, run the TUI."""
    parser = argparse.ArgumentParser(
        prog="relay-control",
        description="Watch a running relay in the terminal.")
    parser.add_argument("relay_dir", nargs="?", default=None,
                        help="the relay directory (default: the nearest .relay)")
    parser.add_argument("--relay-dir", dest="explicit", default=None,
                        help="same, named")
    args = parser.parse_args(argv)
    target = args.explicit or args.relay_dir or find_relay()

    term = os.environ.get("TERM", "")
    if not term or term == "dumb" or not sys.stdout.isatty():
        sys.stderr.write(
            "relay-control needs an interactive terminal (TERM=%r). Render the "
            "HTML dashboard instead: python3 scripts/render_dashboard.py\n" % term)
        return 2
    try:
        relay_model.build(target)
    except relay_model.RelayNotFound as exc:
        sys.stderr.write("%s\n" % exc)
        return 2

    return curses.wrapper(_run, target) or 0

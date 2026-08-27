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

There is no fourth. A view used to be offered the key first through a `handle`
of its own, and three of the five grew one: `legs` and `runners` each bound `T`
so that the filter row they had just drawn could be seen moving, and `models`
bound `Up`/`Dn`/`Enter` for its toggles. Every one of those was a reasonable
thing for a view leg to do and a bad thing to ship together, because **two
handlers for one key is how a view stops responding for reasons nobody can
find**: whichever runs first wins, the second is dead code that reads as live,
and no frame on any screen says which is which. `navigation-and-filters`
deleted all three and put the keys here, once.

What replaces them is `_NAV` below — a *declaration* per view rather than a
handler: which filter row it has, which rows are selectable, and what a detail
of one of those rows shows. A view leg still adds a filter row or a selectable
list without writing a key handler, which is what the old hook was for; what it
can no longer do is bind a key a second time. `navigation.py` holds the
mechanism, this file holds the wiring, and a view module holds what a row looks
like. Everything a view is allowed to assume is written down in
`.relay/skills/pane-conventions.md`.

Every repaint says where it ends
--------------------------------
`paint()` brackets the whole screen in DEC private mode 2026 — `ESC[?2026h`
before it touches the terminal, `ESC[?2026l` once `doupdate()` has flushed the
last cell. That is this program *stating*, in a sequence the terminal reads,
that what lies between is one whole frame.

It matters because it is the only such statement there is. A terminal watching
this program cannot tell a pause in the middle of a repaint from the end of
one: an unwritten byte is invisible, `FIONREAD` answers for input, and no
portable syscall separates blocked-in-read from sleeping. Without the bracket
the frame harness has to fall back on "it stopped writing for 0.2s", and a
screen that is half-painted and quiet at the same instant passes every negative
assertion made about it for the wrong reason. `tests/frame.py` records which it
was on every frame it captures (`Frame.paint_end`); with the bracket in place
this program's frames say `"synchronised"`, which is proof, and roughly forty
S2-S4 visual checks are judged on that rather than on a guess.

An unknown private mode is ignored by a conforming terminal, so this costs
nothing on a terminal that has never heard of 2026 — it is written past curses,
straight at the terminal, because it is not a terminfo capability and nothing
in the screen model should know about it.
"""

import argparse
import collections
import contextlib
import curses
import os
import pathlib
import sys

import relay_model

from . import attention, chrome, contract, legs, models, navigation, overview
from . import runners
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


# --------------------------------------------------------------------------
# what each view lets the keyboard do (ACC-NAV-002/003/004)
# --------------------------------------------------------------------------

#: One view's navigation, declared rather than handled.
#:
#: * `rows(model, state)` — the selectable rows, **after the view's own filter**,
#:   in the order the view draws them. `None` for a view with no selection.
#: * `filters` — the view's filter row, or `None`. `T` is offered only where
#:   there is one, which is what keeps every keybar honest: the Contract and
#:   Models views have no filter row, do not advertise `T`, and do not take it.
#: * `detail` — a `navigation.Detail` for `Enter`, or `None`.
#: * `message(row)` — what `Enter` says on the pane's last body row instead of
#:   opening a detail. The Models view's, and only its: ACC-MODEL-003's Enter is
#:   a statement that the TUI is read-only, which is not a detail of a leg, a
#:   runner or a check (which is all ACC-NAV-004 names).
_Nav = collections.namedtuple("_Nav", "rows filters detail message")

#: A leg, in full. The six fields ACC-NAV-004's evidence names, in the order a
#: supervisor reads them: what the leg is for, what it claims, what it waits on,
#: and then the three things a runner is held to.
_LEG_DETAIL = navigation.Detail(
    title=lambda leg: legs.reference(leg),
    meta=lambda leg: legs.status_word(leg),
    fields=(
        navigation.Field("Goal", lambda leg: leg.get("goal")),
        navigation.Field("Fulfills", lambda leg: leg.get("fulfills")),
        navigation.Field("Depends on", lambda leg: leg.get("dependsOn")),
        navigation.Field("Touches", lambda leg: leg.get("touches")),
        navigation.Field("Boundaries", lambda leg: leg.get("boundaries")),
        navigation.Field("Verification", lambda leg: leg.get("verification")),
    ),
)

#: A runner, in full. Every column the table can drop for want of width
#: (`runners.DROP_ORDER`) is here whole, which is what makes dropping one
#: cheap: the fact is a keystroke away rather than gone.
_RUNNER_DETAIL = navigation.Detail(
    title=lambda runner: "Runner #%s" % (runner.get("n")
                                         if runner.get("n") is not None
                                         else "?"),
    meta=lambda runner: runner.get("status"),
    fields=(
        navigation.Field("Leg", lambda runner: runner.get("leg")),
        navigation.Field("Stage", lambda runner: runner.get("stageName")
                         or runner.get("stage")),
        navigation.Field("Kind", lambda runner: runner.get("kind")),
        navigation.Field("Started", runners.started),
        navigation.Field("Duration",
                         lambda runner: chrome.humanise_duration(
                             runner.get("duration"))),
        navigation.Field("Commit", lambda runner: runner.get("commit")),
        navigation.Field("Baton", lambda runner: runner.get("batonPath")),
    ),
)

#: A check, in full — and its state is `contract.shown_status()`'s answer, not
#: `state.json`'s. A detail reading `passed` over a list drawing `blocked` for
#: the same check would be the two-figures defect the Contract view exists to
#: remove, one screen further in.
_CHECK_DETAIL = navigation.Detail(
    title=lambda check: check.get("id") or "(unnamed check)",
    meta=contract.shown_status,
    fields=(
        navigation.Field("Area", lambda check: check.get("area")),
        navigation.Field("Status", lambda check: (
            [contract.shown_status(check), contract.UNEVIDENCED]
            if contract.unevidenced(check) else contract.shown_status(check))),
        navigation.Field("Stage", lambda check: check.get("stage")),
        navigation.Field("Claimed by", lambda check: check.get("claimedBy")),
        navigation.Field("Judged by", lambda check: check.get("judgedBy")),
        navigation.Field("Reason", lambda check: check.get("reason")),
        navigation.Field("Fix leg", lambda check: check.get("fixLeg")),
        navigation.Field("Evidence", contract.evidence_of),
    ),
)

#: The Overview is deliberately absent: its four panes are read-only, it has no
#: filter row and no selection, and a view with nothing to select is a view
#: where `Up`/`Dn`/`Enter`/`T` do nothing — which is what its keybar says.
_NAV = {
    "legs": _Nav(legs.visible_legs, legs.FILTERS, _LEG_DETAIL, None),
    "runners": _Nav(runners.visible_runners, runners.FILTERS,
                    _RUNNER_DETAIL, None),
    "models": _Nav(models.rows, None, None, models.entered),
    "contract": _Nav(contract.checks_in_order, None, _CHECK_DETAIL, None),
}

#: DEC private mode 2026, synchronised output: "one whole frame begins" and
#: "that frame is complete". See the module docstring for why this program
#: says so rather than leaving a terminal to guess.
_SYNC_BEGIN = "\033[?2026h"
_SYNC_END = "\033[?2026l"


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


def _write_through(sequence):
    """Put one control sequence on the terminal, past curses.

    Curses flushes its own buffer at the end of `doupdate()`, so a sequence
    written and flushed here lands either side of the repaint and never inside
    it. Never raises: a terminal that has gone away mid-paint is the loop's
    problem to notice, not a traceback over the screen.
    """
    try:
        sys.stdout.write(sequence)
        sys.stdout.flush()
    except (OSError, ValueError):  # pragma: no cover - the terminal is gone
        pass


@contextlib.contextmanager
def synchronised_output():
    """Bracket everything drawn inside it as one whole frame (DEC 2026).

    The close is in a `finally` on purpose: a repaint that raised halfway
    through still has to be closed, or the terminal — and the frame harness —
    is left waiting inside a bracket for a frame that is never coming.
    """
    _write_through(_SYNC_BEGIN)
    try:
        yield
    finally:
        _write_through(_SYNC_END)


def _band_budget(available):
    """Rows the attention band may spend, out of the `available` between the
    status bar and the keybar.

    The split, and why it is this one. The band is worst-first: it is the part
    of the screen that says a human has to decide something, so it is not the
    part to drop first. The panes are the context for that decision, and a
    pane needs two rows to exist at all (`chrome.MIN_PANE_HEIGHT`: one title,
    one body). So the band may have everything except those two rows, never
    more than `attention.MAX_ROWS`, and the band itself truncates with `+N
    more` rather than growing into what is left.

    A fraction of the height was the alternative and it is worse where it
    matters: at 8x30 a half share is two rows, which is a marker and nothing
    else — the band would spend its last row counting the item it could have
    shown. Reserving what a pane needs keeps the shape the same at every size
    that can hold both, which is every terminal of six rows or more.

    Below that neither can be whole. The band then takes the lot rather than
    leaving a row too short to hold a pane and too short to hold anything
    else, and what it cannot show it says it cannot show. This is compatible
    with ACC-ROBUST-001/002, which govern 80x24 and narrow *widths*: at 80x24
    this is unchanged at seven rows for the band and fourteen for the panes.
    """
    if available <= 0:
        return 0
    for_a_pane = available - chrome.MIN_PANE_HEIGHT
    if for_a_pane < 1:
        return min(attention.MAX_ROWS, available)
    return min(attention.MAX_ROWS, for_a_pane)


def paint(stdscr, theme, model, state):
    """One whole screen, measured from the live terminal size, and said to be
    whole by the bracket around it (DEC 2026 — see the module docstring).

    Every rectangle below is arithmetic on `getmaxyx()`; there is no constant
    here that is a width or a height. A terminal too short for a region simply
    does not get that region — the header goes last, the keybar next, and the
    view's canvas is whatever is left over.
    """
    with synchronised_output():
        _compose(stdscr, theme, model, state)
        stdscr.noutrefresh()
        curses.doupdate()


def _compose(stdscr, theme, model, state):
    """Draw the screen into the window. Nothing here reaches the terminal."""
    stdscr.erase()
    rows, cols = stdscr.getmaxyx()
    if rows < 1 or cols < 2:
        return

    chrome.draw_header(chrome.Canvas(stdscr, theme, 0, 0, 1, cols), model)
    if rows >= 2:
        chrome.draw_status_bar(chrome.Canvas(stdscr, theme, 1, 0, 1, cols), model)

    module = state.module
    # An open detail replaces the view's body *and* its keybar. A detail takes
    # `Esc`, `Tab` and `q` and nothing else, so leaving `Up/Dn Select` and
    # `T Filter` under it would name three keys the screen does not take.
    opened = _opened_detail(state, model)
    bindings = navigation.DETAIL_BINDINGS if opened else module.BINDINGS
    keybar_row = rows - 1
    top, bottom = 2, keybar_row - 1
    if bottom >= top:
        band = 0
        if state.view == "overview":
            # The band is *offered* rows rather than clamped after the fact:
            # a clamp told neither side, so the band drew four rows believing
            # it had drawn seven and the Overview was left less than a pane.
            band = attention.height(model, cols, _band_budget(bottom - top + 1))
            if band:
                attention.draw(
                    chrome.Canvas(stdscr, theme, top, 0, band, cols), model, state)
        view_top = top + band
        if bottom >= view_top:
            canvas = chrome.Canvas(stdscr, theme, view_top, 0,
                                   bottom - view_top + 1, cols)
            if opened:
                navigation.draw_detail(canvas, *opened)
            else:
                module.draw(canvas, model, state)
    if rows >= 3:
        chrome.draw_keybar(
            chrome.Canvas(stdscr, theme, keybar_row, 0, 1, cols), bindings)


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
        # One key, one handler, and `q` reaches its own line before anything
        # else can want it. There is no view hook above this: see the module
        # docstring for what was here and why it is not.
        if key in _QUIT:
            return 0
        if key == _TAB:
            state.next_view()
        elif key in _SWITCH:
            state.go(_SWITCH[key])
        elif key == _ESC:
            # Out of the detail if one is open, out of the view otherwise. The
            # selection is untouched either way, which is ACC-NAV-004's second
            # half: there is nothing to restore because nothing was moved.
            if not navigation.close_detail(state):
                state.go("overview")
        else:
            _navigate(key, state, model)


def _navigate(key, state, model):
    """`Up`/`Dn`, `Enter` and `T`, for the view that declared it takes them.

    A view with no `_NAV` entry takes none of them; a view with no `filters`
    does not take `T`; a view whose `rows` are empty takes no `Enter`. In every
    one of those cases the key changes nothing and the next repaint is the same
    screen — which is what the keybar promised, since a key a view does not
    take is a key that view does not advertise.
    """
    nav = _NAV.get(state.view)
    if nav is None or navigation.is_open(state):
        # A detail is a full pane with three keys, all of them handled above.
        return
    if key in navigation.FILTER_KEYS:
        if nav.filters:
            navigation.cycle(state, state.view, len(nav.filters))
        return
    if key not in navigation.ENTER_KEYS and key not in (navigation.UP,
                                                        navigation.DOWN):
        return
    rows = nav.rows(model, state)
    if key in (navigation.UP, navigation.DOWN):
        navigation.move(state, state.view,
                        1 if key == navigation.DOWN else -1, len(rows))
    elif rows:
        row = rows[navigation.selected(state, state.view, len(rows))]
        if nav.detail is not None:
            navigation.open_detail(state, state.view)
        elif nav.message is not None:
            state.detail = nav.message(row)


def _opened_detail(state, model):
    """`(spec, row)` for the detail on screen, or None.

    The row is looked up *now*, out of the model this repaint was built from,
    rather than remembered from the keystroke that opened it: the loop rebuilds
    the model every repaint, so a remembered row would be a snapshot of a relay
    that has since moved drawn under a header that has not (ACC-LIVE-001).

    A list that has since emptied — a filter cycled, a relay rewritten — has no
    row to show, so the detail falls back to the list rather than drawing a
    pane of `none`.
    """
    if not navigation.is_open(state):
        return None
    nav = _NAV.get(state.view)
    if nav is None or nav.detail is None:
        return None
    rows = nav.rows(model, state)
    if not rows:
        return None
    return nav.detail, rows[navigation.selected(state, state.view, len(rows))]


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

"""The four-pane Overview (ACC-TUI-003).

The *arrangement* is `tui-skeleton`'s: Active Leg on the left, Legs top-right,
Progress Log bottom-right, Active Runner full width along the bottom, separated
by rules, collapsing to a single column below `chrome.NARROW_COLS`. It comes
from `chrome.overview_frame()`, which hands back a `Pane` per position or `None`
where the terminal was too small for one.

The *contents* of the four panes are ACC-OVER-001..005, and the legs that claim
those checks own the four `_draw_*` functions below. What is here now is the
smallest honest body each pane can have: enough that the frame is not a set of
empty boxes, and shaped so that a later leg deepens a pane rather than inventing
one. Nothing here reads a file; everything is `model`.
"""

from . import chrome
from . import theme as theme_tokens

TITLE = "Overview"

BINDINGS = (
    ("F", "Legs"),
    ("W", "Runners"),
    ("M", "Models"),
    ("C", "Contract"),
    ("Tab", "Next View"),
    ("q", "Quit"),
)


def draw(canvas, model, state):
    panes = chrome.overview_frame(canvas)
    for name, painter in (("active_leg", _draw_active_leg),
                          ("legs", _draw_legs),
                          ("log", _draw_log),
                          ("runner", _draw_runner)):
        pane = panes.get(name)
        if pane is not None:
            painter(pane, model)


# --------------------------------------------------------------------------
# ACC-OVER-001 / -002 — Active Leg
# --------------------------------------------------------------------------


def _draw_active_leg(pane, model):
    leg = model.get("activeLeg")
    if not leg:
        # ACC-OVER-002: say what the relay is doing instead, never a blank box.
        phase = (model.get("relay") or {}).get("phase") or "pending"
        pane.header(None)
        pane.empty({
            "complete": "COMPLETE — every leg has landed",
            "judging": "JUDGING — waiting on the stage gate",
            "blocked": "BLOCKED — waiting on a human call",
        }.get(phase, "%s — no leg is running" % phase.upper()))
        return

    pane.header(leg.get("stage"))
    glyph, attr = pane.theme.status(leg.get("status") or "running")
    pane.segments(0, [(glyph + "  ", attr),
                      (leg.get("id") or "(unnamed leg)", theme_tokens.EMPHASIS)])
    row = 1
    stage_name = leg.get("stageName")
    if stage_name:
        pane.segments(row, [("stage  ", theme_tokens.MUTED),
                            (stage_name, theme_tokens.BODY)])
        row += 1
    goal = leg.get("goal")
    if goal and pane.body_height - row - 1 > 0:
        row += 1
        lines = chrome.wrap(goal, pane.body_width)
        shown, hidden = chrome.paginate(lines, pane.body_height - row)
        for offset, line in enumerate(shown):
            pane.line(row + offset, line)
        # The marker goes at the end of *this* region, not at the end of the
        # pane: `pane.more(hidden)` would land on the last body row and
        # overwrite whatever another region had already drawn there.
        pane.more(hidden, row=row + len(shown))


# --------------------------------------------------------------------------
# ACC-OVER-003 — Legs
# --------------------------------------------------------------------------


def _draw_legs(pane, model):
    counts = model.get("legCounts") or {}
    legs = model.get("legs") or []
    pane.header("%d/%d" % (counts.get("completed", 0), counts.get("total", 0)))
    if not legs:
        pane.empty("no legs planned yet")
        return
    shown, hidden = chrome.paginate(legs, pane.body_height)
    for row, leg in enumerate(shown):
        glyph, attr = pane.theme.status(leg.get("status") or "pending")
        token = theme_tokens.EMPHASIS if leg.get("isActive") else theme_tokens.BODY
        pane.segments(row, [(glyph + "  ", attr),
                            (leg.get("id") or "(unnamed leg)", token)])
    pane.more(hidden)


# --------------------------------------------------------------------------
# ACC-OVER-004 — Progress Log
# --------------------------------------------------------------------------


def _draw_log(pane, model):
    entries = model.get("log") or []
    if not entries:
        # Never `1-0 of 0`: an empty log says so in words, in both places.
        pane.header("none")
        pane.empty("nothing recorded yet")
        return
    shown, hidden = chrome.paginate(entries, pane.body_height)
    # `1-0 of 12` is the same nonsense as `1-0 of 0`: a range the pane did not
    # draw. Too short to show a single entry says how many there are instead.
    pane.header("1-%d of %d" % (len(shown), len(entries)) if shown
                else "%d entries" % len(entries))
    for row, entry in enumerate(shown):
        age = chrome.humanise_age(entry.get("age")) or ""
        pane.segments(row, [
            ("%9s  " % age, theme_tokens.MUTED),
            (entry.get("m") or "", pane.theme.attention(entry.get("level"))),
        ])
    pane.more(hidden)


# --------------------------------------------------------------------------
# ACC-OVER-005 — Active Runner
# --------------------------------------------------------------------------


def _draw_runner(pane, model):
    runner = model.get("activeRunner")
    if not runner:
        pane.header(None)
        pane.empty("no runner is on a leg right now")
        return
    number = runner.get("n")
    pane.header("#%s" % number if number is not None else None)
    pane.segments(0, [
        ("#%s  " % ("?" if number is None else number), theme_tokens.MUTED),
        # The same leg the Active Leg pane names, because the model derives the
        # runner from the leg rather than looking it up (ACC-DATA-003).
        (runner.get("leg") or "(unidentified leg)", theme_tokens.EMPHASIS),
    ])
    pane.right(0, chrome.humanise_duration(runner.get("duration")))

    row = 1
    stage = runner.get("stageName") or runner.get("stage")
    if stage:
        pane.segments(row, [("stage  ", theme_tokens.MUTED),
                            (stage, theme_tokens.BODY)])
        row += 1
    steps = (model.get("activeLeg") or {}).get("verification") or []
    if steps and pane.body_height - row - 1 > 0:
        pane.line(row, "Verification", theme_tokens.PANE_TITLE)
        row += 1
        shown, hidden = chrome.paginate(steps, pane.body_height - row)
        for offset, step in enumerate(shown):
            pane.segments(row + offset, [
                (pane.theme.glyph("bullet") + " ", theme_tokens.MUTED),
                (step, theme_tokens.BODY),
            ])
        pane.more(hidden, row=row + len(shown))

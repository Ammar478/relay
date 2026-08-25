"""The Runners view — ACC-RUN-001..003.

**Owned by the Runners leg.** The filter row with its counts, the eight columns,
and the rule that a column with no data for any row is dropped rather than drawn
empty (ACC-RUN-003) all belong to that leg. What is here is the seam and a body
that says something true.

One figure to keep honest when filling this in: `Active (N)` must equal the
number of legs the model reports running. `model["runnerCounts"]["active"]` is
derived from the same active leg the Overview names, which is what makes the
`Active (0)`-while-two-legs-run defect impossible rather than merely fixed.
"""

from . import chrome
from . import theme as theme_tokens

TITLE = "Runners"

BINDINGS = (
    ("Up/Dn", "Select"),
    ("Enter", "Detail"),
    ("T", "Filter"),
    ("Esc", "Overview"),
    ("Tab", "Next View"),
    ("q", "Quit"),
)


def draw(canvas, model, state):
    pane = canvas.full_pane(TITLE)
    if pane is None:
        return
    counts = model.get("runnerCounts") or {}
    runners = model.get("runners") or []
    pane.header("%d active of %d" % (counts.get("active", 0),
                                     counts.get("total", len(runners))))
    if not runners:
        pane.empty("no runner has landed a baton yet")
        return
    shown, hidden = chrome.paginate(runners, pane.body_height)
    for row, runner in enumerate(shown):
        glyph, attr = pane.theme.status(runner.get("status") or "pending")
        number = runner.get("n")
        pane.segments(row, [
            (glyph + "  ", attr),
            ("%-4s " % ("#%s" % number if number is not None else ""),
             theme_tokens.MUTED),
            (runner.get("leg") or "(unidentified leg)", theme_tokens.BODY),
        ])
        elapsed = chrome.humanise_duration(runner.get("duration"))
        pane.right(row, elapsed or "", theme_tokens.MUTED)
    pane.more(hidden)

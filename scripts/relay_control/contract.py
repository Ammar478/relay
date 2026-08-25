"""The Contract view — ACC-CONT-001..004.

**Owned by the Contract leg.** Grouping by the area segment of a check id with
`N/M evidenced` per group, an overall `passed/total` in the header, wrapped
evidence indented under its check id, pending checks on exactly one row, and
failed checks sorting to the top of their group are all that leg's.

`model["checkGroups"]` already carries the grouping and `model["checks"]` the
rows; the model sorts failed above blocked above pending above passed, so the
ordering ACC-CONT-004 wants is the order the list arrives in.
"""

from . import chrome
from . import theme as theme_tokens

TITLE = "Contract"

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
    counts = model.get("checkCounts") or {}
    checks = model.get("checks") or []
    pane.header("%d/%d passed" % (counts.get("passed", 0),
                                  counts.get("total", len(checks))))
    if not checks:
        pane.empty("no acceptance checks recorded")
        return
    shown, hidden = chrome.paginate(checks, pane.body_height)
    for row, check in enumerate(shown):
        glyph, attr = pane.theme.status(check.get("status") or "pending")
        pane.segments(row, [
            (glyph + "  ", attr),
            (check.get("id") or "(unnamed check)", theme_tokens.BODY),
        ])
    pane.more(hidden)

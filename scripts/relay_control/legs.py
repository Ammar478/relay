"""The Legs view — ACC-LEGS-001..004 and ACC-NAV-002/003/004.

**Owned by the Legs leg.** What `tui-skeleton` settles here is the shape every
full-screen view has, and the smallest body that is not a lie: the legs in plan
order with their status glyph, overflowing with `+N more`. The filter row, the
`Status | Stage/ID | Fulfills` columns, the selection highlight and the detail
view are the Legs leg's, and they go in this file.

`state.selection["legs"]` and `state.filter["legs"]` are already there to be
used; `handle(key, state, model)` is the hook, and adding it here needs no edit
to `app.py`.
"""

from . import chrome
from . import theme as theme_tokens

TITLE = "Legs"

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
    counts = model.get("legCounts") or {}
    legs = model.get("legs") or []
    pane.header("%d/%d" % (counts.get("completed", 0), counts.get("total", 0)))
    if not legs:
        pane.empty("no legs planned yet")
        return
    shown, hidden = chrome.paginate(legs, pane.body_height)
    for row, leg in enumerate(shown):
        glyph, attr = pane.theme.status(leg.get("status") or "pending")
        stage = leg.get("stage") or ""
        pane.segments(row, [
            (glyph + "  ", attr),
            ("%-4s " % stage, theme_tokens.MUTED),
            (leg.get("id") or "(unnamed leg)",
             theme_tokens.EMPHASIS if leg.get("isActive") else theme_tokens.BODY),
        ])
        kind = leg.get("kind")
        if kind and kind != "impl":
            pane.right(row, kind, theme_tokens.KIND)
    pane.more(hidden)

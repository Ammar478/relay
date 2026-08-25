"""The Models view — ACC-MODEL-001..003.

**Owned by the Models leg.** The three role rows with their configured model and
reasoning effort, the one-line guidance per role from SKILL.md (including that
the judge should differ in provider from the runner), and the read-only
experimental toggles are that leg's.

The data is untrusted coach input under `model["extras"]["models"]`, shaped
`{role: {"model": str, "effort": str}}` but guaranteed nothing: the model passes
`dashboard.json`'s extras through verbatim, so this view coerces what it reads
and states a documented default where a role is absent.
"""

from . import chrome
from . import theme as theme_tokens

TITLE = "Models"

ROLES = ("coach", "runner", "judge")

BINDINGS = (
    ("Up/Dn", "Select"),
    ("Enter", "Toggle"),
    ("Esc", "Overview"),
    ("Tab", "Next View"),
    ("q", "Quit"),
)


def draw(canvas, model, state):
    pane = canvas.full_pane(TITLE)
    if pane is None:
        return
    configured = (model.get("extras") or {}).get("models")
    configured = configured if isinstance(configured, dict) else {}
    pane.header("%d of %d configured" % (
        sum(1 for role in ROLES if isinstance(configured.get(role), dict)),
        len(ROLES)))
    for row, role in enumerate(ROLES):
        entry = configured.get(role)
        entry = entry if isinstance(entry, dict) else {}
        name = entry.get("model")
        effort = entry.get("effort")
        pane.segments(row, [
            ("%-8s " % role.title(), theme_tokens.MUTED),
            (str(name) if name else "not configured",
             theme_tokens.BODY if name else theme_tokens.ABSENT),
        ])
        if effort:
            pane.right(row, "effort %s" % effort, theme_tokens.MUTED)
    if pane.body_height > len(ROLES) + 1:
        pane.line(len(ROLES) + 1,
                  "Read-only: edit dashboard.json to change these.",
                  theme_tokens.ABSENT)

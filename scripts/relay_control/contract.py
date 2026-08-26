"""The Contract view — ACC-CONT-001..004.

The honest counterweight to the progress bar. The status bar above this pane
reads `27/36`; this pane reads `25/39 passed`. Legs-done measures motion,
checks-passed measures progress, and when the two diverge the second one is the
true statement about the relay. Everything in this module exists to keep the
second number from drifting towards the first.

The four things it draws, and the check each one is
---------------------------------------------------
* **A heading per area, `AREA N/M evidenced`** (ACC-CONT-001), where the area is
  the middle segment of the check id and the overall figure is repeated in the
  pane's own meta as `N/M passed`.
* **A check's prose, wrapped and indented under its id**, cut with `+N more`
  rather than allowed to overflow (ACC-CONT-002).
* **One row for a check with nothing to say** — glyph, id, state word, and no
  second row (ACC-CONT-003).
* **Failed checks first within their area**, with the reason they failed and the
  leg sent to fix them (ACC-CONT-004).

Why this view recounts what the model already counted
-----------------------------------------------------
`.relay/contract.md` opens with the rule the whole relay is judged by: *a check
passes only with the evidence it names; no evidence means blocked, not passed.*
`state.json` is hand-written, so a check can say `"status": "passed"` and carry
no evidence at all — and the model reports what it was told (ACC-DATA-001). This
view is the last place before a supervisor's eye, so this is where the rule is
applied: `shown_status()` downgrades such a check to `blocked`, draws it with
the blocked glyph, sorts it with the blocked checks, and says on the row beneath
it that a claim was made and not evidenced. The heading counts and the pane meta
are then taken over the rows as drawn, so the glyph, the group count and the
header cannot tell three different stories about the same check.

This is deliberately the *opposite* of ACC-RUN-002's rule ("take the count from
the model, never re-derive it"), and the two agree on the principle: a figure and
the rows it labels must come from one place. In the Runners view that place is
the model, because `Active (N)` and the rows both mean "status == running". Here
the rows are drawn under a rule the model does not apply, so a count taken from
`checkCounts` would be the count of a different set of rows — `26/39 passed`
above a list showing twenty-five ticks and a cross.

**A blocked check and a failed check are not the same state.** Blocked needs a
decision from the supervisor; failed needs a fix leg from the coach. The theme
gives both `✗` and the same colour pair, so the glyph alone cannot separate
them — which is why every check row carries its state as a *word* beside its id.
That word, not the glyph, is what ACC-CONT-004's distinction rests on here.

What this view does not own
---------------------------
The selection highlight and the `Enter` detail of ACC-NAV-002/004 belong to the
navigation leg. `T` is not bound and is not advertised: the Contract view has no
filter row, and a keybar naming a key the view does not take is a lie on every
frame. See `.relay/skills/pane-conventions.md`.
"""

import collections

from . import chrome
from . import theme as theme_tokens

TITLE = "Contract"

BINDINGS = (
    ("Up/Dn", "Select"),
    ("Enter", "Detail"),
    ("Esc", "Overview"),
    ("Tab", "Next View"),
    ("q", "Quit"),
)

#: The four states a check can be shown in, in the order ACC-CONT-004 sorts
#: them within an area: failed, then blocked, then pending, then passed. Sorted
#: on the state this view *shows*, not the one `state.json` claims — a check
#: downgraded by `shown_status()` sorts with the blocked ones, because that is
#: what the reader is looking at.
ORDER = ("failed", "blocked", "pending", "passed")

#: One gutter, between the glyph and the id and between the id and its word.
GAP = 2

#: Where a check's prose starts: under its id (which begins at `1 + GAP`) and
#: indented past it, so a wrapped line can never be mistaken for a check row.
INDENT = 5

#: Rows one *piece* of a check's prose may occupy before it is cut — a check has
#: at most three (its fix leg, its reason, its evidence), and each is budgeted
#: separately because each is a different sentence. The last row of a cut piece
#: is spent on `+N more`, so it shows `PROSE_ROWS - 1` lines and says how many
#: it did not show (ACC-CONT-002).
#:
#: Three, because the pane holds twenty body rows at 80x24 and thirty-nine
#: checks: a block that could grow without bound would spend the whole screen
#: on the first area's evidence and answer "which checks are unevidenced?" with
#: `+N more`. It is a budget, and `+N more` is how the view says what the
#: budget cost.
PROSE_ROWS = 3

#: What the view says about a check claiming `passed` with no evidence. Not
#: "unevidenced" alone: the reader has to be able to tell this from a check
#: nobody has judged yet, and the sentence naming both halves is the only thing
#: on the screen that can.
UNEVIDENCED = "claimed passed with no evidence recorded"

#: How a failed check names the leg that will fix it (ACC-CONT-004).
FIX_LEG = "fix leg: "

#: Emptiness, in words — never `0/0`, never a blank body.
EMPTY = "no acceptance checks recorded"

#: `heading` is carried because a heading may not be the last row drawn: it is
#: a label for the rows under it, and cutting between the two draws exactly the
#: empty heading `.relay/skills/pane-conventions.md` forbids.
_Row = collections.namedtuple("_Row", "heading parts")


# --------------------------------------------------------------------------
# what a check's state actually is
# --------------------------------------------------------------------------


def _text(value):
    """A field of untrusted prose as a string. Anything else is absent."""
    return str(value).strip() if isinstance(value, str) else ""


def evidence_of(check):
    """The evidence this check names, or `""` when it names none."""
    return _text(check.get("evidence"))


def unevidenced(check):
    """Whether this check claims `passed` and has no evidence to show for it."""
    return check.get("status") == "passed" and not evidence_of(check)


def shown_status(check):
    """The state this view may draw, which is not always the one claimed.

    `passed` without evidence is `blocked`. That is the contract's own opening
    rule, and it is applied here rather than in the model because the model
    reports `state.json` as written (ACC-DATA-001) and a supervisor reads this.
    """
    if unevidenced(check):
        return "blocked"
    claimed = check.get("status")
    return claimed if claimed in ORDER else "pending"


def _sort_key(check):
    return ORDER.index(shown_status(check)), str(check.get("id") or "")


def _groups(model):
    """`[(area, checks)]` — the model's grouping, re-sorted on shown state.

    The model sorts each group on the *claimed* state (`CHECK_ORDER`), which is
    the same order until a check is downgraded here; re-sorting is what keeps
    ACC-CONT-004's "failed leads" true of the list a reader can see. Coerced on
    the way in: `checkGroups` is built from `state.json` and this view is not
    the place to discover that a hand-written file had a list where an object
    belonged.
    """
    raw = model.get("checkGroups")
    groups = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        members = [check for check in (entry.get("checks") or [])
                   if isinstance(check, dict)]
        if members:
            groups.append((str(entry.get("area") or "GENERAL"),
                           sorted(members, key=_sort_key)))
    return groups


def _evidenced(checks):
    return sum(1 for check in checks if shown_status(check) == "passed")


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------


def draw(canvas, model, state):
    pane = canvas.full_pane(TITLE)
    if pane is None:                       # too small for a pane at all
        return
    groups = _groups(model)
    if not groups:
        pane.header(None)
        pane.empty(EMPTY)
        return

    checks = [check for _, members in groups for check in members]
    pane.header(_meta(checks, pane.theme))

    rows = _rows(groups, pane.theme, pane.body_width)
    shown, hidden = _fit(rows, pane.body_height)
    for index, row in enumerate(shown):
        pane.segments(index, row.parts)
    # The marker belongs at the end of the region that overflowed, which here
    # runs to the bottom of the pane only when no heading had to be given back.
    pane.more(hidden, row=len(shown))


def _meta(checks, theme):
    """`25/39 passed`, and what was claimed and could not be shown.

    The second half is the whole reason this view exists: a check downgraded by
    `shown_status()` has been taken *out* of the numerator, and a figure that
    quietly shrank is a figure a reader cannot act on. Naming the count beside
    it is the difference between "the relay lost a check" and "the relay claims
    a check nobody evidenced".
    """
    meta = "%d/%d passed" % (_evidenced(checks), len(checks))
    claimed = sum(1 for check in checks if unevidenced(check))
    if claimed:
        meta += " %s %d unevidenced" % (theme.glyph("sep"), claimed)
    return meta


def _rows(groups, theme, width):
    """Every row the view would draw, in reading order, before any cutting.

    One list, cut once (`_fit`). Paginating each group separately would spend a
    row per group on its own `+N more`, and the pane a supervisor reads at
    80x24 has twenty body rows for thirty-nine checks.
    """
    idw = max(len(_id_of(check))
              for _, members in groups for check in members)
    ellipsis = theme.glyph("ellipsis")
    rows = []
    for area, members in groups:
        rows.append(_Row(True, _fit_parts([
            (area, theme_tokens.PANE_TITLE),
            (" %d/%d evidenced" % (_evidenced(members), len(members)),
             theme_tokens.PANE_META),
        ], width, ellipsis)))
        for check in members:
            rows.extend(_check_rows(check, theme, width, idw))
    return rows


def _id_of(check):
    return str(check.get("id") or "(unnamed check)")


def _check_rows(check, theme, width, idw):
    """One check: its own row, then whatever prose it has, indented under it.

    A check with nothing to say is exactly one row — ACC-CONT-003, which is a
    defect this view shipped once as a glyph split across two lines.
    """
    status = shown_status(check)
    glyph, attr = theme.status(status)
    ellipsis = theme.glyph("ellipsis")
    # The glyph and the word carry the same attribute, from the same call: a
    # view cannot draw the right glyph in the wrong colour (ACC-TUI-006), and
    # the word is what separates blocked from failed, which share a glyph.
    rows = [_Row(False, _fit_parts([
        (glyph, attr),
        (" " * GAP, theme_tokens.MUTED),
        (_id_of(check).ljust(idw), theme_tokens.BODY),
        (" " * GAP, theme_tokens.MUTED),
        (status, attr),
    ], width, ellipsis))]
    for text, token in _prose(check, status):
        rows.extend(_prose_rows(text, token, width))
    return rows


def _prose(check, status):
    """`[(text, token)]` — what this check has to say, in the order it says it.

    Deliberately different per state, because a supervisor does something
    different with each: a blocked check needs the reason it could not be
    evidenced so they can decide, and a failed one needs the reason *and* the
    leg that will fix it so they can leave it to the coach.
    """
    out = []
    # The fix leg first, directly under the id: it is one short row, it is the
    # most actionable thing a failed check has, and anything below a block that
    # ended in `+N more` reads as part of what the marker was counting. It is
    # drawn wherever `state.json` records one rather than only on a failed
    # check — ACC-CONT-004 requires it there, and suppressing it elsewhere
    # would be the view hiding a recorded fact to keep its own categories tidy,
    # which the state word already does.
    fix = _text(check.get("fixLeg"))
    if fix:
        out.append((FIX_LEG + fix, theme_tokens.KIND))
    if unevidenced(check):
        out.append((UNEVIDENCED, theme_tokens.ABSENT))
    elif status in ("failed", "blocked"):
        reason = _text(check.get("reason"))
        if reason:
            out.append((reason, theme_tokens.BODY))
    evidence = evidence_of(check)
    if evidence:
        out.append((evidence, theme_tokens.MUTED))
    return out


def _prose_rows(text, token, width):
    """`text` wrapped into the pane, indented, and cut with `+N more`.

    The marker counts *lines*, which is what a reader counts, and it is spent
    out of the block's own budget so that a cut block never claims a line it
    did not have room to promise.
    """
    room = width - INDENT
    if room < 1:
        # A pane this narrow has no room for a line of prose, and a row of
        # nothing but indent is a row spent saying nothing.
        return []
    lines = chrome.wrap(text, room)
    hidden = 0
    if len(lines) > PROSE_ROWS:
        hidden = len(lines) - (PROSE_ROWS - 1)
        lines = lines[:PROSE_ROWS - 1]
    rows = [_Row(False, [(" " * INDENT, theme_tokens.MUTED), (line, token)])
            for line in lines]
    if hidden:
        rows.append(_Row(False, [(" " * INDENT, theme_tokens.MUTED),
                                 ("+%d more" % hidden, theme_tokens.MUTED)]))
    return rows


def _fit(rows, height):
    """`(shown, hidden)` — the rows that fit, one kept back for the marker.

    A heading is never the last row drawn: with no room for anything under it
    it gives its row back to the count of what was hidden, which is a truer
    statement than a label pointing at nothing.

    There is no special case for a pane with no body rows, deliberately:
    `Canvas.pane()` refuses anything under `MIN_PANE_HEIGHT`, so the smallest
    body this is ever called with is one row and `height - 1` is already right
    for it — the marker takes that row and reports the whole list. A branch no
    caller can reach is a branch no test can prove, and this one was written
    twice as `max(0, height - 1)`, where the clamp was unreachable and therefore
    could be changed to `max(1, ...)` without any screen moving.
    """
    if len(rows) <= height:
        return rows, 0
    shown = rows[:height - 1]
    while shown and shown[-1].heading:
        shown = shown[:-1]
    return shown, len(rows) - len(shown)


def _fit_parts(parts, width, ellipsis):
    """`parts` cut to `width` cells, marked once, in the theme's own ellipsis.

    A row left to `Canvas.write()` to clip is cut with `chrome.clip()`'s literal
    `…` default, which curses drops to a blank under a locale that cannot encode
    it — a silent truncation. The cell for the mark is reserved *before* it is
    spent, because `chrome.clip()` hands back `text[:width]` with no mark at all
    when it is left fewer cells than the ellipsis is wide.

    This is the second copy of this helper in the package (`runners.py` has the
    first). It belongs in `chrome.py`, which this leg may not touch; see the
    baton.
    """
    if sum(len(text) for text, _ in parts) <= width:
        return list(parts)
    room = max(0, width - len(ellipsis))
    fitted, spent = [], 0
    for text, token in parts:
        if spent >= room:
            break
        text = text[:room - spent]
        if text:
            fitted.append((text, token))
            spent += len(text)
    fitted.append((ellipsis, theme_tokens.MUTED))
    return fitted

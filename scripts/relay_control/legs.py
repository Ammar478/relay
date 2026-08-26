"""The Legs view — ACC-LEGS-001..004.

`Legs (N)`, a filter row that carries every filter's count, and three columns:
`Status`, `Stage/ID`, `Fulfills`. The arrangement is Mission Control's Features
view (`assets/control.html`), which is what the LEGS checks are judged against.

Three decisions in here are not obvious from the checks, and each one exists
because the obvious answer is wrong:

* **The Status column says the most informative word there is, not the raw one
  and not always the display one.** See `raw_status_note()`. Rendering
  `rawStatus` whenever it differs from `status` marks 27 of the live fixture's
  36 legs with the word `done`, which is what `completed` already says.
* **The kind marker follows the leg's id**, inside the `Stage/ID` cell, because
  `judge` and `fix` are part of what a leg *is* — which is where Mission
  Control puts it too. An impl leg carries no marker, so the three kinds are
  three distinguishable cells and not two (ACC-LEGS-002).
* **`Fulfills` earns its column.** It takes what is left after the other two
  and is dropped entirely when it cannot hold its own label, rather than
  shrinking to an ellipsis with nothing in front of it. What it does draw is
  cut at the column boundary with an ellipsis (ACC-LEGS-003), never wrapped:
  a row of this table is one line at every terminal size.

What this module deliberately does *not* own, and why:

* **Scrolling a long list to follow a selection is ACC-NAV-002's**, and
  `navigation-and-filters` owns it. This view paginates (`chrome.paginate`) and
  says `+N more`. At 80x24 the 36-leg fixture overflows and the tail of the
  plan sits behind that marker; making the window follow
  `state.selection["legs"]` is that leg's job, and duplicating
  `overview._leg_window` here to half-do it would leave two answers to one
  question. Two ends marked, one marker reporting everything hidden: the rule
  is already written down in `.relay/skills/pane-conventions.md`.
* **`Up`/`Dn` and `Enter`.** `T` is bound here because a filter row nobody can
  move is a filter row nobody can see working; the selection and the detail
  view are ACC-NAV-002/004.

Nothing here reads a file, calls `build()`, or mutates the model. `relay_model`
is imported for its *vocabulary* only — the alias table that says which coach
words mean which state — and never for `build()`; see `raw_status_note()`.
"""

import relay_model

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

#: The filter row, in Mission Control's order, and the leg state each entry
#: selects. `None` is "every leg". ACC-LEGS-001 names all five and ACC-NAV-003
#: cycles them, so the order here is the order on the screen.
FILTERS = (("All", None),
           ("Pending", "pending"),
           ("In Progress", "running"),
           ("Completed", "completed"),
           ("Cancelled", "cancelled"))

#: What the Status column calls each display state — the same word its filter
#: uses, so a reader matches a row to a filter without translating.
STATE_WORDS = {state: label for label, state in FILTERS if state}

#: The three column heads ACC-LEGS-001 names, in order.
COLUMNS = ("Status", "Stage/ID", "Fulfills")

#: Between two columns, and between two filters.
GAP = 2
SEPARATOR = " | "

#: A column is drawn only when it can carry its own label whole. A column
#: narrower than its heading is not a column: either the heading is cut to
#: something that labels nothing (`Fulfi…`, `F`), or the heading is dropped and
#: the cells under it stand with nothing saying what they are. Both are worse
#: than the column not being there, so the width of the label *is* the
#: threshold — and `_head_row()` re-states it, so the two cannot drift.
MIN_STAGE_ID = len(COLUMNS[1])
MIN_FULFILLS = len(COLUMNS[2])

#: How much of a coach's word the Status column will resize itself for.
#:
#: The column is sized by the widest thing in it, and one of those things is
#: untrusted prose: a `status` of two hundred characters in `legs.json` would
#: otherwise take two hundred columns and leave the rest of the grid with
#: nothing. A status is a word, not a sentence — twice the longest word this
#: view owns is as much room as a coach gets before it is cut, which is enough
#: for every spelling in the model's alias tables and for `blocked`.
MAX_STATUS_WORD = 2 * max(len(word) for word in STATE_WORDS.values())

#: The two coach words that map onto a display state and mean more than it.
#:
#: `blocked` is the one this exists for. It normalises to `pending` because a
#: view filters on four states and `blocked` is not one of them (ACC-DATA-004),
#: so a supervisor reading `Pending` reads a leg that needs a human decision as
#: a leg that merely has not started yet. `waiting` is the same sentence in
#: another word. Everything else in the alias tables is a plain spelling of the
#: state it maps to.
LOUD = frozenset(("blocked", "waiting"))


# --------------------------------------------------------------------------
# what a leg's raw status is worth saying
# --------------------------------------------------------------------------


def raw_status_note(leg):
    """The coach's own word for `leg`, when the four display states lose it.

    **Not "when it differs from `status`".** That was proposed, and it is
    wrong: 27 of the live fixture's 36 legs have `rawStatus` `done`, which
    differs from `completed` and says nothing `completed` has not already said.
    Marking all 27 is noise, and noise is what stops a supervisor noticing the
    one leg that is `blocked` (ACC-LEGS-004).

    A word is worth a column when it is not simply another spelling of the
    state it mapped to. Two ways that happens:

    * the model had to fall back — `failed`, `needs-a-human`, anything a coach
      invented — and `pending` keeps no trace of it whatsoever;
    * it is one of `LOUD`, which map onto a state that says less than they do.

    The spellings come from `relay_model.STATUS_ALIASES` rather than from a
    copy here. "Which words mean `done`" is one decision: a second table would
    drift from the one the model actually maps with, and this view would go
    quiet for a word the model no longer recognises — the exact failure the
    reconciled model exists to remove one layer down. Only the *vocabulary* is
    borrowed; every fact still arrives in `model`.

    Returns the coach's word as written (it is untrusted prose, and
    `Canvas.write()` is where that stops mattering), or `None` for silence.
    """
    raw = leg.get("rawStatus")
    if not isinstance(raw, str) or not raw.strip():
        return None
    word = raw.strip().lower().replace(" ", "_")
    if word in LOUD:
        return raw.strip()
    spellings = relay_model.STATUS_ALIASES.get(leg.get("status")) or ()
    return None if word in spellings else raw.strip()


def _status_word(leg):
    """What the Status column says about `leg` — its note, or its state."""
    return (raw_status_note(leg) or STATE_WORDS.get(leg.get("status"))
            or str(leg.get("status") or ""))


def _note_attr(pane, note):
    """How a raw-status note is drawn, so it cannot read as an ordinary row.

    The theme already knows what `blocked` and `failed` look like — they are
    check states — so a word it knows takes its own spelling and a word it does
    not takes `EMPHASIS`, which is the token for "the value the reader's eye
    should land on". Either way it is not the state's own attribute, which is
    the whole point: `blocked` must not be drawn like `Pending`.
    """
    word = note.strip().lower().replace(" ", "_")
    if word in theme_tokens.STATUS:
        return pane.theme.status(word)[1]
    return theme_tokens.EMPHASIS


# --------------------------------------------------------------------------
# the filter row
# --------------------------------------------------------------------------


def filter_index(state):
    """Which of `FILTERS` is active. Total over anything `state` may hold."""
    try:
        return int(state.filter["legs"]) % len(FILTERS)
    except (AttributeError, KeyError, TypeError, ValueError):
        return 0


def visible_legs(model, state):
    """The legs the active filter selects, in plan order."""
    legs = model.get("legs") or []
    wanted = FILTERS[filter_index(state)][1]
    if wanted is None:
        return list(legs)
    return [leg for leg in legs if leg.get("status") == wanted]


def _counts(legs):
    """`{filter key: how many legs it selects}` — the figures on the row."""
    counts = {"all": len(legs)}
    for _, state in FILTERS:
        if state is not None:
            counts[state] = sum(1 for leg in legs if leg.get("status") == state)
    return counts


def _filter_texts(counts):
    return ["%s (%d)" % (label, counts["all" if state is None else state])
            for label, state in FILTERS]


def _filter_window(texts, active, width, ellipsis):
    """`(first, last)` — the widest run of filters that fits and includes the
    active one.

    A row clipped from the right would take the active filter off the screen
    the moment a reader cycled past the middle of it, and a highlight nobody
    can see is a filter row that has stopped saying which filter is on. So the
    window is grown outwards from the active entry, right first because that is
    the reading direction, and each end that hides something spends the cells
    to say so — the same rule a pane's `+N more` follows.
    """
    lo = hi = active

    def measure(first, last):
        cells = len(SEPARATOR.join(texts[first:last + 1]))
        if first > 0:
            cells += len(ellipsis) + len(SEPARATOR)
        if last < len(texts) - 1:
            cells += len(SEPARATOR) + len(ellipsis)
        return cells

    if measure(lo, hi) > width:
        return lo, hi                       # one does not fit; it is clipped
    growing = True
    while growing:
        growing = False
        if hi + 1 < len(texts) and measure(lo, hi + 1) <= width:
            hi += 1
            growing = True
        if lo > 0 and measure(lo - 1, hi) <= width:
            lo -= 1
            growing = True
    return lo, hi


def _filter_row(pane, counts, active):
    texts = _filter_texts(counts)
    ellipsis = pane.theme.glyph("ellipsis")
    first, last = _filter_window(texts, active, pane.body_width, ellipsis)
    parts = []
    if first > 0:
        parts.append((ellipsis + SEPARATOR, theme_tokens.MUTED))
    for offset, text in enumerate(texts[first:last + 1]):
        if offset:
            parts.append((SEPARATOR, theme_tokens.MUTED))
        parts.append((text, theme_tokens.SELECTED if first + offset == active
                      else theme_tokens.MUTED))
    if last < len(texts) - 1:
        parts.append((SEPARATOR + ellipsis, theme_tokens.MUTED))
    return parts


# --------------------------------------------------------------------------
# the grid
# --------------------------------------------------------------------------


def _reference(leg):
    """`S2/credential-parity` — the leg, said the way `Stage/ID` heads it."""
    identifier = leg.get("id") or "(unnamed leg)"
    stage = leg.get("stage")
    return "%s/%s" % (stage, identifier) if stage else identifier


def _marker(leg):
    """`judge`, `fix`, or nothing at all for an ordinary impl leg."""
    kind = leg.get("kind")
    return kind if kind in ("fix", "judge") else ""


def _lead(pane, leg):
    """The status glyph and the gap after it — one cell, then two."""
    glyph, attr = pane.theme.status(leg.get("status") or "pending")
    return glyph + "  ", attr


def _layout(pane, legs, width):
    """`(status, stage_id, fulfills)` column widths for `width` cells.

    Measured from every leg in the model rather than from the rows this repaint
    happened to draw, so the grid does not shift under a reader who pages down
    or changes filter.

    Below 80 columns the columns give way in a documented order: `Fulfills`
    first (it is the only one whose absence loses nothing a reader cannot get
    from the detail view), then `Stage/ID` shrinks and clips, and last of all
    the status *word* goes and the glyph stands for it alone.
    """
    lead = max([len(_lead(pane, leg)[0]) for leg in legs] or [3])
    widest = min(max([len(_status_word(leg)) for leg in legs] or [0]),
                 MAX_STATUS_WORD)
    status = max(len(COLUMNS[0]), lead + widest)
    stage = max([len(_reference(leg))
                 + (len(_marker(leg)) + 1 if _marker(leg) else 0)
                 for leg in legs] or [0])
    stage = max(len(COLUMNS[1]), stage)

    rest = width - status - GAP - stage - GAP
    if rest >= MIN_FULFILLS:
        return status, stage, rest
    rest = width - status - GAP
    if rest >= MIN_STAGE_ID:
        return status, rest, 0
    rest = width - lead - GAP
    if rest >= MIN_STAGE_ID:
        return lead, rest, 0
    return max(0, width), 0, 0


def _head_row(layout):
    """The column labels, over the columns they label.

    A label is drawn whole or not at all. `Stage/ID` and `Fulfills` are only
    ever given a column that can hold their label (`MIN_STAGE_ID`,
    `MIN_FULFILLS`), so the only one that can go is `Status` — and it goes at
    the width where the column has stopped carrying a word and is the status
    glyph alone, which the keybar's legend already names (ACC-TUI-006).
    """
    parts = []
    for label, width in zip(COLUMNS, layout):
        if not width:
            break
        text = label if len(label) <= width else ""
        parts.append((text.ljust(width + GAP), theme_tokens.MUTED))
    if parts:
        # The row stops where its last label does. Padding out to the last
        # column's full width would put the ellipsis of a clipped *blank* at
        # the pane's edge, which reads as a heading that was cut.
        parts[-1] = (parts[-1][0].rstrip(), parts[-1][1])
    return parts


def _leg_row(pane, leg, layout):
    """One leg as one line: status, stage and id with its kind, what it fulfils.

    Every cell is clipped to its own column, so the row is exactly as wide as
    the grid however long a coach's prose is (ACC-LEGS-003). The glyph keeps
    its own status attribute whatever the word beside it says — the glyph is
    the leg's state, the word may be the coach's (ACC-TUI-006).
    """
    status_width, stage_width, fulfills_width = layout
    lead, attr = _lead(pane, leg)
    parts = [(lead, attr)]

    room = status_width - len(lead)
    if room > 0:
        note = raw_status_note(leg)
        parts.append((chrome.clip(_status_word(leg), room).ljust(room + GAP),
                      _note_attr(pane, note) if note else attr))
    else:
        parts.append((" " * GAP, theme_tokens.BODY))

    if stage_width:
        marker = _marker(leg)
        suffix = " " + marker if marker else ""
        reference = chrome.clip(_reference(leg),
                                max(1, stage_width - len(suffix)))
        parts.append((reference, theme_tokens.EMPHASIS if leg.get("isActive")
                      else theme_tokens.BODY))
        if marker:
            parts.append((suffix, theme_tokens.KIND))
        pad = stage_width - len(reference) - len(suffix)
        if fulfills_width:
            pad += GAP
        if pad > 0:
            parts.append((" " * pad, theme_tokens.BODY))

    if fulfills_width:
        # An empty cell is the empty list: this leg claims no check. Nothing is
        # invented in its place — a dash or a `0` would be a value the plan
        # does not carry (ACC-DATA-007's rule, one layer up).
        checks = ", ".join(leg.get("fulfills") or [])
        if checks:
            parts.append((chrome.clip(checks, fulfills_width),
                          theme_tokens.MUTED))
    return parts


# --------------------------------------------------------------------------
# the view
# --------------------------------------------------------------------------


def draw(canvas, model, state):
    legs = model.get("legs") or []
    shown = visible_legs(model, state)
    # The title carries the count ACC-LEGS-001 asks for, and it is the count of
    # what this screen is showing: `Legs (27)` under `Completed` is the same
    # figure the filter row highlights (ACC-NAV-003).
    pane = canvas.full_pane("%s (%d)" % (TITLE, len(shown)))
    if pane is None:                        # too small for a pane at all
        return
    if not legs:
        # A filter row of five zeroes is filler, not information.
        pane.header(None)
        pane.empty("no legs planned yet")
        return

    active = filter_index(state)
    row = 0
    if pane.body_height >= 1:
        pane.segments(row, _filter_row(pane, _counts(legs), active))
        row += 1

    layout = _layout(pane, legs, pane.body_width)
    # A heading is never the last row drawn: with no room for a leg under them
    # the column labels give their row up to the content they were labelling.
    if pane.body_height - row >= 2:
        pane.segments(row, _head_row(layout))
        row += 1

    if not shown:
        pane.header(None)
        pane.line(row, "no leg is %s" % FILTERS[active][0].lower(),
                  theme_tokens.ABSENT)
        return

    drawn, hidden = chrome.paginate(shown, pane.body_height - row)
    # Never a range the pane did not draw: too short for a single leg says how
    # many there are instead (ACC-OVER-004's rule, and the same mistake).
    pane.header("1-%d of %d" % (len(drawn), len(shown)) if drawn
                else "%d legs" % len(shown))
    for offset, leg in enumerate(drawn):
        pane.segments(row + offset, _leg_row(pane, leg, layout))
    pane.more(hidden, row=row + len(drawn))


def handle(key, state, model):
    """`T` cycles the filter row. Everything else falls through — `q` above all.

    Bound here rather than in `app.py` because a view owns its own keys, and
    bound at all because a filter row that cannot move is one nobody can see
    working. The selection and the detail view are ACC-NAV-002/004 and belong
    to `navigation-and-filters`.
    """
    if key in (ord("t"), ord("T")):
        state.filter["legs"] = (filter_index(state) + 1) % len(FILTERS)
        return True
    return False

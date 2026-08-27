"""The Runners view — ACC-RUN-001..003.

`Runners (N)`, a filter row `All | Active | Completed | Failed` with counts, and
a table of eight columns: `#`, `Leg`, `Stage`, `Start`, `Duration`, `Commit`,
`Baton`, `Status`.

Three decisions here are regression checks rather than taste, and each one is a
defect this relay exists to remove:

1. **`Active (N)` is the model's own count of running runners.** It is not
   re-derived from anything, and it cannot be: `model["runnerCounts"]["active"]`
   counts the rows whose status is `running`, and a row is running exactly when
   its leg is (ACC-DATA-003 — the active runner *is* the active leg's row, by
   identity). The HTML dashboard this replaces read `Active (0)` while two legs
   ran because the count and the rows came from two different sources.
   ACC-RUN-002 is that defect stated as a check, and the filter that the count
   labels selects rows by the same `status` field the count was taken over.
2. **A field with no source is a marker, never a value.** The model omits what
   it did not measure (ACC-DATA-007): 17 of the agent-service fixture's 27
   completed legs left no baton, so most `Commit` and `Baton` cells are
   legitimately empty. An empty cell reads as a table that failed to draw, and
   an em-dash or a `0` reads as a value somebody measured, so each such cell
   gets one dim mark from the theme — the same "named absence" rule the rest of
   the package follows.
3. **A column with no data for *any* row is dropped** (ACC-RUN-003). A relay
   whose runners have written no batons has nothing to say under `Start`,
   `Duration`, `Commit` or `Baton`, and four columns of markers is worse than
   four columns fewer.

What the table gives way on, and in what order
---------------------------------------------
Eight columns do not fit 80 cells by right, so the widths are *measured from
the values actually rendered* — a column is as wide as its label or its widest
cell, whichever is more — and `Leg` is the elastic one: it absorbs what is
left and is clipped with the theme's ellipsis when there is not enough. Below
that, whole columns go, in `DROP_ORDER`, cheapest first; `Leg` and `Status`
never go, because a row that says neither which leg nor what happened to it is
not a row. The row is built to `pane.body_width`, which already excludes the
screen's reserved last column — see `.relay/skills/pane-conventions.md`.

What this view does not own
---------------------------
**No key.** `T` was bound here so that the filter row could be seen moving, and
this module's own runner asked that the handler be *deleted* rather than left to
shadow a central one — two handlers for one key is how a view stops responding
for reasons nobody can find. `navigation-and-filters` deleted it: `T`, `Up`/`Dn`
and `Enter` are one handler in `app._navigate`. What survives is
`state.filter["runners"]`, which this view honoured before there was a key to
move it, and `visible_runners()`, which is the one answer to "which rows is the
Runners view showing" for the count, the table and the selection alike.
"""

import collections
import time

from . import chrome, navigation
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

#: The filter row, in the order ACC-RUN-001 names it: the label, the runner
#: status it keeps (`None` keeps every row), and the `runnerCounts` key its
#: figure comes from. One tuple, so the label, the count and the rows behind it
#: cannot come apart — which is the whole of ACC-RUN-002.
FILTERS = (
    ("All", None, "total"),
    ("Active", "running", "active"),
    ("Completed", "completed", "completed"),
    ("Failed", "failed", "failed"),
)

#: One cell between columns. Not a constant anybody may treat as a width: the
#: widths are measured from the values, and this is the gutter between them.
GAP = 1

_Column = collections.namedtuple("_Column", "key label value token")


def _number(runner):
    n = runner.get("n")
    return None if n is None else str(n)


def started(runner):
    """When this runner started, as a wall clock reads it. `None` stays `None`.

    A runner starts when the previous runner's baton lands, so a row whose
    predecessor left no baton has no start — that is an absence to render, not
    a zero to print.
    """
    start = runner.get("start")
    if start is None:
        return None
    try:
        return time.strftime("%H:%M", time.localtime(float(start)))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _baton(runner):
    """How long the baton this runner left is — the one fact that says it landed."""
    lines = runner.get("batonLines")
    if not isinstance(lines, int) or isinstance(lines, bool):
        return None
    return "%d ln" % lines


COLUMNS = (
    _Column("n", "#", _number, theme_tokens.MUTED),
    _Column("leg", "Leg", lambda r: r.get("leg"), theme_tokens.BODY),
    _Column("stage", "Stage", lambda r: r.get("stage"), theme_tokens.MUTED),
    _Column("start", "Start", started, theme_tokens.MUTED),
    _Column("duration", "Duration",
            lambda r: chrome.humanise_duration(r.get("duration")),
            theme_tokens.MUTED),
    _Column("commit", "Commit", lambda r: r.get("commit"), theme_tokens.MUTED),
    _Column("baton", "Baton", _baton, theme_tokens.MUTED),
    _Column("status", "Status", lambda r: r.get("status"), None),
)

#: The column that absorbs whatever width is left, and is clipped when there is
#: none. A leg id is the one value in the row that is prose rather than a
#: measurement, so it is the one that can lose its tail and still be read.
ELASTIC = "leg"

#: How narrow the elastic column may be made before whole columns start to go
#: instead. A leg id cut shorter than this has stopped naming its leg, and a
#: table of unreadable ids in eight columns is worse than the same table in
#: five.
MIN_ELASTIC = 12

#: Whole columns give way in this order once narrowing `Leg` is not enough.
#: Cheapest first: a baton's length and a commit's sha are lookups a supervisor
#: makes rarely, a start time is recoverable from the duration, and the stage
#: is on every other screen. `leg` and `status` are not in the list at all.
DROP_ORDER = ("baton", "commit", "start", "stage", "duration", "n")


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------


def draw(canvas, model, state):
    runners = model.get("runners") or []
    counts = model.get("runnerCounts") or {}
    chosen = _filter_index(state)
    # `Runners (N)` is the figure of the filter that is *showing*, taken from
    # the same `runnerCounts` key the filter row highlights — so the title and
    # the highlighted filter are one number under every filter (ACC-NAV-003),
    # and it is still the model's own count and not a re-derived one
    # (ACC-RUN-002).
    label, status, key = FILTERS[chosen]
    pane = canvas.full_pane("%s (%d)"
                            % (TITLE, _count(counts, key, runners, status)))
    if pane is None:                       # too small for a pane at all
        return
    if not runners:
        pane.header(None)
        pane.empty("no runner has been on a leg yet")
        return

    rows = visible_runners(model, state)
    if pane.body_height <= 0:
        return
    _draw_filters(pane, counts, chosen, runners)
    if not rows:
        # A filter with nothing in it is not an empty pane: the row above says
        # how many there are of each, and this says which one is showing.
        pane.header("none")
        pane.line(1, "no runner matches the %s filter" % label,
                  theme_tokens.ABSENT)
        return
    _draw_table(pane, rows, model.get("activeRunner"),
                navigation.selected(state, "runners", len(rows)))


def _draw_filters(pane, counts, chosen, runners):
    """`All (29) | Active (1) | Completed (28) | Failed (0)` on the first row.

    Every figure is the model's own. The one that matters is `Active`, and it
    is taken from the same `runnerCounts` the rows' `status` fields were
    counted into, so the number and the list it labels cannot disagree.
    """
    parts = []
    for index, (label, status, key) in enumerate(FILTERS):
        if index:
            parts.append((" | ", theme_tokens.MUTED))
        parts.append(("%s (%d)" % (label, _count(counts, key, runners, status)),
                      theme_tokens.SELECTED if index == chosen
                      else theme_tokens.MUTED))
    pane.segments(0, chrome.fit_parts(parts, pane.body_width,
                                      pane.theme.glyph("ellipsis")))


def _draw_table(pane, rows, active, chosen):
    """The column labels and the runner rows, fitted to the pane once.

    The layout is computed over *every* row the filter kept rather than over
    the page that fits, so a column neither appears nor changes width as a
    reader pages through the list — or scrolled it, which is now a thing a
    reader can do (ACC-NAV-002).
    """
    cells = [_cells(runner, pane.theme, runner is active) for runner in rows]
    widths = _widths(cells)
    kept = _fit(cells, widths, pane.body_width)

    top = 1
    if pane.body_height - top >= 2:
        # A heading is a label for the rows under it: with no room for any of
        # them it gives its row up to the content rather than pointing at
        # nothing (`.relay/skills/pane-conventions.md`).
        pane.segments(top, _label_segments(kept, widths, pane.theme,
                                           pane.body_width))
        top += 1

    start, shown, above, below = navigation.window(
        cells, pane.body_height - top, chosen)
    # Never a range the pane did not draw: `1-0 of 29` is the same sentence as
    # `1-0 of 0`, so a pane too short for one row says how many there are.
    pane.header("%d-%d of %d" % (start + 1, start + len(shown), len(cells))
                if shown else "%d runners" % len(cells))
    if above:
        pane.line(top, "+%d earlier" % above, theme_tokens.MUTED)
        top += 1
    for offset, row in enumerate(shown):
        parts = _row_segments(row, kept, widths, pane.theme, pane.body_width)
        if start + offset == chosen:
            parts = navigation.highlight(parts, pane.body_width)
        pane.segments(top + offset, parts)
    pane.more(below, row=top + len(shown))


def _cells(runner, theme, active):
    """`{key: (text, token)}` for one runner — every cell, before any fitting.

    Rendering first and measuring the result afterwards is what keeps a
    column's width and its content one fact: a width computed from a value the
    row does not draw is how a table comes to clip a cell it had room for.
    """
    row = {}
    for column in COLUMNS:
        value = column.value(runner)
        if value is None:
            # The named absence. `theme.ABSENT` and one mark from the glyph
            # table, so it degrades with every other glyph and can never be
            # mistaken for a value somebody measured (ACC-RUN-003).
            row[column.key] = (theme.glyph("bullet"), theme_tokens.ABSENT)
        elif column.key == "status":
            # The glyph and its attribute together, never one half of the pair
            # (ACC-TUI-006).
            glyph, attr = theme.status(value)
            row[column.key] = ("%s %s" % (glyph, value), attr)
        elif column.key == ELASTIC and active:
            row[column.key] = (str(value), theme_tokens.EMPHASIS)
        else:
            row[column.key] = (str(value), column.token)
    return row


def _widths(cells):
    """`{key: cells}` — each column as wide as its label or its widest value."""
    return {column.key: max([len(column.label)]
                            + [len(row[column.key][0]) for row in cells])
            for column in COLUMNS}


def _fit(cells, widths, width):
    """The columns to draw, left to right, narrowed and dropped to fit `width`.

    Two reasons a column is not drawn, and they are different sentences:
    ACC-RUN-003's — no row has anything to put in it — and the terminal's.

    `widths` is mutated for the elastic column, which is the point: what the
    label row is spaced to and what the value rows are spaced to have to be the
    same number.
    """
    kept = [column.key for column in COLUMNS if _measured(cells, column.key)]
    for key in DROP_ORDER:
        if _fits(kept, widths, width):
            break
        if key in kept:
            kept.remove(key)
    over = _table_width(kept, widths) - width
    if over > 0 and ELASTIC in kept:
        widths[ELASTIC] = max(1, widths[ELASTIC] - over)
    return kept


def _fits(kept, widths, width):
    """Whether these columns fit once the elastic one has given what it can.

    Narrowing comes before dropping, and this is where the two are ordered: a
    column dropped while `Leg` still had cells to give is a fact taken off the
    screen to keep a leg id nobody needed in full.
    """
    total = _table_width(kept, widths)
    if ELASTIC in kept:
        total -= max(0, widths[ELASTIC] - MIN_ELASTIC)
    return total <= width


def _measured(cells, key):
    """Whether any row has something to say in this column (ACC-RUN-003).

    A column of nothing but absence markers is a column of nothing: the marker
    says "this row has no commit", and a whole column of it says "this relay
    records no commits", which is a sentence about the relay and not a column.
    """
    return any(row[key][1] != theme_tokens.ABSENT for row in cells)


def _table_width(kept, widths):
    return sum(widths[key] for key in kept) + GAP * max(0, len(kept) - 1)


def _label_segments(kept, widths, theme, width):
    ellipsis = theme.glyph("ellipsis")
    return chrome.fit_parts(_segments(
        [(key, (COLUMNS_BY_KEY[key].label, theme_tokens.MUTED))
         for key in kept], widths, ellipsis), width, ellipsis)


def _row_segments(row, kept, widths, theme, width):
    ellipsis = theme.glyph("ellipsis")
    return chrome.fit_parts(
        _segments([(key, row[key]) for key in kept], widths, ellipsis),
        width, ellipsis)


def _segments(pairs, widths, ellipsis="…"):
    """One row: each cell clipped to its column, padded to it, one cell apart.

    The last column is not padded — trailing spaces in a pane are cells a
    narrower terminal would rather have given to the value before them.
    """
    parts = []
    for index, (key, (text, token)) in enumerate(pairs):
        if index:
            parts.append((" " * GAP, theme_tokens.MUTED))
        text = chrome.clip(text, widths[key], ellipsis)
        if index < len(pairs) - 1:
            text = text.ljust(widths[key])
        parts.append((text, token))
    return parts


COLUMNS_BY_KEY = {column.key: column for column in COLUMNS}


# --------------------------------------------------------------------------
# the filter (ACC-NAV-003)
# --------------------------------------------------------------------------


def _filter_index(state):
    """Which filter is showing. Out of range wraps rather than raising."""
    return navigation.filter_index(state, "runners", len(FILTERS))


def visible_runners(model, state):
    """The runner rows the active filter selects, in the model's order.

    One answer, used three times: the table draws these rows, the selection
    indexes into them, and `Enter` opens the detail of one of them. A second
    list comprehension somewhere else is how a selection comes to point at a
    row a reader is not looking at.
    """
    runners = model.get("runners") or []
    wanted = FILTERS[_filter_index(state)][1]
    if wanted is None:
        return list(runners)
    return [runner for runner in runners if runner.get("status") == wanted]


def _count(counts, key, runners, status):
    """One filter's figure, as the model reports it.

    Not re-derived: the model counts the runner rows it built, and a view that
    counted them again by a rule of its own is how two panels came to disagree
    about the same relay in the first place.

    A model that reports no such figure at all is the one case where counting
    is better than trusting. `Active (0)` beside a list of running runners is
    the defect ACC-RUN-002 exists to keep out, and it is exactly what a `0`
    default would put back the day `runnerCounts` loses a key.
    """
    value = counts.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return sum(1 for runner in runners
               if status is None or runner.get("status") == status)

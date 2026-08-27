"""Selection, filters and detail — the mechanism behind ACC-NAV-001..005.

The five views were built in parallel, and three of them grew a `handle()` of
their own so that the thing they had just drawn could be seen moving: `legs`
and `runners` each bound `T`, and `models` bound `Up`/`Dn`/`Enter`. That is a
reasonable thing for a view leg to do and a bad thing to ship, because two
handlers for one key is how a view stops responding for reasons nobody can
find: whichever one runs first wins, the second is dead code that reads as
live, and no frame on any screen says which is which.

So there is now **one handler for one key**, it lives in `app._navigate`, and
every view's `handle()` has been deleted. What is left in the views is what a
view actually owns — what a row *looks* like — and what is here is what
navigating *is*:

    selected(state, view, count)   which row the keyboard is on
    move(state, view, delta, n)    move it, and close whatever Enter opened
    filter_index(state, view, n)   which filter is showing
    cycle(state, view, n)          move that on by one
    window(items, height, focus)   the slice to draw so `focus` is on screen
    highlight(parts, width)        one row, drawn as the selected row
    open_detail(state, view)       the full-pane detail
    close_detail(state)            Esc out of it
    draw_detail(canvas, spec, item)

Nothing here imports a view. A view imports *this* for `selected()`,
`window()` and `highlight()`; `app.py` holds the registry that says which view
has a filter row, which has selectable rows, and what a detail of one of its
rows shows. That is the seam: this module is mechanism, `app.py` is wiring,
a view module is content, and none of the three needs to know the other two's
business to be read.

Two things this module deliberately does *not* do:

* **It does not define a second text-drawing path.** `draw_detail()` builds
  `(text, token)` segments and hands them to `Pane`, which reaches the screen
  through `chrome.Canvas.write()` like everything else — the one choke point
  where a control character in a coach's prose stops being one (ACC-ROBUST-006,
  and the AST sweep in `tests/test_chrome.py` that proves it).
* **It does not hold a model.** `app._run` calls `relay_model.build()` once per
  repaint and the object is replaced every time, so a selection is an *index*
  and a detail is a note of which view is open — never the row itself. A stored
  row would be a snapshot of a relay that has since moved, drawn beside a
  header that has not.
"""

import collections
import curses

from . import chrome
from . import theme as theme_tokens

#: The keys this module answers to. Spelled once, here, so that the sweep in
#: `tests/test_navigation.py` can state "these ordinals appear in exactly one
#: module" and a second binding for any of them is a test failure rather than a
#: silent shadow.
UP = curses.KEY_UP
DOWN = curses.KEY_DOWN
FILTER_KEYS = (ord("t"), ord("T"))
ENTER_KEYS = (10, 13, curses.KEY_ENTER)

#: What the keybar says while a detail is open. A detail takes `Esc`, and the
#: two keys that are never a view's to refuse — and it takes nothing else, so
#: it advertises nothing else. `Up/Dn`, `Enter` and `T` on this screen would be
#: three keys named and not taken, which is a lie on every frame.
DETAIL_BINDINGS = (
    ("Esc", "Back"),
    ("Tab", "Next View"),
    ("q", "Quit"),
)

#: Where a detail's values start, under the label they belong to. Two cells, so
#: a wrapped line can never be read as a label of its own.
INDENT = 2

#: What a field with nothing in it says. In words, and in `theme.ABSENT`: a
#: blank reads as a pane that failed to draw and a dash reads as a value
#: somebody measured. The same sentence `pane.empty()` says at pane scale.
NONE = "none"

#: One field of a detail: the label, and a callable taking the row and giving
#: back a string, a sequence of strings, or None for "this row has no such
#: value". `None` and `""` and `[]` all mean the same thing and all read `none`.
Field = collections.namedtuple("Field", "label value")

#: A whole detail: what the pane calls itself, its right-hand figure, and its
#: fields in reading order. Built in `app.py`, where the view modules are
#: already imported — a check's *shown* status is `contract.shown_status()`'s
#: answer and not `state.json`'s, and this module must not be the second place
#: that decides such a thing.
Detail = collections.namedtuple("Detail", "title meta fields")

#: One row of a detail, before it is cut. `heading` marks a field's label, which
#: may never be the last row drawn — a label with nothing under it points at
#: nothing (`.relay/skills/pane-conventions.md`).
_Row = collections.namedtuple("_Row", "heading parts")


# --------------------------------------------------------------------------
# the selection
# --------------------------------------------------------------------------


def selected(state, view, count):
    """Which row of `count` the keyboard is on, clamped into the list.

    **Clamped on every read, and that is not the same guarantee `move()` gives.**
    `move()` keeps the index inside the list it was moving in; this keeps it
    inside the list being drawn *now*, and the two are different lists as soon
    as a filter narrows one: a selection 35 rows down the plan, then `T` to a
    filter that keeps eight legs, is an index no keystroke touched and every
    caller here would index a list with. That is ACC-NAV-002's "stays within
    bounds at both ends" said once, in the one place every caller goes through.

    An empty list clamps to 0 by the same expression, without a branch of its
    own to disagree with: `min(-1, anything)` is negative and `max(0, ...)`
    takes it back. There is no guard against `state` holding something that is
    not an int either — `state.selection` is written by `move()` and by nothing
    else, so a `try` here would be a branch no caller can reach and no test
    could ever kill.
    """
    return max(0, min(count - 1, int(state.selection.get(view, 0))))


def move(state, view, delta, count):
    """Move the selection by `delta`, and close what the last `Enter` opened.

    The message a `models` Enter left on the pane's last row named *that* row;
    a message left standing under a different row is an answer to a question
    nobody asked. A navigation detail cannot be open here — `app._navigate`
    routes no arrow key while one is — so this only ever closes the message.
    """
    # One clamp, and it is the same one `selected()` reads through: an index
    # stored out of range is an index the next writer of a view has to
    # remember to distrust. An empty list clamps to 0 by the same expression —
    # `min(-1, anything)` is negative and `max(0, ...)` takes it back — so
    # there is no second branch for it to disagree with.
    index = selected(state, view, count) + delta
    state.selection[view] = max(0, min(count - 1, index))
    state.detail = None


# --------------------------------------------------------------------------
# the filter row
# --------------------------------------------------------------------------


def filter_index(state, view, count):
    """Which of `count` filters is showing.

    The wrap lives here and not in `cycle()`, for the reason `selected()`'s
    clamp lives on the read: one normaliser, at the point of use, cannot
    disagree with itself. Written the other way round — `cycle()` storing
    `% count` and this returning it raw — the modulo here would be dead the
    moment `cycle()` became the only writer, which it is.
    """
    return int(state.filter.get(view, 0)) % count


def cycle(state, view, count):
    """`T`: the next filter along. `filter_index()` is what makes it wrap."""
    state.filter[view] = filter_index(state, view, count) + 1


# --------------------------------------------------------------------------
# the detail (ACC-NAV-004)
# --------------------------------------------------------------------------


def open_detail(state, view):
    """`Enter`: the selected row, full pane.

    A dict rather than a row, and a dict rather than a string: the row is a
    snapshot of a model that is rebuilt on the next repaint, and the string is
    already taken — `models` puts its read-only message in the same slot, and
    the two have to be told apart by something that cannot collide.
    """
    state.detail = {"view": view}


def close_detail(state):
    """`Esc`: back to the list. Returns whether a detail was open.

    The selection is not touched, which is the whole of ACC-NAV-004's second
    half: `state.selection[view]` was never what opening a detail changed, so
    there is nothing to restore and nothing that can fail to be restored.
    """
    if not is_open(state):
        return False
    state.detail = None
    return True


def is_open(state):
    """Whether a *navigation* detail is open — not `models`' Enter message."""
    return isinstance(getattr(state, "detail", None), dict)


def draw_detail(canvas, spec, item):
    """One row of a list, full pane: every field it has, and `none` for the rest.

    One list of rows, cut once, the way every multi-region pane in this package
    is cut (`.relay/skills/pane-conventions.md`). A field's label is a heading
    and is never the last row drawn; every value is wrapped into the pane and
    indented under its label; `+N more` counts lines a reader would count.
    """
    pane = canvas.full_pane(str(spec.title(item)))
    if pane is None:                        # too small for a pane at all
        return
    meta = spec.meta(item)
    pane.header(str(meta) if meta else None)
    if pane.body_height <= 0:
        return

    rows = _detail_rows(spec, item, pane.body_width, pane.theme)
    shown, hidden = _fit(rows, pane.body_height)
    for offset, row in enumerate(shown):
        pane.segments(offset, row.parts)
    pane.more(hidden, row=len(shown))


def _detail_rows(spec, item, width, theme):
    """Every row the detail would draw, in reading order, before any cutting."""
    ellipsis = theme.glyph("ellipsis")
    rows = []
    for field in spec.fields:
        rows.append(_Row(True, [(chrome.clip(field.label, width, ellipsis),
                                 theme_tokens.PANE_TITLE)]))
        values = _values(field.value(item))
        if not values:
            rows.append(_Row(False, [(" " * INDENT, theme_tokens.MUTED),
                                     (NONE, theme_tokens.ABSENT)]))
            continue
        for value in values:
            for line in chrome.wrap(value, max(1, width - INDENT)):
                rows.append(_Row(False, [(" " * INDENT, theme_tokens.MUTED),
                                         (line, theme_tokens.BODY)]))
    return rows


def _values(value):
    """A field's value as `[str]` — one entry, several, or none at all.

    A string is one value however long it is; a list is one value per entry,
    because a leg's boundaries are separate sentences and joining them would
    invent a paragraph nobody wrote. Anything else — an int, a bool a coach
    typed where a list belonged — is rendered as what it is rather than
    discarded: `.relay/` is hand-written and a view that quietly drops a value
    it did not expect tells the coach their typo worked.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        out = []
        for entry in value:
            out.extend(_values(entry))
        return out
    return [str(value)]


def _fit(rows, height):
    """`(shown, hidden)` — the rows that fit, one kept back for the marker.

    A heading is never the last row drawn: a field's label with no room for its
    value points at nothing, so it gives its row up to the count of what was
    hidden.
    """
    if len(rows) <= height:
        return rows, 0
    shown = rows[:height - 1]
    while shown and shown[-1].heading:
        shown = shown[:-1]
    return shown, len(rows) - len(shown)


# --------------------------------------------------------------------------
# what a list looks like once it has a selection (ACC-NAV-002)
# --------------------------------------------------------------------------


def window(items, height, focus):
    """`(start, shown, above, below)` — the slice to draw, with `focus` in it.

    Plan order throughout; what moves is the window. `start` is the real index
    of `shown[0]`, so a pane can state the range it actually drew; `above` and
    `below` are what the two markers must report.

    Each end that hides something spends a row saying so — `+N earlier` above,
    `pane.more()`'s `+N more` below — and `above + len(shown) + below` is the
    whole list at every size. When only one marker row fits, that marker
    reports **everything** hidden and `above` is 0: a `+8 more` that silently
    omits the fifteen rows above the window is the same lie as `1-0 of 12`.

    This is `overview._leg_window()`, moved. It was written there for the
    running leg, which is that pane's privileged row; a selection is the same
    problem with a different row privileged, and the Legs view's own module
    docstring said so — *"duplicating `overview._leg_window` here would leave
    two answers to one question"*. So there is one answer, and the Overview
    passes the active leg where the list views pass the selection.
    """
    total = len(items)
    if height <= 0:
        return 0, [], 0, total
    if total <= height:
        return 0, list(items), 0, 0
    if focus is None or focus < height - 1:
        # Nothing privileged, or it is inside the first screenful: plain
        # overflow, one marker at the bottom.
        shown, hidden = chrome.paginate(items, height)
        return 0, shown, 0, hidden

    def start_for(span):
        # One row of context under the focused row where there is room for it,
        # and never scrolled past the focused row itself.
        start = min(max(0, focus - span + 2), total - span)
        return max(0, min(start, focus))

    span = max(1, height - 1)
    start = start_for(span)
    if start > 0 and start + span < total and height >= 3:
        span = height - 2                       # a marker at each end
        start = start_for(span)
    shown = list(items[start:start + span])
    below = total - start - span
    if height - len(shown) - (1 if below else 0) < 1:
        # Only one marker row fits — and it has to *have* a row. A pane that
        # spent its last row on content and then reported what it hid on the
        # row below itself drew neither: `Pane` drops a write outside the pane,
        # so the screen showed one row out of a hundred and nineteen with
        # nothing saying so, which is the same lie as `1-0 of 12`. Reached only
        # at a body of one row, where `span` is the whole of it.
        shown = shown[:max(0, height - 1)]
        below = total - start - len(shown)
        return start, shown, 0, below + start
    return start, shown, start, below


def highlight(parts, width):
    """`parts` drawn as the row the keyboard is on, padded across `width`.

    Two rules, and each one shipped as a defect somewhere before:

    * **The highlight is a row, not a word.** `theme.SELECTED` is reverse video
      and stops where its text does, so the row is padded to the pane's body
      width — and no further: the pane's width already excludes the screen's
      reserved last column, and padding past it breaks `assert_within_width()`
      for every test in the repository. The padding is measured in **cells**
      (`chrome.cell_width`), not characters: a leg id in CJK is drawn two
      columns per character, and `len()` would pad a row that was already full
      out past the margin.
    * **A segment carrying an already-resolved attribute keeps it.** That is
      exactly the `theme.status()` pair — a glyph and its state's colour, which
      ACC-TUI-006 says travel together — because every other token in this
      package is a *name* and only `status()` hands back the resolved pair. The
      highlight says where the keyboard is; the glyph says what state that row
      is in; one attribute for the whole row loses the second.
    """
    out = []
    drawn = 0
    for text, token in parts:
        drawn += chrome.cell_width(text)
        out.append((text, token if isinstance(token, int)
                    else theme_tokens.SELECTED))
    if drawn < width:
        out.append((" " * (width - drawn), theme_tokens.SELECTED))
    return out

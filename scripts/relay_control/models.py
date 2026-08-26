"""The Models view — ACC-MODEL-001..003.

Three role rows — Coach, Runner, Judge — each with the model and reasoning
effort `dashboard.json` pins to it and the one-line reason that role wants that
kind of model; then the experimental judge toggles, with their state, read-only.

Why this view is not like the other five
----------------------------------------
Every other view renders something `relay_model.build()` *reconciled*: it read
several files, resolved their disagreements and published a shape the contract
pins. This one renders what the coach typed. The model deliberately passes
`dashboard.json.models` through as raw `model["extras"]` rather than guessing a
shape for it, because normalising it was left to whoever drew it — so **the
shape is defined here**, and it is defined defensively:

* `extras["models"]` is a mapping of role name to entry. An entry is either a
  mapping (`{"model": str, "effort": str}`) or the bare model name as a string.
* `extras["toggles"]` is a mapping of toggle key to a JSON boolean.
* Everything else is a coach's typo, and a typo is *named on the screen*. A
  role whose entry is a list reads `✗ unreadable (list)`, not `not configured`
  and certainly not the list spelled out as though it were a model. The three
  states a reader has to be able to tell apart are: a model was pinned, no
  model was pinned, and something was written that this view cannot read. A
  screen that spells the third like the second tells a coach their typo worked.

Nothing here trusts a type. `.relay/` is hand-written, the model reports
malformed input as written by contract (ACC-DATA-001), and a view that assumed
`entry["model"]` was a string would take the TUI down over a stray bracket.

Three states, three spellings
-----------------------------
``not configured`` is ACC-MODEL-001's "documented default when absent", and it
is the common case — this repository's own `dashboard.json` has no `models` key
at all, so all three roles read it. That screen must look deliberate rather than
broken, which is why the roles, their reasons and the toggles are all still
drawn: what is missing is one cell of each row, said in words, in `theme.ABSENT`.

``off (default)`` is the same rule for a toggle. "Off" and "off because nobody
wrote it" are different facts, and a screen that spells them the same way has
invented the first one.

Read-only, twice over
---------------------
The pane's own figure says `read-only`, and Enter on a toggle says which file to
edit instead. ACC-MODEL-003 is that the TUI never writes — a dashboard is a view,
never a gate — and there is no code path here that could: this module opens
nothing, and the ACC-TUI-007 sweep over the package proves it.

Layout
------
`Role | Model | Effort` is a table, built the way `runners-view` and `legs-view`
built theirs (`.relay/skills/pane-conventions.md`): every cell is rendered
first, each column is as wide as its label or its widest rendered cell, the
untrusted values are capped before they are measured, and the last cell of a row
is never padded. `Effort` is dropped when no role has one — a column of nothing
but absence marks is a statement about the relay, not a column.

The body is several regions (a table, then the toggles), so it is built as one
list of rows in reading order and truncated **once**: blank separators go first,
a heading is never the last row drawn, and `+N more` counts only rows a reader
would count as a line.
"""

import curses

from . import chrome
from . import theme as theme_tokens

TITLE = "Models"

BINDINGS = (
    ("Up/Dn", "Select"),
    ("Enter", "Toggle"),
    ("Esc", "Overview"),
    ("Tab", "Next View"),
    ("q", "Quit"),
)

#: The three roles ACC-MODEL-001 names, in its order: the key `dashboard.json`
#: writes them under, the label, and the one-line reason ACC-MODEL-002 asks for.
#: The reasons are SKILL.md's Models table, one line each — a *line*, because a
#: reason folded across two rows is not one a reader takes in at a glance, and
#: because the judge's clause is the whole of ACC-MODEL-002's evidence and has
#: to survive at 80 columns intact.
ROLES = (
    ("coach", "Coach",
     "Slow, careful reasoning: strategy, constraints, long-horizon decomposition."),
    ("runner", "Runner",
     "Code fluency and speed: fast generation, confident tool use."),
    ("judge", "Judge",
     "Strict instruction-following, and a different provider from the runner."),
)

#: The experimental toggles ACC-MODEL-003 names, spelled as it spells them, and
#: the key each is read from under `dashboard.json.toggles`.
TOGGLES = (
    ("skipCodeJudge", "Skip code judge"),
    ("skipBehaviourJudge", "Skip behaviour judge"),
)

TOGGLES_HEADING = "Experimental toggles"

#: The column labels, in order. `Role` and `Model` are always drawn; `Effort`
#: goes when no role has one.
COLUMNS = ("Role", "Model", "Effort")

#: What Enter says. It states that the TUI is read-only and names the file to
#: edit — ACC-MODEL-003, in one line, in ASCII: an em-dash here would be a blank
#: cell under a locale that cannot encode it.
READ_ONLY = "is read-only: edit dashboard.json to change it"

#: The pane's own right-hand figure. Not a count — this pane has nothing to
#: count — but the standing half of ACC-MODEL-003, so it is true before anyone
#: presses anything.
META = "read-only"

#: How wide an untrusted value may be before it is cut. A model name longer
#: than this is prose, and prose in a cell sizes the column from the coach's
#: typing rather than from the table (`.relay/skills/pane-conventions.md`:
#: "Untrusted prose decides your column widths unless you stop it").
MAX_NAME = 24
MAX_EFFORT = 12

#: One cell between columns, and the indent that puts a reason and a toggle
#: under the row they belong to. Neither is a width.
GAP = 1
INDENT = "  "


# --------------------------------------------------------------------------
# reading untrusted input
#
# Every function here answers with a value *and* whether what it read was
# unreadable, because "absent" and "mistyped" are different sentences on the
# screen and collapsing them is the defect this view exists not to have.
# --------------------------------------------------------------------------


def _mapping(source, key):
    """`(mapping, problem)` for a key that should hold an object.

    A missing key is not a problem — nobody has to configure a model. A key
    holding a list *is* one, and saying so names what the coach has to fix.
    """
    value = source.get(key)
    if value is None:
        return {}, None
    if isinstance(value, dict):
        return value, None
    return {}, type(value).__name__


def _field(entry, key, limit, ellipsis):
    """`(text, problem)` for one field of a role's entry.

    A string only. A number where a model name belongs is a typo, and rendering
    `4.5` as the model spells the typo as a value somebody chose. An empty or
    blank string claims nothing, so it is the absent case rather than a typo.
    """
    value = entry.get(key)
    if isinstance(value, str):
        text = value.strip()
        return (chrome.clip(text, limit, ellipsis), None) if text else (None, None)
    if value is None:
        return None, None
    return None, "%s: %s" % (key, type(value).__name__)


def _role(models, key, ellipsis):
    """`((name, problem), (effort, problem))` for one role, from raw extras."""
    raw = models.get(key)
    if raw is None:
        return (None, None), (None, None)
    if isinstance(raw, str):
        # The bare spelling: `"coach": "Opus 4.6"`. It says unambiguously which
        # model, so it is read rather than called malformed.
        text = raw.strip()
        name = chrome.clip(text, MAX_NAME, ellipsis) if text else None
        return (name, None), (None, None)
    if not isinstance(raw, dict):
        return (None, type(raw).__name__), (None, None)
    return (_field(raw, "model", MAX_NAME, ellipsis),
            _field(raw, "effort", MAX_EFFORT, ellipsis))


def _toggle(toggles, key):
    """`(text, problem)` for one toggle's state.

    A JSON boolean only. `"yes"` and `1` are a coach reaching for a boolean and
    missing, and reading either as `on` would report a judge as skipped on the
    strength of a typo.
    """
    value = toggles.get(key)
    if value is None:
        return None, None
    if isinstance(value, bool):
        return ("on" if value else "off"), None
    return None, type(value).__name__


def _roles(model, ellipsis):
    """Every role's rendered facts, before any of it is laid out.

    `[(label, reason, (name, problem), (effort, problem))]`, in ROLES order,
    plus whatever is wrong with the `models` key itself.
    """
    extras = model.get("extras")
    extras = extras if isinstance(extras, dict) else {}
    models, problem = _mapping(extras, "models")
    return ([(label, reason) + _role(models, key, ellipsis)
             for key, label, reason in ROLES], problem)


# --------------------------------------------------------------------------
# cells
# --------------------------------------------------------------------------


def _cell(text, problem, absent, theme):
    """`(text, token)` for one table cell — a value, an absence or a typo.

    The typo carries the theme's failure glyph and its attribute together
    (ACC-TUI-006), because it is a state and the pair may not come apart.
    """
    if problem is not None:
        glyph, attr = theme.status("failed")
        return "%s %s (%s)" % (glyph, "unreadable", problem), attr
    if text is None:
        return absent, theme_tokens.ABSENT
    return text, theme_tokens.BODY


def _toggle_cell(text, problem, theme):
    if problem is not None:
        glyph, attr = theme.status("failed")
        return "%s %s (%s)" % (glyph, "unreadable", problem), attr
    if text is None:
        # Off, and nobody wrote it. Two facts, so two spellings.
        return "off (default)", theme_tokens.ABSENT
    return text, theme_tokens.EMPHASIS if text == "on" else theme_tokens.MUTED


def _problem_row(theme, key, problem, width, ellipsis):
    """The row that says a whole `dashboard.json` key is the wrong shape."""
    glyph, attr = theme.status("failed")
    return _row(_clip_parts(
        [("%s %s is unreadable: a %s, not an object" % (glyph, key, problem),
          attr)], width, ellipsis))


# --------------------------------------------------------------------------
# body rows
#
# One list, in reading order, cut once. See `_fit`.
# --------------------------------------------------------------------------


def _row(segments, heading=False):
    return {"segments": segments, "heading": heading}


_BLANK = {"segments": (), "heading": False}


def _fit(rows, height):
    """`(shown, hidden)` for a list of body rows. `height` is at least one.

    The same three rules the Active Leg pane's regions follow, and each was a
    defect somewhere first: blank separators are the cheapest row and go before
    any content; `hidden` counts only rows a reader would count as a line, or
    `+3 more` promises three lines that do not exist; and a heading is never the
    last row shown, because a heading is a label for the rows under it and with
    no room for any of them it gives its row up rather than pointing at nothing.

    There is deliberately no `height <= 0` branch. `draw()` is the only caller
    and it answers that case itself, so a guard here would be a line no screen
    could reach — and a branch nothing reaches is a mutation no test can kill
    (`.relay/skills/pane-conventions.md`, added by `contract-view`). The fix for
    dead code is to delete it, not to write a test for a screen the layout will
    never build.
    """
    if len(rows) <= height:
        return list(rows), 0
    dense = [row for row in rows if row["segments"]]
    if len(dense) <= height:
        return dense, 0
    while True:
        shown = dense[:height - 1]              # one row kept for `+N more`
        if not shown or not shown[-1]["heading"]:
            return shown, len(dense) - len(shown)
        dense = [row for row in dense if row is not shown[-1]]


def _clip_parts(parts, width, ellipsis):
    """`parts` cut to `width` cells, with the cut marked once, in the theme's
    own ellipsis.

    A row left to `Canvas.write()` to clip is cut with the literal `…` that
    `chrome.clip()` defaults to, which curses drops to a blank under a locale
    that cannot encode it — a silent truncation wearing a mark's clothes. And a
    cell is kept back for the mark before it is spent, because `chrome.clip()`
    returns `text[:width]` with no mark at all when it is left fewer cells than
    the ellipsis is wide.
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


def _cells(roles, theme):
    """`[{column: (text, token)}]`, one per role, every cell rendered.

    Rendered first and measured afterwards, so that a column's width and its
    content stay one fact: a width taken from a value the row does not draw is
    how a table clips a cell it had room for.
    """
    rows = []
    for label, _reason, (name, name_problem), (effort, effort_problem) in roles:
        rows.append({
            "Role": (label, theme_tokens.MUTED),
            "Model": _cell(name, name_problem, "not configured", theme),
            "Effort": _cell(effort, effort_problem, theme.glyph("bullet"), theme),
        })
    return rows


def _kept(cells, width):
    """Which columns are drawn, and how wide each one is.

    Two different sentences end a column: no role has anything to put in it —
    a column of nothing but absence marks is a statement about the relay, not a
    column — and the terminal is too narrow for it. `Role` and `Model` are in
    neither case: a row naming neither the role nor its model is not a row.
    """
    kept = [name for name in COLUMNS
            if name in ("Role", "Model") or _measured(cells, name)]
    widths = {name: max([len(name)] + [len(row[name][0]) for row in cells])
              for name in kept}
    if _table_width(kept, widths) > width and "Effort" in kept:
        kept.remove("Effort")
    return kept, widths


def _measured(cells, name):
    return any(row[name][1] != theme_tokens.ABSENT for row in cells)


def _table_width(kept, widths):
    return sum(widths[name] for name in kept) + GAP * max(0, len(kept) - 1)


def _grid_parts(values, kept, widths):
    """One table row: each cell padded to its column, one cell apart.

    The last cell is never padded — `Canvas.write()` clips with an `…` whenever
    it cut something, trailing spaces included, and a head row padded to the
    pane's width ends in a mark saying a whole heading was truncated.
    """
    parts = []
    for index, name in enumerate(kept):
        if index:
            parts.append((" " * GAP, theme_tokens.MUTED))
        text, token = values[name]
        if index < len(kept) - 1:
            text = text.ljust(widths[name])
        parts.append((text, token))
    return parts


def _body_rows(model, pane, state):
    """Every body row, in reading order, before anything is cut."""
    theme = pane.theme
    ellipsis = theme.glyph("ellipsis")
    width = pane.body_width
    roles, models_problem = _roles(model, ellipsis)

    rows = []
    if models_problem is not None:
        rows.append(_problem_row(theme, "models", models_problem, width,
                                 ellipsis))

    cells = _cells(roles, theme)
    kept, widths = _kept(cells, width)
    rows.append(_row(_clip_parts(
        _grid_parts({name: (name, theme_tokens.MUTED) for name in kept},
                    kept, widths),
        width, ellipsis), heading=True))
    for (_label, reason, _name, _effort), values in zip(roles, cells):
        rows.append(_row(_clip_parts(_grid_parts(values, kept, widths),
                                     width, ellipsis)))
        rows.append(_row(_clip_parts([(INDENT + reason, theme_tokens.MUTED)],
                                     width, ellipsis)))

    rows.append(_BLANK)
    rows.append(_row(_clip_parts([(TOGGLES_HEADING, theme_tokens.PANE_TITLE)],
                                 width, ellipsis), heading=True))
    rows.extend(_toggle_rows(model, pane, state, width, ellipsis))
    return rows


def _toggle_rows(model, pane, state, width, ellipsis):
    theme = pane.theme
    extras = model.get("extras")
    extras = extras if isinstance(extras, dict) else {}
    written, problem = _mapping(extras, "toggles")

    rows = []
    if problem is not None:
        rows.append(_problem_row(theme, "toggles", problem, width, ellipsis))

    label_width = max(len(label) for _key, label in TOGGLES)
    chosen = selected(state)
    for index, (key, label) in enumerate(TOGGLES):
        text, trouble = _toggle(written, key)
        state_text, token = _toggle_cell(text, trouble, theme)
        parts = [(INDENT + label.ljust(label_width) + " " * GAP,
                  theme_tokens.MUTED), (state_text, token)]
        if index == chosen:
            # The highlight is a row, not a word: reverse video stops where its
            # text does. Padded to the pane's body width and no further — the
            # pane's width already excludes the screen's reserved last column.
            drawn = sum(len(part[0]) for part in parts)
            parts = [(text, theme_tokens.SELECTED) for text, _ in parts]
            if drawn < width:
                parts.append((" " * (width - drawn), theme_tokens.SELECTED))
        rows.append(_row(_clip_parts(parts, width, ellipsis)))
    return rows


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------


def draw(canvas, model, state):
    ellipsis = canvas.theme.glyph("ellipsis")
    roles, _problem = _roles(model, ellipsis)
    configured = sum(1 for _label, _reason, (name, _p), _effort in roles if name)

    pane = canvas.full_pane("%s (%d/%d)" % (TITLE, configured, len(ROLES)))
    if pane is None:                       # too small for a pane at all
        return
    pane.header(META)
    if pane.body_height <= 0:
        return

    # What Enter said keeps the pane's last body row for as long as it is
    # showing: a message the reader asked for that scrolled off with the rest
    # of the body is a keystroke with no answer.
    note = _note(state)
    height = pane.body_height - (1 if note else 0)
    # A pane of one body row with a message in it has no rows left for the
    # body: the answer to the keystroke wins the row, and nothing is counted as
    # hidden because a marker would want that same cell. Answered here rather
    # than inside `_fit`, so that `_fit` has no branch its caller cannot reach.
    shown, hidden = (_fit(_body_rows(model, pane, state), height) if height
                     else ([], 0))
    for offset, row in enumerate(shown):
        if row["segments"]:
            pane.segments(offset, row["segments"])
    # The marker cannot land on the message's row: `_fit` only reports rows
    # hidden when it kept one back for the marker, so `len(shown)` is at least
    # one row above the last, which is the message's. No guard needed, and a
    # guard that cannot fire is a line no test could ever kill.
    pane.more(hidden, row=len(shown))
    if note:
        pane.line(pane.body_height - 1,
                  chrome.clip(note, pane.body_width, ellipsis),
                  theme_tokens.EMPHASIS)


def _note(state):
    """What Enter last said, or None. Untrusted of its own state object."""
    note = getattr(state, "detail", None)
    return note if isinstance(note, str) and note else None


# --------------------------------------------------------------------------
# keys (ACC-MODEL-003)
# --------------------------------------------------------------------------


def selected(state):
    """Which toggle the keyboard is on. Out of range clamps rather than raising."""
    try:
        index = int(state.selection.get("models", 0))
    except (AttributeError, TypeError, ValueError):  # pragma: no cover
        return 0
    return max(0, min(len(TOGGLES) - 1, index))


def handle(key, state, model):
    """Up/Dn move between the toggles; Enter says why neither of them moves.

    `return False` at the end and nowhere else: a `return True` that fell out of
    the `if` swallows `Tab`, `Esc` and `q`, and the view becomes a room with no
    door. Quitting is never a view's decision.
    """
    if key in (curses.KEY_UP, curses.KEY_DOWN):
        step = 1 if key == curses.KEY_DOWN else -1
        state.selection["models"] = max(
            0, min(len(TOGGLES) - 1, selected(state) + step))
        state.detail = None                # the message named the other row
        return True
    if key in (10, 13, curses.KEY_ENTER):
        state.detail = "%s %s" % (TOGGLES[selected(state)][1], READ_ONLY)
        return True
    return False

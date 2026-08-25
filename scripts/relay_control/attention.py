"""The attention band that sits between the status bar and the panes.

**ACC-TUI-005.** The band is not a view: it has no keybar, it takes no keys and
it is not in `app.VIEWS`. What it has instead is a height that `app.paint()`
asks for before it lays out the Overview, so that the band moves the panes down
without any pane knowing it happened.

    height(model, width) -> int
        Rows the band wants at this width. `app.paint()` clamps the answer to
        the room actually available, so an honest figure is enough.

    draw(canvas, model, state) -> None
        Paint into exactly `height()` rows. The canvas is the band's own
        rectangle, so row 0 here is the first row under the status bar.

Both answers come from one function, `_layout()`, which returns the rows as the
segments that will be drawn. That is the whole design: a band whose height was
computed by one rule and painted by another would push the panes down by the
wrong number of rows, and nothing on the screen would say which of the two was
wrong.

Why the band is never empty
---------------------------
The seam this file replaces said `height()` returns `0` when the model carries
no attention items. It does not, and that is deliberate: a `calm` item is
information — it tells a supervisor coming back to the screen that the quiet is
real and not a frozen repaint. `relay_model` already synthesises `ON TRACK`
when nothing else is raised; when even that is missing (a model built by hand,
a malformed one) the band says so itself rather than vanishing.

Why the band budgets its own rows
---------------------------------
`height(model, width)` is not told how tall the terminal is, and `app.paint()`
clamps a greedy answer without sharing it. A band that asked for twelve rows on
a twenty-four row terminal would silently delete the panes below it. So the
band caps itself at `MAX_ROWS` and spends them worst-first, announcing what it
could not fit the way every pane does: `+N more`.

`model["attention"]` is a list of `{"level", "label", "text", "action"}` already
sorted worst-first by the model, and `NEEDS YOUR CALL` items are `level ==
"bad"`. It reaches here as coach input passed through `relay_model`, so every
field is coerced before it is used: a level that is not a level, a label that is
not a string and an item that is not a dict at all are all screens, not
tracebacks.
"""

from . import chrome
from . import theme as theme_tokens

#: The most rows the band will ever ask for. See the module docstring: this is
#: a budget the band has to set for itself because `height()` cannot see the
#: terminal's height. It is a count of rows, never a width.
MAX_ROWS = 7

#: Text rows one item may spend, by level. The `bad` item is what the band
#: exists for, so it gets room to be read; a note gets a line.
TEXT_ROWS = {"bad": 3, "warn": 2}
DEFAULT_TEXT_ROWS = 1

#: Columns between a label and the text beside it, and the indent a wrapped
#: line takes when the label had to be given a row of its own.
GAP = 2
INDENT = 2

#: How much room the text needs before the label may share its row. Below this
#: the aligned layout leaves a column of labels and a sliver of prose, so the
#: label takes a row of its own and the text is indented under it instead.
MIN_TEXT = 24

#: What the band says when the model carries nothing at all. Not a placeholder
#: for a value — a statement about the model, which is the one thing the band
#: can say truthfully here.
QUIET = {"level": "calm", "label": "QUIET",
         "text": "the model carries no attention items.", "action": None}


# --------------------------------------------------------------------------
# the seam
# --------------------------------------------------------------------------


def height(model, width):
    """Rows the band needs at `width` — the count `draw()` will paint."""
    return len(_layout(model, width))


def draw(canvas, model, state):
    """Paint the band into its own rectangle, row 0 first."""
    rows = _layout(model, canvas.width,
                   ellipsis=canvas.theme.glyph("ellipsis"),
                   marker=canvas.theme.glyph("bullet"))
    for row, parts in enumerate(rows):
        # No bounds check: `Canvas.write()` ignores a row outside the rectangle,
        # so a band `app.paint()` clamped shorter than it asked for simply
        # stops appearing partway down.
        canvas.segments(row, parts)


# --------------------------------------------------------------------------
# layout — one answer, used by both height() and draw()
# --------------------------------------------------------------------------


def _layout(model, width, ellipsis="…", marker="·"):
    """The band as `[[(text, token), ...], ...]` — one list per row.

    The last column is left empty: the chrome reserves it so that a captured
    frame carries no full-width row and `assert_within_width()` can certify it
    rather than wave it through.
    """
    usable = width - 1
    if usable < 4:
        return []

    items = _items(model)
    aligned = max(len(item["label"]) for item in items) + GAP
    # The label shares its row only while that still leaves prose worth reading.
    gutter = aligned if usable - aligned >= MIN_TEXT else 0

    rows = []
    for index, item in enumerate(items):
        left = len(items) - index - 1
        room = MAX_ROWS - len(rows) - (1 if left else 0)
        drawn = _item_rows(item, usable, gutter, ellipsis, marker)
        if room <= 0 or len(drawn) > room:
            # An item is shown whole or not at all, and what was left out is
            # announced the way a pane announces it.
            rows.append([(chrome.clip("+%d more" % (left + 1), usable, ellipsis),
                          theme_tokens.MUTED)])
            break
        rows.extend(drawn)
    return rows


def _item_rows(item, usable, gutter, ellipsis, marker):
    """One attention item as the rows it occupies.

    `NEEDS YOUR CALL` is not distinguished by its wording: the label and the
    text of a `bad` item are drawn with the level's own token, so the two kinds
    of item differ in the attribute plane and not merely in what they spell.
    """
    level = item["level"]
    token = theme_tokens.ATTENTION.get(level, theme_tokens.ATTENTION["note"])
    text_token = token if level == "bad" else theme_tokens.BODY
    allowed = TEXT_ROWS.get(level, DEFAULT_TEXT_ROWS)
    label = item["label"]

    if gutter:
        lines = _fit(item["text"], usable - gutter, allowed, ellipsis)
        rows = [[(label, token), (" " * (gutter - len(label)), theme_tokens.BODY),
                 (lines[0], text_token)]]
        rows += [[(" " * gutter, theme_tokens.BODY), (line, text_token)]
                 for line in lines[1:]]
        indent = gutter
    else:
        lines = _fit(item["text"], usable - INDENT, allowed, ellipsis)
        rows = [[(chrome.clip(label, usable), token)]]
        rows += [[(" " * INDENT, theme_tokens.BODY), (line, text_token)]
                 for line in lines]
        indent = INDENT

    action = item["action"]
    if action:
        # What the human could *do* is not what happened: it is drawn in its
        # own token so a reader can find it without reading the prose again.
        action = chrome.clip("%s %s" % (marker, action), usable - indent, ellipsis)
        used = sum(len(text) for text, _ in rows[-1])
        if used + GAP + len(action) <= usable:
            rows[-1].append((" " * GAP, theme_tokens.BODY))
            rows[-1].append((action, theme_tokens.EMPHASIS))
        else:
            rows.append([(" " * indent, theme_tokens.BODY),
                         (action, theme_tokens.EMPHASIS)])
    return rows


def _fit(text, width, allowed, ellipsis):
    """`text` wrapped to `width`, in at most `allowed` lines.

    What does not fit is not dropped silently: the last line carries whatever
    is left, clipped, so it ends in an ellipsis the reader can see.
    """
    lines = chrome.wrap(text, width)
    if len(lines) > allowed:
        rest = " ".join(lines[allowed - 1:])
        lines = lines[:allowed - 1] + [chrome.clip(rest, width, ellipsis)]
    return lines or [""]


# --------------------------------------------------------------------------
# the model's side
# --------------------------------------------------------------------------


def _items(model):
    """The attention items, coerced. Never empty — see the module docstring."""
    raw = model.get("attention") if isinstance(model, dict) else None
    items = []
    for value in raw if isinstance(raw, list) else []:
        if not isinstance(value, dict):
            continue
        text = _one_line(value.get("text"))
        if not text:
            continue
        level = value.get("level")
        items.append({
            "level": level if isinstance(level, str)
                     and level in theme_tokens.ATTENTION else "note",
            "label": _one_line(value.get("label")) or "NOTE",
            "text": text,
            "action": _one_line(value.get("action")),
        })
    return items or [QUIET]


def _one_line(value):
    """A string with its whitespace collapsed; anything else is no string.

    A newline in a label or an action would put a row of the band somewhere the
    layout did not put it, so it is folded here rather than defended against in
    four places.
    """
    return " ".join(value.split()) if isinstance(value, str) else ""

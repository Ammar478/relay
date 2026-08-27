"""The Mission Control chrome: header, status bar, keybar, panes and rules.

Nothing here knows what a relay *is*. It is given `relay_model.build()`'s output
and a rectangle, and it draws. The split matters because six view legs run in
parallel on top of this file: a view that needed to reach past its `Pane` to
place something would be a view that can collide with another view's pane.

Five invariants this module holds for every view
-----------------------------------------------
1. **Everything is clipped, nothing raises.** `Pane.line()` truncates to the
   pane's width, ignores a row outside the pane, and swallows the `curses.error`
   a write at the screen's last cell raises. "Degrade, not crash" is enforced
   here so that no view has to remember it.
2. **The last column is reserved.** Only the rules would ever have run to it,
   and they stop one short too. ncurses clips in software, so a row that ends
   exactly at the margin is byte-identical to a row truncated to fit — a frame
   with an empty last column is one a test can certify with
   `assert_within_width()` rather than wave through with `allow_full_width`.
   The header, the status bar and the keybar each work in `width - 1` of their
   own accord, because they draw on a bare `Canvas` and only `Canvas.pane()`
   clamps.
3. **Width is counted in cells, never in characters.** `cell_width()` is the
   only measure in this module. `len()` is right for ASCII and wrong by a
   factor of two for the CJK the fixtures already carry, and the pane that
   overflows is the pane that erases the rule beside it.
4. **All arithmetic is from the live terminal size.** `Canvas` is handed a
   rectangle measured from `getmaxyx()` at every repaint. There is no constant
   here that is a width.
5. **Nothing drawn is a control character.** Every string a view hands over is
   relay prose — a leg goal, a commit subject, a coach's log line, a warning —
   and all of it is hand-written into `.relay/`. `Canvas.write()` is the one
   place text becomes cells, so it is the one place a control character stops
   being one. See `sanitise()`.

Layout
------
    row 0            header      (ACC-TUI-001)
    row 1            status bar  (ACC-TUI-002)
    rows 2..n        the attention band, when the model carries items
    rows n+1..h-2    the view's canvas
    row h-1          keybar      (ACC-TUI-004)

Below `NARROW_COLS` the Overview stops being two columns and stacks, in the
order `Active Leg, Legs, Progress Log, Active Runner`; when even a stack does
not fit, panes are dropped from the *end* of that order, so the pane naming what
is happening right now is the last thing to go.
"""

import curses
import functools
import unicodedata

from . import theme as theme_tokens

#: Below this width the Overview stacks instead of splitting into two columns.
NARROW_COLS = 100

#: The smallest a pane can be and still say anything: one title row, one body
#: row. A pane that cannot have this is not drawn at all.
MIN_PANE_HEIGHT = 2

#: The four Overview panes, in the order they are dropped from (last first).
PANE_ORDER = ("active_leg", "legs", "log", "runner")
PANE_TITLES = {
    "active_leg": "Active Leg",
    "legs": "Legs",
    "log": "Progress Log",
    "runner": "Active Runner",
}

#: The keybar's status legend, in the order ACC-TUI-006 names the glyphs.
LEGEND = (
    ("completed", "done"),
    ("running", "running"),
    ("pending", "pending"),
    ("failed", "failed"),
    ("cancelled", "cancelled"),
)


# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------


#: Every code point a terminal reads as an instruction rather than as text:
#: C0 (`0x00`-`0x1F`, which is NUL, BS, TAB, LF, CR and ESC), DEL, and the C1
#: range (`0x80`-`0x9F`) that a UTF-8 terminal decodes straight back into
#: single-byte controls — `0x9B` is CSI, so `\x9b31m` is `ESC[31m` spelled
#: another way. An escape sequence can only *begin* with ESC or a C1, and every
#: byte that would *continue* one is ordinary printable text once its
#: introducer is gone. So this range is the whole of the problem, and what is
#: not in it — an em-dash, CJK, box drawing — is prose and must survive
#: untouched (ACC-DATA-007).
CONTROL_ORDINALS = (tuple(range(0x00, 0x20)) + (0x7F,)
                    + tuple(range(0x80, 0xA0)))


@functools.lru_cache(maxsize=4)
def _control_table(placeholder):
    """The `str.translate` table for one placeholder, built once.

    Cached because `write()` is called a few hundred times per repaint and
    there are two placeholders in the whole program — the UTF-8 mark and its
    ASCII fallback. Keyed by the placeholder rather than shared, because a
    cache that answered with whichever table it built first would draw the
    UTF-8 mark under a locale that cannot encode it, and curses turns that
    into a blank — a strip by another route.
    """
    return dict.fromkeys(CONTROL_ORDINALS, placeholder)


def sanitise(text, placeholder):
    """`text` with every control character replaced by one `placeholder` cell.

    **One cell, not two, and never none.** The three candidate answers were
    strip, escape (`^[`, `\\x1b`) and substitute:

    * Stripping is the lie this project keeps refusing. A goal that quietly
      loses four characters reads as a goal its author wrote that way, and a
      supervisor has no way to tell prose from prose-with-something-removed.
    * Escaping is what ncurses does for you if you let a control character
      through — `addstr` renders ESC as `^[` — and it costs a cell. Every
      width computation in this package (`clip`, `wrap`, `Pane.right`,
      `Pane.header`, `overview._fit`, `attention._item_rows`) measures a cell
      count and spends it as cells. Two cells for one character makes
      each of those quietly wrong, and the observable end of that is the
      reserved last column: a goal with sixty ESCs in it ran a row to the
      margin and wrapped into the pane below, which is invariant 2 gone and
      `Frame.assert_within_width()` unable to certify *any* frame in the
      repository.

    So the substitution is one character for one character, and one cell for
    one cell — the mark is narrow in every glyph table (`cell_width()` is what
    says so). Nothing upstream of here has to know it happened,
    and the placeholder is visible: a reader sees that something was in the
    text without being told what a `0x9B` is.

    `placeholder` comes from the theme (`glyph("control")`) so that it
    degrades with every other glyph — under a locale that cannot encode the
    UTF-8 mark, curses would drop it to a blank, and a blank is stripping by
    another route.
    """
    return text.translate(_control_table(placeholder))


def cell_width(text):
    """How many terminal cells `text` occupies.

    `len()` is a count of Python characters and it is **not** a count of cells.
    A terminal draws a CJK ideograph in two columns and a combining mark in
    none, so a rectangle measured with `len()` is out by up to a factor of two
    on text the fixtures already carry. Measured at 160x48: a leg goal of CJK
    ideographs was given the Active Leg pane's 79 cells, `wrap()` handed the
    pane rows of 79 *characters*, and the row was drawn at **158** — which
    erased the vertical rule beside it, painted over the Legs pane in the next
    column, and wrapped onto the row below. Invariant 2 of this module — the reserved last column, which is
    the whole reason `Frame.assert_within_width()` can certify a frame at all —
    goes with it.

    The rule is Unicode's own rather than a table somebody typed:
    `east_asian_width` names the wide (`W`) and fullwidth (`F`) forms, and the
    marks and format characters a terminal joins onto the cell before them
    (`Mn`, `Me`, `Cf`) take none of their own. `tests/frame.py` measures a
    captured screen by the same rule, so what this module spends and what the
    emulator reports are one statement and not two.

    A control character counts as one cell, which is deliberate and is the
    other half of ACC-ROBUST-006: `Canvas.write()` has already replaced it with
    a one-cell mark by the time anything is measured, and the substitution is
    1:1 in cells precisely so that this function does not have to know.
    """
    return sum(_char_cells(ch) for ch in str(text))


def _char_cells(ch):
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _take_cells(text, cells):
    """`(prefix, width)` — the longest prefix of `text` that fits in `cells`.

    A double-width character that would straddle the far edge is not taken:
    half a character is not a character, and a terminal asked to draw one
    either drops it or spills into the next cell. So the prefix comes back one
    cell short rather than one cell over — short is a blank column, over is
    somebody else's pane.
    """
    if cells <= 0:
        return "", 0
    spent = 0
    for index, ch in enumerate(text):
        width = _char_cells(ch)
        if spent + width > cells:
            return text[:index], spent
        spent += width
    return text, spent


def _drop_cells(text, cells):
    """`(rest, offset)` — `text` with its first `cells` cells taken off.

    For a write that starts left of its canvas: what is off the edge is gone
    and what is left begins at column 0. A wide character straddling the edge
    is dropped whole and `offset` is the one cell it would have half-filled, so
    what follows still lands on the column it belongs to instead of sliding a
    cell to the left.
    """
    spent = 0
    for index, ch in enumerate(text):
        if spent >= cells:
            return text[index:], spent - cells
        spent += _char_cells(ch)
    return "", max(0, spent - cells)


def clip(text, width, ellipsis="…"):
    """`text` cut to `width` cells, and **always** marked where it was cut.

    Two things here that `text[:width]` gets wrong, both of them named
    absences:

    * **Cells, not characters** — see `cell_width()`.
    * **The mark is never the thing dropped.** This used to answer
      `text[:width]`, with no mark at all, whenever it was left fewer cells
      than the ellipsis is wide. So a row cut with exactly one cell to spare
      was cut *silently*, which is the one thing this package never does, in
      the hardest place in it to see. Under `LC_ALL=C` the ellipsis is `...`
      and the silence covered three cells rather than one. Now the mark is
      taken first and the text spends what is left, so a cut says so at every
      width down to one cell — where the answer is the mark and nothing else,
      which is the honest screen for a column that has room for one cell and
      something longer to put in it.
    """
    text = "" if text is None else str(text)
    if width <= 0:
        return ""
    if cell_width(text) <= width:
        return text
    mark, mark_width = _take_cells(ellipsis, width)
    kept, _ = _take_cells(text, width - mark_width)
    return kept + mark


def elide_left(text, width, ellipsis="…"):
    """`text` cut to `width` cells from the *front*, the cut always marked.

    For paths, where the tail is the part that identifies the thing:
    `…/tests/fixtures/agent-service` says more than `/Users/ammar/Documen…`.
    """
    text = "" if text is None else str(text)
    if width <= 0:
        return ""
    total = cell_width(text)
    if total <= width:
        return text
    mark, mark_width = _take_cells(ellipsis, width)
    rest, _ = _drop_cells(text, total - (width - mark_width))
    return mark + rest


def wrap(text, width):
    """`text` broken onto lines of at most `width` cells, on word boundaries."""
    if not text or width <= 0:
        return []
    lines = []
    current = ""
    for word in str(text).split():
        if not current:
            current = word
        elif cell_width(current) + 1 + cell_width(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
        while cell_width(current) > width:
            head, _ = _take_cells(current, width)
            # A single character wider than the whole line: it gets the line
            # anyway rather than the loop spinning for ever, and `Canvas.write()`
            # cuts it to the mark. Dropping it would be a silent deletion.
            head = head or current[:1]
            lines.append(head)
            current = current[len(head):]
    if current:
        lines.append(current)
    return lines


def fit_parts(parts, width, ellipsis):
    """`[(text, token), ...]` cut to `width` cells, marked **once**, at the end.

    A row assembled from several styled segments cannot be cut by clipping the
    segments: each one knows only its own width, so the row is either marked
    once per segment or — the case that actually shipped — not at all, because
    every segment fitted and the row did not.

    Two things it is careful about, and both of them were paid for:

    * **The theme's ellipsis, not `clip()`'s default.** `Canvas.write()` now
      passes the theme's, but a caller assembling a row still has to hand the
      right mark in: under a locale that cannot encode `…` curses drops the cell
      to a blank, and a blank is a silent truncation wearing a mark's clothes.
    * **The cell for the mark is reserved before it is spent**, so the last
      segment cannot fill the row and leave the mark nowhere to go.

    This lived in `runners.py`, `contract.py` and `models.py` — three
    byte-identical copies, written independently by three legs that were each
    forbidden to edit this file, and each of whose batons asked for the move.
    """
    if sum(cell_width(text) for text, _ in parts) <= width:
        return list(parts)
    mark, mark_width = _take_cells(ellipsis, max(0, width))
    room = max(0, width - mark_width)
    fitted, spent = [], 0
    for text, token in parts:
        if spent >= room:
            break
        text, taken = _take_cells(text, room - spent)
        if text:
            fitted.append((text, token))
            spent += taken
    if mark:
        fitted.append((mark, theme_tokens.MUTED))
    return fitted


def paginate(items, height):
    """`(shown, hidden)` — as many items as fit, one row kept for `+N more`.

    The reserved row is why this is a function and not a slice: a pane that
    listed `height` items and then drew `+N more` over the last one would be
    hiding an item to announce that it was hiding items.
    """
    items = list(items)
    if height <= 0:
        return [], len(items)
    if len(items) <= height:
        return items, 0
    return items[:height - 1], len(items) - (height - 1)


def humanise_age(seconds):
    """A relative age a reader can scan: `47m ago`, `3h ago`, `now`.

    `None` is `None` — the model says "not measured" by omitting a value, and a
    view that turned that into `0s ago` would be inventing one.
    """
    if seconds is None:
        return None
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return None
    if seconds < 60:
        return "now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return "%dm ago" % minutes
    hours = minutes // 60
    if hours < 48:
        return "%dh ago" % hours
    return "%dd ago" % (hours // 24)


def humanise_duration(seconds):
    """An elapsed time: `47m 12s`, `6h 38m`. `None` stays `None`."""
    if seconds is None:
        return None
    try:
        seconds = int(float(seconds))
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    if seconds < 60:
        return "%ds" % seconds
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return "%dm %02ds" % (minutes, secs)
    hours, minutes = divmod(minutes, 60)
    return "%dh %02dm" % (hours, minutes)


# --------------------------------------------------------------------------
# drawing surface
# --------------------------------------------------------------------------


class Canvas:
    """A window, a theme, and the rectangle a view may draw in.

    A view is handed one of these and nothing else. Coordinates given to it are
    relative to the rectangle, so a view cannot address a cell outside its own
    region even by accident.
    """

    def __init__(self, win, theme, top, left, height, width):
        self.win = win
        self.theme = theme
        self.top = top
        self.left = left
        self.height = max(0, height)
        self.width = max(0, width)

    # -- primitives ------------------------------------------------------

    def resolve(self, token):
        """A theme token name, or an already-resolved curses attribute.

        Both spellings are accepted so that `theme.status()`, which has to hand
        back a glyph and its attribute together, composes with `segments()`
        without every caller round-tripping the attribute back into a name.
        """
        return token if isinstance(token, int) else self.theme.attr(token)

    def write(self, row, col, text, token=theme_tokens.BODY):
        """Draw `text` at `(row, col)`, clipped to the canvas. Returns cells drawn.

        Never raises: a row or column outside the canvas draws nothing, and the
        `curses.error` that a write ending at the screen's last cell raises is
        swallowed after the cells have landed.

        This is also where a control character stops being one, because this is
        the only place in the package where text reaches the window — every
        `Pane` method, every band, every rule label comes through here. A guard
        that lives at one choke point can be *proved* to cover every path;
        `tests/test_chrome.py` sweeps the package for a second `addstr` for
        exactly that reason. See `sanitise()` for why it substitutes rather
        than strips or escapes.
        """
        if not text or not (0 <= row < self.height) or col >= self.width:
            return 0
        text = sanitise(str(text), self.theme.glyph("control"))
        if col < 0:
            # What is off the left edge is gone; `offset` is the cell a wide
            # character straddling the edge would have half-filled, and is left
            # blank so what follows lands on its own column.
            text, offset = _drop_cells(text, -col)
            col = offset
        # The theme's ellipsis, not `clip()`'s literal default: under a locale
        # that cannot encode `…` curses drops the cell to a blank, and a
        # truncation with a blank where its mark should be is a silent one.
        text = clip(text, self.width - col, self.theme.glyph("ellipsis"))
        if not text:
            return 0
        try:
            self.win.addstr(self.top + row, self.left + col, text,
                            self.resolve(token))
        except curses.error:
            return 0
        return cell_width(text)

    def segments(self, row, parts, col=0):
        """Draw `[(text, token), ...]` end to end. Returns the column reached."""
        for text, token in parts:
            if col >= self.width:
                break
            col += self.write(row, col, text, token)
        return col

    def hrule(self, row, col=0, length=None):
        """A horizontal rule, stopping one column short of the right margin."""
        length = self.width - col if length is None else length
        length = min(length, self.width - col - 1)
        if length <= 0 or not (0 <= row < self.height):
            return
        try:
            self.win.hline(self.top + row, self.left + col, self.theme.hline,
                           length, self.resolve(theme_tokens.RULE))
        except curses.error:
            pass

    def vrule(self, row, col, length):
        length = min(length, self.height - row)
        if length <= 0 or not (0 <= col < self.width):
            return
        try:
            self.win.vline(self.top + row, self.left + col, self.theme.vline,
                           length, self.resolve(theme_tokens.RULE))
        except curses.error:
            pass

    # -- panes -----------------------------------------------------------

    def pane(self, row, col, height, width, title):
        """A titled sub-rectangle, or None when it is too small to say anything.

        The width is clamped so that a pane never occupies the canvas's last
        column. That is invariant 2 of this module held in one place: with the
        margin left empty, a captured frame carries no full-width row, and
        `assert_within_width()` can certify it instead of being waved through
        with `allow_full_width=True`.
        """
        height = min(height, self.height - row)
        width = min(width, self.width - col - 1)
        if height < MIN_PANE_HEIGHT or width < 4:
            return None
        return Pane(Canvas(self.win, self.theme, self.top + row, self.left + col,
                           height, width), title)

    def full_pane(self, title):
        """The whole canvas as one pane — what a full-screen view draws into."""
        return self.pane(0, 0, self.height, self.width, title)


class Pane:
    """One titled rectangle. The only surface a view draws through.

    A pane's rectangle and its title come from the layout, never from the view:
    `overview_frame()` decides where `Active Leg` is and how big it is, and a
    view leg that wanted a different size would be arguing with the layout
    rather than with its neighbour. What the view owns is the `meta` — the
    right-hand figure in the header — and the body.
    """

    def __init__(self, canvas, title):
        self.canvas = canvas
        self.title = title

    @property
    def body_height(self):
        """Rows available for content, the title row already taken off."""
        return max(0, self.canvas.height - 1)

    @property
    def body_width(self):
        return self.canvas.width

    @property
    def theme(self):
        """The theme, so a view can ask for a glyph without reaching further."""
        return self.canvas.theme

    # -- header ----------------------------------------------------------

    def header(self, meta=None):
        """Draw the pane's title, and `meta` right-aligned beside it.

        `meta` is the pane's own count or range — `27/36`, `1-8 of 8`, `none`.
        Pass `None` for a pane that has no such figure; never pass `"0/0"` or a
        dash to fill the space (ACC-OVER-004).
        """
        width = self.canvas.width
        # No `clip()` on the title: `Canvas.write()` clips to this same width,
        # with the theme's own mark. A second clip here could only spell the
        # mark differently.
        self.canvas.write(0, 0, self.title, theme_tokens.PANE_TITLE)
        if meta:
            meta = clip(str(meta), max(0, width - cell_width(self.title) - 2),
                        self.theme.glyph("ellipsis"))
            if meta:
                self.canvas.write(0, width - cell_width(meta), meta,
                                  theme_tokens.PANE_META)
        return self

    # -- body ------------------------------------------------------------

    def line(self, row, text, token=theme_tokens.BODY, col=0):
        """One body row, `row` counted from 0 at the first row under the title."""
        if not (0 <= row < self.body_height):
            return False
        # `Canvas.write()` clips to exactly `body_width - col` already, and with
        # the theme's ellipsis rather than a literal one; clipping again here
        # was the same cut spelled worse.
        return self.canvas.write(row + 1, col, text, token) > 0

    def segments(self, row, parts, col=0):
        """A body row assembled from `[(text, token), ...]`."""
        if not (0 <= row < self.body_height):
            return col
        return self.canvas.segments(row + 1, parts, col)

    def right(self, row, text, token=theme_tokens.MUTED):
        """Right-aligned text on a body row — an elapsed time, a count."""
        if not (0 <= row < self.body_height) or not text:
            return False
        text = clip(str(text), self.body_width, self.theme.glyph("ellipsis"))
        return self.canvas.write(row + 1, self.body_width - cell_width(text),
                                 text, token) > 0

    def empty(self, message):
        """How a pane says there is nothing to show.

        Every pane says it the same way: one dim line, in the body, in words.
        Never an empty box, never a dash, never `1-0 of 0` — a reader has to be
        able to tell "no runners have landed" from "this pane is broken".
        """
        self.line(0, message, theme_tokens.ABSENT)
        return self

    def more(self, hidden, row=None):
        """The overflow marker: `+N more`, on the pane's last body row."""
        if hidden <= 0:
            return False
        row = self.body_height - 1 if row is None else row
        return self.line(row, "+%d more" % hidden, theme_tokens.MUTED)


# --------------------------------------------------------------------------
# ACC-TUI-001 — the header
# --------------------------------------------------------------------------


METRIC_LABELS = (("elapsed", "TIME"), ("input", "Input"),
                 ("cached", "Cached"), ("output", "Output"))


def metrics_of(model):
    """`[(label, value)]` for every metric the model actually measured.

    ACC-DATA-008 makes an unmeasured metric a *missing key*, not a zero, so the
    header can tell "nothing spent" from "not measured" — and ACC-TUI-001 says
    it must: a metric with no value is omitted, not shown as `0`.
    """
    metrics = model.get("metrics") or {}
    tokens = metrics.get("tokens") or {}
    out = []
    for key, label in METRIC_LABELS:
        value = metrics.get("elapsed") if key == "elapsed" else tokens.get(key)
        if value is not None:
            out.append((label, str(value)))
    return out


def _metric_width(metrics):
    """The cells `draw_header()` will spend on `metrics`, separators included.

    A metric's *value* is whatever `dashboard.json` says it is — coach prose,
    by ACC-DATA-001 — so it is measured in cells and not in characters.
    """
    return sum(cell_width(label) + 1 + cell_width(value)
               for label, value in metrics) + 3 * max(0, len(metrics) - 1)


def relay_title(model):
    """What the header calls this relay: title, name, directory, then the app.

    A relay with no `title` in `dashboard.json` and no `relay` in `legs.json`
    still has a directory, and a supervisor with three of these open needs them
    told apart. `Relay Control` is the *program's* name, so it is the last
    resort and not the second: a header that reads `Relay Control` for two
    different relays is a header that has stopped identifying anything.
    """
    relay = model.get("relay") or {}
    return (relay.get("title") or relay.get("name")
            or _directory_name(relay.get("relayDir")) or "Relay Control")


def _directory_name(path):
    """The last segment of `relayDir` that names *this* relay.

    A relay directory is usually `<project>/.relay`, whose own basename names
    every relay there has ever been, so dotted segments are stepped over and
    the one above them is the answer. String work only — this asks the
    filesystem nothing (ACC-TUI-007).
    """
    parts = [part for part in str(path or "").split("/") if part]
    while parts and parts[-1].startswith("."):
        parts.pop()
    return parts[-1] if parts else None


def draw_header(canvas, model):
    """Row 1: the relay's name and working path, and what it has cost so far."""
    relay = model.get("relay") or {}
    title = relay_title(model)
    path = relay.get("path") or ""
    usable = canvas.width - 1

    ell = canvas.theme.glyph("ellipsis")
    title = clip(title, usable, ell)
    metrics = metrics_of(model)
    # Metrics are dropped from the left — TIME first — because a token figure
    # is the one a reader is most often watching change.
    while metrics and cell_width(title) + 4 + _metric_width(metrics) > usable:
        metrics = metrics[1:]
    right = _metric_width(metrics)
    gap = usable - cell_width(title) - (right + 2 if right else 0)
    path = elide_left(path, max(0, gap - 2), ell)

    col = canvas.segments(0, [(title, theme_tokens.TITLE)])
    if path:
        canvas.write(0, col + 2, path, theme_tokens.PATH)
    if metrics:
        col = usable - right
        for index, (label, value) in enumerate(metrics):
            if index:
                col = canvas.segments(
                    0, [(" %s " % canvas.theme.glyph("sep"), theme_tokens.MUTED)], col)
            col = canvas.segments(0, [(label, theme_tokens.METRIC_LABEL),
                                      (" ", theme_tokens.MUTED),
                                      (value, theme_tokens.METRIC_VALUE)], col)


# --------------------------------------------------------------------------
# ACC-TUI-002 — the status bar
# --------------------------------------------------------------------------


def progress_of(model):
    """`(done, total)` legs — the figures the bar and the count both come from.

    One source, so the bar cannot disagree with the number printed beside it.
    A cancelled leg counts towards `total` and not towards `done`: it will never
    complete, and a bar that reached 100% while a leg was abandoned would be
    saying something untrue.
    """
    counts = model.get("legCounts") or {}
    return int(counts.get("completed") or 0), int(counts.get("total") or 0)


def draw_status_bar(canvas, model):
    """Row 2: phase, a progress bar across the width, and `done/total`."""
    relay = model.get("relay") or {}
    phase = relay.get("phase") or "pending"
    attr = theme_tokens.PHASE.get(phase, theme_tokens.PHASE["pending"])
    done, total = progress_of(model)
    count = "%d/%d" % (done, total)
    usable = canvas.width - 1

    # Fitted to `usable`, like the bar and the count beside it. It was not:
    # these segments were spaced against `canvas.width` while everything else
    # on the row worked in `width - 1`, so below about ten columns the phase
    # word ran into the reserved last column — on *every* view, the Overview
    # included, with no keystroke sent. The strict `assert_within_width()`
    # could then certify no frame at all at 3x8 or 2x6, and the row it named
    # belonged to the chrome rather than to the view being tested.
    col = canvas.segments(0, fit_parts(
        [(canvas.theme.glyph("dot"), attr),
         (" ", theme_tokens.BODY),
         (phase.upper(), attr),
         (" ", theme_tokens.BODY)], usable, canvas.theme.glyph("ellipsis")))
    count_col = usable - cell_width(count)
    bar_width = count_col - 1 - col
    if bar_width > 0:
        filled = int(round(bar_width * done / total)) if total else 0
        filled = max(0, min(bar_width, filled))
        canvas.segments(0, [
            (canvas.theme.glyph("bar_fill") * filled, theme_tokens.BAR_FILL),
            (canvas.theme.glyph("bar_empty") * (bar_width - filled),
             theme_tokens.BAR_EMPTY),
        ], col)
    if count_col >= col:
        canvas.write(0, count_col, count, theme_tokens.EMPHASIS)


# --------------------------------------------------------------------------
# ACC-TUI-004 — the keybar
# --------------------------------------------------------------------------


def _fit_bindings(bindings, usable):
    """As many bindings as fit, always keeping the last one — `q Quit`."""
    kept = list(bindings)
    while kept and cell_width("  ".join("%s %s" % b for b in kept)) > usable:
        if len(kept) == 1:
            return []
        del kept[-2]
    return kept


def draw_keybar(canvas, bindings):
    """The last row: this view's bindings, and the status legend beside them.

    The legend lives here rather than in a pane because it belongs to every
    view and to no pane, and because the alternative — a pane that only exists
    to hold a key — is a pane a narrow terminal would have to drop.
    """
    usable = canvas.width - 1
    col = 0
    for index, (key, label) in enumerate(_fit_bindings(bindings, usable)):
        prefix = "  " if index else ""
        col = canvas.segments(0, [(prefix + key, theme_tokens.KEY),
                                  (" " + label, theme_tokens.KEY_LABEL)], col)

    legend = [(canvas.theme.glyph(state), word) for state, word in LEGEND]
    width = sum(cell_width(g) + 1 + cell_width(w)
                for g, w in legend) + 2 * (len(legend) - 1)
    start = usable - width
    if start < col + 2:
        return
    for index, ((state, word), (glyph, _)) in enumerate(zip(LEGEND, legend)):
        if index:
            start = canvas.segments(0, [("  ", theme_tokens.MUTED)], start)
        start = canvas.segments(0, [(glyph, theme_tokens.STATUS[state]),
                                    (" " + word, theme_tokens.KEY_LABEL)], start)


# --------------------------------------------------------------------------
# ACC-TUI-003 — the Overview frame
# --------------------------------------------------------------------------


def overview_frame(canvas):
    """The four Mission Control panes, and the rules between them.

    Returns `{name: Pane or None}` for every name in `PANE_ORDER`. `None` means
    the terminal was too small for that pane and it was dropped — a view reads
    the dict and skips what it was not given, which is the whole of "degrade,
    not crash" as a view sees it.
    """
    if canvas.height < MIN_PANE_HEIGHT or canvas.width < 8:
        return {name: None for name in PANE_ORDER}
    if canvas.width >= NARROW_COLS and canvas.height >= 10:
        return _split_frame(canvas)
    return _stacked_frame(canvas)


def _split_frame(canvas):
    """Two columns and a full-width band: Mission Control's own arrangement."""
    height, width = canvas.height, canvas.width
    runner_height = max(4, height // 4)
    if height - runner_height - 1 < 6:
        runner_height = 0
    top_height = height - (runner_height + 1 if runner_height else 0)

    split = width // 2
    right_col = split + 2
    canvas.vrule(0, split, top_height)

    legs_height = (top_height - 1) // 2
    log_height = top_height - 1 - legs_height

    panes = {
        "active_leg": canvas.pane(0, 0, top_height, split - 1,
                                  PANE_TITLES["active_leg"]),
        "legs": canvas.pane(0, right_col, legs_height, width - right_col,
                            PANE_TITLES["legs"]),
        "log": canvas.pane(legs_height + 1, right_col, log_height,
                           width - right_col, PANE_TITLES["log"]),
        "runner": None,
    }
    canvas.hrule(legs_height, split + 1, width - split - 1)
    if runner_height:
        canvas.hrule(top_height, 0, width)
        panes["runner"] = canvas.pane(top_height + 1, 0, runner_height, width,
                                      PANE_TITLES["runner"])
    return panes


def _stacked_frame(canvas):
    """One column, panes dropped from the end of `PANE_ORDER` as room runs out."""
    height, width = canvas.height, canvas.width
    panes = {name: None for name in PANE_ORDER}
    count = 0
    for candidate in (4, 3, 2, 1):
        if height - (candidate - 1) >= MIN_PANE_HEIGHT * candidate:
            count = candidate
            break
    if not count:
        return panes

    base, extra = divmod(height - (count - 1), count)
    row = 0
    for index, name in enumerate(PANE_ORDER[:count]):
        pane_height = base + (1 if index < extra else 0)
        panes[name] = canvas.pane(row, 0, pane_height, width, PANE_TITLES[name])
        row += pane_height
        if index < count - 1:
            canvas.hrule(row, 0, width)
            row += 1
    return panes

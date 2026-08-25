"""The Mission Control chrome: header, status bar, keybar, panes and rules.

Nothing here knows what a relay *is*. It is given `relay_model.build()`'s output
and a rectangle, and it draws. The split matters because six view legs run in
parallel on top of this file: a view that needed to reach past its `Pane` to
place something would be a view that can collide with another view's pane.

Three invariants this module holds for every view
-------------------------------------------------
1. **Everything is clipped, nothing raises.** `Pane.line()` truncates to the
   pane's width, ignores a row outside the pane, and swallows the `curses.error`
   a write at the screen's last cell raises. "Degrade, not crash" is enforced
   here so that no view has to remember it.
2. **The last column is reserved.** Only the rules would ever have run to it,
   and they stop one short too. ncurses clips in software, so a row that ends
   exactly at the margin is byte-identical to a row truncated to fit — a frame
   with an empty last column is one a test can certify with
   `assert_within_width()` rather than wave through with `allow_full_width`.
3. **All arithmetic is from the live terminal size.** `Canvas` is handed a
   rectangle measured from `getmaxyx()` at every repaint. There is no constant
   here that is a width.

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


def clip(text, width, ellipsis="…"):
    """`text` cut to `width` cells, with an ellipsis when something was lost."""
    text = "" if text is None else str(text)
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= len(ellipsis):
        return text[:width]
    return text[:width - len(ellipsis)] + ellipsis


def elide_left(text, width, ellipsis="…"):
    """`text` cut to `width` cells from the *front*.

    For paths, where the tail is the part that identifies the thing:
    `…/tests/fixtures/agent-service` says more than `/Users/ammar/Documen…`.
    """
    text = "" if text is None else str(text)
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= len(ellipsis):
        return text[-width:]
    return ellipsis + text[-(width - len(ellipsis)):]


def wrap(text, width):
    """`text` broken onto lines of at most `width` cells, on word boundaries."""
    if not text or width <= 0:
        return []
    lines = []
    current = ""
    for word in str(text).split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
        while len(current) > width:
            lines.append(current[:width])
            current = current[width:]
    if current:
        lines.append(current)
    return lines


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
        """
        if not text or not (0 <= row < self.height) or col >= self.width:
            return 0
        if col < 0:
            text = text[-col:]
            col = 0
        text = clip(text, self.width - col)
        if not text:
            return 0
        try:
            self.win.addstr(self.top + row, self.left + col, text,
                            self.resolve(token))
        except curses.error:
            return 0
        return len(text)

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
        self.canvas.write(0, 0, clip(self.title, width), theme_tokens.PANE_TITLE)
        if meta:
            meta = clip(str(meta), max(0, width - len(self.title) - 2))
            if meta:
                self.canvas.write(0, width - len(meta), meta,
                                  theme_tokens.PANE_META)
        return self

    # -- body ------------------------------------------------------------

    def line(self, row, text, token=theme_tokens.BODY, col=0):
        """One body row, `row` counted from 0 at the first row under the title."""
        if not (0 <= row < self.body_height):
            return False
        return self.canvas.write(row + 1, col, clip(text, self.body_width - col),
                                 token) > 0

    def segments(self, row, parts, col=0):
        """A body row assembled from `[(text, token), ...]`."""
        if not (0 <= row < self.body_height):
            return col
        return self.canvas.segments(row + 1, parts, col)

    def right(self, row, text, token=theme_tokens.MUTED):
        """Right-aligned text on a body row — an elapsed time, a count."""
        if not (0 <= row < self.body_height) or not text:
            return False
        text = clip(str(text), self.body_width)
        return self.canvas.write(row + 1, self.body_width - len(text), text,
                                 token) > 0

    def empty(self, message):
        """How a pane says there is nothing to show.

        Every pane says it the same way: one dim line, in the body, in words.
        Never an empty box, never a dash, never `1-0 of 0` — a reader has to be
        able to tell "no runners have landed" from "this pane is broken".
        """
        self.line(0, clip(message, self.body_width), theme_tokens.ABSENT)
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
    return sum(len(label) + 1 + len(value) for label, value in metrics) + \
        3 * max(0, len(metrics) - 1)


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

    title = clip(title, usable)
    metrics = metrics_of(model)
    # Metrics are dropped from the left — TIME first — because a token figure
    # is the one a reader is most often watching change.
    while metrics and len(title) + 4 + _metric_width(metrics) > usable:
        metrics = metrics[1:]
    right = _metric_width(metrics)
    gap = usable - len(title) - (right + 2 if right else 0)
    path = elide_left(path, max(0, gap - 2))

    col = canvas.segments(0, [(title, theme_tokens.TITLE)])
    if path:
        canvas.write(0, col + 2, path, theme_tokens.PATH)
    if metrics:
        col = usable - right
        for index, (label, value) in enumerate(metrics):
            if index:
                col = canvas.segments(0, [(" · ", theme_tokens.MUTED)], col)
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

    col = canvas.segments(0, [(canvas.theme.glyph("dot"), attr),
                              (" ", theme_tokens.BODY),
                              (phase.upper(), attr),
                              (" ", theme_tokens.BODY)])
    count_col = usable - len(count)
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
    while kept and len("  ".join("%s %s" % b for b in kept)) > usable:
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
    width = sum(len(g) + 1 + len(w) for g, w in legend) + 2 * (len(legend) - 1)
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

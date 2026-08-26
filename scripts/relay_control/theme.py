"""Colour pairs, glyphs, and the token names every view draws with.

A view never names a colour, a curses attribute or a colour pair. It names a
*token* — `theme.BODY`, `theme.PANE_TITLE`, `theme.STATUS["running"]` — and asks
the `Theme` for the attribute that token means here. That is the whole point of
this module: six view legs run in parallel on top of it, and six independent
guesses at "what colour is a failed check" is exactly the drift the relay model
exists to prevent one layer down.

Two degradations live here and nowhere else:

* **No colour.** A terminal that cannot do colour still has bold, dim and
  reverse. Every token has a monochrome spelling, so a view's code path is
  identical either way and no view has to ask `has_colors()`.
* **No UTF-8.** curses encodes what `addstr` is given with the process locale's
  codeset. In the POSIX locale that is US-ASCII and every non-ASCII glyph is
  silently dropped to a blank — a wrong screen with no error anywhere. When the
  locale cannot carry them the glyph table falls back to ASCII. Box rules do
  *not*: they are drawn from the ACS alternate character set, which reaches the
  terminal as real box drawing on any terminal ncurses knows.

Why pair 0 is never used for a status glyph
-------------------------------------------
Once `start_color()` has been called, ncurses renders colour pair 0 as an
explicit `37;40` — white on black — unless `use_default_colors()` succeeded. A
white-on-black glyph is then indistinguishable from the plain text around it in
a captured frame, and `○` was the one at risk. `setup()` calls
`use_default_colors()` and every pair is allocated against the default
background, so plain text carries no SGR at all and each status glyph carries
its own.
"""

import curses
import locale

# --------------------------------------------------------------------------
# tokens — the names views use
# --------------------------------------------------------------------------

TITLE = "title"                  # the relay's own name, row 1 of the header
PATH = "path"                    # the working path beside it
METRIC_LABEL = "metric.label"    # TIME / Input / Cached / Output
METRIC_VALUE = "metric.value"    # the figure itself
PANE_TITLE = "pane.title"        # a pane's heading
PANE_META = "pane.meta"          # a pane's right-hand count or range
RULE = "rule"                    # the lines between panes
BODY = "body"                    # ordinary pane content
MUTED = "muted"                  # present but unemphasised: ages, hints
ABSENT = "absent"                # "nothing here" and unmeasured placeholders
EMPHASIS = "emphasis"            # a value worth the reader's eye
SELECTED = "selected"            # the row the keyboard is on
KEY = "key"                      # a keybar key glyph
KEY_LABEL = "key.label"          # what that key does
BAR_FILL = "bar.fill"            # progress bar, done
BAR_EMPTY = "bar.empty"          # progress bar, not done yet
KIND = "kind"                    # a leg's kind marker (judge / fix)

#: Leg and check states → the token their glyph and text are drawn with.
STATUS = {
    "completed": "status.completed",
    "running": "status.running",
    "pending": "status.pending",
    "failed": "status.failed",
    "cancelled": "status.cancelled",
    "passed": "status.completed",
    "blocked": "status.failed",
}

#: Relay phases → the token the status bar's dot and word are drawn with.
PHASE = {
    "running": "phase.running",
    "judging": "phase.judging",
    "blocked": "phase.blocked",
    "complete": "phase.complete",
    "pending": "phase.pending",
}

#: Attention levels (`model["attention"][n]["level"]`) → token.
ATTENTION = {
    "bad": "attention.bad",
    "warn": "attention.warn",
    "note": "attention.note",
    "calm": "attention.calm",
}

# --------------------------------------------------------------------------
# what each token means
# --------------------------------------------------------------------------

# token -> (colour, flags with colour, flags without colour)
#
# The monochrome column is not a fallback nobody looked at: it is the only way
# a colour-blind terminal tells a failed check from a passed one, so every
# token that carries meaning by colour carries it by weight as well.
_SPEC = {
    TITLE:            ("cyan",    ("bold",),   ("bold",)),
    PATH:             (None,      ("dim",),    ("dim",)),
    METRIC_LABEL:     (None,      ("dim",),    ("dim",)),
    METRIC_VALUE:     (None,      (),          ()),
    PANE_TITLE:       (None,      ("bold",),   ("bold",)),
    PANE_META:        (None,      ("dim",),    ("dim",)),
    RULE:             (None,      ("dim",),    ("dim",)),
    BODY:             (None,      (),          ()),
    MUTED:            (None,      ("dim",),    ("dim",)),
    ABSENT:           (None,      ("dim",),    ("dim",)),
    EMPHASIS:         (None,      ("bold",),   ("bold",)),
    SELECTED:         (None,      ("reverse",), ("reverse",)),
    KEY:              (None,      ("bold",),   ("bold",)),
    KEY_LABEL:        (None,      ("dim",),    ("dim",)),
    BAR_FILL:         ("green",   (),          ("reverse",)),
    BAR_EMPTY:        (None,      ("dim",),    ("dim",)),
    KIND:             ("magenta", ("dim",),    ("dim",)),

    # Five states, five distinguishable spellings. None of them is
    # white-on-black; see the module docstring.
    "status.completed": ("green",  (),         ()),
    "status.running":   ("yellow", ("bold",),  ("bold",)),
    "status.pending":   ("white",  ("dim",),   ("dim",)),
    "status.failed":    ("red",    ("bold",),  ("bold", "reverse")),
    "status.cancelled": ("blue",   ("dim",),   ("dim", "underline")),

    "phase.running":  ("green",  ("bold",), ("bold",)),
    "phase.judging":  ("yellow", ("bold",), ("bold",)),
    "phase.blocked":  ("red",    ("bold",), ("bold", "reverse")),
    "phase.complete": ("cyan",   ("bold",), ("bold",)),
    "phase.pending":  ("white",  ("dim",),  ("dim",)),

    "attention.bad":  ("red",    ("bold",), ("bold", "reverse")),
    "attention.warn": ("yellow", ("bold",), ("bold",)),
    "attention.note": ("cyan",   (),        ()),
    "attention.calm": ("green",  (),        ()),
}

_COLOURS = {
    "black": curses.COLOR_BLACK,
    "red": curses.COLOR_RED,
    "green": curses.COLOR_GREEN,
    "yellow": curses.COLOR_YELLOW,
    "blue": curses.COLOR_BLUE,
    "magenta": curses.COLOR_MAGENTA,
    "cyan": curses.COLOR_CYAN,
    "white": curses.COLOR_WHITE,
}

_FLAGS = {
    "bold": curses.A_BOLD,
    "dim": curses.A_DIM,
    "reverse": curses.A_REVERSE,
    "underline": curses.A_UNDERLINE,
    "standout": curses.A_STANDOUT,
}

# --------------------------------------------------------------------------
# glyphs
# --------------------------------------------------------------------------

#: The five status glyphs ACC-TUI-006 names, plus the punctuation the chrome
#: draws. One table, so a view never writes a literal `✓`.
#:
#: `control` is the odd one out: it is not punctuation a view asks for but the
#: mark `chrome.sanitise()` leaves where a control character was. It is here
#: rather than in `chrome.py` so that it degrades with everything else — under
#: a locale that cannot encode it, curses would drop the mark to a blank and
#: the substitution would become a silent deletion.
GLYPHS = {
    "completed": "✓",
    "running": "●",
    "pending": "○",
    "failed": "✗",
    "cancelled": "−",
    "passed": "✓",
    "blocked": "✗",
    "dot": "●",
    "sep": "·",
    "bullet": "·",
    "ellipsis": "…",
    "bar_fill": "█",
    "bar_empty": "░",
    "updown": "↑↓",
    "control": "▯",
}

#: What each glyph becomes when the locale cannot encode it. Still five
#: distinguishable marks: an ASCII terminal loses the shapes, not the meaning.
ASCII_GLYPHS = {
    "completed": "+",
    "running": "*",
    "pending": ".",
    "failed": "x",
    "cancelled": "-",
    "passed": "+",
    "blocked": "x",
    "dot": "*",
    "sep": "|",
    "bullet": "-",
    "ellipsis": "...",
    "bar_fill": "#",
    "bar_empty": "-",
    "updown": "up/dn",
    "control": "?",
}


def locale_is_utf8():
    """Whether this process's locale can encode the glyph table.

    Reads the locale, never a file. `setlocale(LC_ALL, "")` is what tells both
    Python and ncurses which encoding the terminal is in; without it curses
    encodes to ASCII regardless of what the user's terminal can show.
    """
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:  # pragma: no cover - a broken locale is still a locale
        pass
    try:
        codeset = locale.nl_langinfo(locale.CODESET)
    except (AttributeError, ValueError):  # pragma: no cover - platform fallback
        codeset = locale.getpreferredencoding(False)
    return (codeset or "").replace("-", "").upper() == "UTF8"


class Theme:
    """The tokens, resolved for one terminal.

    Built by `setup()` after `initscr()`; nothing here works before that,
    because a colour pair cannot be allocated until curses is up.
    """

    def __init__(self, colour, utf8, pairs):
        self.colour = colour
        self.utf8 = utf8
        self._pairs = pairs
        self._glyphs = GLYPHS if utf8 else ASCII_GLYPHS
        self._cache = {}
        # Rules come from the alternate character set, not from the glyph
        # table: ACS reaches the terminal as real box drawing whatever the
        # locale is, and ncurses substitutes ASCII itself where a terminal has
        # no line-drawing at all.
        self.hline = curses.ACS_HLINE
        self.vline = curses.ACS_VLINE

    # -- attributes ------------------------------------------------------

    def attr(self, token):
        """The curses attribute `token` means. An unknown token is body text.

        Unknown rather than fatal on purpose: a view leg that reaches for a
        token this module has not grown yet gets readable text and a screen,
        not a traceback in the middle of a repaint.
        """
        if token in self._cache:
            return self._cache[token]
        colour, on, mono = _SPEC.get(token, _SPEC[BODY])
        flags = on if self.colour else mono
        value = 0
        for flag in flags:
            value |= _FLAGS.get(flag, 0)
        if self.colour and colour is not None:
            value |= self._pairs.get(colour, 0)
        self._cache[token] = value
        return value

    def status(self, state):
        """`(glyph, attr)` for a leg or check state — the pair, never one half.

        Returning both together is what stops a view drawing the right glyph in
        the wrong colour, which is half of ACC-TUI-006.
        """
        key = state if state in GLYPHS else "pending"
        return self._glyphs[key], self.attr(STATUS.get(state, STATUS["pending"]))

    def phase(self, phase):
        return self.attr(PHASE.get(phase, PHASE["pending"]))

    def attention(self, level):
        return self.attr(ATTENTION.get(level, ATTENTION["note"]))

    # -- glyphs ----------------------------------------------------------

    def glyph(self, name):
        return self._glyphs.get(name, GLYPHS.get(name, "?"))


def setup():
    """Start colour, allocate one pair per colour, and return the `Theme`.

    Call once, straight after `initscr()`. Every failure here degrades: a
    terminal with no colour, no default colours, or too few pairs gets the
    monochrome spellings rather than an exception.
    """
    utf8 = locale_is_utf8()
    colour = False
    background = curses.COLOR_BLACK
    try:
        curses.start_color()
        colour = curses.has_colors()
    except curses.error:
        colour = False
    if colour:
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:  # pragma: no cover - terminal without default colours
            background = curses.COLOR_BLACK

    pairs = {}
    if colour:
        index = 1
        for name in sorted(_COLOURS):
            if index >= curses.COLOR_PAIRS:  # pragma: no cover - tiny terminfo
                break
            try:
                curses.init_pair(index, _COLOURS[name], background)
            except curses.error:  # pragma: no cover - terminal refused the pair
                continue
            pairs[name] = curses.color_pair(index)
            index += 1
    return Theme(colour=colour and bool(pairs), utf8=utf8, pairs=pairs)

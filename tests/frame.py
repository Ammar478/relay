"""Frame capture harness — run a terminal program under a pty and read the screen.

This is the evidence mechanism for every visual check in this project. A *frame*
is the visible terminal screen, as plain text, after some action. Frames are
deterministic, assertable, and can be written to `.relay/evidence/`.

Nothing here is specific to the relay TUI: it drives any program that draws on a
terminal. Python 3 standard library only.

Quick start
-----------

    import sys
    from frame import TerminalSession

    with TerminalSession([sys.executable, "scripts/relay_control.py"],
                         rows=24, cols=80) as term:
        frame = term.wait_for("Overview")      # wait for the first paint
        frame.assert_contains("RUNNING")
        frame.assert_within_width()            # nothing wider than 80 cells

        frame = term.send("<Down><Down><Enter>")   # waits for the program to act
        assert frame.find("cutover-flip") is not None

        frame = term.send("<Enter>", expect="Leg detail")   # the sound form

        frame = term.resize(48, 160)           # SIGWINCH, then recapture
        frame.dump(".relay/evidence/legs-160x48.txt")

        term.send("q")
        assert term.wait() == 0                # clean exit

One-shot form, when a judge just wants the frames:

    frames = run_frames([sys.executable, "scripts/relay_control.py"],
                        keys=["F", "<Down>", "<Enter>", "<Esc>", "q"],
                        rows=24, cols=80, wait_for="Overview")
    # frames[0] is the first paint, frames[n] is the screen after keys[n-1]

Key notation
------------

`send()` and `run_frames(keys=...)` take strings. Ordinary characters are sent
literally; `<Name>` is a named key:

    <Up> <Down> <Left> <Right> <Home> <End> <PageUp> <PageDown>
    <Enter> <Tab> <BackTab> <Esc> <Space> <Backspace> <Delete> <Insert>
    <F1>..<F12>
    <C-c>, <C-a>, ... (control keys; <^C> works too)
    <lt>  a literal "<"

A step in `run_frames(keys=...)` may also be `(keys, expect)`, which is
`send(keys, expect=...)`.

Names are case-insensitive. Arrow/Home/End encoding follows the *program's own*
DECCKM state, exactly as a real terminal does: the harness watches for
`ESC [ ? 1 h` in the output and switches to `ESC O A` form, so ncurses apps that
call `keypad(True)` receive real KEY_UP/KEY_DOWN.

Synchronising with the program
------------------------------

A frame is evidence only if it is the screen the program drew *after* the
action. `send()` therefore does not simply write the keys and read whatever is
there; it waits, on two positive signals:

* **Delivery.** The keys are waited out of the pty's input queue, which is
  something a terminal can observe without the program's cooperation: bytes sit
  there until the program reads them (`FIONREAD` on the slave fd). So "the
  program has seen this keystroke" is an observation, not an assumption, and a
  frame can no longer predate the keystroke it is meant to show. A program that
  is alive and still has not read within `timeout` fails the call — a wedged
  TUI is a finding, not a frame.

  Delivery says nothing about the answer, and the barrier claims nothing about
  it. What it does claim is the other direction, which *is* provable: output
  readable while the keys are still queued was written before the program took
  them, so it can never be a response to them. The barrier consumes exactly
  that output — which also keeps a program that is slow to read from blocking
  on a full output buffer — and leaves the pty alone the moment the queue is
  empty, so the repaint is still there for the response wait.
* **The answer.** Then the program's answer is waited for: the first byte it
  writes after delivery, and the repaint settling after that, bounded by
  `redraw` (0.75s) so a keystroke that legitimately draws nothing stays cheap.
  `send(keys, expect="...")` names text the repaint must show and is the sound
  form — it fails loudly with the frame instead of returning a stale one, and
  the frame it returns is one the program had *finished* writing: the needle
  triggers the capture rather than being it, and the wait runs on until the
  program has been quiet for `paint` (0.2s). Without that, a program painting a
  screen region by region hands back a frame that passes on the needle and
  shows the previous screen everywhere else, and text that survives a repaint
  (a pane heading) ends the wait before the program has drawn anything at all.

Two residual failure modes, stated plainly:

* A keystroke whose repaint *starts* later than `redraw`, with no `expect`
  given, still yields the pre-repaint frame — a terminal cannot tell "still
  thinking" from "decided to draw nothing". That is a bounded wait, not a
  guess: name the text with `expect=` (or raise `redraw=`) whenever a
  transition may be slower than that.
* A program that writes on its own timetable — a clock, a progress tick, a
  repaint it began in the instant before it read the keys — can have that write
  taken for the answer. Only writes the harness observes while the keys are
  still queued are provably not the answer; one that lands microseconds after
  the read is indistinguishable from a response to it, and no observation a
  terminal can make closes that gap. `expect=` is the answer here too: it waits
  for text, not for bytes.

`resize()` has no delivery barrier to use, because a signal leaves nothing in
the input queue: `expect=` is the only sound signal there.

What the screen emulator does
-----------------------------

Escape sequences are *interpreted*, not stripped. The screen is a grid of cells
that the sequences mutate, so the captured text is what a human would see:
cursor addressing (CUP/HVP, CUU/CUD/CUF/CUB, CHA/VPA, CNL/CPL), erases (ED, EL,
ECH), insert/delete of lines and characters (IL/DL, ICH/DCH, IRM), scroll
regions (DECSTBM) and scrolling (IND, RI, NEL, SU/SD), repeat (REP), tab stops,
the alternate screen (`?1049`), autowrap (`?7`), save/restore cursor, the
alternate character set and UTF-8 decoding across read boundaries. Escape bytes
never reach the text: frames carry what a human would see, never the sequences
that put it there.

That includes the box drawing. `curses.border()` on `xterm-256color` does not
send `┌` and `│`; it sends `ESC ( 0`, then the letters `l q k x m j`, then
`ESC ( B`. The emulator keeps G0-G3 and the DEC Special Graphics table (SO/SI
included), so a border arrives as a border. An emulator that dropped the
designation would report `lqqqk` — and then `assert_contains("│")` could never
pass, `assert_not_contains` would trip over the injected runs, and every frame
written to `.relay/evidence/` would be a wrong artefact.

No sequence can wedge the emulator either. A count is clamped to the screen
wherever one is looped or allocated over — a scroll of 200 million and a scroll
of 24 leave the same screen — because `feed()` runs inside a drain that checks
its deadline only *between* reads, so one twelve-byte sequence would otherwise
hang the harness with no exception and no timeout. A hung judge is
indistinguishable from a slow one.

Known limits, on purpose: no scrollback (only the visible screen), no origin
mode (`?6`), and no reply to cursor-position queries.

The attribute plane
-------------------

Alongside the text, every cell records *how it was drawn* — the SGR parameters,
exactly as the program sent them. Nothing is resolved to RGB and nothing is
normalised, because a judge asserts "this was drawn with SGR 32 and bold", not
"this was #00ff00":

    frame.attrs_at(row, col)        # the CellAttrs of one cell
    frame.attr_runs(row)            # contiguous runs of like-attributed cells
    frame.run_with("FAILED")        # the run containing a substring
    frame.attrs_for("FAILED")       # that run's CellAttrs
    frame.assert_attrs("FAILED", fg=31, has="bold")
    frame.assert_attrs_differ("FAILED", "PASSED")

None of those can pass vacuously. `assert_attrs` refuses a call with nothing to
assert and a flag name that is not a flag (`lacks="bolt"` used to pass on any
styling at all), and `run_with` refuses a needle that was drawn two different
ways in two places rather than answering for the first one it finds — name a
`row` to say which copy is meant. Frames compare on text *and* attributes, so
`after != before` still sees a status that went from green to red.

`CellAttrs.fg` / `.bg` are parameter tuples — `(32,)`, `(38, 5, 214)`,
`(38, 2, r, g, b)` — or `None` for the terminal default. `.flags` is a frozenset
of names (`bold`, `dim`, `italic`, `underline`, `blink`, `reverse`, `invisible`,
`strike`), also reachable as `.bold`, `.reverse` and friends. `.other` keeps any
SGR parameter the harness does not model, so an unknown parameter is *recorded*
rather than silently mistaken for something else — a child that emits no SGR at
all simply has a plane of `DEFAULT_ATTRS`.

Erasing follows back-colour erase, which is what `xterm-256color` advertises
(`bce`): a cell blanked by ED/EL/ECH, or scrolled/inserted into existence, keeps
the *current background* and loses everything else. `Screen.reset()` and
`Screen.resize()` produce default cells.

Gotchas worth knowing before you debug something
------------------------------------------------

* `COLUMNS` and `LINES` are always removed from the child environment. ncurses
  prefers them over the pty size, and they would silently defeat `rows=`/`cols=`.
* `ESCDELAY=25` is set by default so `<Esc>` is not swallowed for a second by
  ncurses' escape-sequence timeout. Override via `env=` if a test needs the
  default.
* Terminal attributes start at the pty defaults (echo on). `initial_attrs`
  records them at launch; compare with `termios_attrs()` after exit to prove the
  program restored the terminal. Both read the master side, because macOS
  revokes the slave fd the moment the child session leader exits.
* The child's stderr goes to the pty, so a traceback appears in the frame. That
  is deliberate — a crashed TUI is visible evidence rather than a silent blank.
* A curses program writes its endwin cleanup on the way out and blocks there if
  nobody is reading the pty, so `wait()` and `close()` keep draining while they
  wait. Do not replace them with a bare `os.waitpid`.
* A resize while the program holds the alternate screen keeps the saved primary
  screen, clipped and padded to the new size. ncurses takes the alternate screen
  (`?1049`) on `xterm-256color`, so a SIGWINCH mid-run and a clean quit meet
  here: dropping it would mean the program could never hand back the screen it
  was given.
* `lines` are right-stripped; use `raw_lines` when a column position matters.
* Width is *display* width: wide (East Asian W/F) characters count as two cells,
  combining marks as zero.

Catching content that is too wide
---------------------------------

A real terminal wraps an over-long line onto the next row, so no row is ever
wider than the terminal — a naive "is any line > cols" check can never fail. The
emulator therefore records, as it draws, what ran past the right margin on each
row: the width at which the row *continued* onto the next one, and the cells
destroyed at the margin when autowrap was off. `Frame.overlong_lines()` returns
`(row, width)` per logical line from those records — never recomputed from the
current size, so a resize can neither drop a violation nor inflate a width into
a number that was never on the screen.

`Frame.assert_within_width()` fails with the offending rows and the whole frame
in the message. A line that exactly fills the width is not a violation, but it
is not a pass either: ncurses clips at the window edge and cursor-addresses
every row rather than letting the terminal wrap, so on a curses screen there is
nothing to read and the helper would be incapable of failing. It therefore
refuses to certify a frame with a row at the last column — nothing a terminal
can observe tells an exact fit from content truncated to fit — and the caller
says which it is with `assert_within_width(allow_full_width=True)`. Also note
that `contains` / `assert_not_contains` see a needle the terminal broke across
two rows; a line-by-line search does not.
"""

from __future__ import annotations

import codecs
import fcntl
import os
import pty
import re
import select
import signal
import struct
import termios
import time
import unicodedata
from pathlib import Path
from typing import NamedTuple

__all__ = [
    "DEFAULT_ATTRS",
    "AttrRun",
    "CellAttrs",
    "Frame",
    "RowOverflow",
    "Screen",
    "TerminalSession",
    "display_width",
    "encode_keys",
    "run_frames",
]

DEFAULT_ROWS = 24
DEFAULT_COLS = 80
DEFAULT_TERM = "xterm-256color"

# The DEC Special Graphics set: what `ESC ( 0` turns the ASCII range 0x5F-0x7E
# into until `ESC ( B` turns it back. ncurses draws every box, rule and arrow
# through this set — `curses.border()` on xterm-256color sends the letters
# `l q k x m j` between the two designations — so an emulator that drops the
# designation renders "lqqqk" where the terminal shows "┌───┐", and every
# assertion about a pane border in every frame it captures is wrong.
_DEC_SPECIAL_GRAPHICS = {
    "_": " ", "`": "◆", "a": "▒", "b": "␉", "c": "␌", "d": "␍", "e": "␊",
    "f": "°", "g": "±", "h": "␤", "i": "␋", "j": "┘", "k": "┐", "l": "┌",
    "m": "└", "n": "┼", "o": "⎺", "p": "⎻", "q": "─", "r": "⎼", "s": "⎽",
    "t": "├", "u": "┤", "v": "┴", "w": "┬", "x": "│", "y": "≤", "z": "≥",
    "{": "π", "|": "≠", "}": "£", "~": "·",
}

# Which slot `ESC (`, `ESC )`, `ESC *`, `ESC +` (94-character sets) and
# `ESC -`, `ESC .`, `ESC /` (96-character sets) designate into.
_CHARSET_SLOTS = {
    "(": "G0", ")": "G1", "*": "G2", "+": "G3",
    "-": "G1", ".": "G2", "/": "G3",
}

# A CSI parameter is clamped on the way in for one reason only: CPython
# refuses to build an int from more than 4300 digits, so a long enough digit
# run raises inside `feed()` instead of drawing anything. The cap is still far
# larger than any grid, so it decides nothing on its own — every routine that
# loops or allocates over a count clamps it to the screen as well.
_MAX_PARAM = 9_999_999
_MAX_PARAM_DIGITS = 7


def _param(text):
    """One CSI parameter as a number, or None when it is not one.

    Seven digits or fewer is at most `_MAX_PARAM`, so the length guard is the
    whole cap: nothing else here decides anything.
    """
    if not text.isdigit():
        return None
    if len(text) > _MAX_PARAM_DIGITS:
        return _MAX_PARAM
    return int(text)


# ---------------------------------------------------------------------------
# text measurement
# ---------------------------------------------------------------------------


def _char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def display_width(text: str) -> int:
    """Width of `text` in terminal cells."""
    return sum(_char_width(ch) for ch in text)


# ---------------------------------------------------------------------------
# attributes
# ---------------------------------------------------------------------------


class CellAttrs(NamedTuple):
    """How one cell was drawn: the SGR parameters that drew it, as given.

    `fg` and `bg` are parameter tuples — `(32,)` for SGR 32, `(38, 5, 214)` for
    256-colour 214, `(38, 2, r, g, b)` for direct colour — or `None` for the
    terminal's default. Nothing is resolved to a palette or an RGB value: a
    judge asserts which parameters drew a cell, not what shade they produced.

    `flags` names the boolean attributes that were on. `other` holds any SGR
    parameter this harness does not model, so an unrecognised one is recorded
    rather than mistaken for something it is not.
    """

    fg: tuple = None
    bg: tuple = None
    flags: frozenset = frozenset()
    other: frozenset = frozenset()

    # -- the flags, spelled out for readable assertions -------------------

    @property
    def bold(self) -> bool:
        return "bold" in self.flags

    @property
    def dim(self) -> bool:
        return "dim" in self.flags

    @property
    def italic(self) -> bool:
        return "italic" in self.flags

    @property
    def underline(self) -> bool:
        return "underline" in self.flags

    @property
    def blink(self) -> bool:
        return "blink" in self.flags

    @property
    def reverse(self) -> bool:
        return "reverse" in self.flags

    @property
    def invisible(self) -> bool:
        return "invisible" in self.flags

    @property
    def strike(self) -> bool:
        return "strike" in self.flags

    @property
    def is_default(self) -> bool:
        """True when the cell was drawn with no SGR in effect."""
        return self == DEFAULT_ATTRS

    def describe(self) -> str:
        """One short line, for an assertion message."""
        if self.is_default:
            return "default"
        parts = []
        if self.fg is not None:
            parts.append("fg=" + ";".join(str(n) for n in self.fg))
        if self.bg is not None:
            parts.append("bg=" + ";".join(str(n) for n in self.bg))
        if self.flags:
            parts.append("+".join(sorted(self.flags)))
        if self.other:
            parts.append("other=" + ",".join(str(n) for n in sorted(self.other)))
        return " ".join(parts)

    def __repr__(self) -> str:
        return "<attrs %s>" % self.describe()


DEFAULT_ATTRS = CellAttrs()


class RowOverflow(NamedTuple):
    """What ran past the right margin on one row, and how.

    `continued` is the width at which the row wrapped onto the next one, kept
    as a width rather than a flag so that a later resize cannot rescale it
    into a number that was never on the screen. `lost` counts the cells
    destroyed at the right margin because autowrap was off: content the
    program drew and the screen never showed.
    """

    continued: int = 0
    lost: int = 0


_NO_OVERFLOW = RowOverflow()


class AttrRun(NamedTuple):
    """A stretch of one row drawn with identical attributes.

    `start` is inclusive and `end` exclusive, both in grid columns.
    """

    row: int
    start: int
    end: int
    text: str
    attrs: CellAttrs

    def __repr__(self) -> str:
        return "<run row %d cols %d-%d %r %s>" % (
            self.row,
            self.start,
            self.end - 1,
            self.text,
            self.attrs.describe(),
        )


# SGR parameters that turn a flag on, and the ones that turn flags off.
_SGR_ON = {
    1: "bold",
    2: "dim",
    3: "italic",
    4: "underline",
    5: "blink",
    6: "blink",
    7: "reverse",
    8: "invisible",
    9: "strike",
}
_SGR_OFF = {
    22: ("bold", "dim"),
    23: ("italic",),
    24: ("underline",),
    25: ("blink",),
    26: (),
    27: ("reverse",),
    28: ("invisible",),
    29: ("strike",),
}
_SGR_EXTENDED = (38, 48, 58)


def _extended_colour(code, tokens, index):
    """Consume the semicolon-separated arguments of SGR 38/48/58.

    Returns `(value, next_index)`; `value` is the whole parameter list as given,
    so `38;5;214` becomes `(38, 5, 214)` — the same tuple the colon form
    `38:5:214` produces.
    """

    def take():
        nonlocal index
        if index < len(tokens):
            value = tokens[index][0]
            index += 1
            return value
        return None

    kind = take()
    if kind == 5:
        which = take()
        return (code, 5, 0 if which is None else which), index
    if kind == 2:
        channels = [take() for _ in range(3)]
        return (code, 2) + tuple(0 if c is None else c for c in channels), index
    if kind is None:
        return None, index
    return (code, kind), index


def _apply_sgr(attrs: CellAttrs, raw: str) -> CellAttrs:
    """Apply one SGR sequence's parameter string to `attrs`.

    Unknown parameters land in `other` instead of being dropped, and never stop
    the known ones in the same sequence from applying.
    """
    tokens = []
    for chunk in raw.split(";"):
        tokens.append(tuple(_param(part) for part in chunk.split(":")))
    fg, bg = attrs.fg, attrs.bg
    flags = set(attrs.flags)
    other = set(attrs.other)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        code = token[0] or 0  # an omitted parameter means 0
        if len(token) > 1 and code in _SGR_EXTENDED:
            # colon form: the colour carries its own arguments
            value = tuple(part for part in token if part is not None)
            if code == 38:
                fg = value
            elif code == 48:
                bg = value
            continue
        if code == 0:
            fg = bg = None
            flags.clear()
            other.clear()
        elif code in _SGR_ON:
            flags.add(_SGR_ON[code])
        elif code in _SGR_OFF:
            flags.difference_update(_SGR_OFF[code])
        elif 30 <= code <= 37 or 90 <= code <= 97:
            fg = (code,)
        elif code == 39:
            fg = None
        elif 40 <= code <= 47 or 100 <= code <= 107:
            bg = (code,)
        elif code == 49:
            bg = None
        elif code in _SGR_EXTENDED:
            value, index = _extended_colour(code, tokens, index)
            if code == 38:
                fg = value
            elif code == 48:
                bg = value
            # 58 (underline colour) is consumed so its arguments cannot be
            # mistaken for parameters of their own
        else:
            other.add(code)
    return CellAttrs(fg, bg, frozenset(flags), frozenset(other))


def _as_colour(value):
    """Normalise an expected colour: `32` and `(32,)` mean the same thing."""
    if value is None:
        return None
    if isinstance(value, int):
        return (value,)
    return tuple(value)


_KNOWN_FLAGS = frozenset(_SGR_ON.values())


def _as_flags(value, where="assert_attrs()"):
    """Normalise `has`/`lacks` and refuse a name that is not a flag.

    A misspelt name used to be checked against a set that could never contain
    it, so `lacks="bolt"` passed on any styling whatever — an assertion that
    cannot fail is a check that reports success.
    """
    names = (value,) if isinstance(value, str) else tuple(value)
    unknown = [name for name in names if name not in _KNOWN_FLAGS]
    if unknown:
        raise ValueError(
            "%s: %s is not an attribute flag. The flags are: %s"
            % (
                where,
                ", ".join(repr(name) for name in unknown),
                ", ".join(sorted(_KNOWN_FLAGS)),
            )
        )
    return names


_UNSET = object()


# ---------------------------------------------------------------------------
# Frame
# ---------------------------------------------------------------------------


class Frame:
    """One captured screen: a text plane and, when captured, an attribute plane.

    `lines` are right-stripped; `raw_lines` are padded to the full width.
    `attrs` is a row-major grid of `CellAttrs`, or None for a frame built from
    text alone (the attribute helpers then fail with that explanation rather
    than a `TypeError`). `cells` is the grid one string per cell, which is how
    column positions stay right when a wide character occupies two of them.
    """

    def __init__(self, lines, rows=None, cols=None, wrapped=(), label=None,
                 attrs=None, cells=None, overflow=None):
        self.lines = [line.rstrip() for line in lines]
        self.rows = rows if rows is not None else len(self.lines)
        self.cols = cols if cols is not None else max(
            [display_width(line) for line in self.lines] or [0]
        )
        if overflow is not None:
            self.overflow = list(overflow)
            self.wrapped = frozenset(
                row for row, record in enumerate(self.overflow) if record.continued
            )
        else:
            self.wrapped = frozenset(wrapped)
            self.overflow = [
                RowOverflow(self.cols if row in self.wrapped else 0)
                for row in range(len(self.lines))
            ]
        self.label = label
        # Taken as given: Screen.frame() hands over fresh copies.
        self.attrs = attrs
        self.cells = cells

    # -- text ------------------------------------------------------------

    @property
    def raw_lines(self):
        out = []
        for line in self.lines:
            pad = self.cols - display_width(line)
            out.append(line + " " * pad if pad > 0 else line)
        return out

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def row(self, index: int) -> str:
        return self.lines[index]

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        label = " %r" % self.label if self.label else ""
        return "<Frame %dx%d%s>" % (self.rows, self.cols, label)

    def __eq__(self, other):
        """Text *and* attributes.

        `after != before` is how "the screen changed" is asserted, and a
        colour-only change — a status that went from green to red without
        moving a character — is exactly the change a text-only comparison
        reports as no change at all.
        """
        if isinstance(other, Frame):
            return self.lines == other.lines and self.attrs == other.attrs
        return NotImplemented

    # -- searching -------------------------------------------------------

    def find(self, needle: str, start: int = 0):
        """Index of the first line containing `needle`, or None."""
        for i in range(start, len(self.lines)):
            if needle in self.lines[i]:
                return i
        return None

    def find_all(self, needle: str):
        """Indices of every line containing `needle`."""
        return [i for i, line in enumerate(self.lines) if needle in line]

    def logical_lines(self):
        """`(row, text)` per logical line, wrapped runs joined back together.

        A row the terminal broke onto the next carries the rest of its text on
        that next row, so a search line by line cannot see a needle that
        straddles the break. Rows that merely sit next to each other are never
        joined: joining those would invent text nobody wrote.
        """
        joined = []
        raw = self.raw_lines
        row = 0
        while row < len(self.lines):
            start = row
            text = ""
            while row in self.wrapped and row < len(self.lines) - 1:
                text += raw[row]
                row += 1
            joined.append((start, text + self.lines[row]))
            row += 1
        return joined

    def _logical_find(self, needle: str):
        """The row a logical occurrence of `needle` starts on, or None."""
        for start, text in self.logical_lines():
            if needle in text:
                return start
        return None

    def contains(self, needle: str) -> bool:
        """Whether the text is on the screen, wrapped onto two rows or not."""
        return self._logical_find(needle) is not None

    def line_with(self, needle: str) -> str:
        """The first line containing `needle`. Fails loudly if there is none."""
        index = self.find(needle)
        if index is None:
            raise AssertionError(self._message("no line contains %r" % needle))
        return self.lines[index]

    def search(self, pattern: str):
        """Index of the first line matching a regex, or None."""
        rx = re.compile(pattern)
        for i, line in enumerate(self.lines):
            if rx.search(line):
                return i
        return None

    # -- attributes ------------------------------------------------------

    def _require_attrs(self):
        if self.attrs is None:
            raise AssertionError(
                self._message(
                    "this frame carries no attribute plane: it was built from "
                    "text alone. Capture it with Screen.frame() / "
                    "TerminalSession.frame() to assert on colour."
                )
            )
        return self.attrs

    def _cell_row(self, row: int):
        """One string per grid column, so column indices survive wide chars."""
        if self.cells is not None:
            return self.cells[row]
        return list(self.raw_lines[row])

    def attrs_at(self, row: int, col: int) -> CellAttrs:
        """The attributes the cell at `(row, col)` was drawn with."""
        plane = self._require_attrs()
        if not 0 <= row < len(plane) or not 0 <= col < len(plane[row]):
            raise AssertionError(
                self._message(
                    "no cell at row %d column %d in a %dx%d frame"
                    % (row, col, self.rows, self.cols)
                )
            )
        return plane[row][col]

    def attr_runs(self, row: int, trim: bool = True):
        """The contiguous runs of like-attributed cells on `row`.

        Trailing padding is dropped unless `trim=False`, so the runs line up
        with what `lines` shows. Only *default-attributed* blanks are dropped:
        blanks carrying a background colour are part of what a viewer sees — a
        highlighted row runs to the right margin — and are kept.
        """
        plane = self._require_attrs()
        if not 0 <= row < len(plane):
            raise AssertionError(
                self._message("no row %d in a %dx%d frame" % (row, self.rows, self.cols))
            )
        attrs_row = plane[row]
        cells = self._cell_row(row)
        runs = []
        start = 0
        for col in range(1, len(attrs_row) + 1):
            if col == len(attrs_row) or attrs_row[col] != attrs_row[start]:
                runs.append(
                    AttrRun(
                        row, start, col, "".join(cells[start:col]), attrs_row[start]
                    )
                )
                start = col
        if trim and runs and runs[-1].attrs == DEFAULT_ATTRS:
            last = runs.pop()
            end = last.end
            while end > last.start and cells[end - 1] == _BLANK:
                end -= 1
            if end > last.start:
                runs.append(
                    AttrRun(row, last.start, end, "".join(cells[last.start:end]),
                            last.attrs)
                )
        return runs

    def run_with(self, needle: str, row: int = None) -> AttrRun:
        """The single run that `needle` was drawn in.

        Fails loudly if the text is not there; if it straddles two runs —
        itself a finding, since it means the substring was not drawn in one
        style; or if it appears more than once and the copies were *not* drawn
        alike, because then there is no single answer to give. Looking only at
        the first copy is how a green STATUS on one row certified a red one on
        another. Name a `row` to say which copy is meant; copies that agree
        need no naming.
        """
        self._require_attrs()
        if not needle:
            raise ValueError("run_with() needs a non-empty substring")
        if row is not None:
            if not 0 <= row < len(self.attrs):
                raise AssertionError(
                    self._message(
                        "no row %d in a %dx%d frame" % (row, self.rows, self.cols)
                    )
                )
            candidates = [row]
        else:
            candidates = self.find_all(needle)
            if not candidates:
                if self._logical_find(needle) is not None:
                    raise AssertionError(
                        self._message(
                            "%r is on the screen but the terminal wrapped it "
                            "across two rows, so it was not drawn as one run"
                            % needle
                        )
                    )
                raise AssertionError(self._message("no line contains %r" % needle))
        runs = []
        for index in candidates:
            runs.extend(self._runs_with(index, needle))
        if not runs:
            raise AssertionError(
                self._message("row %d does not contain %r" % (candidates[0], needle))
            )
        if len({run.attrs for run in runs}) > 1:
            raise AssertionError(
                self._attr_message(
                    "%r is drawn %d different ways and no row was named: %s"
                    % (
                        needle,
                        len({run.attrs for run in runs}),
                        "; ".join(
                            "row %d col %d %s"
                            % (run.row, run.start, run.attrs.describe())
                            for run in runs
                        ),
                    ),
                    runs[0].row,
                )
            )
        return runs[0]

    def _runs_with(self, row: int, needle: str):
        """The run each occurrence of `needle` on `row` was drawn in."""
        cells = self._cell_row(row)
        joined = "".join(cells)
        # a cell holds one character or, for the second half of a wide one,
        # nothing at all — so this maps string offsets back to grid columns
        columns = [col for col, cell in enumerate(cells) for _ in cell]
        runs = self.attr_runs(row, trim=False)
        found = []
        position = joined.find(needle)
        while position >= 0:
            found.append(
                self._run_covering(
                    row,
                    columns[position],
                    columns[position + len(needle) - 1],
                    needle,
                    runs,
                )
            )
            position = joined.find(needle, position + 1)
        return found

    def _run_covering(self, row: int, first: int, last: int, needle: str, runs):
        for run in runs:
            if run.start <= first and last < run.end:
                return run
        spanned = [run for run in runs if run.start <= last and first < run.end]
        raise AssertionError(
            self._attr_message(
                "%r on row %d is not drawn as one run — it spans %d: %s"
                % (
                    needle,
                    row,
                    len(spanned),
                    ", ".join(
                        "%r %s" % (run.text, run.attrs.describe()) for run in spanned
                    ),
                ),
                row,
            )
        )

    def attrs_for(self, needle: str, row: int = None) -> CellAttrs:
        """The attributes `needle` was drawn with."""
        return self.run_with(needle, row=row).attrs

    def assert_attrs(self, needle: str, fg=_UNSET, bg=_UNSET, has=(), lacks=(),
                     row: int = None):
        """Assert how `needle` was drawn.

        `fg`/`bg` take a parameter tuple or the bare int (`31` == `(31,)`), and
        `None` means the terminal default. `has`/`lacks` take a flag name or a
        list of them. At least one of the four is required — without one this
        passed on any styling at all — and a name that is not a flag is
        refused rather than quietly never matched.
        """
        if fg is _UNSET and bg is _UNSET and not has and not lacks:
            raise ValueError(
                "assert_attrs(%r) has nothing to assert: give at least one of "
                "fg, bg, has, lacks, or it passes on any styling at all" % needle
            )
        wanted = _as_flags(has, "assert_attrs(has=)")
        unwanted = _as_flags(lacks, "assert_attrs(lacks=)")
        run = self.run_with(needle, row=row)
        actual = run.attrs
        problems = []
        if fg is not _UNSET and actual.fg != _as_colour(fg):
            problems.append("foreground is %r, expected %r" % (actual.fg, _as_colour(fg)))
        if bg is not _UNSET and actual.bg != _as_colour(bg):
            problems.append("background is %r, expected %r" % (actual.bg, _as_colour(bg)))
        for flag in wanted:
            if flag not in actual.flags:
                problems.append("%s is not set" % flag)
        for flag in unwanted:
            if flag in actual.flags:
                problems.append("%s is set" % flag)
        if problems:
            raise AssertionError(
                self._attr_message(
                    "%r is drawn %s: %s"
                    % (needle, actual.describe(), "; ".join(problems)),
                    run.row,
                )
            )
        return self

    def assert_attrs_differ(self, one: str, other: str):
        """Assert two pieces of text were not drawn the same way."""
        first = self.run_with(one)
        second = self.run_with(other)
        if first.attrs == second.attrs:
            raise AssertionError(
                self._message(
                    "%r and %r are both drawn %s — they should be "
                    "distinguishable" % (one, other, first.attrs.describe())
                )
            )
        return self

    # -- assertions ------------------------------------------------------

    def assert_contains(self, needle: str, message: str = None):
        if not self.contains(needle):
            raise AssertionError(
                self._message(message or "frame does not contain %r" % needle)
            )
        return self

    def assert_not_contains(self, needle: str, message: str = None):
        """Fail if the text is on the screen — including across a wrap.

        A line-by-line search cannot see a needle the terminal broke over two
        rows, so on a narrow screen showing FAILED this used to pass.
        """
        index = self._logical_find(needle)
        if index is not None:
            raise AssertionError(
                self._message(
                    message
                    or "frame contains %r on row %d" % (needle, index)
                )
            )
        return self

    def _overflow_at(self, row: int) -> RowOverflow:
        if 0 <= row < len(self.overflow):
            return self.overflow[row]
        return _NO_OVERFLOW

    def overlong_lines(self):
        """`(row, width)` for every logical line wider than the terminal.

        Two things put a row here, and the emulator records both as they
        happen rather than inferring them from the screen afterwards:

        * the row wrapped onto the next one — the width reported is the sum of
          the widths those rows were *drawn* at, never a width recomputed from
          the current size, which is how a resize used to report 360 cells of
          content that were only ever 200;
        * cells were destroyed at the right margin because autowrap was off —
          the width reported is what the program drew, including what the
          screen never showed.

        A row that exactly fills the width is not a violation; see
        `assert_within_width` for what a terminal cannot tell about those.
        """
        violations = []
        row = 0
        while row < len(self.lines):
            record = self._overflow_at(row)
            if record.continued:
                start = row
                width = 0
                while self._overflow_at(row).continued and row < len(self.lines) - 1:
                    width += self._overflow_at(row).continued
                    row += 1
                width += display_width(self.lines[row]) + self._overflow_at(row).lost
                violations.append((start, width))
            else:
                width = display_width(self.lines[row]) + record.lost
                if width > self.cols:
                    violations.append((row, width))
            row += 1
        return violations

    def full_width_rows(self):
        """Rows whose content reaches the last column.

        Nothing a terminal can observe tells one of these from a row some
        program truncated to make it fit: both arrive as `cols` cells of text
        and a cursor address for the next row.
        """
        rows = []
        for row, line in enumerate(self.lines):
            if row in self.wrapped:
                continue
            if self.cells is not None and self.cols and row < len(self.cells):
                occupied = self.cells[row][self.cols - 1] != _BLANK
            else:
                occupied = display_width(line) >= self.cols
            if occupied:
                rows.append(row)
        return rows

    def assert_within_width(self, allow_full_width: bool = False):
        """Fail if any line is wider than the terminal.

        A row that reaches the last column is *refused*, not passed. ncurses
        clips at the window edge and cursor-addresses every row rather than
        letting the terminal wrap, so on a curses screen there are no wrap
        flags to read and this helper would otherwise be incapable of failing
        — a check that always reports success. What the terminal can say is
        which rows run to the margin; whether that is an exact fit or content
        cut off to make it fit is the caller's to state, with
        `allow_full_width=True` (and then the pass means "nothing wrapped and
        nothing was destroyed at the margin", which is all it ever meant).
        """
        violations = self.overlong_lines()
        if violations:
            detail = ", ".join(
                "row %d is %d cells" % (row, width) for row, width in violations
            )
            raise AssertionError(
                self._message(
                    "content is wider than the %d-column terminal: %s"
                    % (self.cols, detail)
                )
            )
        if not allow_full_width:
            full = self.full_width_rows()
            if full:
                raise AssertionError(
                    self._message(
                        "cannot certify this frame: %s of %d run to the last "
                        "column, and a terminal cannot tell content that "
                        "fitted exactly from content truncated to fit — "
                        "ncurses clips at the window edge instead of wrapping. "
                        "Assert on what those rows should say, or pass "
                        "allow_full_width=True if a full-width row is intended."
                        % (
                            ", ".join("row %d" % row for row in full),
                            self.cols,
                        )
                    )
                )
        return self

    # -- evidence --------------------------------------------------------

    def dump(self, path, header: str = None) -> Path:
        """Write the frame to `path` as plain text. Returns the path."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = self.text
        if header:
            body = "# " + header + "\n" + body
        path.write_text(body + "\n", encoding="utf-8")
        return path

    def _message(self, reason: str) -> str:
        header = "%s (%dx%d)" % (self.label or "frame", self.rows, self.cols)
        rule = "-" * max(len(header), min(self.cols, 100))
        numbered = "\n".join(
            "%3d|%s" % (i, line) for i, line in enumerate(self.lines)
        )
        return "%s\n%s\n%s\n%s\n%s" % (reason, header, rule, numbered, rule)

    def _attr_message(self, reason: str, row: int = None) -> str:
        """`_message` plus how the row in question was drawn, run by run."""
        base = self._message(reason)
        if self.attrs is None or row is None:
            return base
        detail = ["how row %d was drawn:" % row]
        for run in self.attr_runs(row, trim=False):
            detail.append(
                "  cols %3d-%-3d %-24s %s"
                % (run.start, run.end - 1, repr(run.text), run.attrs.describe())
            )
        return base + "\n" + "\n".join(detail)


# ---------------------------------------------------------------------------
# Screen — the terminal emulator
# ---------------------------------------------------------------------------

_BLANK = " "
_WIDE_PLACEHOLDER = ""


class Screen:
    """A grid of cells that escape sequences mutate.

    Feed it the bytes a program writes to its terminal; ask it for `lines()` or
    a `frame()`.
    """

    def __init__(self, rows: int = DEFAULT_ROWS, cols: int = DEFAULT_COLS):
        self.rows = rows
        self.cols = cols
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.reset()

    # -- state -----------------------------------------------------------

    def reset(self):
        self._attrs = DEFAULT_ATTRS
        self._grid = [self._blank_row() for _ in range(self.rows)]
        self._attr_grid = [self._blank_attr_row() for _ in range(self.rows)]
        self._overflow = [_NO_OVERFLOW] * self.rows
        self.cursor_row = 0
        self.cursor_col = 0
        self._pending_wrap = False
        self.scroll_top = 0
        self.scroll_bottom = self.rows - 1
        self.autowrap = True
        self.insert_mode = False
        self.cursor_visible = True
        self.application_cursor_keys = False
        self.application_keypad = False
        self.in_alt_screen = False
        self._saved_cursor = None
        self._saved_screen = None
        self._last_char = " "
        self._tabs = set(range(8, self.cols, 8))
        # G0..G3 hold the designated character sets; GL says which one the
        # printable range is currently taken from. "B" is ASCII, "0" is DEC
        # Special Graphics. SO/SI switch GL, ESC ( ) * + designate.
        self._charsets = {"G0": "B", "G1": "B", "G2": "B", "G3": "B"}
        self._gl = "G0"
        self._charset_slot = None
        self._state = "ground"
        self._csi = ""
        self._string_esc = False

    def _blank_row(self):
        return [_BLANK] * self.cols

    def _blank_attrs(self) -> CellAttrs:
        """What a cell blanked *right now* carries.

        Back-colour erase, which is what `xterm-256color` advertises (`bce`):
        an erased cell keeps the current background and loses everything else.
        """
        background = self._attrs.bg
        return DEFAULT_ATTRS if background is None else CellAttrs(bg=background)

    def _blank_attr_row(self):
        return [self._blank_attrs()] * self.cols

    def resize(self, rows: int, cols: int):
        """Resize the grid, keeping the top-left content."""
        grid = [self._blank_row_of(cols) for _ in range(rows)]
        attr_grid = [[DEFAULT_ATTRS] * cols for _ in range(rows)]
        # A row that overflowed still overflowed: the record says at which
        # width, so keeping it is neither a rescale nor a loss. Dropping it
        # when narrowing manufactured a clean bill of health for a screen that
        # had one, and keeping a bare flag while widening reported a width
        # that was never on the screen.
        overflow = [_NO_OVERFLOW] * rows
        for r in range(min(rows, self.rows)):
            for c in range(min(cols, self.cols)):
                grid[r][c] = self._grid[r][c]
                attr_grid[r][c] = self._attr_grid[r][c]
            overflow[r] = self._overflow[r]
        self.rows = rows
        self.cols = cols
        self._grid = grid
        self._attr_grid = attr_grid
        self._overflow = overflow
        self.scroll_top = 0
        self.scroll_bottom = rows - 1
        self.cursor_row = min(self.cursor_row, rows - 1)
        self.cursor_col = min(self.cursor_col, cols - 1)
        self._pending_wrap = False
        self._tabs = set(range(8, cols, 8))
        # A resize while the program holds the alternate screen must not cost it
        # the primary screen it is holding: that screen is what it gives back on
        # the way out, and a TUI is resized far more often than it exits.
        if self._saved_screen is not None:
            self._saved_screen = self._fit_saved_screen(self._saved_screen, rows, cols)

    @staticmethod
    def _blank_row_of(cols):
        return [_BLANK] * cols

    @staticmethod
    def _fit_plane(plane, rows, cols, blank):
        """A saved grid reshaped to `rows` x `cols`: clipped, then padded."""
        fitted = [
            row[:cols] + [blank] * max(0, cols - len(row)) for row in plane[:rows]
        ]
        fitted.extend([blank] * cols for _ in range(rows - len(fitted)))
        return fitted

    @classmethod
    def _fit_saved_screen(cls, saved, rows, cols):
        """A saved (grid, attrs, overflow, cursor) tuple reshaped to a size."""
        grid, attr_grid, overflow, row, col = saved
        return (
            cls._fit_plane(grid, rows, cols, _BLANK),
            cls._fit_plane(attr_grid, rows, cols, DEFAULT_ATTRS),
            (list(overflow) + [_NO_OVERFLOW] * rows)[:rows],
            min(row, rows - 1),
            min(col, cols - 1),
        )

    # -- output ----------------------------------------------------------

    def lines(self):
        """The visible screen, one padded string per row."""
        return ["".join(row) for row in self._grid]

    def cells(self):
        """The visible screen, one string per grid cell."""
        return [row[:] for row in self._grid]

    def attrs(self):
        """How every cell was drawn, row by row."""
        return [row[:] for row in self._attr_grid]

    def wrapped_rows(self):
        """Rows whose content continued onto the following row."""
        return [i for i, record in enumerate(self._overflow) if record.continued]

    def overflow(self):
        """What ran past the right margin, row by row."""
        return self._overflow[:]

    def frame(self, label: str = None) -> Frame:
        return Frame(
            self.lines(),
            rows=self.rows,
            cols=self.cols,
            wrapped=self.wrapped_rows(),
            label=label,
            attrs=self.attrs(),
            cells=self.cells(),
            overflow=self.overflow(),
        )

    # -- input -----------------------------------------------------------

    def feed(self, data):
        """Feed bytes (or str) written by the program."""
        if isinstance(data, (bytes, bytearray)):
            data = self._decoder.decode(bytes(data))
        for ch in data:
            self._feed_char(ch)

    def _feed_char(self, ch):
        state = self._state
        if state == "ground":
            self._ground(ch)
        elif state == "esc":
            self._escape(ch)
        elif state == "csi":
            self._csi_char(ch)
        elif state == "string":
            self._string_char(ch)
        elif state == "charset":
            self._charset(ch)

    # -- ground ----------------------------------------------------------

    def _ground(self, ch):
        code = ord(ch)
        if code == 0x1B:
            self._state = "esc"
        elif code == 0x0D:
            self.cursor_col = 0
            self._pending_wrap = False
        elif code in (0x0A, 0x0B, 0x0C):
            self._index()
            self._pending_wrap = False
        elif code == 0x08:
            if self._pending_wrap:
                self._pending_wrap = False
            elif self.cursor_col > 0:
                self.cursor_col -= 1
        elif code == 0x09:
            self._tab()
        elif code == 0x0E:
            self._gl = "G1"          # shift out
        elif code == 0x0F:
            self._gl = "G0"          # shift in
        elif code in (0x07, 0x00):
            pass
        elif code < 0x20:
            pass
        else:
            self._put(ch)

    def _tab(self):
        stops = [t for t in sorted(self._tabs) if t > self.cursor_col]
        self.cursor_col = stops[0] if stops else self.cols - 1
        self._pending_wrap = False

    def _translate(self, ch):
        """One character as the *designated* set draws it.

        Idempotent: a glyph the table produced is not in the table's keys, so
        REP repeating `_last_char` repeats the glyph rather than re-mapping.
        """
        if self._charsets[self._gl] != "0":
            return ch
        return _DEC_SPECIAL_GRAPHICS.get(ch, ch)

    def _put(self, ch):
        ch = self._translate(ch)
        width = _char_width(ch)
        if width == 0:
            return
        overflowed = False
        if self._pending_wrap:
            if self.autowrap:
                self._record_wrap()
            else:
                # autowrap off: a real terminal overwrites the last cell, so
                # this character is drawn and the one under it is destroyed.
                # Nothing on the screen shows that afterwards, so it is
                # recorded here or it is lost silently.
                self._record_margin_loss(width)
                overflowed = True
            self._pending_wrap = False
        if width == 2 and self.cursor_col == self.cols - 1:
            # a wide character cannot straddle the right margin
            if self.autowrap:
                self._record_wrap()
            else:
                if not overflowed:
                    self._record_margin_loss(width)   # once, not once per test
                return
        if not overflowed:
            # this row is being written, so any overflow recorded for it
            # described content that is no longer there
            self._overflow[self.cursor_row] = _NO_OVERFLOW
        row = self._grid[self.cursor_row]
        attr_row = self._attr_grid[self.cursor_row]
        if self.insert_mode:
            shift = width
            keep = self.cols - self.cursor_col
            row[self.cursor_col:] = ([_BLANK] * shift + row[self.cursor_col:])[:keep]
            attr_row[self.cursor_col:] = (
                [self._blank_attrs()] * shift + attr_row[self.cursor_col:]
            )[:keep]
        row[self.cursor_col] = ch
        attr_row[self.cursor_col] = self._attrs
        if width == 2:
            row[self.cursor_col + 1] = _WIDE_PLACEHOLDER
            attr_row[self.cursor_col + 1] = self._attrs
        self._last_char = ch
        end = self.cursor_col + width
        if end >= self.cols:
            self.cursor_col = self.cols - 1
            self._pending_wrap = True
        else:
            self.cursor_col = end

    # -- escape ----------------------------------------------------------

    def _escape(self, ch):
        self._state = "ground"
        if ch == "[":
            self._csi = ""
            self._state = "csi"
        elif ch in "]P^_X":
            self._string_esc = False
            self._state = "string"
        elif ch in _CHARSET_SLOTS:
            self._charset_slot = _CHARSET_SLOTS[ch]
            self._state = "charset"
        elif ch == "7":
            self._save_cursor()
        elif ch == "8":
            self._restore_cursor()
        elif ch == "D":
            self._index()
        elif ch == "M":
            self._reverse_index()
        elif ch == "E":
            self.cursor_col = 0
            self._index()
        elif ch == "H":
            self._tabs.add(self.cursor_col)
        elif ch == "c":
            self.reset()
        elif ch == "=":
            self.application_keypad = True
        elif ch == ">":
            self.application_keypad = False
        # anything else: a one-character escape we do not need

    def _charset(self, ch):
        """The character after `ESC ( ` designates a set into that slot.

        "0" is DEC Special Graphics — the box-drawing set every curses border
        is drawn with; anything else (ASCII is "B") draws the characters
        themselves.
        """
        self._state = "ground"
        slot = self._charset_slot
        self._charset_slot = None
        if slot is not None:
            self._charsets[slot] = ch

    def _string_char(self, ch):
        # OSC / DCS / APC / PM: runs until BEL or ST (ESC \)
        if self._string_esc:
            self._string_esc = False
            self._state = "ground"
            return
        if ch == "\x07":
            self._state = "ground"
        elif ch == "\x1b":
            self._string_esc = True

    # -- CSI -------------------------------------------------------------

    def _csi_char(self, ch):
        code = ord(ch)
        if 0x30 <= code <= 0x3F or 0x20 <= code <= 0x2F:
            self._csi += ch
            return
        if 0x40 <= code <= 0x7E:
            self._state = "ground"
            self._dispatch_csi(self._csi, ch)
            return
        # a control character inside a sequence: execute it, stay in CSI
        if code < 0x20:
            self._ground(ch)
            return
        self._state = "ground"

    def _dispatch_csi(self, raw, final):
        private = ""
        if raw and raw[0] in "?<>=":
            private = raw[0]
            raw = raw[1:]
        raw = raw.rstrip(" !\"#$%&'()*+,-./")
        if final == "m" and not private:
            # SGR: the one sequence that draws nothing and changes everything
            self._attrs = _apply_sgr(self._attrs, raw)
            return
        params = []
        for chunk in raw.split(";"):
            params.append(_param(chunk.split(":")[0]))
        if not params:
            params = [None]

        def p(index, default=1):
            if index < len(params) and params[index] is not None:
                return params[index]
            return default

        if private == "?":
            if final in "hl":
                self._private_mode(params, final == "h")
            return
        if private:
            return

        if final == "A":
            self._move_up(p(0))
        elif final == "B":
            self._move_down(p(0))
        elif final == "C":
            self.cursor_col = min(self.cols - 1, self.cursor_col + p(0))
            self._pending_wrap = False
        elif final == "D":
            self.cursor_col = max(0, self.cursor_col - p(0))
            self._pending_wrap = False
        elif final == "E":
            self._move_down(p(0))
            self.cursor_col = 0
        elif final == "F":
            self._move_up(p(0))
            self.cursor_col = 0
        elif final in ("G", "`"):
            self.cursor_col = self._clamp_col(p(0) - 1)
            self._pending_wrap = False
        elif final == "d":
            self.cursor_row = self._clamp_row(p(0) - 1)
            self._pending_wrap = False
        elif final in ("H", "f"):
            self.cursor_row = self._clamp_row(p(0) - 1)
            self.cursor_col = self._clamp_col(p(1) - 1)
            self._pending_wrap = False
        elif final == "J":
            self._erase_display(p(0, 0))
        elif final == "K":
            self._erase_line(p(0, 0))
        elif final == "L":
            self._insert_lines(p(0))
        elif final == "M":
            self._delete_lines(p(0))
        elif final == "P":
            self._delete_chars(p(0))
        elif final == "@":
            self._insert_chars(p(0))
        elif final == "X":
            self._erase_chars(p(0))
        elif final == "S":
            self._scroll_up(p(0))
        elif final == "T":
            self._scroll_down(p(0))
        elif final == "b":
            for _ in range(self._clamp_repeat(p(0))):
                self._put(self._last_char)
        elif final == "g":
            if p(0, 0) == 3:
                self._tabs.clear()
            else:
                self._tabs.discard(self.cursor_col)
        elif final == "r":
            top = p(0) - 1
            bottom = p(1, self.rows) - 1
            if 0 <= top < bottom < self.rows:
                self.scroll_top = top
                self.scroll_bottom = bottom
            else:
                self.scroll_top = 0
                self.scroll_bottom = self.rows - 1
            self.cursor_row = 0
            self.cursor_col = 0
            self._pending_wrap = False
        elif final in "hl":
            if 4 in [x for x in params if x is not None]:
                self.insert_mode = final == "h"
        elif final == "s":
            self._save_cursor()
        elif final == "u":
            self._restore_cursor()
        # n, c, t and friends: consumed, nothing to draw

    def _private_mode(self, params, on):
        for mode in params:
            if mode == 1:
                self.application_cursor_keys = on
            elif mode == 7:
                self.autowrap = on
            elif mode == 25:
                self.cursor_visible = on
            elif mode in (47, 1047, 1049):
                self._switch_screen(on, save_cursor=(mode == 1049))

    def _switch_screen(self, to_alt, save_cursor):
        if to_alt:
            if self.in_alt_screen:
                return
            self._saved_screen = (
                [row[:] for row in self._grid],
                [row[:] for row in self._attr_grid],
                self._overflow[:],
                self.cursor_row,
                self.cursor_col,
            )
            self._grid = [self._blank_row() for _ in range(self.rows)]
            self._attr_grid = [self._blank_attr_row() for _ in range(self.rows)]
            self._overflow = [_NO_OVERFLOW] * self.rows
            self.in_alt_screen = True
        else:
            if not self.in_alt_screen:
                return
            self.in_alt_screen = False
            if self._saved_screen is None:
                return
            grid, attr_grid, overflow, row, col = self._fit_saved_screen(
                self._saved_screen, self.rows, self.cols
            )
            self._saved_screen = None
            self._grid = grid
            self._attr_grid = attr_grid
            self._overflow = overflow
            if save_cursor:
                self.cursor_row = self._clamp_row(row)
                self.cursor_col = self._clamp_col(col)

    # -- cursor / scrolling ----------------------------------------------

    def _clamp_repeat(self, count):
        """A repeat count reduced to one that leaves the identical screen.

        Once every cell holds the same character, each further `cols` repeats
        put the screen and the cursor back exactly where they were: the rows
        that scroll away are indistinguishable from the rows that replace
        them. Writing the screen twice over reaches that state from any
        starting cursor position, so anything past it is taken modulo the
        width — the clamp is an equivalence, not an approximation.
        """
        settled = 2 * self.rows * self.cols
        if count <= settled or self.cols <= 0:
            return count
        return settled + (count - settled) % self.cols

    def _clamp_row(self, row):
        return max(0, min(self.rows - 1, row))

    def _clamp_col(self, col):
        return max(0, min(self.cols - 1, col))

    def _move_up(self, count):
        limit = self.scroll_top if self.cursor_row >= self.scroll_top else 0
        self.cursor_row = max(limit, self.cursor_row - count)
        self._pending_wrap = False

    def _move_down(self, count):
        limit = (
            self.scroll_bottom
            if self.cursor_row <= self.scroll_bottom
            else self.rows - 1
        )
        self.cursor_row = min(limit, self.cursor_row + count)
        self._pending_wrap = False

    def _save_cursor(self):
        self._saved_cursor = (self.cursor_row, self.cursor_col)

    def _restore_cursor(self):
        if self._saved_cursor:
            self.cursor_row = self._clamp_row(self._saved_cursor[0])
            self.cursor_col = self._clamp_col(self._saved_cursor[1])
        else:
            self.cursor_row = self.cursor_col = 0
        self._pending_wrap = False

    def _index(self):
        if self.cursor_row == self.scroll_bottom:
            self._scroll_up(1)
        elif self.cursor_row < self.rows - 1:
            self.cursor_row += 1

    def _reverse_index(self):
        if self.cursor_row == self.scroll_top:
            self._scroll_down(1)
        elif self.cursor_row > 0:
            self.cursor_row -= 1

    def _region_height(self):
        return self.scroll_bottom - self.scroll_top + 1

    def _scroll_up(self, count):
        # Scrolling the region away twice leaves the same blank region as
        # scrolling it away once, so a count past the region height is the
        # same screen — and a count of two hundred million is a hung harness,
        # because feed() runs inside a drain that only checks its deadline
        # between reads.
        for _ in range(min(count, self._region_height())):
            del self._grid[self.scroll_top]
            del self._attr_grid[self.scroll_top]
            del self._overflow[self.scroll_top]
            self._grid.insert(self.scroll_bottom, self._blank_row())
            self._attr_grid.insert(self.scroll_bottom, self._blank_attr_row())
            self._overflow.insert(self.scroll_bottom, _NO_OVERFLOW)

    def _scroll_down(self, count):
        for _ in range(min(count, self._region_height())):
            del self._grid[self.scroll_bottom]
            del self._attr_grid[self.scroll_bottom]
            del self._overflow[self.scroll_bottom]
            self._grid.insert(self.scroll_top, self._blank_row())
            self._attr_grid.insert(self.scroll_top, self._blank_attr_row())
            self._overflow.insert(self.scroll_top, _NO_OVERFLOW)

    def _record_wrap(self):
        """The cursor ran off the right margin and the row continued below."""
        self._overflow[self.cursor_row] = RowOverflow(
            continued=self.cols, lost=self._overflow[self.cursor_row].lost
        )
        self._index()
        self.cursor_col = 0

    def _record_margin_loss(self, width=1):
        """Content the right margin swallowed because autowrap was off.

        Counted in cells, so a wide character that cannot fit costs two: the
        row is that much wider than the screen ever showed.
        """
        record = self._overflow[self.cursor_row]
        self._overflow[self.cursor_row] = RowOverflow(
            continued=record.continued, lost=record.lost + width
        )

    # -- erasing / editing -----------------------------------------------

    def _clear_row(self, row, start=0, end=None):
        end = self.cols if end is None else end
        blank_attrs = self._blank_attrs()
        for col in range(start, end):
            self._grid[row][col] = _BLANK
            self._attr_grid[row][col] = blank_attrs
        if start == 0 and end == self.cols:
            self._overflow[row] = _NO_OVERFLOW

    def _erase_display(self, mode):
        if mode == 0:
            self._clear_row(self.cursor_row, self.cursor_col)
            self._overflow[self.cursor_row] = _NO_OVERFLOW
            for row in range(self.cursor_row + 1, self.rows):
                self._clear_row(row)
        elif mode == 1:
            self._clear_row(self.cursor_row, 0, self.cursor_col + 1)
            for row in range(0, self.cursor_row):
                self._clear_row(row)
        else:
            for row in range(self.rows):
                self._clear_row(row)
        self._pending_wrap = False

    def _erase_line(self, mode):
        if mode == 0:
            self._clear_row(self.cursor_row, self.cursor_col)
            self._overflow[self.cursor_row] = _NO_OVERFLOW
        elif mode == 1:
            self._clear_row(self.cursor_row, 0, self.cursor_col + 1)
        else:
            self._clear_row(self.cursor_row)
        self._pending_wrap = False

    def _in_region(self):
        return self.scroll_top <= self.cursor_row <= self.scroll_bottom

    def _insert_lines(self, count):
        if not self._in_region():
            return
        # past the foot of the region every further insert only pushes a blank
        # row out, so the screen stops changing: clamping is equivalent
        for _ in range(min(count, self.scroll_bottom - self.cursor_row + 1)):
            del self._grid[self.scroll_bottom]
            del self._attr_grid[self.scroll_bottom]
            del self._overflow[self.scroll_bottom]
            self._grid.insert(self.cursor_row, self._blank_row())
            self._attr_grid.insert(self.cursor_row, self._blank_attr_row())
            self._overflow.insert(self.cursor_row, _NO_OVERFLOW)
        self.cursor_col = 0
        self._pending_wrap = False

    def _delete_lines(self, count):
        if not self._in_region():
            return
        for _ in range(min(count, self.scroll_bottom - self.cursor_row + 1)):
            del self._grid[self.cursor_row]
            del self._attr_grid[self.cursor_row]
            del self._overflow[self.cursor_row]
            self._grid.insert(self.scroll_bottom, self._blank_row())
            self._attr_grid.insert(self.scroll_bottom, self._blank_attr_row())
            self._overflow.insert(self.scroll_bottom, _NO_OVERFLOW)
        self.cursor_col = 0
        self._pending_wrap = False

    def _delete_chars(self, count):
        row = self._grid[self.cursor_row]
        attr_row = self._attr_grid[self.cursor_row]
        col = self.cursor_col
        keep = self.cols - col
        # no clamp needed and none pretended: the slice below is empty once
        # `count` reaches the end of the row, and nothing here is allocated
        # per count the way `_insert_chars` would be
        row[col:] = (row[col + count:] + [_BLANK] * self.cols)[:keep]
        attr_row[col:] = (
            attr_row[col + count:] + [self._blank_attrs()] * self.cols
        )[:keep]
        self._pending_wrap = False

    def _insert_chars(self, count):
        row = self._grid[self.cursor_row]
        attr_row = self._attr_grid[self.cursor_row]
        col = self.cursor_col
        keep = self.cols - col
        # inserting more blanks than the row can hold blanks the rest of the
        # row and nothing else — and building the list first would allocate
        # once per count
        count = min(count, keep)
        row[col:] = ([_BLANK] * count + row[col:])[:keep]
        attr_row[col:] = ([self._blank_attrs()] * count + attr_row[col:])[:keep]
        self._pending_wrap = False

    def _erase_chars(self, count):
        end = min(self.cols, self.cursor_col + count)
        self._clear_row(self.cursor_row, self.cursor_col, end)
        self._pending_wrap = False


# ---------------------------------------------------------------------------
# key encoding
# ---------------------------------------------------------------------------

_NAMED_KEYS = {
    "enter": "\r",
    "return": "\r",
    "cr": "\r",
    "newline": "\n",
    "tab": "\t",
    "backtab": "\x1b[Z",
    "shift-tab": "\x1b[Z",
    "esc": "\x1b",
    "escape": "\x1b",
    "space": " ",
    "backspace": "\x7f",
    "delete": "\x1b[3~",
    "del": "\x1b[3~",
    "insert": "\x1b[2~",
    "pageup": "\x1b[5~",
    "pgup": "\x1b[5~",
    "pagedown": "\x1b[6~",
    "pgdn": "\x1b[6~",
    "lt": "<",
    "gt": ">",
    "f1": "\x1bOP",
    "f2": "\x1bOQ",
    "f3": "\x1bOR",
    "f4": "\x1bOS",
    "f5": "\x1b[15~",
    "f6": "\x1b[17~",
    "f7": "\x1b[18~",
    "f8": "\x1b[19~",
    "f9": "\x1b[20~",
    "f10": "\x1b[21~",
    "f11": "\x1b[23~",
    "f12": "\x1b[24~",
}

# normal (DECCKM off) and application (DECCKM on) forms
_CURSOR_KEYS = {
    "up": ("\x1b[A", "\x1bOA"),
    "down": ("\x1b[B", "\x1bOB"),
    "right": ("\x1b[C", "\x1bOC"),
    "left": ("\x1b[D", "\x1bOD"),
    "home": ("\x1b[H", "\x1bOH"),
    "end": ("\x1b[F", "\x1bOF"),
}

_TOKEN_RE = re.compile(r"<([^<>]+)>")


def _named_key(name: str, application_cursor: bool) -> str:
    key = name.strip().lower()
    if key in _CURSOR_KEYS:
        return _CURSOR_KEYS[key][1 if application_cursor else 0]
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    if key.startswith("c-") and len(key) == 3:
        return chr(ord(key[2].upper()) & 0x1F)
    if key.startswith("^") and len(key) == 2:
        return chr(ord(key[1].upper()) & 0x1F)
    raise ValueError(
        "unknown key %r — known names: %s, <C-x>, <lt>"
        % (name, ", ".join(sorted(set(_NAMED_KEYS) | set(_CURSOR_KEYS))))
    )


def encode_keys(keys, application_cursor: bool = False) -> str:
    """Turn a key script into the characters a terminal would send.

    Ordinary characters pass through; `<Name>` becomes that key's sequence. See
    the module docstring for the list of names.
    """
    if not isinstance(keys, str):
        return "".join(encode_keys(part, application_cursor) for part in keys)
    out = []
    pos = 0
    for match in _TOKEN_RE.finditer(keys):
        out.append(keys[pos:match.start()])
        out.append(_named_key(match.group(1), application_cursor))
        pos = match.end()
    out.append(keys[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------
# TerminalSession
# ---------------------------------------------------------------------------


def _set_winsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


# How many bytes are still queued for the program to read. Absent on a platform
# that does not define it, in which case the delivery barrier has no signal and
# says so rather than guessing.
_FIONREAD = getattr(termios, "FIONREAD", None)

# How long the delivery barrier waits between looks at that queue. It bounds
# how late the barrier notices the keys were taken, and so how much of the
# program's answer it can swallow before the response wait gets a chance at it.
_DELIVERY_POLL = 0.002


class TerminalSession:
    """A program running under a pty of a fixed size, with a screen attached.

    Usable as a context manager; `close()` is always safe to call twice.
    """

    def __init__(
        self,
        argv,
        rows: int = DEFAULT_ROWS,
        cols: int = DEFAULT_COLS,
        env=None,
        cwd=None,
        term: str = DEFAULT_TERM,
        settle: float = 0.25,
        idle: float = 0.08,
        timeout: float = 5.0,
        redraw: float = 0.75,
        paint: float = 0.2,
        escdelay: str = "25",
    ):
        self.argv = list(argv)
        self.rows = rows
        self.cols = cols
        self.cwd = str(cwd) if cwd else None
        self.settle = settle
        self.idle = idle
        self.timeout = timeout
        self.redraw = redraw
        # How long a program has to have stopped writing before a frame counts
        # as a screen it finished: a pause shorter than this is part of the
        # same repaint. Longer than `idle` on purpose — `idle` says a burst has
        # settled, `paint` says the screen is done.
        self.paint = paint
        self.screen = Screen(rows, cols)
        self.pid = None
        self.master_fd = None
        self.exit_code = None
        self.initial_attrs = None
        self._slave_fd = None
        self._closed = False

        environment = dict(os.environ if env is None else env)
        # ncurses prefers COLUMNS/LINES over the pty size; they would defeat the
        # explicit size this harness exists to guarantee.
        environment.pop("COLUMNS", None)
        environment.pop("LINES", None)
        if term is not None:
            environment["TERM"] = term
        elif "TERM" not in environment:
            environment["TERM"] = DEFAULT_TERM
        if escdelay is not None:
            environment.setdefault("ESCDELAY", escdelay)
        self.env = environment

    # -- lifecycle -------------------------------------------------------

    def start(self) -> Frame:
        """Fork the program under a pty and return the first painted frame."""
        if self.pid is not None:
            raise RuntimeError("session already started")
        master_fd, slave_fd = pty.openpty()
        _set_winsize(slave_fd, self.rows, self.cols)
        try:
            self.initial_attrs = termios.tcgetattr(master_fd)
        except termios.error:  # pragma: no cover - platform fallback
            self.initial_attrs = termios.tcgetattr(slave_fd)
        pid = os.fork()
        if pid == 0:  # pragma: no cover - child process
            try:
                os.close(master_fd)
                os.setsid()
                try:
                    fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
                except OSError:
                    pass
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)
                if self.cwd:
                    os.chdir(self.cwd)
                os.execvpe(self.argv[0], self.argv, self.env)
            except BaseException:
                os._exit(127)
        self.pid = pid
        self.master_fd = master_fd
        # The slave stays open in the parent so that termios can be inspected
        # after the child exits, and so reads never differ between macOS (EOF)
        # and Linux (EIO) when the child goes away.
        self._slave_fd = slave_fd
        os.set_blocking(master_fd, False)
        self._drain(wait_for_first=True)
        return self.frame()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    @property
    def is_running(self) -> bool:
        if self.pid is None or self.exit_code is not None:
            return False
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            self.exit_code = -1
            return False
        if pid == 0:
            return True
        self.exit_code = self._status_to_code(status)
        return False

    @staticmethod
    def _status_to_code(status):
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        return None

    def wait(self, timeout: float = None):
        """Wait for the program to exit; return its exit code."""
        timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._drain(settle=0.02, idle=0.02)
            if not self.is_running:
                self._drain(settle=0.05, idle=0.05)
                return self.exit_code
        raise AssertionError(
            "program %r did not exit within %.1fs\n%s"
            % (self.argv, timeout, self.frame().text)
        )

    def _wait_exit(self, timeout: float) -> bool:
        """Wait for the program to go away, reading its output all the while.

        The reading matters: a curses program's exit path writes its endwin
        cleanup to the terminal, and blocks there for ever if nobody drains it.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running:
                return True
            if self.master_fd is None:
                time.sleep(0.01)
            else:
                self._drain(settle=0.02, idle=0.02, timeout=0.05)
        return not self.is_running

    def close(self, timeout: float = 2.0):
        """Stop the program if it is still running and release the pty."""
        if self._closed:
            return self.exit_code
        try:
            if self.pid is not None and self.is_running:
                self.signal(signal.SIGTERM)
                if not self._wait_exit(timeout / 2):
                    self.signal(signal.SIGKILL)
                    self._wait_exit(timeout / 2)
        finally:
            self._closed = True
            for fd in (self.master_fd, self._slave_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            self.master_fd = None
            self._slave_fd = None
        # macOS keeps a killed session leader in an exiting state until the pty
        # is closed, so the last reap happens after the fds are gone.
        if self.pid is not None and self.exit_code is None:
            self._wait_exit(timeout)
        return self.exit_code

    def termios_attrs(self):
        """Current terminal attributes, for restore checks after exit.

        Read from the master side: macOS revokes the slave fd once the session
        leader exits, which is exactly when a quit-restores-the-terminal check
        wants to look.
        """
        for fd in (self.master_fd, self._slave_fd):
            if fd is None:
                continue
            try:
                return termios.tcgetattr(fd)
            except termios.error:
                continue
        raise RuntimeError("no usable pty fd — session is closed")

    # -- capture ---------------------------------------------------------

    def frame(self, label: str = None) -> Frame:
        """The current screen, without waiting for more output."""
        return self.screen.frame(label=label or " ".join(self.argv[-1:]))

    def read(self, settle: float = None, timeout: float = None) -> Frame:
        """Drain pending output and return the resulting frame."""
        self._drain(settle=settle, timeout=timeout)
        return self.frame()

    def send(self, keys, settle: float = None, expect: str = None,
             timeout: float = None, regex: bool = False) -> Frame:
        """Send a key script, wait for the program to act on it, return the frame.

        The returned frame is the screen *after* the keystroke, not before it:

        1. the keys are written, then waited out of the pty's input queue, which
           is how the program reading them becomes an observation rather than an
           assumption — see `_await_delivery`;
        2. the program's answer is waited for. `expect` names text the repaint
           must show and is the sound form — the wait ends when it appears *and*
           the program has stopped writing, so the frame is a whole screen and
           not a half-painted one, and it fails loudly with the frame if the
           text never appears. Without `expect` the wait ends at the first byte
           the program writes and the repaint settling after it, bounded by
           `redraw` for a keystroke that draws nothing at all.

        `settle` overrides that bound for one call; `timeout` bounds both the
        delivery barrier and `expect`.
        """
        text = encode_keys(keys, self.screen.application_cursor_keys)
        return self.send_bytes(
            text.encode("utf-8"), settle=settle, expect=expect, timeout=timeout,
            regex=regex,
        )

    def send_bytes(self, data, settle: float = None, expect: str = None,
                   timeout: float = None, regex: bool = False) -> Frame:
        if self.master_fd is None:
            raise RuntimeError("session is not running")
        if isinstance(data, str):
            data = data.encode("utf-8")
        os.write(self.master_fd, data)
        self._await_delivery(data, timeout=timeout)
        if expect is not None:
            return self.wait_for(expect, timeout=timeout, regex=regex)
        window = self.redraw if settle is None else settle
        self._await_response(window)
        return self.frame()

    def _input_pending(self):
        """Bytes written to the program that it has not read yet, or None.

        Read from the *slave* fd: its input queue is the one the program reads
        from, and it is the same on macOS and Linux. None means there is no
        signal to be had — the pty is gone, the platform has no `FIONREAD`, or
        the line discipline cannot answer (BSD counts only complete lines in
        canonical mode, so a half-typed line reads as zero). A missing signal
        never fails a test; it only means this barrier does not fire.
        """
        fd = self._slave_fd
        if fd is None or _FIONREAD is None:
            return None
        try:
            return struct.unpack("i", fcntl.ioctl(fd, _FIONREAD, b"\0" * 4))[0]
        except (OSError, ValueError):
            return None

    def _await_delivery(self, sent, timeout: float = None) -> None:
        """Wait until the program has taken `sent` out of the pty input queue.

        This is the one positive signal a terminal can read without the
        program's cooperation, and it rules out the whole class of frames
        captured before the program ever saw the key. A program that is alive
        and still has not read after `timeout` raises: a TUI that has stopped
        reading its input is a finding, not a frame.

        Delivery says nothing about the answer, and this barrier deliberately
        reports nothing about it. What it does do is clear the pty of output
        that *cannot* be the answer: everything readable while the keys are
        still queued was written before the program took them. Consuming it
        here keeps a program that is slow to read from blocking on a full
        output buffer, and keeps the response wait from mistaking it for the
        repaint. The instant the queue is empty the pty is left alone, so
        whatever the program writes next is still there for `_await_response`
        to find — which is what keeps a prompt program cheap without a flag
        guessing at causality.
        """
        timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while True:
            pending = self._input_pending()
            if not pending:
                return
            if not self.is_running:
                return
            now = time.monotonic()
            if now >= deadline:
                raise AssertionError(
                    self.frame()._message(
                        "the program has not read the %d byte(s) sent to it (%r) "
                        "within %.1fs — it is not reading its input"
                        % (pending, sent, timeout)
                    )
                )
            self._consume_while_queued(min(_DELIVERY_POLL, deadline - now))

    def _consume_while_queued(self, budget: float) -> bool:
        """Read output that the program wrote before it read the keys.

        The queue is checked again immediately before the read, because the
        program may have taken the keys while this call sat in `select`: bytes
        that become readable once the queue is empty are left in the pty rather
        than swallowed, since they may be the answer and the response wait is
        where that is decided.

        The ordering is what makes the exclusion sound rather than a guess. If
        the input queue is non-empty at the moment output becomes readable, the
        program has not taken the keys yet, so that output was written before
        the read and cannot be a response to it.
        """
        if self.master_fd is None:
            return False
        try:
            ready, _, _ = select.select([self.master_fd], [], [], max(budget, 0.0))
        except (OSError, ValueError, TypeError):
            return False
        if not ready or not self._input_pending():
            return False
        try:
            data = os.read(self.master_fd, 65536)
        except BlockingIOError:
            return False
        except OSError:
            return False
        if not data:
            return False
        self.screen.feed(data)
        return True

    def _await_response(self, window: float) -> bool:
        """Wait for the program's answer to something it has just been sent.

        Returns as soon as output arrives and then settles, so a program that
        answers at once pays only its own latency — this is a bounded positive
        wait, not a sleep. `window` bounds the silent case: a keystroke that
        legitimately draws nothing must not cost the full timeout. Returns
        whether anything was drawn.
        """
        slice_ = max(self.idle, 0.05)
        deadline = time.monotonic() + window
        while True:
            if self._drain(settle=slice_, idle=self.idle):
                return True
            if not self.is_running:
                # Nothing more is coming; take whatever the exit path wrote.
                self._drain(settle=0.02, idle=0.02)
                return False
            if time.monotonic() >= deadline:
                return False

    def _await_paint_end(self, window: float = None) -> Frame:
        """Read until the program stops writing; return the finished frame.

        The same bounded positive wait as `_await_response`, run to the *end* of
        a repaint instead of the start of it: read until the program has been
        quiet for `paint`, which is what "it has finished the screen" reduces to
        for a terminal. A pause shorter than that counts as part of the same
        screen; `redraw` caps the whole wait, so a program that never stops
        writing cannot hang the capture — it only makes the frame the last
        thing this could see, which is the best a terminal can do for a screen
        that never settles.
        """
        self._drain(settle=self.paint, idle=self.paint,
                    timeout=self.redraw if window is None else window)
        return self.frame()

    def wait_for(self, needle: str, timeout: float = None, regex: bool = False) -> Frame:
        """Wait until the screen shows `needle`; return the frame it finished.

        The needle triggers the capture; it is not the capture. A program paints
        a screen in pieces, so the instant the needle's own cells land the rest
        of the screen may still be the previous frame — and text that survives
        the repaint (a pane heading) is on screen before the program has drawn
        anything at all. So once the needle is there the wait continues until
        the program has stopped writing, and the frame returned is the one it
        finished. See `_await_paint_end`.

        Fails with the last frame in the message if the needle never appears.
        """
        timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        rx = re.compile(needle) if regex else None
        while True:
            frame = self.frame()
            found = frame.search(needle) is not None if rx else frame.contains(needle)
            if found:
                return self._await_paint_end()
            if time.monotonic() >= deadline:
                raise AssertionError(
                    frame._message(
                        "%r never appeared within %.1fs%s"
                        % (
                            needle,
                            timeout,
                            "" if self.is_running else " (program has exited)",
                        )
                    )
                )
            self._drain(settle=0.05, idle=0.05, wait_for_first=False)

    def resize(self, rows: int, cols: int, settle: float = None,
               expect: str = None, timeout: float = None,
               regex: bool = False) -> Frame:
        """Resize the pty and deliver SIGWINCH, then wait for the redraw.

        A signal leaves nothing in the input queue, so there is no delivery
        barrier to be had here: `expect` — text the program paints at the new
        size — is the only sound signal that the redraw has happened. Without
        it the wait ends at the first byte of the redraw, bounded by `redraw`
        for a program that does not repaint at all.
        """
        if self.master_fd is None:
            raise RuntimeError("session is not running")
        self.rows = rows
        self.cols = cols
        _set_winsize(self.master_fd, rows, cols)
        if self._slave_fd is not None:
            _set_winsize(self._slave_fd, rows, cols)
        self.screen.resize(rows, cols)
        self.signal(signal.SIGWINCH)
        if expect is not None:
            return self.wait_for(expect, timeout=timeout, regex=regex)
        self._await_response(self.redraw if settle is None else settle)
        return self.frame()

    def signal(self, sig):
        """Send a signal to the program's process group."""
        if self.pid is None:
            return
        try:
            os.killpg(os.getpgid(self.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                pass

    # -- reading ---------------------------------------------------------

    def _drain(self, settle=None, idle=None, timeout=None, wait_for_first=False):
        """Read output until it goes quiet.

        Returns once no byte has arrived for `idle` seconds. `timeout` caps a
        program that keeps talking; `wait_for_first` keeps waiting up to the cap
        for the very first byte (interpreter start-up).
        """
        if self.master_fd is None:
            return False
        settle = self.settle if settle is None else settle
        idle = self.idle if idle is None else idle
        timeout = self.timeout if timeout is None else timeout
        idle = min(idle, settle)
        started = time.monotonic()
        hard_deadline = started + timeout
        got_data = False
        last = started
        while True:
            now = time.monotonic()
            if now >= hard_deadline:
                return got_data
            if got_data or not wait_for_first:
                if now - last >= (idle if got_data else settle):
                    return got_data
            budget = min(idle, hard_deadline - now)
            try:
                ready, _, _ = select.select([self.master_fd], [], [], max(budget, 0.0))
            except (OSError, ValueError, TypeError):
                return got_data
            if not ready:
                continue
            try:
                data = os.read(self.master_fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                return got_data
            if not data:
                return got_data
            self.screen.feed(data)
            got_data = True
            last = time.monotonic()


# ---------------------------------------------------------------------------
# one-shot helper
# ---------------------------------------------------------------------------


def run_frames(
    argv,
    keys=(),
    rows: int = DEFAULT_ROWS,
    cols: int = DEFAULT_COLS,
    wait_for: str = None,
    **kwargs,
):
    """Run `argv`, send each key script in `keys`, return every frame.

    `frames[0]` is the first paint; `frames[n]` is the screen after `keys[n-1]`.
    `wait_for` makes the first frame deterministic by waiting for some text the
    program paints on start-up.

    Every step is synchronised the way `TerminalSession.send()` is. A step given
    as `(keys, expect)` waits for `expect` to appear before its frame is taken,
    which is the sound form for a transition the program is slow to paint.
    """
    frames = []
    with TerminalSession(argv, rows=rows, cols=cols, **kwargs) as term:
        if wait_for:
            frames.append(term.wait_for(wait_for))
        else:
            frames.append(term.frame())
        for step in keys:
            if isinstance(step, (tuple, list)):
                script, expect = step
                frames.append(term.send(script, expect=expect))
            else:
                frames.append(term.send(step))
    return frames

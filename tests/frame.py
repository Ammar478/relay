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

        frame = term.send("<Down><Down><Enter>")
        assert frame.find("cutover-flip") is not None

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

Names are case-insensitive. Arrow/Home/End encoding follows the *program's own*
DECCKM state, exactly as a real terminal does: the harness watches for
`ESC [ ? 1 h` in the output and switches to `ESC O A` form, so ncurses apps that
call `keypad(True)` receive real KEY_UP/KEY_DOWN.

What the screen emulator does
-----------------------------

Escape sequences are *interpreted*, not stripped. The screen is a grid of cells
that the sequences mutate, so the captured text is what a human would see:
cursor addressing (CUP/HVP, CUU/CUD/CUF/CUB, CHA/VPA, CNL/CPL), erases (ED, EL,
ECH), insert/delete of lines and characters (IL/DL, ICH/DCH, IRM), scroll
regions (DECSTBM) and scrolling (IND, RI, NEL, SU/SD), repeat (REP), tab stops,
the alternate screen (`?1049`), autowrap (`?7`), save/restore cursor and UTF-8
decoding across read boundaries. SGR and other attribute or mouse sequences are
consumed and discarded — frames carry text, never escape bytes.

Known limits, on purpose: no colour or attribute capture (assert on text and
layout, not on styling), no scrollback (only the visible screen), no origin mode
(`?6`), and no reply to cursor-position queries.

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
* `lines` are right-stripped; use `raw_lines` when a column position matters.
* Width is *display* width: wide (East Asian W/F) characters count as two cells,
  combining marks as zero.

Catching content that is too wide
---------------------------------

A real terminal wraps an over-long line onto the next row, so no row is ever
wider than the terminal — a naive "is any line > cols" check can never fail. The
emulator therefore records which rows *continued* onto the next one.
`Frame.overlong_lines()` returns `(row, width)` for each such logical line and
`Frame.assert_within_width()` fails with the offending rows and the whole frame
in the message. A line that exactly fills the width is not a violation.
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

__all__ = [
    "Frame",
    "Screen",
    "TerminalSession",
    "display_width",
    "encode_keys",
    "run_frames",
]

DEFAULT_ROWS = 24
DEFAULT_COLS = 80
DEFAULT_TERM = "xterm-256color"


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
# Frame
# ---------------------------------------------------------------------------


class Frame:
    """One captured screen, as plain text.

    `lines` are right-stripped; `raw_lines` are padded to the full width.
    """

    def __init__(self, lines, rows=None, cols=None, wrapped=(), label=None):
        self.lines = [line.rstrip() for line in lines]
        self.rows = rows if rows is not None else len(self.lines)
        self.cols = cols if cols is not None else max(
            [display_width(line) for line in self.lines] or [0]
        )
        self.wrapped = frozenset(wrapped)
        self.label = label

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
        if isinstance(other, Frame):
            return self.lines == other.lines
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

    def contains(self, needle: str) -> bool:
        return self.find(needle) is not None

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

    # -- assertions ------------------------------------------------------

    def assert_contains(self, needle: str, message: str = None):
        if not self.contains(needle):
            raise AssertionError(
                self._message(message or "frame does not contain %r" % needle)
            )
        return self

    def assert_not_contains(self, needle: str, message: str = None):
        index = self.find(needle)
        if index is not None:
            raise AssertionError(
                self._message(
                    message
                    or "frame contains %r on row %d" % (needle, index)
                )
            )
        return self

    def overlong_lines(self):
        """`(row, width)` for every logical line wider than the terminal.

        A row that wrapped onto the next row is reported with the total width of
        the whole wrapped run. A row that exactly fills the width is not a
        violation.
        """
        violations = []
        row = 0
        while row < len(self.lines):
            if row in self.wrapped:
                start = row
                while row in self.wrapped and row < len(self.lines) - 1:
                    row += 1
                width = (row - start) * self.cols + display_width(self.lines[row])
                violations.append((start, width))
            else:
                width = display_width(self.lines[row])
                if width > self.cols:
                    violations.append((row, width))
            row += 1
        return violations

    def assert_within_width(self):
        """Fail if any line is wider than the terminal."""
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
        self._grid = [self._blank_row() for _ in range(self.rows)]
        self._wrapped = [False] * self.rows
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
        self._state = "ground"
        self._csi = ""
        self._string_esc = False

    def _blank_row(self):
        return [_BLANK] * self.cols

    def resize(self, rows: int, cols: int):
        """Resize the grid, keeping the top-left content."""
        grid = [self._blank_row_of(cols) for _ in range(rows)]
        wrapped = [False] * rows
        for r in range(min(rows, self.rows)):
            for c in range(min(cols, self.cols)):
                grid[r][c] = self._grid[r][c]
            if cols >= self.cols:
                wrapped[r] = self._wrapped[r]
        self.rows = rows
        self.cols = cols
        self._grid = grid
        self._wrapped = wrapped
        self.scroll_top = 0
        self.scroll_bottom = rows - 1
        self.cursor_row = min(self.cursor_row, rows - 1)
        self.cursor_col = min(self.cursor_col, cols - 1)
        self._pending_wrap = False
        self._tabs = set(range(8, cols, 8))
        self._saved_screen = None

    @staticmethod
    def _blank_row_of(cols):
        return [_BLANK] * cols

    # -- output ----------------------------------------------------------

    def lines(self):
        """The visible screen, one padded string per row."""
        return ["".join(row) for row in self._grid]

    def wrapped_rows(self):
        """Rows whose content continued onto the following row."""
        return [i for i, flag in enumerate(self._wrapped) if flag]

    def frame(self, label: str = None) -> Frame:
        return Frame(
            self.lines(),
            rows=self.rows,
            cols=self.cols,
            wrapped=self.wrapped_rows(),
            label=label,
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
            self._state = "ground"

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
        elif code in (0x07, 0x00, 0x0E, 0x0F):
            pass
        elif code < 0x20:
            pass
        else:
            self._put(ch)

    def _tab(self):
        stops = [t for t in sorted(self._tabs) if t > self.cursor_col]
        self.cursor_col = stops[0] if stops else self.cols - 1
        self._pending_wrap = False

    def _put(self, ch):
        width = _char_width(ch)
        if width == 0:
            return
        if self._pending_wrap:
            if self.autowrap:
                self._wrapped[self.cursor_row] = True
                self._index()
                self.cursor_col = 0
            self._pending_wrap = False
        if width == 2 and self.cursor_col == self.cols - 1:
            # a wide character cannot straddle the right margin
            if self.autowrap:
                self._wrapped[self.cursor_row] = True
                self._index()
                self.cursor_col = 0
            else:
                return
        row = self._grid[self.cursor_row]
        if self.insert_mode:
            shift = width
            row[self.cursor_col:] = ([_BLANK] * shift + row[self.cursor_col:])[
                : self.cols - self.cursor_col
            ]
        row[self.cursor_col] = ch
        if width == 2:
            row[self.cursor_col + 1] = _WIDE_PLACEHOLDER
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
        elif ch in "()*+-./":
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
        params = []
        for chunk in raw.split(";"):
            chunk = chunk.split(":")[0]
            params.append(int(chunk) if chunk.isdigit() else None)
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
            for _ in range(p(0)):
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
        # m (SGR), n, c, t and friends: consumed, nothing to draw

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
                self._wrapped[:],
                self.cursor_row,
                self.cursor_col,
            )
            self._grid = [self._blank_row() for _ in range(self.rows)]
            self._wrapped = [False] * self.rows
            self.in_alt_screen = True
        else:
            if not self.in_alt_screen:
                return
            self.in_alt_screen = False
            if self._saved_screen is None:
                return
            grid, wrapped, row, col = self._saved_screen
            self._saved_screen = None
            self._grid = [r[: self.cols] + [_BLANK] * (self.cols - len(r)) for r in grid]
            while len(self._grid) < self.rows:
                self._grid.append(self._blank_row())
            self._grid = self._grid[: self.rows]
            self._wrapped = (wrapped + [False] * self.rows)[: self.rows]
            if save_cursor:
                self.cursor_row = self._clamp_row(row)
                self.cursor_col = self._clamp_col(col)

    # -- cursor / scrolling ----------------------------------------------

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

    def _scroll_up(self, count):
        for _ in range(count):
            del self._grid[self.scroll_top]
            del self._wrapped[self.scroll_top]
            self._grid.insert(self.scroll_bottom, self._blank_row())
            self._wrapped.insert(self.scroll_bottom, False)

    def _scroll_down(self, count):
        for _ in range(count):
            del self._grid[self.scroll_bottom]
            del self._wrapped[self.scroll_bottom]
            self._grid.insert(self.scroll_top, self._blank_row())
            self._wrapped.insert(self.scroll_top, False)

    # -- erasing / editing -----------------------------------------------

    def _clear_row(self, row, start=0, end=None):
        end = self.cols if end is None else end
        for col in range(start, end):
            self._grid[row][col] = _BLANK
        if start == 0 and end == self.cols:
            self._wrapped[row] = False

    def _erase_display(self, mode):
        if mode == 0:
            self._clear_row(self.cursor_row, self.cursor_col)
            self._wrapped[self.cursor_row] = False
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
            self._wrapped[self.cursor_row] = False
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
        for _ in range(count):
            del self._grid[self.scroll_bottom]
            del self._wrapped[self.scroll_bottom]
            self._grid.insert(self.cursor_row, self._blank_row())
            self._wrapped.insert(self.cursor_row, False)
        self.cursor_col = 0
        self._pending_wrap = False

    def _delete_lines(self, count):
        if not self._in_region():
            return
        for _ in range(count):
            del self._grid[self.cursor_row]
            del self._wrapped[self.cursor_row]
            self._grid.insert(self.scroll_bottom, self._blank_row())
            self._wrapped.insert(self.scroll_bottom, False)
        self.cursor_col = 0
        self._pending_wrap = False

    def _delete_chars(self, count):
        row = self._grid[self.cursor_row]
        col = self.cursor_col
        remainder = row[col + count:]
        row[col:] = (remainder + [_BLANK] * self.cols)[: self.cols - col]
        self._pending_wrap = False

    def _insert_chars(self, count):
        row = self._grid[self.cursor_row]
        col = self.cursor_col
        row[col:] = ([_BLANK] * count + row[col:])[: self.cols - col]
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
        escdelay: str = "25",
    ):
        self.argv = list(argv)
        self.rows = rows
        self.cols = cols
        self.cwd = str(cwd) if cwd else None
        self.settle = settle
        self.idle = idle
        self.timeout = timeout
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

    def send(self, keys, settle: float = None) -> Frame:
        """Send a key script, wait for the screen to settle, return the frame."""
        text = encode_keys(keys, self.screen.application_cursor_keys)
        return self.send_bytes(text.encode("utf-8"), settle=settle)

    def send_bytes(self, data, settle: float = None) -> Frame:
        if self.master_fd is None:
            raise RuntimeError("session is not running")
        if isinstance(data, str):
            data = data.encode("utf-8")
        os.write(self.master_fd, data)
        self._drain(settle=settle)
        return self.frame()

    def wait_for(self, needle: str, timeout: float = None, regex: bool = False) -> Frame:
        """Wait until the screen shows `needle`; return that frame.

        Fails with the last frame in the message if it never appears.
        """
        timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        rx = re.compile(needle) if regex else None
        while True:
            frame = self.frame()
            found = frame.search(needle) is not None if rx else frame.contains(needle)
            if found:
                return frame
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

    def resize(self, rows: int, cols: int, settle: float = None) -> Frame:
        """Resize the pty and deliver SIGWINCH, then capture."""
        if self.master_fd is None:
            raise RuntimeError("session is not running")
        self.rows = rows
        self.cols = cols
        _set_winsize(self.master_fd, rows, cols)
        if self._slave_fd is not None:
            _set_winsize(self._slave_fd, rows, cols)
        self.screen.resize(rows, cols)
        self.signal(signal.SIGWINCH)
        self._drain(settle=settle)
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
    """
    frames = []
    with TerminalSession(argv, rows=rows, cols=cols, **kwargs) as term:
        if wait_for:
            frames.append(term.wait_for(wait_for))
        else:
            frames.append(term.frame())
        for step in keys:
            frames.append(term.send(step))
    return frames

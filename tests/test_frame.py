"""Tests for the frame-capture harness (tests/frame.py).

Two layers:

* Screen unit tests feed raw escape sequences straight into the emulator. Each
  one is written so that a "strip the escape sequences" implementation would
  produce different — and wrong — text.
* Session tests run real programs under a pty: curses demo apps and small raw
  ANSI programs written to tmp_path by fixtures. `scripts/relay_control.py` is
  deliberately not used; it does not exist yet.
"""

import os
import subprocess
import sys
import termios
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frame import (  # noqa: E402
    PAINT_EXITED,
    PAINT_QUIET,
    PAINT_SYNCHRONISED,
    Frame,
    Screen,
    TerminalSession,
    display_width,
    encode_keys,
    run_frames,
)

# --------------------------------------------------------------------------
# demo programs
# --------------------------------------------------------------------------

DEMO_MENU = r'''
import curses
import sys

ITEMS = ["alpha", "beta", "gamma", "delta", "epsilon"]


def draw(stdscr, rows, cols, sel, chosen):
    stdscr.erase()
    stdscr.addnstr(0, 0, "SIZE %dx%d" % (rows, cols), cols - 1)
    for i, item in enumerate(ITEMS):
        row = 2 + i
        if row >= rows - 1:
            break
        mark = "> " if i == sel else "  "
        attr = curses.A_REVERSE | curses.A_BOLD if i == sel else curses.A_NORMAL
        stdscr.addnstr(row, 0, mark + item, cols - 1, attr)
    if chosen is not None and rows > 8:
        stdscr.addnstr(8, 0, "CHOSE " + chosen, cols - 1)
    stdscr.addnstr(rows - 1, 0, "Up/Down move  Enter pick  q Quit", cols - 1,
                   curses.A_DIM)
    stdscr.refresh()


def main(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.timeout(100)
    sel = 0
    chosen = None
    last = None
    while True:
        rows, cols = stdscr.getmaxyx()
        state = (rows, cols, sel, chosen)
        if state != last:
            draw(stdscr, rows, cols, sel, chosen)
            last = state
        ch = stdscr.getch()
        if ch == -1:
            continue
        if ch == curses.KEY_RESIZE:
            curses.update_lines_cols()
            last = None
            continue
        if ch == curses.KEY_DOWN:
            sel = min(len(ITEMS) - 1, sel + 1)
        elif ch == curses.KEY_UP:
            sel = max(0, sel - 1)
        elif ch in (10, 13, curses.KEY_ENTER):
            chosen = ITEMS[sel]
        elif ch == 27:
            chosen = None
        elif ch in (ord("q"), ord("Q")):
            break


curses.wrapper(main)
sys.exit(0)
'''

# Writes a line of N characters with no regard for the terminal width. A real
# terminal wraps it; the width helper has to notice.
DEMO_WIDE = r'''
import os
import sys
import termios
import tty

n = int(sys.argv[1])
fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
try:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.write("HEADER\r\n")
    sys.stdout.write("X" * n)
    sys.stdout.flush()
    os.read(fd, 1)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
'''

# Echoes the raw bytes of one keypress, so the tests can prove which encoding
# the harness sent. argv[1] == "app" turns on DECCKM first.
DEMO_KEYS = r'''
import os
import sys
import termios
import tty

fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
try:
    if sys.argv[1] == "app":
        sys.stdout.write("\x1b[?1h")
    sys.stdout.write("\x1b[2J\x1b[HREADY")
    sys.stdout.flush()
    data = os.read(fd, 64)
    sys.stdout.write("\x1b[3;1HGOT " + repr(data))
    sys.stdout.flush()
    os.read(fd, 1)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
'''

# Draws with absolute cursor addressing and erases; strip-the-escapes gets a
# completely different answer.
DEMO_ANSI = r'''
import os
import sys
import termios
import tty

fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
try:
    out = sys.stdout
    out.write("\x1b[2J\x1b[H")
    out.write("PLACEHOLDER-ONE")
    out.write("\x1b[1;1H")
    out.write("REAL")
    out.write("\x1b[0K")
    out.write("\x1b[5;10HDEEP")
    out.flush()
    os.read(fd, 1)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
'''

DEMO_EXIT_CODE = r'''
import os
import sys
import termios
import tty

fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
try:
    sys.stdout.write("\x1b[2J\x1b[HWAITING")
    sys.stdout.flush()
    os.read(fd, 1)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
sys.exit(int(sys.argv[1]))
'''

DEMO_GLYPHS = r'''
import curses
import sys


def main(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.addstr(0, 0, "done ✓ running ● pending ○")
    stdscr.addstr(1, 0, "failed ✗ cancelled −")
    stdscr.refresh()
    while True:
        if stdscr.getch() in (ord("q"), ord("Q")):
            break


curses.wrapper(main)
sys.exit(0)
'''

# A program whose response to a keystroke is unambiguous and *slow*. Three
# shapes of slowness, each of which a "write the keys and read whatever is on
# screen" capture gets wrong in a different way:
#
#   late-read   the key sits unread in the pty while the program is busy, so
#               the screen still shows the state from before the keystroke.
#   late-paint  the program reads the key at once but takes its time redrawing.
#   deaf        the program never reads its input at all — a wedged TUI.
#
# The repaint erases the screen first, so "READY" and "AFTER-KEY" can never
# both be on screen: a frame shows one state or the other, never a blur.
DEMO_SLOW = r'''
import os
import sys
import termios
import time
import tty

mode = sys.argv[1]
delay = float(sys.argv[2])

fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
try:
    sys.stdout.write("\x1b[2J\x1b[HREADY")
    sys.stdout.flush()
    if mode == "deaf":
        time.sleep(30)
    if mode == "late-read":
        time.sleep(delay)
    data = os.read(fd, 64)
    if mode == "late-paint":
        time.sleep(delay)
    sys.stdout.write("\x1b[2J\x1b[HAFTER-KEY " + repr(data.decode("utf-8", "replace")))
    sys.stdout.flush()
    os.read(fd, 1)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
'''

# A program whose *writing* is deliberately out of step with its reading. Every
# mode below writes something that a "some byte arrived, so that must be the
# answer" capture mistakes for the repaint it is waiting for:
#
#   before-read  an earlier repaint, still going out when the key lands. It is
#                written before the key is read, so it cannot be the answer to
#                it; the real answer comes `delay` later.
#   regions      a screen painted region by region, the first region (carrying
#                the text a caller would wait for) going out before the key is
#                read and the rest of it after.
#   title        a repaint under a heading that never changes, so text a caller
#                names with `expect=` is already on screen before the keystroke.
#   sync-regions the same region-by-region repaint, bracketed in DEC 2026. The
#                program says where the repaint begins and ends, so no pause
#                inside it can be mistaken for the end of it.
#   chatty       a program that starts a repaint and never stops writing. There
#                is no quiet window to find, and the screen at the end of the
#                wait is definitely partial.
#   sync-stale   a *complete* bracketed repaint flushed while the key is still
#                queued, and then an unbracketed answer. The bracket closed
#                before the program read the key, so it says nothing about the
#                repaint that answers it.
#
# The first two synchronise on TIOCOUTQ — the count of this program's output the
# terminal has not taken yet — rather than on a sleep: "the harness has seen
# every byte of the noise" is then a fact, not a race, and so is "the key was
# read after it".
DEMO_NOISY = r'''
import fcntl
import os
import select
import struct
import sys
import termios
import time
import tty

mode = sys.argv[1]
delay = float(sys.argv[2])

fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)


def flushed():
    """Wait until the terminal has taken everything written so far."""
    sys.stdout.flush()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            queued = struct.unpack(
                "i", fcntl.ioctl(1, termios.TIOCOUTQ, b"\0" * 4))[0]
        except Exception:
            time.sleep(0.01)
            return
        if queued == 0:
            return
        time.sleep(0.0002)


try:
    if mode == "title":
        sys.stdout.write("\x1b[2J\x1b[1;1HTITLE pane\x1b[3;1HBODY one")
        flushed()
        os.read(fd, 64)
        time.sleep(delay)
        sys.stdout.write("\x1b[3;1HBODY two")
        sys.stdout.flush()
    elif mode == "sync-regions":
        sys.stdout.write("\x1b[2J\x1b[HREADY")
        flushed()
        select.select([fd], [], [], 5)
        sys.stdout.write("\x1b[?2026h\x1b[2J\x1b[1;1HPANE header")
        flushed()
        os.read(fd, 64)
        time.sleep(delay)
        sys.stdout.write("\x1b[3;1HBODY middle")
        flushed()
        time.sleep(delay)
        sys.stdout.write("\x1b[5;1HFOOT bottom\x1b[?2026l")
        sys.stdout.flush()
    elif mode == "sync-stale":
        sys.stdout.write("\x1b[2J\x1b[HREADY")
        flushed()
        select.select([fd], [], [], 5)
        sys.stdout.write("\x1b[?2026h\x1b[2J\x1b[1;1HPANE header\x1b[?2026l")
        flushed()
        os.read(fd, 64)
        time.sleep(delay)
        sys.stdout.write("\x1b[3;1HBODY middle")
        sys.stdout.flush()
    elif mode == "chatty":
        sys.stdout.write("\x1b[2J\x1b[HREADY")
        flushed()
        os.read(fd, 64)
        sys.stdout.write("\x1b[2J\x1b[1;1HPANE header")
        sys.stdout.flush()
        stop = time.monotonic() + 5
        while time.monotonic() < stop:
            time.sleep(delay)
            sys.stdout.write("\x1b[9;1Htick")
            sys.stdout.flush()
    else:
        sys.stdout.write("\x1b[2J\x1b[HREADY")
        flushed()
        # The key is here, and is deliberately left unread while the program
        # writes. select() reports it without taking it out of the queue.
        select.select([fd], [], [], 5)
        if mode == "before-read":
            sys.stdout.write("".join("\x1b[3;1HOLD-PAINT" for _ in range(60)))
        else:
            sys.stdout.write("\x1b[2J\x1b[1;1HPANE header")
        flushed()
        data = os.read(fd, 64)
        if mode == "before-read":
            time.sleep(delay)
            sys.stdout.write(
                "\x1b[2J\x1b[HAFTER-KEY " + repr(data.decode("utf-8", "replace")))
            sys.stdout.flush()
        else:
            time.sleep(delay)
            sys.stdout.write("\x1b[3;1HBODY middle")
            sys.stdout.flush()
            time.sleep(delay)
            sys.stdout.write("\x1b[5;1HFOOT bottom")
            sys.stdout.flush()
    os.read(fd, 1)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
'''

# Writes to the primary screen, then hands it to curses — which takes the
# alternate screen (`smcup` is `\E[?1049h` on xterm-256color) and gives it back
# on the way out. This is the ACC-ROBUST-003 / ACC-NAV-005 path: the app is
# resized while it holds the alternate screen, and what it found must still be
# there when it quits.
DEMO_ALT_SCREEN = r'''
import curses
import sys

sys.stdout.write("SHELL-LINE\r\n")
sys.stdout.flush()


def main(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.timeout(100)
    last = None
    while True:
        size = stdscr.getmaxyx()
        if size != last:
            stdscr.erase()
            stdscr.addnstr(0, 0, "APP-SCREEN %dx%d" % size, size[1] - 1)
            stdscr.refresh()
            last = size
        ch = stdscr.getch()
        if ch == curses.KEY_RESIZE:
            curses.update_lines_cols()
            last = None
        elif ch in (ord("q"), ord("Q")):
            break


curses.wrapper(main)
sys.exit(0)
'''


DEMO_BORDER = r'''
import curses
import sys


def main(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.erase()
    rows, cols = stdscr.getmaxyx()
    stdscr.addnstr(0, 0, "BORDER DEMO", cols - 1)
    win = curses.newwin(5, 20, 1, 2)
    win.border()
    win.addnstr(2, 2, "PANE", 16)
    stdscr.noutrefresh()
    win.noutrefresh()
    curses.doupdate()
    while True:
        if stdscr.getch() in (ord("q"), ord("Q")):
            break


curses.wrapper(main)
sys.exit(0)
'''

# A curses program that draws one row right up to the last column. ncurses
# cursor-addresses every row instead of letting the terminal wrap, so nothing
# the terminal can observe distinguishes this from a row ncurses truncated to
# make it fit.
DEMO_EDGE = r'''
import curses
import sys


def main(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.erase()
    rows, cols = stdscr.getmaxyx()
    stdscr.addnstr(0, 0, "EDGE DEMO", cols - 1)
    try:
        stdscr.addnstr(1, 0, "#" * cols, cols)
    except curses.error:
        pass          # ncurses complains about the cursor, not the cells
    stdscr.refresh()
    while True:
        if stdscr.getch() in (ord("q"), ord("Q")):
            break


curses.wrapper(main)
sys.exit(0)
'''

# A program whose SIGWINCH redraw pauses in the middle. A resize has no
# delivery barrier to lean on — a signal leaves nothing in the input queue —
# so `expect=` is the only signal there, and this is the shape that makes the
# quiet window behind it visible.
DEMO_SLOW_RESIZE = r'''
import os
import signal
import sys
import termios
import time
import tty

delay = float(sys.argv[1])
fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)


def redraw(signum, unused):
    # os.write, not sys.stdout: a second SIGWINCH landing inside this handler
    # is a reentrant call on the BufferedWriter, which raises.
    os.write(1, b"\x1b[2J\x1b[1;1HSIZE pane")
    time.sleep(delay)
    os.write(1, b"\x1b[3;1HBODY resized")


signal.signal(signal.SIGWINCH, redraw)
try:
    os.write(1, b"\x1b[2J\x1b[HREADY")
    while not os.read(fd, 1):
        pass
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
'''

# A curses program that draws a screen and then dies on it. `curses.wrapper`
# runs endwin() on the way out, which sends `?1049l` and puts the primary
# screen back — so the frame after the crash is the traceback on an otherwise
# empty screen, and what the program had drawn is gone from it.
DEMO_CRASH = r'''
import curses
import sys


def main(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.addstr(0, 0, "PANE the app drew")
    stdscr.addstr(1, 0, "STATUS running")
    stdscr.refresh()
    stdscr.getch()
    raise RuntimeError("the app fell over")


curses.wrapper(main)
sys.exit(0)
'''

# Escape sequences whose parameter is far larger than the screen, fed in a
# *subprocess* with a timeout. Never in-process: an unclamped `_scroll_up`
# loops once per count inside `feed()`, and a loop of two hundred million
# cannot be interrupted by the test that started it — a thread you join() does
# not die (.relay/skills/probing-paths-that-never-return.md). The exit code is
# the result.
PROBE_HUGE_COUNTS = r'''
import resource
import sys

sys.path.insert(0, sys.argv[1])
import frame

screen = frame.Screen(rows=24, cols=80)
screen.feed("filler text")
for _ in range(3):
    for final in "STLM@PXb":
        screen.feed("\x1b[200000000" + final)
    # a digit run past CPython's int-from-string limit: int() raises rather
    # than looping, which wedges feed() just as thoroughly
    screen.feed("\x1b[" + "9" * 5000 + "S")
    screen.feed("\x1b[" + "9" * 5000 + "m")
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
if sys.platform != "darwin":
    peak *= 1024                 # Linux reports kilobytes, macOS bytes
print("SURVIVED %d" % peak)
'''


@pytest.fixture
def program(tmp_path):
    """Write one of the demo programs to disk and return its path."""

    def _write(name, source):
        path = tmp_path / (name + ".py")
        path.write_text(source)
        return str(path)

    return _write


@pytest.fixture
def menu(program):
    return program("menu", DEMO_MENU)


@pytest.fixture
def slow(program):
    return program("slow", DEMO_SLOW)


@pytest.fixture
def noisy(program):
    return program("noisy", DEMO_NOISY)


def session(path, *args, **kwargs):
    return TerminalSession([sys.executable, path, *args], **kwargs)


# --------------------------------------------------------------------------
# Screen: escape sequence handling
#
# Every test below is chosen so that stripping escapes with a regex gives a
# different result. The expected value is what a human sees on a real terminal.
# --------------------------------------------------------------------------


def feed(data, rows=5, cols=20):
    screen = Screen(rows, cols)
    screen.feed(data)
    return screen


def test_cup_absolute_addressing_overwrites_earlier_text():
    # strip-the-escapes yields "AAAAB"
    screen = feed("AAAA\x1b[1;1HB")
    assert screen.lines()[0].rstrip() == "BAAA"


def test_cup_row_and_column_are_one_based():
    screen = feed("\x1b[3;5HZ")
    assert screen.lines()[2] == "    Z" + " " * 15
    assert screen.cursor_row == 2
    assert screen.cursor_col == 5


def test_hvp_behaves_like_cup():
    screen = feed("\x1b[2;3fQ")
    assert screen.lines()[1].rstrip() == "  Q"


def test_cup_defaults_to_home():
    screen = feed("junk\x1b[HX")
    assert screen.lines()[0].rstrip() == "Xunk"


def test_cursor_up_down_forward_back():
    # down 2, forward 3, write, up 1, back 2, write
    screen = feed("\x1b[2B\x1b[3CA\x1b[1A\x1b[2DB")
    assert screen.lines()[2].rstrip() == "   A"
    assert screen.lines()[1].rstrip() == "  B"


def test_cursor_movement_clamps_at_edges():
    screen = feed("\x1b[99A\x1b[99DTOP\x1b[99B\x1b[99CE")
    assert screen.lines()[0].rstrip() == "TOP"
    assert screen.lines()[-1].rstrip().endswith("E")
    assert display_width(screen.lines()[-1]) == screen.cols


def test_ed_2_clears_whole_display_but_not_the_cursor():
    screen = feed("hello\x1b[2;1Hworld\x1b[2JX")
    lines = [line.rstrip() for line in screen.lines()]
    assert lines[0] == ""
    # cursor stayed where it was (row 2, col 6) — ED does not home it
    assert lines[1] == "     X"


def test_ed_0_clears_from_cursor_to_end_of_display():
    screen = feed("AAA\r\nBBB\r\nCCC\x1b[2;2H\x1b[0J")
    lines = [line.rstrip() for line in screen.lines()]
    assert lines[0] == "AAA"
    assert lines[1] == "B"
    assert lines[2] == ""


def test_ed_1_clears_from_start_of_display_to_cursor():
    screen = feed("AAA\r\nBBB\r\nCCC\x1b[2;2H\x1b[1J")
    lines = [line.rstrip() for line in screen.lines()]
    assert lines[0] == ""
    assert lines[1] == "  B"
    assert lines[2] == "CCC"


def test_el_0_erases_to_end_of_line():
    # strip-the-escapes yields "HELLO WORLDHI"
    screen = feed("HELLO WORLD\rHI\x1b[0K")
    assert screen.lines()[0].rstrip() == "HI"


def test_el_1_erases_to_start_of_line():
    screen = feed("HELLO\x1b[1;3H\x1b[1K")
    # columns 1..3 inclusive of the cursor are blanked, "LO" survives
    assert screen.lines()[0].rstrip() == "   LO"


def test_el_2_erases_the_whole_line_only():
    screen = feed("AAA\r\nBBB\x1b[2K")
    lines = [line.rstrip() for line in screen.lines()]
    assert lines[0] == "AAA"
    assert lines[1] == ""


def test_sgr_is_consumed_and_never_printed():
    screen = feed("\x1b[1;7;38;5;214mBOLD\x1b[0m\x1b[mPLAIN")
    assert screen.lines()[0].rstrip() == "BOLDPLAIN"
    assert "[" not in screen.lines()[0]
    assert "m" not in screen.lines()[0].replace("BOLD", "").replace("PLAIN", "")


def test_carriage_return_and_linefeed():
    screen = feed("one\r\ntwo\nthree")
    lines = [line.rstrip() for line in screen.lines()]
    assert lines[0] == "one"
    assert lines[1] == "two"
    # bare LF does not return the column: "three" starts under the end of "two"
    assert lines[2] == "   three"


def test_backspace_and_tab():
    screen = feed("abc\b\bX\tY")
    line = screen.lines()[0]
    assert line.startswith("aXc")
    assert line[8] == "Y"


def test_linefeed_at_bottom_scrolls_the_screen():
    screen = feed("1\r\n2\r\n3\r\n4\r\n5\r\n6", rows=5, cols=10)
    assert [line.rstrip() for line in screen.lines()] == ["2", "3", "4", "5", "6"]


def test_scroll_region_confines_scrolling():
    # region rows 2..4; filling it and pushing one more scrolls only inside it
    screen = feed(
        "\x1b[2;4r\x1b[1;1HTOP\x1b[5;1HBOT\x1b[2;1HA\r\nB\r\nC\r\nD",
        rows=5,
        cols=10,
    )
    lines = [line.rstrip() for line in screen.lines()]
    assert lines[0] == "TOP"
    assert lines[4] == "BOT"
    assert lines[1:4] == ["B", "C", "D"]


def test_reverse_index_scrolls_down_at_top_of_region():
    screen = feed("A\r\nB\r\nC\x1b[1;1H\x1bMZ", rows=4, cols=10)
    lines = [line.rstrip() for line in screen.lines()]
    assert lines[0] == "Z"
    assert lines[1] == "A"
    assert lines[2] == "B"


def test_insert_and_delete_line():
    screen = feed("A\r\nB\r\nC\x1b[1;1H\x1b[LZ", rows=4, cols=10)
    assert [line.rstrip() for line in screen.lines()][:4] == ["Z", "A", "B", "C"]
    screen.feed("\x1b[1;1H\x1b[M")
    assert [line.rstrip() for line in screen.lines()][:3] == ["A", "B", "C"]


def test_delete_and_insert_character():
    screen = feed("abcdef\x1b[1;2H\x1b[2P")
    assert screen.lines()[0].rstrip() == "adef"
    screen.feed("\x1b[1;2H\x1b[1@")
    assert screen.lines()[0].rstrip() == "a def"


def test_erase_character_blanks_without_shifting():
    screen = feed("abcdef\x1b[1;2H\x1b[3X")
    assert screen.lines()[0].rstrip() == "a   ef"


def test_save_and_restore_cursor():
    screen = feed("\x1b[3;3H\x1b7\x1b[1;1HTOP\x1b8X")
    assert screen.lines()[0].rstrip() == "TOP"
    assert screen.lines()[2].rstrip() == "  X"


def test_alternate_screen_is_restored_on_exit():
    screen = feed("shell text\x1b[?1049h\x1b[2J\x1b[1;1Happ text")
    assert screen.lines()[0].rstrip() == "app text"
    screen.feed("\x1b[?1049l")
    assert screen.lines()[0].rstrip() == "shell text"


def test_alternate_screen_survives_a_resize():
    """A resize must not throw away the primary screen the app is holding.

    ncurses uses `?1049` on xterm-256color, so a SIGWINCH delivered to a running
    TUI lands exactly here: dropping the saved screen means the app can never
    give back the terminal it was handed.
    """
    screen = feed("shell text\x1b[?1049h\x1b[2J\x1b[1;1Happ text")
    screen.resize(8, 30)
    assert screen.lines()[0].rstrip() == "app text"
    screen.feed("\x1b[?1049l")
    assert screen.lines()[0].rstrip() == "shell text"
    assert len(screen.lines()) == 8
    assert all(len(line) == 30 for line in screen.lines())


def test_a_saved_screen_wider_than_the_new_size_is_clipped_not_lost():
    screen = feed("0123456789ABCDEFGHIJ\x1b[?1049h\x1b[2Japp", rows=5, cols=20)
    screen.resize(3, 10)
    screen.feed("\x1b[?1049l")
    assert screen.lines()[0] == "0123456789"
    assert len(screen.lines()) == 3


def test_a_zero_padded_parameter_addresses_the_cell_it_names():
    """Leading zeros carry no value; counting them addressed the wrong cell.

    The parameter cap is a guard against CPython refusing `int()` on a very
    long digit run, and it counted *characters*. A padded parameter longer
    than the cap therefore arrived as the maximum and clamped to the far edge
    of the screen: `ESC[00000002;00000003H` put its text in the last cell
    instead of row 2, column 3.
    """
    assert feed("\x1b[01;01HA").lines()[0][0] == "A"
    assert feed("\x1b[003;005HB").lines()[2][4] == "B"
    padded = feed("\x1b[00000002;00000003HC")
    assert padded.lines()[1][2] == "C"
    assert padded.lines()[-1].strip() == ""
    # zero itself still means "the default", not "the last row"
    assert feed("\x1b[0;0HD").lines()[0][0] == "D"


def test_a_zero_count_means_one_and_a_zero_mode_means_zero():
    """`CSI 0 A` is one row, not none — but `CSI 0 J` is still mode 0.

    A count read literally as zero made every one of these a no-op, and a
    no-op is the shape of a defect that never shows up in a frame: the screen
    simply stays as it was.
    """
    assert feed("\x1b[3;3HX\x1b[0AY").lines()[1][3] == "Y"      # CUU 0 -> 1
    assert feed("\x1b[1;3HX\x1b[0BY").lines()[1][3] == "Y"      # CUD 0 -> 1
    assert feed("abcdef\x1b[1;2H\x1b[0P").lines()[0].rstrip() == "acdef"
    assert feed("abcdef\x1b[1;2H\x1b[0X").lines()[0].rstrip() == "a cdef"
    assert feed("ab\x1b[0b").lines()[0].rstrip() == "abb"        # REP 0 -> 1
    # ...while a parameter that names a mode keeps meaning mode zero
    assert feed("abc\x1b[1;2H\x1b[0J").lines()[0].rstrip() == "a"
    assert feed("abc\x1b[1;2H\x1b[0K").lines()[0].rstrip() == "a"
    assert feed("\x1b[31m\x1b[0mP").frame().attrs_at(0, 0).fg is None


def test_ed_3_clears_the_scrollback_and_leaves_the_screen_alone():
    """`ESC[3J` erases scrollback. This screen has none, so it erases nothing.

    Treating it as ED 2 blanked the visible screen: a program that clears its
    scrollback on start-up — which many do, right after drawing — lost the
    screen it had just painted, and the frame handed to a judge was empty.
    """
    screen = feed("hello\x1b[3J")
    assert screen.lines()[0].rstrip() == "hello"
    # and an erase parameter that is not an erase at all does nothing either
    assert feed("hello\x1b[9J").lines()[0].rstrip() == "hello"
    # ED 2 still clears, so this is not "J does nothing"
    assert feed("hello\x1b[2J").lines()[0].rstrip() == ""


def test_an_escape_with_an_intermediate_byte_leaves_no_glyph():
    """An unmodelled escape must be swallowed whole, not half.

    Dropping the intermediate and returning to ground printed the sequence's
    *final* byte as text: `ESC # 8` left an "8", `ESC SP F` an "F". A frame
    then carried a character no program ever asked to be drawn.
    """
    assert feed("\x1b#8AB").lines()[0].rstrip() == "AB"       # DECALN
    assert feed("\x1b F" + "XY").lines()[0].rstrip() == "XY"  # S7C1T
    assert feed("\x1b%GZZ").lines()[0].rstrip() == "ZZ"       # select UTF-8
    assert feed("\x1b#!8TOP").lines()[0].rstrip() == "TOP"   # two intermediates
    assert feed("\x1b#3TOP").lines()[0].rstrip() == "TOP"    # DECDHL
    # the charset designators are intermediates too, and still designate
    assert feed("\x1b(0lqk\x1b(BX").lines()[0].rstrip() == "┌─┐X"


def test_synchronized_output_brackets_are_tracked():
    """DEC 2026 is the one thing a program can say about its own painting."""
    screen = Screen(5, 20)
    assert screen.synchronized_update is False
    assert screen.synchronized_updates == 0
    screen.feed("\x1b[?2026hHALF")
    assert screen.synchronized_update is True
    assert screen.synchronized_updates == 0
    screen.feed(" DONE\x1b[?2026l")
    assert screen.synchronized_update is False
    assert screen.synchronized_updates == 1
    assert screen.lines()[0].rstrip() == "HALF DONE"
    # a close with nothing open is not an update, and neither is a re-open
    screen.feed("\x1b[?2026l\x1b[?2026h\x1b[?2026h")
    assert screen.synchronized_updates == 1
    screen.feed("\x1b[?2026l")
    assert screen.synchronized_updates == 2


def test_the_alternate_screen_is_kept_when_the_program_gives_it_back():
    """What a TUI drew before it died is evidence; `?1049l` throws it away."""
    screen = feed("shell text\x1b[?1049h\x1b[2J\x1b[1;1Happ text")
    assert screen.last_alt_frame() is None      # nothing given back yet
    screen.feed("\x1b[?1049l")
    assert screen.lines()[0].rstrip() == "shell text"
    kept = screen.last_alt_frame()
    assert kept is not None
    assert kept.lines[0] == "app text"
    assert kept.rows == 5 and kept.cols == 20


def test_osc_title_is_consumed():
    screen = feed("\x1b]0;a window title\x07VISIBLE")
    assert screen.lines()[0].rstrip() == "VISIBLE"


def test_charset_designation_is_consumed():
    screen = feed("\x1b(B\x1b)0PLAIN")
    assert screen.lines()[0].rstrip() == "PLAIN"


def test_private_mode_sets_are_consumed_and_tracked():
    screen = feed("\x1b[?25l\x1b[?1h\x1b[?1000hTEXT")
    assert screen.lines()[0].rstrip() == "TEXT"
    assert screen.cursor_visible is False
    assert screen.application_cursor_keys is True


def test_utf8_glyphs_survive_split_writes():
    screen = Screen(2, 20)
    data = "✓ ok".encode()
    screen.feed(data[:1])
    screen.feed(data[1:])
    assert screen.lines()[0].rstrip() == "✓ ok"


def test_autowrap_marks_the_row_as_continued():
    screen = feed("A" * 25, rows=5, cols=20)
    assert screen.lines()[0] == "A" * 20
    assert screen.lines()[1].rstrip() == "A" * 5
    assert screen.wrapped_rows() == [0]


def test_exact_width_line_does_not_wrap():
    screen = feed("A" * 20, rows=5, cols=20)
    assert screen.lines()[0] == "A" * 20
    assert screen.lines()[1].rstrip() == ""
    assert screen.wrapped_rows() == []


def test_display_width_counts_wide_characters_as_two():
    assert display_width("abc") == 3
    assert display_width("日本") == 4
    assert display_width("✓●○✗−") == 5


# --------------------------------------------------------------------------
# Screen: the alternate character set
#
# `curses.border()` on xterm-256color does not send box-drawing characters. It
# sends ESC ( 0, then the letters l q k x m j, then ESC ( B. A harness that
# drops the designation renders the letters, so every pane border in every
# frame it captures is a lie: `assert_contains("│")` can never pass and
# `assert_not_contains(<short needle>)` is corrupted by the injected runs.
# --------------------------------------------------------------------------


def test_dec_special_graphics_draws_the_box_characters_it_was_sent():
    # drop-the-designation renders "lqqk"
    screen = feed("\x1b(0lqqk\x1b(Bplain")
    assert screen.lines()[0].rstrip() == "┌──┐plain"


def test_the_whole_graphics_table_is_mapped_not_guessed():
    screen = feed("\x1b(0lqkxmjtuvwn\x1b(B", cols=20)
    assert screen.lines()[0].rstrip() == "┌─┐│└┘├┤┴┬┼"


def test_shift_out_selects_g1_and_shift_in_returns_to_ascii():
    # SO/SI are single control bytes: ignoring them leaves "xx"
    screen = feed("\x1b)0\x0ex\x0fx")
    assert screen.lines()[0].rstrip() == "│x"


def test_designating_ascii_again_ends_the_graphics_run():
    screen = feed("\x1b(0q\x1b(Bq")
    assert screen.lines()[0].rstrip() == "─q"


def test_an_sgr_reset_does_not_end_the_graphics_run():
    # SGR 0 resets attributes, never the charset: ncurses relies on this
    screen = feed("\x1b(0\x1b[0mq")
    assert screen.lines()[0].rstrip() == "─"


def test_a_hard_reset_returns_to_ascii():
    screen = feed("\x1b(0\x1bcq")
    assert screen.lines()[0].rstrip() == "q"


def test_repeat_repeats_the_graphics_glyph_not_the_letter():
    screen = feed("\x1b(0q\x1b[3b\x1b(B")
    assert screen.lines()[0].rstrip() == "────"


def test_graphics_characters_are_one_cell_wide():
    screen = feed("\x1b(0" + "q" * 20 + "\x1b(B", rows=3, cols=20)
    assert screen.lines()[0] == "─" * 20
    assert screen.wrapped_rows() == []


# --------------------------------------------------------------------------
# Screen: a parameter larger than the screen must not wedge the emulator
#
# `feed()` runs inside `_drain`, which checks its deadline only *between*
# reads. One twelve-byte sequence looping two hundred million times therefore
# hangs the harness with no exception and no timeout, and a hung judge is
# indistinguishable from a slow one.
# --------------------------------------------------------------------------


def test_a_huge_escape_parameter_cannot_hang_the_emulator(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE_HUGE_COUNTS)
    result = subprocess.run(
        [sys.executable, str(probe), str(Path(__file__).resolve().parent)],
        capture_output=True,
        text=True,
        timeout=4.0,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.startswith("SURVIVED"), result.stdout
    # a count is clamped before anything is *allocated* over it too: an
    # unclamped `CSI 200000000 @` builds a ten-million-element list, which no
    # timeout catches and no assertion about the screen can see
    peak = int(result.stdout.split()[1])
    assert peak < 100 * 1024 * 1024, "peak memory %d bytes" % peak


def test_a_scroll_past_the_screen_matches_scrolling_the_screen_away():
    """The clamp has to be *equivalent*, not merely fast."""
    clamped = feed("one\r\ntwo\r\nthree\x1b[300S")
    one_at_a_time = feed("one\r\ntwo\r\nthree" + "\x1b[S" * 300)
    assert clamped.lines() == one_at_a_time.lines()
    assert [line.rstrip() for line in clamped.lines()] == [""] * 5


def test_a_reverse_scroll_past_the_screen_matches_scrolling_one_at_a_time():
    clamped = feed("one\r\ntwo\r\nthree\x1b[300T")
    one_at_a_time = feed("one\r\ntwo\r\nthree" + "\x1b[T" * 300)
    assert clamped.lines() == one_at_a_time.lines()


def test_inserting_and_deleting_more_lines_than_the_screen_holds():
    inserted = feed("one\r\ntwo\r\nthree\x1b[1;1H\x1b[300L")
    step = feed("one\r\ntwo\r\nthree\x1b[1;1H" + "\x1b[L" * 300)
    assert inserted.lines() == step.lines()
    deleted = feed("one\r\ntwo\r\nthree\x1b[1;1H\x1b[300M")
    step = feed("one\r\ntwo\r\nthree\x1b[1;1H" + "\x1b[M" * 300)
    assert deleted.lines() == step.lines()


def test_a_repeat_past_the_screen_matches_writing_the_characters():
    """REP is only clamped where the clamp cannot be seen.

    Past a full screen every cell holds the same character, so a further
    `cols` repeats put the screen and the cursor back exactly where they were.
    The oracle is writing the characters out.
    """
    # 5007 is not a whole number of screens *or* of rows: a clamp that drops
    # the residue leaves the cursor in the wrong column
    repeated = feed("A\x1b[5007b")
    written = feed("A" * 5008)
    assert repeated.lines() == written.lines()
    assert (repeated.cursor_row, repeated.cursor_col) == (
        written.cursor_row,
        written.cursor_col,
    )


def test_inserting_more_characters_than_the_row_holds():
    clamped = feed("abcdef\x1b[1;3H\x1b[5000@")
    step = feed("abcdef\x1b[1;3H" + "\x1b[@" * 5000)
    assert clamped.lines() == step.lines()
    assert clamped.lines()[0] == "ab" + " " * 18


def test_a_parameter_too_long_to_be_a_number_is_survived():
    """CPython refuses int() past 4300 digits; the emulator must not.

    The clamped value still has to mean "more than the screen holds": three
    rows of content have to leave, not one.
    """
    screen = feed("one\r\ntwo\r\nthree\x1b[" + "9" * 5000 + "S")
    assert [line.rstrip() for line in screen.lines()] == [""] * 5
    screen = feed("\x1b[" + "9" * 5000 + "mHI")
    assert screen.lines()[0].rstrip() == "HI"


# --------------------------------------------------------------------------
# Screen: what "this row continued onto the next" is worth
#
# `overlong_lines()` reads wrap flags, so every way of getting a wrap flag
# wrong is a way of making `assert_within_width()` pass on a screen it should
# fail, or fail on a screen that is fine.
# --------------------------------------------------------------------------


def test_a_narrowing_resize_does_not_lose_an_overlong_line():
    screen = Screen(5, 20)
    screen.feed("X" * 50)
    assert screen.frame().overlong_lines() == [(0, 50)]
    screen.resize(5, 10)
    assert screen.frame().overlong_lines() == [(0, 50)]


def test_a_widening_resize_does_not_inflate_the_width():
    screen = Screen(5, 20)
    screen.feed("X" * 50)
    screen.resize(5, 40)
    # 50 cells of content: not 90, which is what recomputing from the new width
    # invents
    assert screen.frame().overlong_lines() == [(0, 50)]


def test_a_wrap_that_fits_the_new_width_is_not_a_violation():
    """A row that wrapped at 10 cells is not too wide for a 40-column screen.

    The record is kept — it says the row continued at 10 — but the reader has
    to measure it against the screen it is being asked about. Reporting it
    unconditionally made `assert_within_width()` fail on a frame where every
    row fits, which is the mirror image of the defect it was written for.
    """
    screen = Screen(4, 10)
    screen.feed("A" * 15)
    assert screen.frame().overlong_lines() == [(0, 15)]
    screen.resize(4, 40)
    assert screen.frame().overlong_lines() == []
    screen.frame().assert_within_width()
    # narrowing back is still a violation: 15 cells do not fit in 10
    screen.resize(4, 10)
    assert screen.frame().overlong_lines() == [(0, 15)]


def test_a_widened_row_is_not_joined_through_its_padding():
    """A continued row is cut at the width it continued at, not the new one.

    The grid is not reflowed by a resize, so a row that wrapped at 10 now
    holds 10 characters and 30 fresh blanks. Pasting the padded row in spliced
    a run of spaces into the middle of a line the program wrote without one.
    """
    screen = Screen(4, 10)
    screen.feed("SPLIT-WORD")     # exactly 10, no wrap yet
    screen.feed("S")              # the eleventh cell wraps the row
    screen.resize(4, 40)
    frame = screen.frame()
    assert frame.logical_lines()[0] == (0, "SPLIT-WORDS")
    assert frame.contains("SPLIT-WORDS")
    assert not frame.contains("SPLIT-WORD ")


def test_a_row_rewritten_after_it_wrapped_is_no_longer_continued():
    screen = Screen(5, 10)
    screen.feed("A" * 15)
    assert screen.frame().overlong_lines() == [(0, 15)]
    screen.feed("\x1b[1;1HBBBBB")
    assert screen.wrapped_rows() == []
    assert screen.frame().overlong_lines() == []


def test_a_wide_character_the_margin_cannot_hold_is_recorded_in_cells():
    screen = Screen(5, 10)
    screen.feed("\x1b[?7l" + "z" * 9 + "\u65e5")
    frame = screen.frame()
    assert frame.lines[0] == "z" * 9      # the wide glyph never fitted
    assert frame.overlong_lines() == [(0, 11)]


def test_content_written_past_the_margin_with_autowrap_off_is_recorded():
    screen = Screen(5, 10)
    screen.feed("\x1b[?7l" + "Z" * 14)
    frame = screen.frame()
    assert frame.lines[0] == "Z" * 10          # what a human sees
    assert screen.wrapped_rows() == []          # nothing continued anywhere
    assert frame.overlong_lines() == [(0, 14)]  # four cells were destroyed
    with pytest.raises(AssertionError) as excinfo:
        frame.assert_within_width()
    assert "14" in str(excinfo.value)


# --------------------------------------------------------------------------
# Frame helpers
# --------------------------------------------------------------------------


def test_frame_find_and_line_helpers():
    frame = Frame(("alpha", "beta   ", "gamma"), rows=3, cols=10)
    assert frame.find("beta") == 1
    assert frame.find("nope") is None
    assert frame.find_all("a") == [0, 1, 2]
    assert frame.line_with("beta") == "beta"
    assert frame.contains("gamma")
    with pytest.raises(AssertionError):
        frame.line_with("missing")


def test_frame_assert_contains_reports_the_whole_frame():
    frame = Frame(("alpha", "beta"), rows=2, cols=10)
    frame.assert_contains("alpha")
    with pytest.raises(AssertionError) as excinfo:
        frame.assert_contains("delta")
    assert "alpha" in str(excinfo.value)
    assert "beta" in str(excinfo.value)


def test_frame_lines_are_rstripped_but_raw_lines_are_padded():
    screen = feed("hi")
    frame = screen.frame()
    assert frame.lines[0] == "hi"
    assert frame.raw_lines[0] == "hi" + " " * 18
    assert all(len(line) == 20 for line in frame.raw_lines)


def test_frame_dump_writes_text(tmp_path):
    frame = Frame(("alpha", "beta"), rows=2, cols=10)
    out = frame.dump(tmp_path / "evidence" / "f.txt")
    assert out.read_text() == "alpha\nbeta\n"
    out2 = frame.dump(tmp_path / "with-header.txt", header="80x24 overview")
    assert out2.read_text().splitlines()[0] == "# 80x24 overview"


# --------------------------------------------------------------------------
# Frame: an assertion that cannot fail is a check that reports success
# --------------------------------------------------------------------------


def test_a_needle_split_by_a_wrap_is_still_on_the_screen():
    """A line-by-line search cannot see text the terminal wrapped."""
    screen = Screen(3, 10)
    screen.feed("XXXXXFAILED")
    frame = screen.frame()
    assert frame.lines[:2] == ["XXXXXFAILE", "D"]
    assert frame.contains("FAILED")
    frame.assert_contains("FAILED")
    with pytest.raises(AssertionError) as excinfo:
        frame.assert_not_contains("FAILED")
    assert "row 0" in str(excinfo.value)


def test_text_is_not_invented_across_rows_that_did_not_wrap():
    screen = Screen(3, 10)
    screen.feed("XXXXXFAILE\r\nD")
    frame = screen.frame()
    assert frame.lines[:2] == ["XXXXXFAILE", "D"]
    assert not frame.contains("FAILED")
    frame.assert_not_contains("FAILED")


def test_two_frames_that_differ_only_in_colour_are_not_equal():
    """`frame_after != frame_before` is how "the screen changed" is asserted."""
    red = Screen(1, 10)
    red.feed("\x1b[31mFAILED")
    green = Screen(1, 10)
    green.feed("\x1b[32mFAILED")
    assert red.frame().lines == green.frame().lines
    assert red.frame() != green.frame()


def test_two_frames_drawn_the_same_way_are_equal():
    one = Screen(1, 10)
    one.feed("\x1b[31mFAILED")
    two = Screen(1, 10)
    two.feed("\x1b[31mFAILED")
    assert one.frame() == two.frame()
    assert Frame(("a", "b")) == Frame(("a", "b"))


# --------------------------------------------------------------------------
# TerminalSession: pty, size, keys, resize
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rows,cols", [(24, 80), (20, 60), (40, 100), (60, 200)])
def test_size_is_set_explicitly_and_reported_by_curses(menu, rows, cols):
    with session(menu, rows=rows, cols=cols) as term:
        frame = term.wait_for("SIZE")
        assert frame.line_with("SIZE").strip() == "SIZE %dx%d" % (rows, cols)
        assert frame.rows == rows
        assert frame.cols == cols
        assert len(frame.lines) == rows


def test_size_is_independent_of_the_real_terminal(menu):
    """The real terminal (COLUMNS/LINES in the environment) must not leak in."""
    env = dict(os.environ, COLUMNS="512", LINES="7")
    with session(menu, rows=24, cols=80, env=env) as term:
        frame = term.wait_for("SIZE")
        assert frame.line_with("SIZE").strip() == "SIZE 24x80"


def test_keystroke_script_moves_the_selection(menu):
    with session(menu, rows=24, cols=80) as term:
        term.wait_for("alpha")
        frame = term.send("<Down><Down>")
        assert frame.line_with("gamma").startswith("> gamma")
        assert frame.line_with("alpha").startswith("  alpha")
        frame = term.send("<Up>")
        assert frame.line_with("beta").startswith("> beta")
        frame = term.send("<Enter>")
        assert frame.contains("CHOSE beta")


def test_attributes_do_not_leak_into_the_text(menu):
    with session(menu, rows=24, cols=80) as term:
        frame = term.wait_for("alpha")
        # the selected row is drawn with A_REVERSE | A_BOLD
        assert frame.line_with("alpha") == "> alpha"
        assert "\x1b" not in frame.text
        assert "[7m" not in frame.text


def test_run_frames_returns_one_frame_per_step(menu):
    frames = run_frames(
        [sys.executable, menu],
        keys=["<Down>", "<Down>", "<Enter>", "q"],
        rows=24,
        cols=80,
        wait_for="alpha",
    )
    assert len(frames) == 5
    assert frames[0].line_with("alpha").startswith("> alpha")
    assert frames[2].line_with("gamma").startswith("> gamma")
    assert frames[3].contains("CHOSE gamma")


def test_named_keys_use_normal_encoding_by_default(program):
    path = program("keys", DEMO_KEYS)
    with session(path, "normal", rows=10, cols=60) as term:
        term.wait_for("READY")
        frame = term.send("<Up>")
        assert frame.contains(r"\x1b[A")


def test_named_keys_switch_to_application_mode_when_the_app_asks(program):
    path = program("keys", DEMO_KEYS)
    with session(path, "app", rows=10, cols=60) as term:
        term.wait_for("READY")
        assert term.screen.application_cursor_keys is True
        frame = term.send("<Up>")
        assert frame.contains(r"\x1bOA")


def test_control_keys_are_encoded(program):
    path = program("keys", DEMO_KEYS)
    with session(path, "normal", rows=10, cols=60) as term:
        term.wait_for("READY")
        frame = term.send("<C-a>")
        assert frame.contains(r"\x01")


def test_encode_keys_covers_the_documented_names():
    assert encode_keys("q") == "q"
    assert encode_keys("<Enter>") == "\r"
    assert encode_keys("<Tab>") == "\t"
    assert encode_keys("<Esc>") == "\x1b"
    assert encode_keys("<Down>") == "\x1b[B"
    assert encode_keys("<Down>", application_cursor=True) == "\x1bOB"
    assert encode_keys("<C-c>") == "\x03"
    assert encode_keys("<lt>x") == "<x"
    assert encode_keys("F<Tab>q") == "F\tq"
    with pytest.raises(ValueError):
        encode_keys("<NotAKey>")


def test_raw_ansi_program_is_interpreted_not_stripped(program):
    path = program("ansi", DEMO_ANSI)
    with session(path, rows=10, cols=40) as term:
        frame = term.wait_for("REAL")
        # strip-the-escapes would show "PLACEHOLDER-ONEREALDEEP" on row 1
        assert frame.lines[0] == "REAL"
        assert "PLACEHOLDER" not in frame.text
        assert frame.lines[4] == "         DEEP"
        assert frame.find("DEEP") == 4


def test_resize_delivers_sigwinch_and_the_app_redraws(menu):
    with session(menu, rows=48, cols=160) as term:
        frame = term.wait_for("SIZE 48x160")
        assert frame.cols == 160
        frame = term.resize(24, 80)
        frame = term.wait_for("SIZE 24x80")
        assert frame.rows == 24
        assert frame.cols == 80
        assert len(frame.lines) == 24
        frame.assert_within_width()
        # the app is still alive and still responds to keys
        frame = term.send("<Down>")
        assert frame.line_with("beta").startswith("> beta")


def test_glyphs_are_captured_without_mojibake(program):
    path = program("glyphs", DEMO_GLYPHS)
    with session(path, rows=10, cols=60) as term:
        frame = term.wait_for("done")
        assert frame.lines[0] == "done ✓ running ● pending ○"
        assert frame.lines[1] == "failed ✗ cancelled −"
        assert "�" not in frame.text
        assert "â" not in frame.text


def test_quit_is_clean_and_exit_code_is_zero(menu):
    with session(menu, rows=24, cols=80) as term:
        term.wait_for("alpha")
        before = term.initial_attrs
        term.send("q")
        assert term.wait(timeout=5) == 0
        after = term.termios_attrs()
        assert before == after
        assert bool(after[3] & termios.ECHO)


def test_non_zero_exit_code_is_reported(program):
    path = program("exiter", DEMO_EXIT_CODE)
    with session(path, "3", rows=10, cols=40) as term:
        term.wait_for("WAITING")
        term.send("x")
        assert term.wait(timeout=5) == 3


def test_close_kills_a_program_that_will_not_quit(menu):
    term = session(menu, rows=24, cols=80)
    term.start()
    term.wait_for("alpha")
    assert term.is_running
    term.close()
    assert not term.is_running


# --------------------------------------------------------------------------
# The width helper has to catch a real defect
# --------------------------------------------------------------------------


def test_width_helper_flags_a_line_wider_than_the_terminal(program):
    """The failure the harness exists to catch: content wider than the screen."""
    path = program("wide", DEMO_WIDE)
    with session(path, "100", rows=10, cols=80) as term:
        frame = term.wait_for("HEADER")
        # a real terminal wraps, so no *row* is over 80 cells ...
        assert all(display_width(line) <= 80 for line in frame.raw_lines)
        # ... but the harness knows row 1 continued onto row 2
        assert frame.overlong_lines() == [(1, 100)]
        with pytest.raises(AssertionError) as excinfo:
            frame.assert_within_width()
        assert "100" in str(excinfo.value)
        assert "80" in str(excinfo.value)


def test_width_helper_passes_on_a_line_that_exactly_fits(program):
    path = program("wide", DEMO_WIDE)
    with session(path, "80", rows=10, cols=80) as term:
        frame = term.wait_for("HEADER")
        assert frame.overlong_lines() == []
        # a row that fills the width is not a violation, but the terminal
        # cannot tell a fit from a truncation: the caller says which
        with pytest.raises(AssertionError):
            frame.assert_within_width()
        frame.assert_within_width(allow_full_width=True)
        assert frame.lines[1] == "X" * 80


def test_width_helper_passes_on_a_well_behaved_curses_app(menu):
    """No wrap and no row at the margin: the pass is provable, not vacuous."""
    with session(menu, rows=24, cols=80) as term:
        frame = term.wait_for("alpha")
        frame.assert_within_width()
        assert frame.overlong_lines() == []
        assert all(len(line) < 80 for line in frame.lines)


def test_dump_captured_frame_to_evidence(menu, tmp_path):
    with session(menu, rows=24, cols=80) as term:
        frame = term.wait_for("alpha")
        out = frame.dump(tmp_path / "evidence" / "overview-80x24.txt",
                         header="menu 80x24 overview")
        text = out.read_text()
        assert text.splitlines()[0] == "# menu 80x24 overview"
        assert "SIZE 24x80" in text
        assert "> alpha" in text


def test_a_curses_border_reaches_the_frame_as_box_drawing(program):
    """The frames S2 and S3 are judged through carry real pane borders."""
    path = program("border", DEMO_BORDER)
    with session(path, rows=10, cols=40) as term:
        frame = term.wait_for("PANE")
        frame.assert_contains("┌")
        frame.assert_contains("│")
        frame.assert_contains("┘")
        assert frame.line_with("┌").strip() == "┌" + "─" * 18 + "┐"
        assert frame.line_with("PANE").strip() == "│ PANE" + " " * 13 + "│"
        # the letters ncurses sends inside the graphics set must not reach the
        # text: "lqqqqk" / "x" is what dropping ESC ( 0 shows a judge
        frame.assert_not_contains("lqq")
        frame.assert_not_contains("qqj")
        term.send("q")


def test_the_width_helper_refuses_to_certify_a_curses_app_at_the_margin(program):
    """The guard that tells a working width helper from a disabled one.

    ncurses cursor-addresses every row rather than letting the terminal wrap,
    so no curses screen ever carries a wrap flag and `overlong_lines()` is
    empty whatever the app drew. A row that runs to the last column is either
    an exact fit or content ncurses clipped, and nothing the terminal can
    observe tells them apart — so the helper says so instead of passing.
    """
    path = program("edge", DEMO_EDGE)
    with session(path, rows=10, cols=40) as term:
        frame = term.wait_for("EDGE DEMO")
        assert frame.overlong_lines() == []
        with pytest.raises(AssertionError) as excinfo:
            frame.assert_within_width()
        assert "row 1" in str(excinfo.value)
        assert "truncat" in str(excinfo.value)
        # the caller can say which it is; nothing else can
        frame.assert_within_width(allow_full_width=True)
        term.send("q")


DEMO_ENV = r'''
import os
import sys

print("TERM=" + os.environ.get("TERM", "unset"))
print("COLUMNS=" + os.environ.get("COLUMNS", "unset"))
print("LINES=" + os.environ.get("LINES", "unset"))
print("ESCDELAY=" + os.environ.get("ESCDELAY", "unset"))
sys.exit(0)
'''

DEMO_REFUSES = r'''
import sys

sys.stderr.write("relay-control: no .relay directory here\n")
sys.exit(2)
'''


def test_term_is_set_and_can_be_overridden(program):
    path = program("env", DEMO_ENV)
    with session(path, rows=10, cols=60) as term:
        frame = term.wait_for("TERM=")
        assert frame.line_with("TERM=") == "TERM=xterm-256color"
        assert frame.line_with("COLUMNS=") == "COLUMNS=unset"
        assert frame.line_with("LINES=") == "LINES=unset"
        assert frame.line_with("ESCDELAY=") == "ESCDELAY=25"
    with TerminalSession([sys.executable, path], rows=10, cols=60,
                         term="dumb") as term:
        assert term.wait_for("TERM=").line_with("TERM=") == "TERM=dumb"


def test_a_program_that_exits_at_once_leaves_its_message_in_the_frame(program):
    """The shape ACC-ROBUST-004 needs: a message, not a traceback, plus rc."""
    path = program("refuses", DEMO_REFUSES)
    with session(path, rows=10, cols=80) as term:
        frame = term.wait_for("relay-control")
        assert frame.lines[0] == "relay-control: no .relay directory here"
        frame.assert_not_contains("Traceback")
        assert term.wait(timeout=5) == 2


# --------------------------------------------------------------------------
# send() has to synchronise with the child
#
# A frame handed to a judge must be the screen the program drew *after* the
# keystroke. The defect these pin: send() wrote the keys and returned whatever
# was on screen once the pty had been quiet for a moment, so the frame could
# predate the keystroke entirely — and every assertion made on it, especially
# every negative one ("no traceback", "nothing changed", "no line too wide"),
# passed without the program having processed the key at all.
#
# Each test below fails against a harness that only drains: the program is
# deliberately slower than any quiet-period heuristic.
# --------------------------------------------------------------------------


def test_send_waits_for_the_program_to_read_the_keys(slow):
    """The frame must not predate the keystroke it is supposed to show."""
    with session(slow, "late-read", "0.5", rows=10, cols=60) as term:
        term.wait_for("READY")
        frame = term.send("x")
        assert frame.contains("AFTER-KEY"), "send() returned a pre-keystroke frame"
        assert not frame.contains("READY")


def test_send_waits_for_a_repaint_that_starts_late(slow):
    """Reading the key is not drawing it: the repaint has to be waited for too."""
    with session(slow, "late-paint", "0.3", rows=10, cols=60) as term:
        term.wait_for("READY")
        frame = term.send("x")
        assert frame.contains("AFTER-KEY"), "send() returned a pre-repaint frame"


def test_send_takes_the_text_to_wait_for(slow):
    """`expect=` is the strongest form: a positive signal with no time limit but
    its own, and a loud failure instead of a stale frame. What it is *not* is a
    guarantee the screen was finished — see the paint-end tests below."""
    with session(slow, "late-paint", "0.9", rows=10, cols=60) as term:
        term.wait_for("READY")
        frame = term.send("x", expect="AFTER-KEY")
        assert frame.line_with("AFTER-KEY") == "AFTER-KEY 'x'"


def test_send_expect_fails_with_the_frame_it_actually_got(slow):
    with session(slow, "late-paint", "0.1", rows=10, cols=60) as term:
        term.wait_for("READY")
        with pytest.raises(AssertionError) as excinfo:
            term.send("x", expect="NEVER-DRAWN", timeout=1.0)
        message = str(excinfo.value)
        assert "NEVER-DRAWN" in message
        assert "AFTER-KEY" in message  # the real screen is in the failure


def test_send_fails_when_the_program_never_reads_its_input(slow):
    """A wedged TUI is a finding, not a frame."""
    with session(slow, "deaf", "0", rows=10, cols=60) as term:
        term.wait_for("READY")
        with pytest.raises(AssertionError) as excinfo:
            term.send("x", timeout=0.5)
        assert "has not read" in str(excinfo.value)


def test_run_frames_synchronises_every_step(slow):
    """The one-shot judge API inherits the synchronisation."""
    frames = run_frames(
        [sys.executable, slow, "late-read", "0.4"],
        keys=["x"],
        rows=10,
        cols=60,
        wait_for="READY",
    )
    assert len(frames) == 2
    assert frames[0].contains("READY")
    assert frames[1].contains("AFTER-KEY")


def test_run_frames_takes_the_text_a_step_should_reach(slow):
    """A step given as `(keys, expect)` waits for the paint it names."""
    frames = run_frames(
        [sys.executable, slow, "late-paint", "0.9"],
        keys=[("x", "AFTER-KEY")],
        rows=10,
        cols=60,
        wait_for="READY",
    )
    assert frames[1].line_with("AFTER-KEY") == "AFTER-KEY 'x'"


def test_a_write_that_precedes_the_read_is_not_taken_for_the_answer(noisy):
    """Delivery is not the answer: what a program writes *before* it reads the
    keys cannot be its response to them.

    The program flushes an earlier repaint while the key is still queued, and
    only then reads it — TIOCOUTQ makes that order a fact rather than a race.
    A barrier that ends its wait on "a byte arrived in the same slice as the
    read" takes that flush for the answer, collapses the response window and
    hands back the pre-keystroke screen.
    """
    with session(noisy, "before-read", "0.4", rows=10, cols=60) as term:
        term.wait_for("READY")
        frame = term.send("x")
        assert frame.contains("AFTER-KEY"), (
            "send() took the program's own earlier output for the answer and "
            "returned the pre-keystroke frame"
        )
        assert not frame.contains("OLD-PAINT")


def test_expect_returns_a_frame_the_program_had_finished_writing(noisy):
    """The needle triggers the capture; it is not the capture.

    The program paints its screen region by region, and the region carrying the
    text the caller waits for goes out first. A capture taken the instant that
    text lands passes on the needle and shows the previous screen everywhere
    else.
    """
    with session(noisy, "regions", "0.04", rows=10, cols=60) as term:
        term.wait_for("READY")
        frame = term.send("x", expect="PANE header")
        assert frame.contains("BODY middle"), (
            "expect= returned a half-painted screen:\n%s" % frame.text
        )
        assert frame.contains("FOOT bottom"), (
            "expect= returned a half-painted screen:\n%s" % frame.text
        )


def test_expect_does_not_return_the_pre_keystroke_screen(noisy):
    """Text that is already on screen must not end the wait on its own.

    A pane heading survives the repaint under it, so `expect=` naming it is
    satisfied before the program has drawn anything at all. The frame returned
    still has to be the one drawn after the keystroke.
    """
    with session(noisy, "title", "0.05", rows=10, cols=60) as term:
        term.wait_for("TITLE pane")
        frame = term.send("x", expect="TITLE pane")
        assert frame.contains("BODY two"), (
            "expect= returned the pre-keystroke screen:\n%s" % frame.text
        )
        assert not frame.contains("BODY one")


# --------------------------------------------------------------------------
# When is a repaint over?
#
# `expect=` used to be documented as "the sound form" on the strength of a
# quiet window: wait until the program has not written for `paint` seconds and
# call the screen finished. A program that pauses longer than that inside one
# repaint is quiet and half-painted at the same instant, and the frame then
# shows neither the screen it had nor the screen it is drawing — every
# *negative* assertion passes on it for the wrong reason.
#
# No observation a terminal can make separates a pause from an ending. So the
# tests below pin three things instead: the two endings that *are* proof, the
# one that is a guess and is labelled as one, and where the guess's boundary
# is — the boundary, not the constant, so that moving `paint` moves it.
# --------------------------------------------------------------------------


def test_a_pause_longer_than_the_quiet_window_is_not_claimed_as_the_end(noisy):
    """The judge's reproduction, at the defaults, with nothing hidden.

    Clear the screen, draw the surviving title, paint the body half a second
    later: the frame `expect=` hands back has neither the old body nor the new
    one. The screen really was like that — a terminal draws what it is sent —
    so what has to be true is that the harness does not *claim* the program
    had finished it.
    """
    with session(noisy, "regions", "0.5", rows=10, cols=60) as term:
        term.wait_for("READY")
        frame = term.send("x", expect="PANE header")
        assert frame.contains("PANE header")
        assert not frame.contains("BODY middle")        # torn, and admitted
        assert frame.paint_end == PAINT_QUIET
        assert frame.paint_finished is False
        with pytest.raises(AssertionError) as excinfo:
            frame.assert_finished()
        message = str(excinfo.value)
        assert "not proof" in message
        assert "PANE header" in message                 # the screen came too
        # and the provenance travels with every other failure it reports
        with pytest.raises(AssertionError) as other:
            frame.assert_contains("BODY middle")
        assert "captured on quiet" in str(other.value)


def test_the_quiet_window_is_a_boundary_and_it_moves(noisy):
    """One child, two windows: the pause is short or long *relative to* it.

    Pinning the boundary rather than the constant is the point. The same
    program, pausing the same 0.3s inside its repaint, is torn under a 0.1s
    window and whole under a 0.6s one — so a caller whose TUI pauses knows
    which knob to turn, and a later change to the default cannot make this
    test pass for a new reason.
    """
    with session(noisy, "regions", "0.3", rows=10, cols=60,
                 paint=0.1, redraw=3.0) as term:
        term.wait_for("READY")
        torn = term.send("x", expect="PANE header")
    assert not torn.contains("BODY middle")

    with session(noisy, "regions", "0.3", rows=10, cols=60,
                 paint=0.1, redraw=3.0) as term:
        term.wait_for("READY")
        whole = term.send("x", expect="PANE header", quiet=0.6)
    assert whole.contains("BODY middle"), whole.text
    assert whole.contains("FOOT bottom"), whole.text
    assert whole.paint_end == PAINT_QUIET       # still a guess, just a wider one
    assert whole.paint_finished is False


def test_a_bracketed_repaint_is_waited_out_however_long_it_pauses(noisy):
    """DEC 2026 turns the guess into a statement, and the wait obeys it.

    The same region-by-region repaint, pausing 0.3s twice, under a 0.05s quiet
    window that would end the wait five times over. The program said where the
    repaint ends, so that — not the silence — is where it ends.
    """
    with session(noisy, "sync-regions", "0.3", rows=10, cols=60,
                 paint=0.05, redraw=3.0) as term:
        term.wait_for("READY")
        frame = term.send("x", expect="PANE header")
    assert frame.contains("BODY middle"), frame.text
    assert frame.contains("FOOT bottom"), frame.text
    assert frame.paint_end == PAINT_SYNCHRONISED
    assert frame.paint_finished is True
    frame.assert_finished()


def test_a_bracket_closed_before_the_key_was_read_does_not_end_the_wait(noisy):
    """Proof of what, exactly? A 2026 bracket is proof about *one* repaint.

    This program closes a whole bracketed repaint while the key is still in
    the input queue — so, by the same argument the delivery barrier rests on,
    that repaint cannot be the answer to the key. Crediting it would end the
    wait before the program had drawn anything and stamp the frame with the
    strongest label the harness has.
    """
    with session(noisy, "sync-stale", "0.3", rows=10, cols=60,
                 redraw=3.0) as term:
        term.wait_for("READY")
        frame = term.send("x", expect="PANE header", quiet=0.5)
    assert frame.paint_end == PAINT_QUIET, (
        "a bracket that closed before the key was read was taken for the "
        "answer to it"
    )
    assert frame.contains("BODY middle"), frame.text


def test_a_resize_carries_the_same_boundary_and_the_same_knob(program):
    """A signal leaves nothing in the input queue, so `expect=` is all there is.

    Which makes the quiet window behind `expect=` load-bearing here in a way it
    is not for a keystroke, and the knob has to reach it.
    """
    path = program("slowresize", DEMO_SLOW_RESIZE)
    with session(path, "0.3", rows=10, cols=60, paint=0.1, redraw=3.0) as term:
        term.wait_for("READY")
        torn = term.resize(12, 70, expect="SIZE pane")
        assert not torn.contains("BODY resized")
        assert torn.paint_end == PAINT_QUIET
        term.read(settle=0.6)          # let that repaint finish before the next
        whole = term.resize(14, 72, expect="SIZE pane", quiet=0.6)
        assert whole.contains("BODY resized"), whole.text


def test_expect_refuses_a_screen_the_program_had_not_stopped_writing(noisy):
    """The one case a terminal is certain about is a failure, not a frame.

    A program still writing when the window runs out has definitely handed
    over part of a repaint. Returning it as the finished screen was the same
    false pass by another route.
    """
    with session(noisy, "chatty", "0.05", rows=10, cols=60,
                 redraw=0.6) as term:
        term.wait_for("READY")
        with pytest.raises(AssertionError) as excinfo:
            term.send("x", expect="PANE header")
        message = str(excinfo.value)
        assert "still writing" in message
        assert "PANE header" in message              # with the screen it got
        assert "redraw=" in message                  # and what to do about it


def test_a_program_that_has_exited_proves_the_screen_it_left(program):
    """Nothing more can arrive, so the screen is whatever it left behind."""
    path = program("refuses", DEMO_REFUSES)
    with session(path, rows=10, cols=80) as term:
        frame = term.wait_for("relay-control")
        assert frame.paint_end == PAINT_EXITED
        assert frame.paint_finished is True
        frame.assert_finished()


def test_a_frame_nobody_waited_on_makes_no_claim(menu):
    """A frame taken without waiting for the end of a repaint says so."""
    with session(menu, rows=24, cols=80) as term:
        term.wait_for("alpha")
        assert term.frame().paint_end is None
        moved = term.send("<Down>")               # no expect=, no claim
        assert moved.paint_end is None
        assert moved.paint_finished is False
        with pytest.raises(AssertionError) as excinfo:
            moved.assert_finished()
        assert "not captured by waiting" in str(excinfo.value)


def test_a_crashed_program_leaves_the_screen_it_drew(program):
    """`?1049l` on the way out is not allowed to be the end of the evidence.

    curses.wrapper runs endwin() before it re-raises, which restores the
    primary screen. The traceback is on the frame; the screen the program was
    showing when it fell over is not, and that is the half a judge needs.
    """
    path = program("crash", DEMO_CRASH)
    with session(path, rows=10, cols=60) as term:
        term.wait_for("PANE the app drew")
        term.send_bytes(b"x")
        assert term.wait(timeout=5) == 1
        after = term.frame()
        assert after.contains("the app fell over")   # the traceback is there
        assert not after.contains("PANE the app drew")
        drew = term.last_alt_frame()
        assert drew is not None
        assert drew.contains("PANE the app drew")
        assert drew.contains("STATUS running")
        assert drew.rows == 10 and drew.cols == 60


def test_a_write_that_fails_comes_with_the_screen(slow):
    """A failure without the screen is nearly unusable in a judge's report.

    Every other failure path here attaches the frame; the write did not, and a
    bare OSError says nothing about what the program was showing.
    """
    with session(slow, "late-read", "0.1", rows=10, cols=60) as term:
        frame = term.wait_for("READY")
        assert frame.contains("READY")
        real = term.master_fd
        unwritable = os.open(os.devnull, os.O_RDONLY)
        term.master_fd = unwritable
        try:
            with pytest.raises(AssertionError) as excinfo:
                term.send("x")
        finally:
            term.master_fd = real
            os.close(unwritable)
        message = str(excinfo.value)
        assert "could not write" in message
        assert "READY" in message


def test_synchronising_does_not_slow_a_program_that_answers_at_once(menu):
    """The wait is positive, not a sleep: a prompt app pays only its own latency.

    It also pins the other half of that: the delivery barrier must leave the
    program's answer in the pty for the response wait to find. A barrier that
    swallows the repaint while waiting turns every keystroke into the full
    `redraw` window — 12 keys took 9.6s when it did, and 3.9-6.5s when it only
    does so when the timing falls that way. The measured cost of a keystroke
    here is ~0.08s (the `idle` settle), so 12 of them run in about 1.0s.
    """
    import time as _time

    with session(menu, rows=24, cols=80) as term:
        term.wait_for("alpha")
        started = _time.monotonic()
        for _ in range(6):
            term.send("<Down>")
            term.send("<Up>")
        elapsed = _time.monotonic() - started
    assert elapsed < 2.5, "12 synchronised keystrokes took %.1fs" % elapsed


def test_resize_then_quit_restores_the_primary_screen(program):
    """ACC-ROBUST-003 and ACC-NAV-005 meet here.

    A curses app takes the alternate screen, is resized while it holds it, and
    must hand back the screen it was given when it quits.
    """
    path = program("altscreen", DEMO_ALT_SCREEN)
    with session(path, rows=48, cols=160) as term:
        term.wait_for("APP-SCREEN 48x160")
        term.resize(24, 80)
        frame = term.wait_for("APP-SCREEN 24x80")
        frame.assert_within_width()
        assert not frame.contains("SHELL-LINE")  # still on the alternate screen
        term.send("q")
        assert term.wait(timeout=5) == 0
        frame = term.frame()
        assert frame.contains("SHELL-LINE"), "the primary screen was not restored"
        assert not frame.contains("APP-SCREEN")
        assert term.initial_attrs == term.termios_attrs()


# --------------------------------------------------------------------------
# Headless behaviour
# --------------------------------------------------------------------------


def test_harness_works_with_no_controlling_terminal(menu):
    """Runs the size test again in a subprocess whose stdio is a pipe."""
    script = (
        "import sys;"
        "sys.path.insert(0, %r);"
        "from frame import TerminalSession;"
        "t = TerminalSession([%r, %r], rows=30, cols=90);"
        "t.start();"
        "f = t.wait_for('SIZE');"
        "t.close();"
        "print(f.line_with('SIZE').strip())"
        % (str(Path(__file__).resolve().parent), sys.executable, menu)
    )
    env = dict(os.environ)
    env.pop("COLUMNS", None)
    env.pop("LINES", None)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "SIZE 30x90"

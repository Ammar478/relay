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
        frame.assert_within_width()
        assert frame.lines[1] == "X" * 80


def test_width_helper_passes_on_a_well_behaved_curses_app(menu):
    with session(menu, rows=24, cols=80) as term:
        frame = term.wait_for("alpha")
        frame.assert_within_width()
        assert frame.overlong_lines() == []


def test_dump_captured_frame_to_evidence(menu, tmp_path):
    with session(menu, rows=24, cols=80) as term:
        frame = term.wait_for("alpha")
        out = frame.dump(tmp_path / "evidence" / "overview-80x24.txt",
                         header="menu 80x24 overview")
        text = out.read_text()
        assert text.splitlines()[0] == "# menu 80x24 overview"
        assert "SIZE 24x80" in text
        assert "> alpha" in text


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
    """`expect=` is the sound form: a positive signal with no time limit but its
    own, and a loud failure instead of a stale frame."""
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


def test_synchronising_does_not_slow_a_program_that_answers_at_once(menu):
    """The wait is positive, not a sleep: a prompt app pays only its own latency."""
    import time as _time

    with session(menu, rows=24, cols=80) as term:
        term.wait_for("alpha")
        started = _time.monotonic()
        for _ in range(6):
            term.send("<Down>")
            term.send("<Up>")
        elapsed = _time.monotonic() - started
    assert elapsed < 6.0, "12 synchronised keystrokes took %.1fs" % elapsed


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

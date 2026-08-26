"""Tests for the attribute plane of the frame-capture harness (tests/frame.py).

The text plane answers "what does the screen say"; the attribute plane answers
"how was it drawn". A judge needs both: a status dashboard tells a failed check
from a passed one partly by colour, and a text-only frame cannot show that.

Same two layers as `test_frame.py`:

* Screen unit tests feed raw escape sequences straight into the emulator. Every
  one is written so that an implementation which consumed and discarded SGR —
  the behaviour before this file existed — gives a *different* answer.
* Session tests run real curses programs under a pty and read the colours
  ncurses actually emitted.

Nothing here resolves a colour to RGB. `fg`/`bg` are the SGR parameters as the
program sent them: `(32,)` is "SGR 32", `(38, 5, 214)` is "SGR 38;5;214".
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frame import (  # noqa: E402
    DEFAULT_ATTRS,
    CellAttrs,
    Frame,
    Screen,
    TerminalSession,
)

# --------------------------------------------------------------------------
# demo programs
# --------------------------------------------------------------------------

# Three colour pairs on one line, plus bold, reverse and a plain row. The
# single default-attributed space between the coloured words is what makes the
# line three *spans* rather than one.
DEMO_COLOURS = r'''
import curses
import sys


def main(stdscr):
    curses.curs_set(0)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    stdscr.addstr(0, 0, "PASS", curses.color_pair(1))
    stdscr.addstr(0, 5, "FAIL", curses.color_pair(2))
    stdscr.addstr(0, 10, "WARN", curses.color_pair(3))
    stdscr.addstr(2, 0, "BOLDROW", curses.A_BOLD)
    stdscr.addstr(3, 0, "REVROW", curses.A_REVERSE)
    stdscr.addstr(4, 0, "PLAINROW")
    stdscr.refresh()
    while True:
        if stdscr.getch() in (ord("q"), ord("Q")):
            break


curses.wrapper(main)
sys.exit(0)
'''

# The ACC-TUI-006 shape: every status glyph in its own colour pair.
DEMO_STATUS = r'''
import curses
import sys


def main(stdscr):
    curses.curs_set(0)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    glyphs = [
        ("✓", "completed", 1),
        ("●", "running", 2),
        ("○", "pending", 3),
        ("✗", "failed", 4),
        ("−", "cancelled", 5),
    ]
    for i, (glyph, name, pair) in enumerate(glyphs):
        stdscr.addstr(i, 0, glyph, curses.color_pair(pair))
        stdscr.addstr(i, 2, name)
    stdscr.refresh()
    while True:
        if stdscr.getch() in (ord("q"), ord("Q")):
            break


curses.wrapper(main)
sys.exit(0)
'''

# A child that never emits a single SGR sequence.
DEMO_NO_SGR = r'''
import os
import sys
import termios
import tty

fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
try:
    sys.stdout.write("\x1b[2J\x1b[HMONOCHROME")
    sys.stdout.write("\x1b[3;1HSECOND LINE")
    sys.stdout.flush()
    os.read(fd, 1)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
'''

# SGR parameters this harness does not model, mixed in with ones it does.
DEMO_ODD_SGR = r'''
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
    out.write("\x1b[1;73;99;38;5;214mODD\x1b[0m")
    out.write("\x1b[3;1H\x1b[38;2;10;20;30mTRUECOLOR\x1b[m")
    out.flush()
    os.read(fd, 1)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
'''


@pytest.fixture
def program(tmp_path):
    """Write one of the demo programs to disk and return its path."""

    def _write(name, source):
        path = tmp_path / (name + ".py")
        path.write_text(source)
        return str(path)

    return _write


def session(path, *args, **kwargs):
    return TerminalSession([sys.executable, path, *args], **kwargs)


def feed(data, rows=5, cols=20):
    screen = Screen(rows, cols)
    screen.feed(data)
    return screen


# --------------------------------------------------------------------------
# Screen: SGR is recorded, not discarded
# --------------------------------------------------------------------------


def test_text_plane_is_unchanged_by_the_attribute_plane():
    """The guard on the whole leg: SGR still never reaches the text."""
    screen = feed("\x1b[1;7;38;5;214mBOLD\x1b[0m\x1b[mPLAIN")
    assert screen.lines()[0].rstrip() == "BOLDPLAIN"
    assert "\x1b" not in screen.lines()[0]


def test_three_colour_runs_on_one_line_are_three_spans():
    # discard-the-attributes: one run covering the whole line
    screen = feed("\x1b[32mPASS\x1b[0m \x1b[31mFAIL\x1b[0m \x1b[33mWARN\x1b[0m")
    frame = screen.frame()
    runs = frame.attr_runs(0)
    assert [run.text for run in runs] == ["PASS", " ", "FAIL", " ", "WARN"]
    assert [run.attrs.fg for run in runs] == [(32,), None, (31,), None, (33,)]
    assert runs[1].attrs.is_default
    assert len({runs[0].attrs, runs[2].attrs, runs[4].attrs}) == 3


def test_run_with_finds_the_run_containing_a_substring():
    screen = feed("\x1b[32mPASS\x1b[0m \x1b[31mFAIL\x1b[0m")
    frame = screen.frame()
    run = frame.run_with("FAIL")
    assert (run.row, run.start, run.end, run.text) == (0, 5, 9, "FAIL")
    assert run.attrs.fg == (31,)
    assert frame.attrs_for("PASS").fg == (32,)
    frame.assert_attrs_differ("PASS", "FAIL")


def test_attrs_at_reports_the_cell_that_was_drawn():
    screen = feed("ab\x1b[7mcd")
    frame = screen.frame()
    assert frame.attrs_at(0, 1).is_default
    assert frame.attrs_at(0, 2).reverse is True
    assert frame.attrs_at(0, 3).reverse is True
    # a cell nothing was written to is default, not the last SGR state
    assert frame.attrs_at(0, 10) == DEFAULT_ATTRS


def test_bold_and_reverse_are_recorded_separately():
    screen = feed("\x1b[1mB\x1b[7mBR\x1b[22mR\x1b[27mN")
    frame = screen.frame()
    assert frame.attrs_at(0, 0).flags == frozenset({"bold"})
    assert frame.attrs_at(0, 1).flags == frozenset({"bold", "reverse"})
    assert frame.attrs_at(0, 3).flags == frozenset({"reverse"})
    assert frame.attrs_at(0, 4).flags == frozenset()
    assert frame.attrs_at(0, 0).bold is True
    assert frame.attrs_at(0, 0).reverse is False


def test_sgr_reset_returns_cells_to_default():
    for reset in ("\x1b[0m", "\x1b[m"):
        screen = feed("\x1b[1;31;44mHOT" + reset + "COLD")
        frame = screen.frame()
        hot = frame.attrs_for("HOT")
        assert (hot.fg, hot.bg, hot.flags) == ((31,), (44,), frozenset({"bold"}))
        assert frame.attrs_for("COLD") == DEFAULT_ATTRS
        assert frame.attrs_for("COLD").is_default


def test_colours_are_recorded_as_given_and_never_normalised():
    screen = feed(
        "\x1b[38;5;214mIDX\x1b[0m"
        "\x1b[91mBRIGHT\x1b[0m"
        "\x1b[38;2;10;20;30mRGB\x1b[0m"
    )
    frame = screen.frame()
    # 256-colour stays 38;5;214 — not turned into an RGB triple
    assert frame.attrs_for("IDX").fg == (38, 5, 214)
    # bright red stays 91 — not rewritten as 38;5;9
    assert frame.attrs_for("BRIGHT").fg == (91,)
    assert frame.attrs_for("RGB").fg == (38, 2, 10, 20, 30)


def test_colon_and_semicolon_extended_colour_agree():
    semi = feed("\x1b[38;5;214mX").frame().attrs_at(0, 0)
    colon = feed("\x1b[38:5:214mX").frame().attrs_at(0, 0)
    assert semi.fg == colon.fg == (38, 5, 214)


def test_colour_index_zero_is_a_colour_and_not_an_absent_parameter():
    """`0` is the number zero, everywhere a parameter is read.

    Palette entry 0 is a colour a pane background is plausibly drawn with, and
    an all-zero parameter treated as "no parameter" drops it out of the tuple —
    `(38, 5)` instead of `(38, 5, 0)` — so the cell records a colour nobody
    could have asked for.
    """
    assert feed("\x1b[38:5:0mX").frame().attrs_at(0, 0).fg == (38, 5, 0)
    assert feed("\x1b[48:5:0mX").frame().attrs_at(0, 0).bg == (48, 5, 0)
    assert feed("\x1b[38;5;0mX").frame().attrs_at(0, 0).fg == (38, 5, 0)
    assert feed("\x1b[38:2:0:0:0mX").frame().attrs_at(0, 0).fg == (38, 2, 0, 0, 0)
    itu = feed("\x1b[38:2::10:20:30mX").frame().attrs_at(0, 0)
    assert itu.fg == (38, 2, 10, 20, 30)


def test_default_colour_parameters_clear_the_colour():
    screen = feed("\x1b[31;44mC\x1b[39mF\x1b[49mN")
    frame = screen.frame()
    assert (frame.attrs_at(0, 0).fg, frame.attrs_at(0, 0).bg) == ((31,), (44,))
    assert (frame.attrs_at(0, 1).fg, frame.attrs_at(0, 1).bg) == (None, (44,))
    assert frame.attrs_at(0, 2) == DEFAULT_ATTRS


def test_unrecognised_sgr_parameter_is_tolerated_and_kept():
    screen = feed("\x1b[1;99mODD\x1b[0mPLAIN")
    frame = screen.frame()
    assert screen.lines()[0].rstrip() == "ODDPLAIN"
    odd = frame.attrs_for("ODD")
    assert odd.bold is True            # the parameters it does know still apply
    assert odd.other == frozenset({99})  # and the one it does not is recorded
    assert frame.attrs_for("PLAIN").is_default


def test_a_lone_unknown_final_byte_does_not_disturb_attributes():
    # CSI sequences that are not SGR must not touch the attribute state
    screen = feed("\x1b[31m\x1b[?25l\x1b[6nRED")
    assert screen.frame().attrs_for("RED").fg == (31,)


def test_repeat_uses_the_current_attributes():
    screen = feed("\x1b[32mA\x1b[3b")
    frame = screen.frame()
    assert frame.lines[0].rstrip() == "AAAA"
    runs = frame.attr_runs(0)
    assert len(runs) == 1
    assert runs[0].text == "AAAA"
    assert runs[0].attrs.fg == (32,)


# --------------------------------------------------------------------------
# Screen: the attribute plane survives everything the text plane survives
# --------------------------------------------------------------------------


def test_attributes_follow_absolute_cursor_addressing():
    screen = feed("\x1b[32mGREEN\x1b[3;5H\x1b[31mRED")
    frame = screen.frame()
    assert frame.attrs_for("GREEN").fg == (32,)
    assert frame.attrs_for("RED").fg == (31,)
    assert frame.run_with("RED").start == 4


def test_overwriting_a_cell_replaces_its_attributes():
    # discard-the-attributes cannot tell these two screens apart
    screen = feed("\x1b[31mAAAA\x1b[1;1H\x1b[32mB")
    frame = screen.frame()
    assert frame.lines[0].rstrip() == "BAAA"
    assert frame.attrs_at(0, 0).fg == (32,)
    assert frame.attrs_at(0, 1).fg == (31,)


def test_erase_line_clears_attributes_to_the_current_background():
    """Back-colour erase: an erased cell keeps the background, nothing else."""
    screen = feed("\x1b[1;31;44mHELLO\x1b[1;3H\x1b[0K")
    frame = screen.frame()
    assert frame.lines[0].rstrip() == "HE"
    kept = frame.attrs_at(0, 0)
    assert (kept.fg, kept.bg, kept.flags) == ((31,), (44,), frozenset({"bold"}))
    erased = frame.attrs_at(0, 3)
    assert erased.bg == (44,)          # the background survives the erase
    assert erased.fg is None           # the foreground does not
    assert erased.flags == frozenset()  # nor do bold/reverse/underline


def test_erase_with_a_default_background_restores_default_cells():
    screen = feed("\x1b[1;31;44mHELLO\x1b[0m\x1b[2J")
    frame = screen.frame()
    assert frame.text.strip() == ""
    assert all(
        frame.attrs_at(row, col) == DEFAULT_ATTRS
        for row in range(frame.rows)
        for col in range(frame.cols)
    )


def test_erase_character_clears_attributes_without_shifting():
    screen = feed("\x1b[32mabcdef\x1b[1;2H\x1b[3X")
    frame = screen.frame()
    assert frame.lines[0].rstrip() == "a   ef"
    assert frame.attrs_at(0, 0).fg == (32,)
    assert frame.attrs_at(0, 2) == DEFAULT_ATTRS
    assert frame.attrs_at(0, 4).fg == (32,)


def test_attributes_survive_a_scroll():
    screen = feed(
        "\x1b[32mONE\r\n\x1b[31mTWO\r\n\x1b[0mTHREE\r\nFOUR\r\nFIVE\r\nSIX",
        rows=5,
        cols=10,
    )
    frame = screen.frame()
    # ONE scrolled off; TWO is now the top row and kept its colour
    assert frame.lines[0] == "TWO"
    assert frame.attrs_for("TWO").fg == (31,)
    assert frame.attrs_for("SIX").is_default


def test_attributes_survive_an_explicit_scroll_region():
    screen = feed(
        "\x1b[2;4r\x1b[1;1H\x1b[36mTOP\x1b[2;1H\x1b[32mA\r\n\x1b[31mB\r\nC\r\nD",
        rows=5,
        cols=10,
    )
    frame = screen.frame()
    assert [line.rstrip() for line in frame.lines[:5]] == ["TOP", "B", "C", "D", ""]
    assert frame.attrs_for("TOP").fg == (36,)   # outside the region, untouched
    assert frame.attrs_for("B").fg == (31,)     # scrolled up, colour intact


def test_attributes_survive_reverse_index():
    screen = feed("\x1b[32mA\r\n\x1b[31mB\x1b[1;1H\x1bM\x1b[0mZ", rows=4, cols=10)
    frame = screen.frame()
    assert [line.rstrip() for line in frame.lines[:3]] == ["Z", "A", "B"]
    assert frame.attrs_for("Z").is_default
    assert frame.attrs_for("A").fg == (32,)
    assert frame.attrs_for("B").fg == (31,)


def test_attributes_survive_insert_and_delete_line():
    screen = feed("\x1b[32mA\r\n\x1b[31mB\x1b[1;1H\x1b[L\x1b[0mZ", rows=4, cols=10)
    frame = screen.frame()
    assert [line.rstrip() for line in frame.lines[:3]] == ["Z", "A", "B"]
    assert frame.attrs_for("A").fg == (32,)
    screen.feed("\x1b[1;1H\x1b[M")
    frame = screen.frame()
    assert [line.rstrip() for line in frame.lines[:2]] == ["A", "B"]
    assert frame.attrs_for("A").fg == (32,)
    assert frame.attrs_for("B").fg == (31,)


def test_attributes_survive_insert_and_delete_character():
    screen = feed("\x1b[32mabc\x1b[31mdef\x1b[1;2H\x1b[2P")
    frame = screen.frame()
    assert frame.lines[0].rstrip() == "adef"
    assert frame.attrs_at(0, 0).fg == (32,)
    assert frame.attrs_at(0, 1).fg == (31,)   # "d" shifted left with its colour
    screen.feed("\x1b[1;2H\x1b[1@")
    frame = screen.frame()
    assert frame.lines[0].rstrip() == "a def"
    assert frame.attrs_at(0, 1) == DEFAULT_ATTRS   # the hole is blank
    assert frame.attrs_at(0, 2).fg == (31,)


def test_insert_mode_shifts_attributes_with_the_text():
    screen = feed("\x1b[31mXYZ\x1b[1;1H\x1b[4h\x1b[32mA")
    frame = screen.frame()
    assert frame.lines[0].rstrip() == "AXYZ"
    assert frame.attrs_at(0, 0).fg == (32,)
    assert frame.attrs_at(0, 1).fg == (31,)


def test_attributes_survive_the_alternate_screen():
    screen = feed("\x1b[32mshell\x1b[?1049h\x1b[2J\x1b[1;1H\x1b[31mapp")
    frame = screen.frame()
    assert frame.lines[0] == "app"
    assert frame.attrs_for("app").fg == (31,)
    screen.feed("\x1b[?1049l")
    frame = screen.frame()
    assert frame.lines[0] == "shell"
    assert frame.attrs_for("shell").fg == (32,)


def test_the_saved_attribute_plane_survives_a_resize():
    """A SIGWINCH while the app holds the alternate screen must not cost the
    primary screen its colours either."""
    screen = feed("\x1b[32mshell\x1b[?1049h\x1b[2J\x1b[1;1H\x1b[31mapp")
    screen.resize(6, 24)
    screen.feed("\x1b[?1049l")
    frame = screen.frame()
    assert frame.lines[0] == "shell"
    assert frame.attrs_for("shell").fg == (32,)
    assert len(frame.attrs) == 6
    assert all(len(row) == 24 for row in frame.attrs)


def test_reset_clears_the_attribute_plane():
    screen = feed("\x1b[7mREV\x1bcPLAIN")
    frame = screen.frame()
    assert frame.lines[0].rstrip() == "PLAIN"
    assert frame.attrs_for("PLAIN").is_default


def test_resize_keeps_the_attributes_of_the_kept_corner():
    screen = feed("\x1b[32mGREEN", rows=5, cols=20)
    screen.resize(4, 10)
    frame = screen.frame()
    assert frame.lines[0] == "GREEN"
    assert frame.attrs_for("GREEN").fg == (32,)
    assert len(frame.attrs) == 4
    assert all(len(row) == 10 for row in frame.attrs)


def test_wide_characters_keep_columns_and_attributes_aligned():
    screen = feed("\x1b[32m日本\x1b[31mAB")
    frame = screen.frame()
    assert frame.lines[0].rstrip() == "日本AB"
    assert frame.attrs_at(0, 0).fg == (32,)
    assert frame.attrs_at(0, 1).fg == (32,)   # the wide char's second cell
    assert frame.attrs_at(0, 4).fg == (31,)
    assert frame.run_with("AB").start == 4


# --------------------------------------------------------------------------
# Frame helpers: diagnosable failures
# --------------------------------------------------------------------------


def test_a_frame_built_from_text_alone_says_so():
    frame = Frame(("alpha", "beta"), rows=2, cols=10)
    assert frame.attrs is None
    with pytest.raises(AssertionError) as excinfo:
        frame.attrs_at(0, 0)
    assert "attribute plane" in str(excinfo.value)
    with pytest.raises(AssertionError):
        frame.attr_runs(0)


def test_attrs_at_out_of_range_fails_with_the_frame():
    frame = feed("hi").frame()
    with pytest.raises(AssertionError) as excinfo:
        frame.attrs_at(99, 0)
    message = str(excinfo.value)
    assert "99" in message
    assert "hi" in message          # the frame itself is in the message


def test_run_with_missing_text_fails_with_the_frame():
    frame = feed("\x1b[32mPASS").frame()
    with pytest.raises(AssertionError) as excinfo:
        frame.run_with("NOPE")
    assert "NOPE" in str(excinfo.value)
    assert "PASS" in str(excinfo.value)


def test_run_with_reports_a_substring_that_spans_two_runs():
    frame = feed("\x1b[32mAB\x1b[31mCD").frame()
    with pytest.raises(AssertionError) as excinfo:
        frame.run_with("BC")
    message = str(excinfo.value)
    assert "BC" in message
    assert "fg=32" in message      # both runs are described ...
    assert "fg=31" in message      # ... so the reader can see the split


def test_assert_attrs_reports_what_was_actually_drawn():
    frame = feed("\x1b[1;32mPASS\x1b[0m \x1b[31mFAIL").frame()
    frame.assert_attrs("PASS", fg=32, has="bold")
    frame.assert_attrs("PASS", fg=(32,), bg=None, has=["bold"], lacks=["reverse"])
    frame.assert_attrs("FAIL", fg=31, lacks="bold")
    with pytest.raises(AssertionError) as excinfo:
        frame.assert_attrs("FAIL", fg=32, has="underline")
    message = str(excinfo.value)
    assert "fg=31" in message        # what it is
    assert "32" in message           # what was expected
    assert "underline" in message
    assert "FAIL" in message


def test_assert_attrs_differ_fails_when_two_things_look_the_same():
    frame = feed("\x1b[31mONE\x1b[0m \x1b[31mTWO").frame()
    with pytest.raises(AssertionError) as excinfo:
        frame.assert_attrs_differ("ONE", "TWO")
    assert "fg=31" in str(excinfo.value)
    frame = feed("\x1b[31mONE\x1b[0m \x1b[32mTWO").frame()
    frame.assert_attrs_differ("ONE", "TWO")


def test_attr_runs_trims_the_trailing_blank_padding():
    frame = feed("\x1b[32mX", rows=2, cols=20).frame()
    runs = frame.attr_runs(0)
    assert len(runs) == 1
    assert runs[0].text == "X"
    assert frame.attr_runs(1) == []


def test_cell_attrs_describe_itself_readably():
    attrs = CellAttrs(fg=(38, 5, 214), bg=(40,), flags=frozenset({"bold"}))
    text = attrs.describe()
    assert "38;5;214" in text
    assert "40" in text
    assert "bold" in text
    assert DEFAULT_ATTRS.describe() == "default"
    assert "38;5;214" in repr(attrs)


# --------------------------------------------------------------------------
# Assertions that could pass without asserting anything
#
# Every helper below had a way of reporting success while proving nothing: no
# expectation at all, a misspelt flag name, or a needle drawn two different
# ways in two places where only the first was ever looked at.
# --------------------------------------------------------------------------


def test_assert_attrs_with_nothing_to_assert_is_refused():
    screen = feed("\x1b[31;1mSTATUS")
    frame = screen.frame()
    with pytest.raises(ValueError) as excinfo:
        frame.assert_attrs("STATUS")
    assert "fg" in str(excinfo.value)
    frame.assert_attrs("STATUS", fg=31)          # still fine with a criterion


def test_assert_attrs_rejects_a_flag_name_it_does_not_know():
    """`lacks="bolt"` used to pass on anything at all."""
    screen = feed("\x1b[1mSTATUS")
    frame = screen.frame()
    for kwargs in ({"lacks": "bolt"}, {"has": "bolt"}, {"lacks": ["bold", "revrese"]}):
        with pytest.raises(ValueError) as excinfo:
            frame.assert_attrs("STATUS", **kwargs)
        assert "underline" in str(excinfo.value)   # the known names are listed
    frame.assert_attrs("STATUS", has="bold", lacks="reverse")


def test_run_with_refuses_a_needle_drawn_two_ways_on_two_rows():
    """A green STATUS on row 0 and a red one on row 2 is not one answer."""
    screen = feed("\x1b[32mSTATUS\x1b[0m\r\n\r\n\x1b[31mSTATUS")
    frame = screen.frame()
    with pytest.raises(AssertionError) as excinfo:
        frame.run_with("STATUS")
    message = str(excinfo.value)
    assert "row 0" in message and "row 2" in message
    with pytest.raises(AssertionError):
        frame.assert_attrs("STATUS", fg=32)
    # naming the row is how the caller says which one it means
    assert frame.run_with("STATUS", row=0).attrs.fg == (32,)
    frame.assert_attrs("STATUS", fg=32, row=0)
    frame.assert_attrs("STATUS", fg=31, row=2)


def test_run_with_refuses_a_needle_drawn_two_ways_on_one_row():
    screen = feed("\x1b[32mOK\x1b[0m--\x1b[31mOK")
    frame = screen.frame()
    with pytest.raises(AssertionError) as excinfo:
        frame.run_with("OK")
    assert "col" in str(excinfo.value)


def test_run_with_answers_when_every_copy_agrees():
    """Two panes drawing the same label the same way is not ambiguous."""
    screen = feed("\x1b[36mLEG\x1b[0m\r\n\x1b[36mLEG")
    frame = screen.frame()
    assert frame.run_with("LEG").row == 0
    frame.assert_attrs("LEG", fg=36)


# --------------------------------------------------------------------------
# TerminalSession: what ncurses actually emits
# --------------------------------------------------------------------------


def test_curses_colour_pairs_give_three_distinct_spans(program):
    path = program("colours", DEMO_COLOURS)
    with session(path, rows=10, cols=40) as term:
        frame = term.wait_for("PASS")
        assert frame.lines[0].rstrip() == "PASS FAIL WARN"
        runs = [run for run in frame.attr_runs(0) if run.text.strip()]
        assert [run.text for run in runs] == ["PASS", "FAIL", "WARN"]
        assert len({run.attrs for run in runs}) == 3
        assert all(not run.attrs.is_default for run in runs)
        # green / red / yellow, as the SGR parameters ncurses sent
        assert frame.attrs_for("PASS").fg == (32,)
        assert frame.attrs_for("FAIL").fg == (31,)
        assert frame.attrs_for("WARN").fg == (33,)
        frame.assert_attrs_differ("PASS", "FAIL")


def test_curses_bold_and_reverse_reach_the_attribute_plane(program):
    path = program("colours", DEMO_COLOURS)
    with session(path, rows=10, cols=40) as term:
        frame = term.wait_for("PLAINROW")
        frame.assert_attrs("BOLDROW", has="bold")
        frame.assert_attrs("REVROW", has="reverse", lacks="bold")
        # Plain text is not "default" here: once a program calls start_color()
        # without use_default_colors(), ncurses renders colour pair 0 as an
        # explicit white-on-black, and the harness records what was actually
        # sent rather than what the program meant.
        plain = frame.attrs_for("PLAINROW")
        assert plain.flags == frozenset()
        assert (plain.fg, plain.bg) == ((37,), (40,))
        frame.assert_attrs_differ("BOLDROW", "PLAINROW")


def test_status_glyphs_are_each_drawn_in_their_own_colour(program):
    """The ACC-TUI-006 shape: glyph text *and* glyph colour, from one frame."""
    path = program("status", DEMO_STATUS)
    with session(path, rows=10, cols=40) as term:
        frame = term.wait_for("completed")
        assert [line.rstrip() for line in frame.lines[:5]] == [
            "✓ completed",
            "● running",
            "○ pending",
            "✗ failed",
            "− cancelled",
        ]
        assert "�" not in frame.text
        assert "â" not in frame.text
        glyph_attrs = [frame.attrs_at(row, 0) for row in range(5)]
        assert len(set(glyph_attrs)) == 5           # five distinct colours
        assert all(not a.is_default for a in glyph_attrs)
        assert [a.fg for a in glyph_attrs] == [(32,), (36,), (37,), (31,), (33,)]
        # the labels beside the glyphs all share one style, and the glyph is
        # drawn differently from its own label
        labels = [frame.attrs_for(name) for name in
                  ("completed", "running", "pending", "failed", "cancelled")]
        assert len(set(labels)) == 1
        frame.assert_attrs_differ("✓", "completed")
        frame.assert_attrs_differ("✗", "failed")


def test_a_child_that_never_emits_sgr_gets_a_default_plane(program):
    path = program("nosgr", DEMO_NO_SGR)
    with session(path, rows=10, cols=40) as term:
        frame = term.wait_for("MONOCHROME")
        assert frame.lines[0] == "MONOCHROME"
        assert frame.attrs is not None
        assert all(
            frame.attrs_at(row, col) == DEFAULT_ATTRS
            for row in range(frame.rows)
            for col in range(frame.cols)
        )
        # the helpers still work; they just report "default" everywhere
        assert frame.attrs_for("MONOCHROME").is_default
        assert frame.attr_runs(0)[0].text == "MONOCHROME"
        frame.assert_attrs("SECOND LINE", fg=None, bg=None, lacks="bold")


def test_sgr_a_child_emits_that_the_harness_does_not_model_is_tolerated(program):
    path = program("odd", DEMO_ODD_SGR)
    with session(path, rows=10, cols=40) as term:
        frame = term.wait_for("ODD")
        assert frame.lines[0] == "ODD"
        assert frame.lines[2] == "TRUECOLOR"
        odd = frame.attrs_for("ODD")
        assert odd.bold is True
        assert odd.fg == (38, 5, 214)
        assert odd.other == frozenset({73, 99})
        assert frame.attrs_for("TRUECOLOR").fg == (38, 2, 10, 20, 30)

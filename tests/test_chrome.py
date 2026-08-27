"""Frame tests for the Relay Control chrome (scripts/relay_control/).

One test (or one class) per TUI check in `.relay/contract.md` that
`tui-skeleton` claims: ACC-TUI-001 header, -002 status bar, -003 the four-pane
Overview, -004 the keybar, -006 status glyphs and their colour pairs, -007 the
TUI reads no relay file of its own.

Every visual claim is asserted against a frame captured from a real curses
process running under a pty (`tests/frame.py`). Nothing here inspects the
program's internals: if a property is not visible on a captured screen it is
not asserted at all.

Two rules this file follows, both from `.relay/skills/`:

* Figures are read from the fixture's own files **at assert time**. The
  agent-service fixture has been refreshed once already and every hardcoded
  number in the contract had to be amended. `leg_figures()` is the only source
  of `done/total` here, and it parses `legs.json` itself rather than asking
  `relay_model.build()` — so a model that miscounts cannot certify itself.
* `send(keys, expect=NEEDLE)` names text the *repaint introduces*. Switching to
  the Legs view cannot wait on `"Legs"`: the Overview already draws a pane with
  that title, so the wait would return before the program had drawn anything.
  Each transition below waits on something only the destination view paints.
"""

import ast
import curses
import json
import locale
import os
import sys
import unicodedata
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRIPTS = REPO / "scripts"
PACKAGE = SCRIPTS / "relay_control"
ENTRY = PACKAGE / "__main__.py"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))

from frame import TerminalSession, display_width  # noqa: E402

import relay_model  # noqa: E402
from relay_control import app, chrome  # noqa: E402
from relay_control import theme as theme_tokens  # noqa: E402

# git-backed helpers, imported rather than copied: a commit subject is one of
# the four kinds of untrusted prose this file has to poison, and
# `test_relay_model.py` already owns "how a test makes a repository".
from test_relay_model import HAS_GIT, git_run  # noqa: E402

# The keybar is drawn on every view at every size, so it is the one needle that
# always means "the program has painted a screen".
READY = "q Quit"

WIDE = (48, 160)
STANDARD = (24, 80)

OVERVIEW_KEYS = "F Legs  W Runners  M Models  C Contract  Tab Next View  q Quit"

# Every glyph ACC-TUI-006 names, and the SGR foreground parameter its colour
# pair must reach the terminal as. ncurses emits 8-colour SGR on
# xterm-256color, so these are `(32,)` and not `(38, 5, 2)`.
GLYPH_COLOURS = {
    "✓": (32, (), ("dim",)),         # completed, green
    "●": (33, ("bold",), ("dim",)),  # running, yellow
    "○": (37, ("dim",), ("bold",)),  # pending, dim white
    "✗": (31, ("bold",), ("dim",)),  # failed, red
    "−": (34, ("dim",), ("bold",)),  # cancelled, dim blue
}

# Sequences a UTF-8 glyph decays into when something in the pipeline mishandles
# it. None of them may appear in any captured frame (ACC-TUI-006).
MOJIBAKE = ("Ã", "â", "Â", "�")


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------


def _utf8_env():
    """A child environment whose locale can encode the status glyphs.

    curses encodes what `addstr` is given with the process locale's codeset. In
    the POSIX locale that is US-ASCII, and every non-ASCII glyph is silently
    dropped to a blank — a screen that is wrong without erroring. The tests
    name a UTF-8 locale explicitly rather than inheriting the developer's, so
    that what they assert is the program's doing and not the shell's.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("LC_ALL", "LC_CTYPE", "LANG")}
    keep = locale.setlocale(locale.LC_CTYPE)
    try:
        for candidate in ("C.UTF-8", "en_US.UTF-8", "UTF-8"):
            try:
                locale.setlocale(locale.LC_CTYPE, candidate)
            except locale.Error:
                continue
            env["LC_ALL"] = candidate
            return env
    finally:
        locale.setlocale(locale.LC_CTYPE, keep)
    return env


UTF8_ENV = _utf8_env()


def session(relay_dir, size=WIDE, **kwargs):
    """A started TerminalSession running the TUI against `relay_dir`.

    Returns once the program has painted a whole screen, so the first frame a
    test takes is one the program finished rather than one caught mid-repaint.
    """
    rows, cols = size
    kwargs.setdefault("env", UTF8_ENV)
    term = TerminalSession(
        [sys.executable, str(ENTRY), str(relay_dir)],
        rows=rows, cols=cols, **kwargs,
    )
    term.start()
    term.wait_for(READY)
    return term


def frame_of(relay_dir, size=WIDE, **kwargs):
    """One finished frame of the TUI against `relay_dir`, at `size`."""
    term = session(relay_dir, size=size, **kwargs)
    try:
        return term.frame()
    finally:
        term.close()


#: A key nothing binds. The Overview has no `handle()` at all and this is in
#: neither the quit keys nor the view jumps, so the loop reads it, changes
#: nothing, and repaints — which is exactly one whole frame and no new content.
IDLE_KEY = "z"


def repaint(term, expect=READY):
    """A frame the program *proved* whole, off a keystroke that changes nothing.

    `session()` cannot hand one back: `TerminalSession.start()` has already
    read the first paint — bracket and all — by the time any wait takes its
    DEC 2026 baseline, so a `wait_for` straight after it can only end on the
    quiet window. Provoking a repaint is what puts a closed bracket *after*
    the baseline.

    The needle is text that survives the repaint, which on its own would be
    unsound — it is on screen before the program has drawn anything. Here it
    is only the trigger for the capture: the wait ends on the bracket, whose
    baseline is taken after the keys were read, so nothing but a repaint that
    post-dates the keystroke can end it. `paint_end == "synchronised"` is that
    statement, and it is the assertion doing the work, not the needle.
    """
    return term.send(IDLE_KEY, expect=expect)


def proved_frame_of(relay_dir, size=WIDE, **kwargs):
    """`frame_of`, but a frame the program stated was whole."""
    term = session(relay_dir, size=size, **kwargs)
    try:
        return repaint(term)
    finally:
        term.close()


def column_of(frame, needle, row=None):
    """The grid column `needle` starts at. Fails loudly when it is not drawn."""
    if row is None:
        row = frame.find(needle)
        if row is None:
            raise AssertionError(frame._message("no line contains %r" % needle))
    return frame.run_with(needle, row=row).start


def row_of(frame, needle):
    row = frame.find(needle)
    if row is None:
        raise AssertionError(frame._message("no line contains %r" % needle))
    return row


# --------------------------------------------------------------------------
# figures, read from the fixture at assert time
# --------------------------------------------------------------------------


def leg_figures(relay_dir):
    """`(done, total)` counted out of the fixture's own `legs.json`.

    Deliberately not `relay_model.build()`: the numbers the status bar claims
    are checked against the files, so a model that miscounted could not certify
    the view that draws its answer. Only the status vocabulary is shared, since
    "what does `done` mean" is one decision and not two.
    """
    path = Path(relay_dir) / "legs.json"
    if not path.exists():
        return 0, 0
    data = json.loads(path.read_text())
    legs = [leg for leg in data.get("legs", []) if isinstance(leg, dict)]
    done = sum(1 for leg in legs
               if relay_model.normalise_status(leg.get("status")) == "completed")
    return done, len(legs)


def dashboard(relay_dir):
    path = Path(relay_dir) / "dashboard.json"
    return json.loads(path.read_text()) if path.exists() else {}


def bar_fill(frame, row=1):
    """`(filled, width)` of the progress bar on `row`, measured off the screen.

    The bar's width is whatever the layout gave it at this terminal size, which
    is the point: no test here may know a width the program computed.
    """
    line = frame.lines[row]
    filled = line.count("█")
    empty = line.count("░")
    assert filled + empty > 0, frame._message("row %d draws no progress bar" % row)
    return filled, filled + empty


# --------------------------------------------------------------------------
# ACC-TUI-001 — header
# --------------------------------------------------------------------------


def test_the_header_names_the_relay_and_the_working_path():
    frame = frame_of(FIXTURES / "agent-service")
    title = dashboard(FIXTURES / "agent-service")["title"]
    header = frame.lines[0]
    assert title in header, frame._message("row 0 does not name the relay")
    assert "agent-service" in header, frame._message(
        "row 0 does not show the working path")
    assert column_of(frame, title, row=0) < column_of(frame, "agent-service", row=0)


def test_the_header_shows_every_measured_metric():
    relay = FIXTURES / "tokens"
    frame = frame_of(relay)
    extras = dashboard(relay)
    header = frame.lines[0]
    for label, value in (("TIME", extras["elapsed"]),
                         ("Input", extras["tokens"]["input"]),
                         ("Cached", extras["tokens"]["cached"]),
                         ("Output", extras["tokens"]["output"])):
        assert "%s %s" % (label, value) in header, frame._message(
            "row 0 does not show %s" % label)
    # The metric group sits to the right of the relay's own name.
    assert column_of(frame, "TIME", row=0) > column_of(frame, extras["title"], row=0)


def test_the_header_omits_a_metric_the_model_has_no_value_for():
    frame = frame_of(FIXTURES / "agent-service")
    header = frame.lines[0]
    for label in ("TIME", "Input", "Cached", "Output"):
        assert label not in header, frame._message(
            "agent-service measures nothing, yet row 0 shows %r" % label)


def test_a_partly_measured_relay_shows_only_what_it_measured(tmp_path):
    (tmp_path / "legs.json").write_text(json.dumps(
        {"relay": "half", "legs": [{"id": "one", "status": "running"}]}))
    (tmp_path / "dashboard.json").write_text(json.dumps(
        {"title": "Half measured", "path": "~/half", "elapsed": "2h 10m",
         "tokens": {"input": "12.0K"}}))
    frame = frame_of(tmp_path)
    header = frame.lines[0]
    assert "TIME 2h 10m" in header
    assert "Input 12.0K" in header
    assert "Cached" not in header, frame._message("nothing measured cached")
    assert "Output" not in header, frame._message("nothing measured output")


# --------------------------------------------------------------------------
# ACC-TUI-002 — status bar
# --------------------------------------------------------------------------


def test_the_status_bar_shows_the_phase_with_a_status_dot():
    frame = frame_of(FIXTURES / "agent-service")
    status = frame.lines[1]
    assert "RUNNING" in status, frame._message("row 1 does not name the phase")
    assert status.index("●") < status.index("RUNNING"), frame._message(
        "the status dot must precede the phase")
    frame.assert_attrs("RUNNING", fg=32, row=1)
    # The dot takes the phase's own colour, not the running leg's.
    frame.assert_attrs("●", fg=32, row=1)


def test_the_status_bar_counts_the_fixtures_own_legs():
    relay = FIXTURES / "agent-service"
    frame = frame_of(relay)
    done, total = leg_figures(relay)
    assert "%d/%d" % (done, total) in frame.lines[1], frame._message(
        "row 1 does not show %d/%d" % (done, total))
    # right-aligned: nothing but the count reaches the right-hand end
    assert frame.lines[1].rstrip().endswith("%d/%d" % (done, total))


@pytest.mark.parametrize("size", [WIDE, STANDARD, (30, 100)])
def test_the_progress_bar_fill_tracks_the_ratio(size):
    relay = FIXTURES / "agent-service"
    frame = frame_of(relay, size=size)
    done, total = leg_figures(relay)
    filled, width = bar_fill(frame)
    expected = width * done / total
    assert abs(filled - expected) <= 1, frame._message(
        "bar is %d of %d cells; %d/%d wants %.1f"
        % (filled, width, done, total, expected))


def test_a_complete_relay_reads_complete():
    relay = FIXTURES / "all-done"
    frame = frame_of(relay)
    done, total = leg_figures(relay)
    assert "COMPLETE" in frame.lines[1], frame._message("row 1 is not COMPLETE")
    assert "%d/%d" % (done, total) in frame.lines[1]


def test_a_relay_with_no_legs_draws_an_empty_bar_not_a_full_one():
    frame = frame_of(FIXTURES / "empty")
    filled, width = bar_fill(frame)
    assert filled == 0, frame._message("nothing is done, yet the bar is filled")
    assert "0/0" in frame.lines[1]


# --------------------------------------------------------------------------
# ACC-TUI-003 — the four-pane Overview
# --------------------------------------------------------------------------


PANES = ("Active Leg", "Legs", "Progress Log", "Active Runner")


def test_overview_draws_the_four_panes_in_the_mission_control_arrangement():
    frame = frame_of(FIXTURES / "agent-service", size=WIDE)
    rows = {title: row_of(frame, title) for title in PANES}
    cols = {title: column_of(frame, title) for title in PANES}
    half = frame.cols // 2

    # Active Leg left, Legs top-right, on the same row.
    assert cols["Active Leg"] < half
    assert cols["Legs"] > half
    assert rows["Active Leg"] == rows["Legs"]
    # Progress Log below Legs, in the same right-hand column.
    assert cols["Progress Log"] > half
    assert rows["Progress Log"] > rows["Legs"]
    # Active Runner full width along the bottom, below both columns.
    assert cols["Active Runner"] < half
    assert rows["Active Runner"] > rows["Progress Log"]
    # ...and above the keybar, which is the last row.
    assert rows["Active Runner"] < frame.rows - 1


def test_the_overview_panes_are_separated_by_rules():
    frame = frame_of(FIXTURES / "agent-service", size=WIDE)
    top = row_of(frame, "Active Leg")
    bottom = row_of(frame, "Active Runner")
    split = frame.raw_lines[top].index("│")
    assert column_of(frame, "Legs") > split

    # A vertical rule runs the height of the two columns...
    for row in range(top, bottom - 1):
        assert frame.raw_lines[row][split] == "│", frame._message(
            "row %d has no column rule at col %d" % (row, split))
    # ...and a horizontal rule separates the Active Runner pane from them.
    rule = frame.lines[bottom - 1]
    assert set(rule) == {"─"}, frame._message(
        "row %d is not a rule across the whole width" % (bottom - 1))
    assert len(rule) == frame.cols - 1, frame._message(
        "the rule stops at column %d, not the reserved margin" % len(rule))
    # ...and one separates Legs from Progress Log, on the right only.
    divider = row_of(frame, "Progress Log") - 1
    assert frame.raw_lines[divider][:split].strip() == "", frame._message(
        "the Legs/Progress Log rule spills into the left column")
    assert "─" in frame.raw_lines[divider][split + 1:]


def test_overview_stacks_into_one_column_when_narrow():
    frame = frame_of(FIXTURES / "agent-service", size=STANDARD)
    rows = [row_of(frame, title) for title in PANES]
    assert rows == sorted(rows), frame._message(
        "narrow Overview must stack the panes in the documented order: %r" % (rows,))
    for title in PANES:
        assert column_of(frame, title) < frame.cols // 2
    assert "│" not in frame.text, frame._message(
        "a stacked Overview has no column rule")


@pytest.mark.parametrize("size", [WIDE, STANDARD, (30, 100), (40, 120), (24, 60)])
def test_no_row_is_wider_than_the_terminal(size):
    """Certified, not waved through.

    A passing `assert_within_width(allow_full_width=True)` would mean only
    "nothing wrapped": ncurses clips in software, so a row ending exactly at
    the margin is byte-identical to one truncated to fit, and the helper
    refuses to certify it. The chrome therefore reserves the last column — even
    the rules stop one short — so the strict form applies and the frame is
    certified rather than excused. That is asserted directly below, because it
    is the property the strict call depends on.
    """
    relay = FIXTURES / "agent-service"
    frame = frame_of(relay, size=size)
    assert not frame.full_width_rows(), frame._message(
        "the chrome reserves the last column so that no row can be mistaken "
        "for a truncated one, yet rows %r reach it" % (frame.full_width_rows(),))
    frame.assert_within_width()

    rules = [row for row, line in enumerate(frame.lines)
             if line and set(line) == {"─"}]
    assert rules, frame._message("no pane rule was drawn at all")
    for row in rules:
        assert len(frame.lines[row]) == frame.cols - 1, frame._message(
            "the rule on row %d is %d cells wide, not %d"
            % (row, len(frame.lines[row]), frame.cols - 1))
    done, total = leg_figures(relay)
    assert frame.lines[1].rstrip().endswith("%d/%d" % (done, total)), frame._message(
        "the status bar count was clipped at the right edge")


# --------------------------------------------------------------------------
# ACC-TUI-004 — the keybar
# --------------------------------------------------------------------------


def test_the_keybar_lists_the_overview_bindings():
    frame = frame_of(FIXTURES / "agent-service", size=WIDE)
    last = frame.rows - 1
    assert frame.lines[last].startswith(OVERVIEW_KEYS), frame._message(
        "the last row is not the Overview keybar")


def test_the_keybar_emphasises_the_key_and_dims_the_label():
    frame = frame_of(FIXTURES / "agent-service", size=WIDE)
    last = frame.rows - 1
    frame.assert_attrs("F", has="bold", lacks="dim", row=last)
    frame.assert_attrs("Legs", has="dim", lacks="bold", row=last)
    frame.assert_attrs("Tab", has="bold", row=last)
    frame.assert_attrs("Next View", has="dim", row=last)


def test_the_keybar_fits_the_standard_terminal():
    frame = frame_of(FIXTURES / "agent-service", size=STANDARD)
    last = frame.rows - 1
    assert frame.lines[last].startswith(OVERVIEW_KEYS), frame._message(
        "the Overview keybar must fit 80 columns intact")


def test_the_keybar_follows_the_view():
    """Every transition waits on text only the destination view paints.

    `expect="Legs"` would be unsound here: the Overview draws a pane with that
    title, so the wait would be satisfied by the screen already showing.
    """
    term = session(FIXTURES / "agent-service", size=WIDE)
    try:
        last = term.rows - 1
        legs = term.send("F", expect="Esc Overview")
        assert "T Filter" in legs.lines[last]
        assert "Esc Overview" in legs.lines[last]

        runners = term.send("W", expect="Runners")
        assert "Esc Overview" in runners.lines[last]

        models = term.send("M", expect="Models")
        assert "Esc Overview" in models.lines[last]

        contract = term.send("C", expect="Contract")
        assert "Esc Overview" in contract.lines[last]

        back = term.send("<Esc>", expect="Active Leg")
        assert back.lines[last].startswith(OVERVIEW_KEYS)
    finally:
        term.close()


def test_tab_cycles_forward_through_the_views():
    term = session(FIXTURES / "agent-service", size=WIDE)
    try:
        term.send("<Tab>", expect="Esc Overview")     # Overview -> Legs
        term.send("<Tab>", expect="Runners")          # Legs -> Runners
        term.send("<Tab>", expect="Models")           # Runners -> Models
        term.send("<Tab>", expect="Contract")         # Models -> Contract
        home = term.send("<Tab>", expect="Active Leg")  # Contract -> Overview
        assert home.lines[home.rows - 1].startswith(OVERVIEW_KEYS)
    finally:
        term.close()


# --------------------------------------------------------------------------
# ACC-TUI-006 — status glyphs and their colour pairs
# --------------------------------------------------------------------------


def test_the_status_legend_draws_every_glyph():
    frame = frame_of(FIXTURES / "agent-service", size=WIDE)
    last = frame.rows - 1
    legend = frame.lines[last]
    for glyph, word in (("✓", "done"), ("●", "running"),
                        ("○", "pending"), ("✗", "failed"),
                        ("−", "cancelled")):
        assert "%s %s" % (glyph, word) in legend, frame._message(
            "the legend does not show %r %s" % (glyph, word))


def test_every_status_glyph_carries_its_own_colour_pair():
    frame = frame_of(FIXTURES / "agent-service", size=WIDE)
    last = frame.rows - 1
    for glyph, (fg, has, lacks) in GLYPH_COLOURS.items():
        frame.assert_attrs(glyph, fg=fg, has=list(has), lacks=list(lacks), row=last)
    # Distinct pairs, not five names for one colour.
    seen = {frame.run_with(glyph, row=last).attrs for glyph in GLYPH_COLOURS}
    assert len(seen) == len(GLYPH_COLOURS), frame._message(
        "two status glyphs are drawn identically: %r" % (seen,))


@pytest.mark.parametrize("relay,size", [
    ("agent-service", WIDE),
    ("agent-service", STANDARD),
    ("agent-service", (12, 40)),
    ("tokens", (16, 90)),
    ("stale-currentleg", (16, 90)),
])
def test_an_overflow_marker_never_overwrites_the_row_it_shares(relay, size):
    """`+N more` owns its row or is not drawn.

    The marker is placed at the end of the *region* that overflowed, not at the
    end of the pane. Placing it at the end of the pane put `+1 more` on top of
    the row above it — `+1 moreMeasured stage` — which reads as neither.
    """
    frame = frame_of(FIXTURES / relay, size=size)
    collision = frame.search(r"\+\d+ more\S")
    assert collision is None, frame._message(
        "row %s draws an overflow marker into another row's text"
        % (collision,))


@pytest.mark.parametrize("relay,size", [
    ("agent-service", (12, 40)),
    ("tokens", (16, 90)),
    ("all-done", STANDARD),
    ("empty", STANDARD),
])
def test_the_progress_log_never_claims_a_range_it_did_not_draw(relay, size):
    """`1-0 of 12` is `1-0 of 0`'s twin, and ACC-OVER-004 forbids both."""
    frame = frame_of(FIXTURES / relay, size=size)
    assert frame.search(r"1-0 of ") is None, frame._message(
        "the Progress Log header claims a range with nothing in it")


def test_a_pane_with_nothing_to_show_says_so_in_words():
    """Emptiness is a sentence, never a blank box (the pane convention).

    A titled pane with an empty body cannot be told from a pane that crashed
    while drawing, which is why every pane says it the same way through
    `Pane.empty()`.
    """
    frame = frame_of(FIXTURES / "empty", size=WIDE)
    for message in ("no legs planned yet",
                    "nothing recorded yet",
                    "no runner is on a leg right now"):
        assert frame.contains(message), frame._message(
            "an empty relay must say %r somewhere" % message)
    assert frame.contains("PENDING"), frame._message(
        "the Active Leg pane must state the phase when nothing is running")

    done = frame_of(FIXTURES / "all-done", size=WIDE)
    assert done.contains("COMPLETE"), done._message(
        "a finished relay must say so in the Active Leg pane")


def test_a_pane_header_carries_its_own_figure():
    """The right-hand meta is the pane's, and it is a range it actually drew."""
    frame = frame_of(FIXTURES / "agent-service", size=WIDE)
    log_row = row_of(frame, "Progress Log")
    assert frame.search(r"Progress Log\s+1-\d+ of \d+") == log_row, \
        frame._message("the Progress Log header carries no drawn range")
    done, total = leg_figures(FIXTURES / "agent-service")
    legs_row = row_of(frame, "Legs")
    assert frame.lines[legs_row].rstrip().endswith("%d/%d" % (done, total)), \
        frame._message("the Legs pane header carries no done/total count")


def test_the_legs_pane_draws_a_glyph_per_leg_in_its_status_colour():
    frame = frame_of(FIXTURES / "agent-service", size=WIDE)
    legs_row = row_of(frame, "Legs")
    body = frame.lines[legs_row + 1: row_of(frame, "Progress Log") - 1]
    assert any("✓" in line for line in body), frame._message(
        "the Legs pane draws no completed glyph")
    assert any("●" in line for line in body), frame._message(
        "the Legs pane draws no running glyph")


@pytest.mark.parametrize("relay,size", [
    ("agent-service", WIDE),
    ("agent-service", STANDARD),
    ("tokens", WIDE),
    ("all-done", STANDARD),
    ("empty", WIDE),
    ("malformed", STANDARD),
])
def test_no_captured_frame_carries_mojibake(relay, size):
    # Both assertions here are negative — "this sequence is *not* on the
    # screen" — and a half-painted screen satisfies them without being asked.
    # `proved_frame_of` is the form that refuses to be satisfied that way.
    frame = proved_frame_of(FIXTURES / relay, size=size)
    frame.assert_finished()
    for sequence in MOJIBAKE:
        assert sequence not in frame.text, frame._message(
            "a mojibake sequence %r reached the screen" % sequence)
    assert "Traceback" not in frame.text, frame._message("the TUI raised")


def test_colour_degrades_to_monochrome():
    """A terminal with no colour still gets a readable, distinguishable screen."""
    frame = frame_of(FIXTURES / "agent-service", size=WIDE, term="vt100")
    assert "RUNNING" in frame.lines[1]
    assert frame.lines[frame.rows - 1].startswith(OVERVIEW_KEYS)
    for title in PANES:
        assert frame.contains(title), frame._message(
            "%r is missing on a monochrome terminal" % title)
    assert "Traceback" not in frame.text


# --------------------------------------------------------------------------
# degradation and lifecycle
# --------------------------------------------------------------------------


@pytest.mark.parametrize("size", [(12, 40), (8, 30), (5, 20), (3, 12)])
def test_it_degrades_below_80x24_without_crashing(size):
    """Degrade, not crash: fewer panes, never a traceback and never a hang."""
    term = session(FIXTURES / "agent-service", size=size)
    try:
        # A frame the program stated was whole: "no traceback" and "nothing is
        # too wide" are both true of a screen it had not finished drawing.
        frame = repaint(term)
        frame.assert_finished()
        assert "Traceback" not in frame.text, frame._message("the TUI raised")
        frame.assert_within_width()
        assert frame.text.strip(), frame._message("nothing was drawn at all")
        assert term.is_running, frame._message("the TUI exited instead of degrading")
    finally:
        term.close()


def test_a_resize_redraws_at_the_new_size():
    term = session(FIXTURES / "agent-service", size=WIDE)
    try:
        wide = term.frame()
        assert wide.contains("Active Runner")
        narrow = term.resize(*STANDARD, expect=OVERVIEW_KEYS)
        assert narrow.cols == 80
        narrow.assert_within_width()
        assert "Traceback" not in narrow.text
        for title in PANES:
            assert narrow.contains(title)
    finally:
        term.close()


def test_quit_exits_cleanly():
    term = session(FIXTURES / "agent-service", size=STANDARD)
    try:
        term.send("q")
        assert term.wait(timeout=3.0) == 0
    finally:
        term.close()


# --------------------------------------------------------------------------
# ACC-TUI-007 — the TUI reads no relay file of its own
# --------------------------------------------------------------------------

# Ways a module could reach a relay file behind `relay_model`'s back. Bare
# names first, then attribute calls, then whole modules.
FORBIDDEN_NAMES = frozenset({"open", "eval", "exec", "compile", "__import__"})
FORBIDDEN_ATTRS = frozenset({
    "open", "read", "read_text", "read_bytes", "readline", "readlines",
    "load", "iterdir", "glob", "rglob", "walk", "listdir", "scandir",
    "run", "call", "check_call", "check_output", "Popen", "popen", "system",
    "getoutput", "getstatusoutput", "urlopen",
})
FORBIDDEN_MODULES = frozenset({
    "subprocess", "shutil", "sqlite3", "urllib", "socket", "requests",
    "configparser", "csv", "tempfile", "glob", "fileinput", "shelve", "pickle",
})


def package_modules():
    """Every Python module under `scripts/relay_control/`.

    Sorted so a failure names the same file every run, and returned as a list
    so a caller can assert it is not empty — a sweep over nothing passes for
    the wrong reason, which is the defect that blocked the last gate.
    """
    return sorted(PACKAGE.rglob("*.py"))


def sweep(paths):
    """`(findings, nodes)` — every relay-file read in `paths`, and the AST node
    count that was actually walked to find them.

    The node count is returned so a caller can prove the sweep inspected
    something. A filter that skipped every file, or a parse that produced
    nothing, both report zero findings and are indistinguishable from a clean
    package on the findings alone.
    """
    findings = []
    nodes = 0
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            nodes += 1
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                        findings.append((path.name, node.lineno,
                                         "import %s" % alias.name))
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in FORBIDDEN_MODULES:
                    findings.append((path.name, node.lineno,
                                     "from %s import" % node.module))
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
                    findings.append((path.name, node.lineno, "%s()" % func.id))
                elif isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_ATTRS:
                    findings.append((path.name, node.lineno, ".%s()" % func.attr))
    return findings, nodes


def test_no_module_under_relay_control_reads_a_relay_file():
    modules = package_modules()
    findings, nodes = sweep(modules)
    assert not findings, (
        "the TUI must get every fact from relay_model.build(); found %r" % (findings,))
    # The sweep is only evidence if it looked at something.
    assert len(modules) >= 6, "swept only %r" % ([p.name for p in modules],)
    assert {"app.py", "chrome.py", "theme.py"} <= {p.name for p in modules}
    assert nodes > 500, "the sweep walked only %d AST nodes" % nodes


def test_the_reader_sweep_finds_a_planted_reader():
    """Non-vacuity, proved where it matters: inside the real package.

    A sweep that reports "clean" is worthless until it has been shown to fail.
    Planting the reader in `scripts/relay_control/` rather than in `tmp_path`
    also proves the *collection* step reaches the package, which is the half a
    vacuous filter gets wrong.
    """
    planted = PACKAGE / "_planted_reader_probe.py"
    planted.write_text(
        "import json\n"
        "import pathlib\n"
        "def cheat(relay_dir):\n"
        "    return json.load(open(pathlib.Path(relay_dir) / 'state.json'))\n"
    )
    try:
        modules = package_modules()
        assert planted in modules, "the sweep does not reach the package it audits"
        findings, nodes = sweep(modules)
        assert nodes > 500
        planted_findings = [f for f in findings if f[0] == planted.name]
        assert planted_findings, (
            "the sweep passed a module that calls json.load(open(...)) — it is "
            "vacuous, and would pass a TUI that read the relay behind the "
            "model's back")
    finally:
        planted.unlink(missing_ok=True)


def test_the_tui_gets_its_facts_from_relay_model_build():
    sources = {path.name: path.read_text() for path in package_modules()}
    joined = "\n".join(sources.values())
    assert "import relay_model" in joined, (
        "no module under scripts/relay_control/ imports relay_model")
    calls = [name for name, text in sources.items() if "build(" in text]
    assert calls, "no module under scripts/relay_control/ calls build()"


# --------------------------------------------------------------------------
# every repaint is bracketed — the harness is handed proof, not silence
# --------------------------------------------------------------------------
#
# `tests/frame.py` decides a repaint has ended one of three ways, and only two
# of them are proof (`Frame.paint_end`). Without a bracket this program's
# frames end on `"quiet"` — it stopped writing for 0.2s — and a program that
# pauses longer than that inside one repaint is quiet and half-painted at the
# same instant. Roughly forty S2-S4 checks are judged off these frames, so the
# assertions below are what keeps every one of them a proof rather than a
# guess: if the bracketing is ever removed, these fail and the rest quietly
# drop back to the heuristic without anything saying so.


def test_the_first_paint_closes_one_bracket_and_leaves_none_open():
    """Off the emulator's own counter, which is where the first paint lives.

    `TerminalSession.start()` has read the whole first paint before any wait
    can take a DEC 2026 baseline, so no `paint_end` can speak for it. What can
    is `Screen.synchronized_updates`: it counts brackets the program *closed*.
    One repaint has happened, so it is exactly one — a program that opened a
    bracket and never closed it would count zero and leave the flag standing,
    and every wait after it would hang until its window ran out.
    """
    term = session(FIXTURES / "agent-service")
    try:
        assert term.screen.synchronized_updates == 1, (
            "the first paint closed %d DEC 2026 brackets, not one"
            % term.screen.synchronized_updates)
        assert not term.screen.synchronized_update, (
            "the program is still inside a bracket it never closed")
        repaint(term)
        assert term.screen.synchronized_updates == 2, (
            "a second repaint did not close a second bracket (%d)"
            % term.screen.synchronized_updates)
    finally:
        term.close()


def test_a_repaint_is_proved_whole_rather_than_merely_quiet():
    """The deliverable: this TUI's frames are proof, not the 0.2s heuristic.

    Without the bracketing `paint_end` is `"quiet"` — "it stopped writing for
    0.2s" — and a program that pauses longer than that inside one repaint is
    quiet and half-painted at the same instant. Roughly forty S2-S4 visual
    checks are judged off these frames; if this assertion ever goes, every one
    of them silently drops back to a guess.
    """
    term = session(FIXTURES / "agent-service")
    try:
        frame = repaint(term)
        assert frame.paint_end == "synchronised", frame._message(
            "this frame was captured on %r. The TUI is not bracketing its "
            "repaints in DEC 2026, so the harness has nothing but silence to "
            "go on" % frame.paint_end)
        frame.assert_finished()
    finally:
        term.close()


def test_a_view_switch_is_proved_whole():
    """The transition frames are the ones the S3 view checks are judged on."""
    term = session(FIXTURES / "agent-service")
    try:
        frame = term.send("F", expect="Esc Overview")
        frame.assert_finished()
        assert frame.paint_end == "synchronised", frame._message(
            "captured on %r" % frame.paint_end)
    finally:
        term.close()


def test_a_resize_repaint_is_proved_whole():
    """A SIGWINCH has no delivery barrier, so the bracket is all there is."""
    term = session(FIXTURES / "agent-service", size=WIDE)
    try:
        frame = term.resize(*STANDARD, expect=OVERVIEW_KEYS)
        assert frame.paint_end == "synchronised", frame._message(
            "the redraw after a resize was captured on %r" % frame.paint_end)
        frame.assert_finished()
    finally:
        term.close()


@pytest.mark.parametrize("size", [WIDE, STANDARD, (12, 40), (8, 30), (5, 20),
                                  (3, 12)])
def test_every_terminal_size_brackets_its_repaints(size):
    """Including the sizes whose paint draws almost nothing: those are frames too."""
    term = session(FIXTURES / "agent-service", size=size)
    try:
        assert term.screen.synchronized_updates == 1
        repaint(term).assert_finished()
    finally:
        term.close()


def test_the_bracket_leaves_nothing_on_the_screen_or_in_the_attributes():
    """An unknown private mode is inert: it draws no cell and sets no SGR.

    Read off the captured planes rather than off the wire, because what would
    go wrong — the sequence escaping into the text, or leaving a parameter
    behind in the attribute plane — is only visible there.
    """
    frame = proved_frame_of(FIXTURES / "agent-service")
    for residue in ("2026", "[?", "?20"):
        frame.assert_not_contains(residue)
    for bad in MOJIBAKE:
        frame.assert_not_contains(bad)
    stray = [(row, col, cell.describe())
             for row, attrs in enumerate(frame.attrs)
             for col, cell in enumerate(attrs) if cell.other]
    assert not stray, frame._message(
        "the repaint left unmodelled SGR parameters at %r" % (stray[:8],))


def test_a_terminal_that_does_not_know_the_bracket_still_gets_a_whole_screen():
    """`TERM=vt100`: no colour, no alternate screen, and no 2026 in terminfo.

    The sequence is written past curses, so it goes out whatever the terminal
    claims to understand — which is what a private mode is for. What this
    proves is that doing so corrupts nothing on a terminal that ignores it.
    """
    term = session(FIXTURES / "agent-service", term="vt100")
    try:
        assert term.screen.synchronized_updates == 1
        frame = repaint(term)
        assert frame.paint_end == "synchronised", frame._message(
            "captured on %r at TERM=vt100" % frame.paint_end)
        frame.assert_contains(READY)
        frame.assert_not_contains("2026")
        frame.assert_not_contains("Traceback")
        frame.assert_within_width()
        assert term.is_running
    finally:
        term.close()


def test_the_bracket_is_closed_even_when_the_repaint_raises(monkeypatch):
    """Exactly one open and one close, and the close survives an exception.

    No screen can show this: a repaint that died inside an unclosed bracket
    leaves the terminal — and every wait in `tests/frame.py` — inside a frame
    that never ends, which looks like a hang and not like a traceback. The
    pairing is asserted where it lives, on the context manager itself.
    """
    seen = []
    monkeypatch.setattr(app, "_write_through", seen.append)

    with app.synchronised_output():
        pass
    assert seen == [app._SYNC_BEGIN, app._SYNC_END], (
        "one repaint wrote %r" % (seen,))

    seen.clear()
    with pytest.raises(RuntimeError):
        with app.synchronised_output():
            raise RuntimeError("a repaint that died halfway through")
    assert seen == [app._SYNC_BEGIN, app._SYNC_END], (
        "a repaint that raised wrote %r — an open bracket is a frame that "
        "never ends" % (seen,))


# --------------------------------------------------------------------------
# untrusted prose cannot reach the terminal as an instruction
# --------------------------------------------------------------------------
#
# Every string this program draws is relay prose: a leg goal, a boundary, a
# check's evidence, a commit subject, a coach's log line, an attention item, a
# model name. All of it is hand-written into `.relay/` and the model reports it
# verbatim by contract (ACC-DATA-001 names malformed input; ACC-DATA-007 keeps
# an em-dash in a quoted subject). So the renderer is where a control character
# has to stop being one, and `chrome.Canvas.write()` is the single place text
# becomes cells.
#
# The sharpest consequence is the one the section above rests on. This program
# says a repaint is whole by writing `ESC[?2026l`, and roughly forty S2-S4
# checks are judged on frames that carry that proof. Those bytes in a leg goal
# are byte for byte the program's own — no terminal can separate them — so a
# forged close either hands back a frame the program never vouched for, or,
# once the imbalance shows, makes `tests/frame.py` declare the whole session
# unsound and refuse every frame captured from it. A leg goal must not be able
# to invalidate the evidence for the rest of the relay.
#
# What is asserted here is what a terminal can observe: the sequence did not
# take effect, the brackets still balance, the screen stays inside its panes
# and inside its width, and the prose is still readable with a visible mark
# where the control character was.

ESC = "\x1b"
SYNC_CLOSE = ESC + "[?2026l"        # "the repaint is whole" — the TUI's own word
SYNC_OPEN = ESC + "[?2026h"
CURSOR_TO_5_5 = ESC + "[5;5H"       # CUP: put the cursor somewhere else
RED_SGR = ESC + "[31m"              # SGR: paint everything after this red
ERASE_DISPLAY = ESC + "[2J"

#: How ncurses draws a control character that reached `addstr` — its own
#: `unctrl` spelling. That accidental mitigation is not the guard and cannot
#: be: it costs two cells for one character, which is what broke the reserved
#: last column below. Caret notation on a captured screen is therefore the
#: evidence that a control character got as far as the window.
CARETS = ("^[", "^?", "^@", "^H", "^M", "^I", "^J", "^L", "^A", "^G")

#: The mark left where a control character was, read from the theme rather
#: than spelled here — it has an ASCII spelling too, and a test that hardcoded
#: one of them would be asserting the wrong screen under `LC_ALL=C`. Read with
#: `.get` so that a theme missing the entry fails in the test that says what
#: is wrong, rather than as a `KeyError` during collection that stops the file
#: before any of it runs. `\ufffd` is in `MOJIBAKE`, so it can never be on a
#: legitimate screen and every assertion below then fails loudly.
MARK = theme_tokens.GLYPHS.get("control", "\ufffd")
ASCII_MARK = theme_tokens.ASCII_GLYPHS.get("control", "\ufffd")


def prose_relay(directory, goal="a goal with nothing odd in it",
                log_message="a log line with nothing odd in it",
                leg_id="poisoned", attention=None, models=None,
                check_id="ACC-PROSE-001", path=None):
    """A one-leg relay whose prose is whatever the caller passes.

    Every field here is somewhere a human types free text into `.relay/`:
    `legs.json` carries the goal and the leg's id, `dashboard.json` carries the
    log, the attention items and the model names, `state.json` carries the
    check ids. `json.dumps` escapes a control character on the way out, so the
    fixture on disk is plain text and the program still reads the byte.
    """
    directory = Path(directory)
    (directory / "batons").mkdir(parents=True, exist_ok=True)
    (directory / "legs.json").write_text(json.dumps({
        "relay": "prose",
        "stages": [{"id": "S1", "name": "Stage one", "legs": [leg_id]}],
        "legs": [{"id": leg_id, "stage": "S1", "kind": "fix", "goal": goal,
                  "fulfills": [check_id],
                  "boundaries": ["only the renderer"],
                  "verification": ["python3 -m pytest tests/test_chrome.py -q"],
                  "status": "running"}],
    }))
    (directory / "state.json").write_text(json.dumps({
        "relay": "prose", "phase": "running", "currentStage": "S1",
        "currentLeg": leg_id,
        "checks": {check_id: {"status": "pending", "stage": "S1",
                              "claimedBy": leg_id}},
    }))
    extras = {"title": "Prose relay", "path": str(path or directory)}
    if log_message is not None:
        extras["log"] = [{"t": "now", "m": log_message}]
    if attention is not None:
        extras["attention"] = [{"level": "warn", "label": "WARN",
                                "text": attention}]
    if models is not None:
        extras["models"] = models
    (directory / "dashboard.json").write_text(json.dumps(extras))
    return directory


def assert_nothing_took_effect(term, frame):
    """The four statements a terminal can make about a sequence that did not run.

    Grouped because every test below wants all four and a test that quietly
    dropped one would pass on a screen that had been repainted by its fixture.
    """
    assert frame.paint_end == "synchronised", frame._message(
        "captured on %r — a repaint this program did not vouch for"
        % frame.paint_end)
    frame.assert_finished()
    assert term.screen.synchronized_faults == 0, frame._message(
        "%d DEC 2026 sequences arrived that a program bracketing its own "
        "repaints cannot send: prose forged a bracket"
        % term.screen.synchronized_faults)
    for caret in CARETS:
        frame.assert_not_contains(caret)
    frame.assert_within_width()
    assert "Traceback" not in frame.text, frame._message("the TUI raised")


def rows_that_differ(one, other):
    return [row for row, (a, b) in enumerate(zip(one.lines, other.lines))
            if a != b]


def rule_columns(frame):
    """Where the box rules are, row by row — the panes' own edges.

    A sequence that moved the cursor, scrolled, or overran a pane shows up
    here before it shows up anywhere else: the rules are drawn by the layout
    from the terminal's size and nothing a view draws may disturb them.
    """
    return [tuple(col for col, ch in enumerate(line) if ch in "│─")
            for line in frame.lines]


# -- the forged bracket, which is the reason this leg exists ----------------


def test_a_leg_goal_cannot_forge_the_end_of_a_repaint(tmp_path):
    """`ESC[?2026l` in a goal is the program's own "the screen is whole".

    Rendered, it either ends a wait on a repaint that had not happened or —
    once the extra close shows up with nothing open — makes the harness call
    the whole session unsound and refuse every frame taken from it. Either way
    a leg goal has invalidated the evidence for the rest of the relay.
    """
    relay = prose_relay(tmp_path, goal="Forge %s a close and %s an open"
                                       % (SYNC_CLOSE, SYNC_OPEN))
    term = session(relay, size=WIDE)
    try:
        frame = repaint(term)
        assert_nothing_took_effect(term, frame)
        # The prose is still readable and it is prose: the mark says a
        # character was removed, and the rest is text.
        assert MARK + "[?2026l" in frame.text, frame._message(
            "the forged close is not drawn as text")
        assert MARK + "[?2026h" in frame.text, frame._message(
            "the forged open is not drawn as text")
        assert frame.contains("Forge"), frame._message("the goal is gone")
        # The program's own brackets are still the only ones on the wire.
        assert term.screen.synchronized_updates == 2, (
            "the stream carries %d closed brackets, not the two this program "
            "painted" % term.screen.synchronized_updates)
    finally:
        # close() re-checks the balance and fails the session if prose forged
        # a bracket at any point, including after the last frame was taken.
        term.close()


def test_a_forged_bracket_in_the_log_leaves_the_session_sound(tmp_path):
    """The same sequence by the other route a coach can reach: `dashboard.json`.

    Asserted through `term.wait()` rather than off a frame, because the
    session's own refusal is what a judge downstream relies on: a run whose
    brackets stopped balancing raises there even if every frame already
    handed over looked fine.
    """
    relay = prose_relay(tmp_path, log_message="landed %s%s early"
                                              % (SYNC_CLOSE, SYNC_CLOSE))
    term = session(relay, size=STANDARD)
    try:
        frame = repaint(term)
        assert_nothing_took_effect(term, frame)
        assert MARK + "[?2026l" + MARK + "[?2026l" in frame.text
        term.send("q")
        assert term.wait(timeout=5.0) == 0, "the TUI did not exit cleanly"
    finally:
        term.close()


# -- the other three shapes of sequence -------------------------------------


def test_a_log_message_cannot_move_the_cursor(tmp_path):
    """A CUP in a log line would draw the rest of it wherever it liked.

    The assertion is the strongest available and does not depend on knowing
    where `ESC[5;5H` would have landed: against the same relay with clean
    prose, *exactly one row* of the screen may differ, and it is the row the
    log line is on. A sequence that took effect changes a second one.
    """
    clean = proved_frame_of(prose_relay(
        tmp_path / "clean", log_message="a log line and then some tail",
        path=tmp_path))
    poisoned = proved_frame_of(prose_relay(
        tmp_path / "poisoned",
        log_message="a log line%sand then some tail" % CURSOR_TO_5_5,
        path=tmp_path))

    log_row = row_of(poisoned, "a log line")
    assert rows_that_differ(clean, poisoned) == [log_row], poisoned._message(
        "the cursor sequence changed rows other than the log's own: %r"
        % (rows_that_differ(clean, poisoned),))
    assert MARK + "[5;5H" in poisoned.text


def test_a_commit_subject_cannot_colour_the_screen(tmp_path):
    """An SGR in a commit subject, through the derived Progress Log.

    A commit subject is prose nobody in this repository wrote: it comes out of
    the runner's own repository, and `git log` hands it over byte for byte —
    verified here by asserting the model carries the escape before the frame
    is ever captured, so a model that had quietly stripped it could not
    certify the renderer.

    Missing git **fails** here rather than skipping. That is this suite's
    settled position — `test_relay_model.test_git_is_installed` asserts it,
    and `test_no_test_module_skips_for_a_reason_this_machine_decides` refuses
    a skip in this module — because a skip is a green test that ran nothing.
    """
    assert HAS_GIT, ("git is not installed, so the one path that carries prose "
                     "nobody in this repository wrote — a commit subject — "
                     "would go unproven. This suite requires git; see "
                     "test_relay_model.test_git_is_installed")
    relay = prose_relay(tmp_path / "repo", leg_id="landed", log_message=None)
    subject = "landed: paint %sTHIS-MUST-NOT-BE-RED" % RED_SGR
    git_run(relay, "init", "-q", "-b", "main")
    git_run(relay, "add", "-A")
    git_run(relay, "commit", "-q", "-m", subject)
    sha = git_run(relay, "rev-parse", "--short", "HEAD").stdout.strip()
    (relay / "batons" / "landed.md").write_text(
        "# landed\n**Status:** success\n**Commit:** `%s`\n" % sha)

    carried = [entry["m"] for entry in relay_model.build(relay)["log"]
               if RED_SGR in (entry["m"] or "")]
    assert carried, "the model dropped the escape; this fixture proves nothing"

    term = session(relay, size=WIDE)
    try:
        frame = repaint(term)
        assert_nothing_took_effect(term, frame)
        # The whole subject, escape and all, is one uniform run. `run_with`
        # refuses to answer for a substring drawn in two styles, so an SGR
        # that had taken effect fails here rather than being argued about.
        run = frame.run_with("landed: paint %s[31mTHIS-MUST-NOT-BE-RED" % MARK)
        assert run.attrs.fg != (31,), frame._message(
            "the commit subject is drawn %s" % run.attrs.describe())
        # Nor is anything painted after it: an SGR that ran colours everything
        # drawn after it, not only the line it was on. The Active Runner pane
        # and the keybar are both painted later in the same repaint.
        frame.assert_attrs("Active Runner", fg=None)
        frame.assert_attrs(" Quit", fg=None)
    finally:
        term.close()


def test_every_bare_control_character_is_drawn_rather_than_obeyed(tmp_path):
    """Every C0, DEL and C1 code point in one log line, at once.

    The baseline is the *same* relay with each control character already
    written as the mark, so the two screens must be identical cell for cell —
    **no row may differ at all.** That is only sayable because the
    substitution is one character for one character, and it says the whole
    claim in one assertion: nothing ran, nothing moved, nothing was lost, and
    nothing cost a second cell.

    Each of these was reachable and each did something different: `\r`
    returned to the *window's* left margin and redrew a Progress Log line on
    top of the Active Leg pane twelve rows away; `\b` deleted the characters
    before it; `\t` shifted the row to a tab stop the layout knew nothing
    about; `\n` drew the rest of the line a row lower, outside the pane;
    `\x7f` reached the screen as `^?`; `\x9b` is `ESC[` spelled in one byte;
    and `\x00` was not a rendering problem at all — `addstr` raised
    `ValueError: embedded null character` and the TUI died mid-repaint, which
    is "degrade, not crash" gone for one byte of a coach's prose.

    The line is poisoned rather than the goal because `chrome.wrap()` splits
    on `str.split()`'s whitespace — which includes `\t\n\r\v\f`, `\x1c`
    to `\x1f` and `\x85` — so a goal folds those into word breaks before the
    renderer ever sees them. `Pane.line()` does no such thing, which is why
    the log is where the whole range arrives intact.
    """
    poison = "".join(chr(ordinal) for ordinal in chrome.CONTROL_ORDINALS)
    assert len(poison) == 32 + 1 + 32, "the guarded range changed shape"
    clean = proved_frame_of(prose_relay(
        tmp_path / "clean", log_message="before" + MARK * len(poison) + "after",
        path=tmp_path))
    term = session(prose_relay(
        tmp_path / "poisoned", log_message="before" + poison + "after",
        path=tmp_path), size=WIDE)
    try:
        frame = repaint(term)
        assert_nothing_took_effect(term, frame)
        assert term.is_running, frame._message(
            "the TUI exited rather than drawing the prose")
        assert rows_that_differ(clean, frame) == [], frame._message(
            "the poisoned screen is not the screen that the same prose draws "
            "with the marks already in it; rows %r differ"
            % (rows_that_differ(clean, frame),))
        # Non-vacuity: the poisoned line really is on the screen, and marked.
        assert frame.contains("before" + MARK * 8), frame._message(
            "the poisoned log line was not drawn at all")
    finally:
        term.close()


# -- width: the reserved last column, which is what makes frames certifiable -


#: Long enough that the escapes alone would overrun the widest pane if each
#: cost the two cells ncurses spends on one.
GOAL_FILL = 60
LOG_FILL = 40


@pytest.mark.parametrize("size", [WIDE, STANDARD, (12, 40), (8, 30)])
def test_a_control_character_costs_exactly_one_cell(size, tmp_path):
    """Sixty escapes in a goal must not widen the row they are on.

    This is the reason the substitution is one character for one character.
    Every width computation in the package counts Python characters and spends
    them as cells (`clip`, `wrap`, `Pane.right`, `overview._fit`); let a
    control character through and ncurses spends two on it, and the observed
    result was a goal row running to the terminal's last column and wrapping
    into the pane below. That is invariant 2 of `chrome.py` gone, and with it
    `assert_within_width()`'s ability to certify any frame in this repository.
    """
    tail = " a goal that fills the pane"
    clean = proved_frame_of(prose_relay(
        tmp_path / "clean", goal="G" * GOAL_FILL + tail,
        log_message="G" * LOG_FILL, path=tmp_path), size=size)
    poisoned = proved_frame_of(prose_relay(
        tmp_path / "poisoned", goal=ESC * GOAL_FILL + tail,
        log_message="\x7f" * LOG_FILL, path=tmp_path), size=size)

    poisoned.assert_within_width()
    assert rule_columns(clean) == rule_columns(poisoned), poisoned._message(
        "the escapes moved the panes' own rules — a row overran its pane")
    # Cell for cell: the escaped screen occupies exactly the cells the plain
    # one did, row by row. Two cells for one escape shows up as a longer row
    # here whether it overran the pane, wrapped, or merely pushed the tail off.
    assert ([len(line.rstrip()) for line in clean.lines]
            == [len(line.rstrip()) for line in poisoned.lines]), \
        poisoned._message(
            "the escaped screen is not the same width row by row: %r"
            % ([(row, len(a.rstrip()), len(b.rstrip()))
                for row, (a, b) in enumerate(zip(clean.lines, poisoned.lines))
                if len(a.rstrip()) != len(b.rstrip())],))
    if size == WIDE:
        # Non-vacuity at the one size that fits the whole fixture: without it
        # the equality above is satisfied by a screen that drew neither.
        assert MARK * GOAL_FILL in poisoned.text, poisoned._message(
            "%d escapes did not become %d marks" % (GOAL_FILL, GOAL_FILL))


def test_legitimate_non_ascii_is_left_alone(tmp_path):
    """Only control characters are the target (ACC-DATA-007).

    An em-dash in a quoted commit subject is deliberately not stripped one
    layer down, and the fixtures carry CJK and box drawing. A guard that took
    those too would be solving the problem by deleting the prose.
    """
    keep = "em—dash 日本語 box ─│ tick ✓ ellipsis … quote « »"
    frame = proved_frame_of(prose_relay(
        tmp_path, goal=keep, log_message=keep), size=WIDE)
    for piece in ("em—dash", "日本語", "tick ✓", "quote « »"):
        assert frame.contains(piece), frame._message(
            "%r did not survive the renderer" % piece)
    assert MARK not in frame.text, frame._message(
        "a control-character mark was drawn for prose that has none")
    for bad in MOJIBAKE:
        frame.assert_not_contains(bad)


# -- every path from the model to the screen, not only the ones remembered ---


#: One relay with a control character in every kind of string a view draws.
#: The `n` is what proves a view actually rendered the poisoned field: a test
#: that only asserted "no caret notation" would pass on a view that drew
#: nothing at all.
def everything_poisoned(directory):
    return prose_relay(
        directory,
        leg_id="poisoned%s[31mleg" % ESC,
        check_id="ACC-%s[2JPROSE-001" % ESC,
        goal="a goal that forges %s a close" % SYNC_CLOSE,
        log_message="a log line that %s moves the cursor" % CURSOR_TO_5_5,
        attention="an attention item that %s erases the screen" % ERASE_DISPLAY,
        models={"runner": {"model": "opus%s[5m-5" % ESC, "effort": "high"},
                "judge": {"model": "sonnet", "effort": "medium"}},
    )


VIEW_KEYS = {
    # view -> (key that opens it, text only that view paints)
    "overview": (None, "Active Runner"),
    "legs": ("F", "Esc Overview"),
    "runners": ("W", "Esc Overview"),
    # The keybar phrase, exactly as the three views beside it use it: the
    # Overview's own keybar has no `Esc Overview`, so it is introduced by the
    # repaint. It replaced a sentence copied out of `models.py`, which pinned
    # this table to one view's prose and went stale the moment that view grew
    # past its stub (`models-view`, S3).
    "models": ("M", "Esc Overview"),
    "contract": ("C", "Esc Overview"),
}


@pytest.mark.parametrize("view", sorted(VIEW_KEYS))
def test_no_view_lets_prose_through_to_the_terminal(view, tmp_path):
    """The guard is at one choke point so that this test can be exhaustive.

    Six call sites would each need remembering; one `Canvas.write()` needs
    proving once, and then every view is a check that the path really does go
    through it. Each case asserts the mark is *drawn*, so a view that stopped
    rendering the poisoned field could not pass by drawing nothing.
    """
    key, needle = VIEW_KEYS[view]
    term = session(everything_poisoned(tmp_path), size=WIDE)
    try:
        frame = repaint(term) if key is None else term.send(key, expect=needle)
        assert_nothing_took_effect(term, frame)
        assert MARK in frame.text, frame._message(
            "the %s view drew no poisoned field at all — this case proves "
            "nothing" % view)
    finally:
        term.close()


@pytest.mark.parametrize("size", [WIDE, STANDARD, (12, 40), (8, 30), (5, 20),
                                  (3, 12)])
def test_prose_is_still_prose_at_every_terminal_size(size, tmp_path):
    term = session(everything_poisoned(tmp_path), size=size)
    try:
        frame = repaint(term)
        assert_nothing_took_effect(term, frame)
        assert term.is_running, frame._message("the TUI exited instead")
    finally:
        term.close()


def test_prose_is_still_prose_on_a_terminal_with_no_colour(tmp_path):
    term = session(everything_poisoned(tmp_path), size=WIDE, term="vt100")
    try:
        frame = repaint(term)
        assert_nothing_took_effect(term, frame)
        assert MARK in frame.text, frame._message("nothing poisoned was drawn")
    finally:
        term.close()


def test_the_mark_degrades_to_ascii_when_the_locale_cannot_carry_it(tmp_path):
    """`LC_ALL=C`: curses drops a non-ASCII glyph to a blank, silently.

    So the mark has an ASCII spelling like every other glyph. Without one the
    substitution would become a deletion on exactly the terminals least able
    to show what happened — and a deleted control character is the silent
    answer this project keeps refusing.

    The prose is three escapes between two words, and the assertion is the
    exact rendering. `ASCII_MARK in frame.text` was the first spelling of this
    and it was **vacuous**: `?` is already on any screen showing `[?2026l`, so
    a mark hardcoded past the theme — dropped to a blank here — passed it.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("LC_ALL", "LC_CTYPE", "LANG")}
    env["LC_ALL"] = "C"
    term = session(prose_relay(
        tmp_path, log_message="before" + ESC * 3 + "after"), size=WIDE, env=env)
    try:
        frame = repaint(term)
        assert_nothing_took_effect(term, frame)
        assert frame.contains("before" + ASCII_MARK * 3 + "after"), \
            frame._message(
                "under LC_ALL=C three escapes did not render as %r — a mark "
                "this locale cannot encode is dropped to a blank, which is a "
                "silent strip" % (ASCII_MARK * 3))
        assert MARK not in frame.text, frame._message(
            "the UTF-8 mark reached a screen whose locale cannot carry it")
    finally:
        term.close()


# -- the guard itself: what it covers, and that it is not vacuous ------------


def test_sanitise_replaces_exactly_the_control_characters(tmp_path):
    """Derived from Unicode, not from a list somebody typed.

    A frame can show that four sequences did not run; only this can say that
    *no* code point reaches the window as a control character. The set is
    asserted against `unicodedata`'s own `Cc` category over the whole code
    space, so a range trimmed by hand fails here rather than three legs later.
    """
    everything = {ordinal for ordinal in range(0x110000)
                  if unicodedata.category(chr(ordinal)) == "Cc"}
    assert set(chrome.CONTROL_ORDINALS) == everything, (
        "the guarded range is not Unicode's control category: missing %r, "
        "extra %r" % (sorted(everything - set(chrome.CONTROL_ORDINALS))[:8],
                      sorted(set(chrome.CONTROL_ORDINALS) - everything)[:8]))

    for ordinal in range(0x100):
        out = chrome.sanitise(chr(ordinal), MARK)
        expected = MARK if ordinal in everything else chr(ordinal)
        assert out == expected, "chr(%#04x) sanitised to %r" % (ordinal, out)
        # One character in, one character out: `len()` is the cell count
        # everywhere upstream of the write, and this is what keeps it so.
        assert len(out) == 1

    for keep in "em—dash 日本語 ─│✓… «»":
        assert chrome.sanitise(keep, MARK) == keep

    # Two placeholders in one process, and the second one again after the
    # first: the table is cached per placeholder, and a cache that answered
    # with whichever it built first would draw the UTF-8 mark on a terminal
    # that cannot encode it — which curses turns into a blank, i.e. a strip.
    assert chrome.sanitise(ESC, ASCII_MARK) == ASCII_MARK
    assert chrome.sanitise(ESC, MARK) == MARK
    assert chrome.sanitise(ESC, ASCII_MARK) == ASCII_MARK


def test_the_control_mark_has_a_spelling_in_both_glyph_tables():
    """One cell, visible, and encodable wherever the program runs.

    The mark is the whole of "a supervisor can tell something was there", so
    the two ways to lose it are asserted here rather than left to a frame:
    a table with no entry falls back to `Theme.glyph`'s `?`, and an ASCII
    table holding a non-ASCII mark is dropped to a blank by curses under
    `LC_ALL=C` — which turns the substitution back into a silent strip.
    """
    for name in ("GLYPHS", "ASCII_GLYPHS"):
        mark = getattr(theme_tokens, name).get("control")
        assert mark, "theme.%s carries no `control` mark" % name
        assert len(mark) == 1, (
            "theme.%s spells the control mark %r — it must be one cell, or "
            "every width computation in the package is off by one per control "
            "character" % (name, mark))
        assert not mark.isspace(), (
            "theme.%s spells the control mark as whitespace, which is a "
            "silent deletion wearing a mark's clothes" % name)
    theme_tokens.ASCII_GLYPHS["control"].encode("ascii")


#: Every curses call that puts text on a window. If a second one of these ever
#: appears in the package, prose has a second route to the terminal.
DRAWING_CALLS = frozenset({
    "addstr", "addnstr", "addch", "addwstr", "addnwstr", "add_wch",
    "insstr", "insnstr", "insch", "ins_wstr", "ins_nwstr", "ins_wch",
    "echochar", "echo_wchar",
})


def drawing_sweep(paths):
    """`(findings, nodes)` — every text-drawing call in `paths` and its owner.

    A finding is `(file, function, call, lineno)`. `nodes` is returned for the
    same reason `sweep()` returns it: a sweep that walked nothing reports no
    findings and is indistinguishable from a clean package.
    """
    findings = []
    nodes = 0
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        owners = {}
        for parent in ast.walk(tree):
            name = getattr(parent, "name", None)
            for child in ast.iter_child_nodes(parent):
                if isinstance(parent, (ast.ClassDef, ast.FunctionDef,
                                       ast.AsyncFunctionDef)):
                    owners[child] = "%s.%s" % (owners.get(parent, ""), name) \
                        if owners.get(parent) else name
                elif parent in owners:
                    owners[child] = owners[parent]
        for node in ast.walk(tree):
            nodes += 1
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in DRAWING_CALLS:
                findings.append((path.name, owners.get(node, "<module>"),
                                 node.func.attr, node.lineno))
    return findings, nodes


def canvas_write_body():
    """The AST of `chrome.Canvas.write`, or a loud failure."""
    tree = ast.parse((PACKAGE / "chrome.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Canvas":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "write":
                    return item
    raise AssertionError("chrome.Canvas.write no longer exists")


def test_every_string_on_the_screen_goes_through_one_guarded_write():
    """One choke point, proved rather than remembered.

    The panes a runner happens to think of are not the boundary; the boundary
    is "text becomes cells", and there is exactly one call in the package that
    does it. This is the test that would *find* a drawing path that skipped
    the guard — a second `addstr` anywhere, or a `write()` that stopped
    sanitising before it draws.
    """
    modules = package_modules()
    findings, nodes = drawing_sweep(modules)
    assert nodes > 500, "the sweep walked only %d AST nodes" % nodes
    assert len(modules) >= 6, "swept only %r" % ([p.name for p in modules],)
    assert [(f[0], f[1]) for f in findings] == [("chrome.py", "Canvas.write")], (
        "text reaches a curses window from more than one place, so the "
        "control-character guard cannot cover it: %r" % (findings,))

    write = canvas_write_body()
    guards = [node.lineno for node in ast.walk(write)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "sanitise"]
    draws = [node.lineno for node in ast.walk(write)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in DRAWING_CALLS]
    assert guards, "chrome.Canvas.write no longer sanitises what it draws"
    assert min(guards) < min(draws), (
        "Canvas.write sanitises at line %d but draws at line %d — the guard "
        "runs after the cells have landed" % (min(guards), min(draws)))


def test_the_drawing_sweep_finds_a_planted_writer():
    """Non-vacuity, in the real package — the half a filter gets wrong.

    Planted in `scripts/relay_control/` rather than in `tmp_path` so that the
    collection step is proved to reach the package it audits, exactly as
    `test_the_reader_sweep_finds_a_planted_reader` does for ACC-TUI-007.
    """
    planted = PACKAGE / "_planted_writer_probe.py"
    planted.write_text(
        "def sneak(win, model):\n"
        "    win.addstr(0, 0, model['legs'][0]['goal'])\n"
    )
    try:
        modules = package_modules()
        assert planted in modules, "the sweep does not reach the package it audits"
        findings, nodes = drawing_sweep(modules)
        assert nodes > 500
        assert [f for f in findings if f[0] == planted.name], (
            "the sweep passed a module that calls win.addstr() directly — it "
            "is vacuous, and would pass a view drawing untrusted prose past "
            "the control-character guard")
    finally:
        planted.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# invariant 3 — width is counted in cells, not in code points
# --------------------------------------------------------------------------
#
# `chrome.py` measured every rectangle with `len()`, which counts Python
# characters. A terminal draws a CJK ideograph in two columns, so a pane of CJK
# was laid out at half the width it was painted at. Measured on a captured
# frame at 160x48: the Active Leg pane is 79 cells wide, `wrap()` handed it rows
# of 79 *characters*, and the row reached 158 cells — over the vertical rule at
# column 80, across the Legs pane behind it, and wrapping onto the row below.
# `assert_within_width()` passed throughout, because 158 is less than 160 and
# nothing reached the last column: the guarantee the reserved column exists to
# give is exactly the one a pane at twice its size defeats.
#
# Every claim in this section is about *columns*, so the ones taken off a frame
# read `frame.cells` — the grid, one string per screen cell — and never
# `frame.lines`, whose string indices stop being columns the moment a row
# carries a wide character.


#: Text whose cell width is not its character count, with the width a terminal
#: actually gives it. Spelled out rather than computed from `chrome`, because a
#: test that reads its expectation from the module under test cannot fail when
#: that module changes.
CELL_WIDTHS = [
    ("", 0),
    ("abc", 3),
    ("日本語", 6),            # three ideographs, six columns
    ("ＡＢ", 4),              # fullwidth latin, two columns each
    ("ｱｲｳ", 3),              # halfwidth katakana, one column each
    ("한국어", 6),
    ("日a本", 5),             # mixed, and not divisible by two
    ("e\u0301", 1),           # a base and a combining acute share one cell
    ("─│", 2),                # the box rules the layout draws
    ("✓●○✗−…▯", 7),          # every mark the chrome puts on a screen, the
                              # control-character mark included: ACC-ROBUST-006
                              # turns on that substitution costing one cell
]


@pytest.mark.parametrize("text,width", CELL_WIDTHS)
def test_the_renderer_measures_text_in_terminal_cells(text, width):
    assert chrome.cell_width(text) == width


@pytest.mark.parametrize("text,width", CELL_WIDTHS)
def test_the_renderer_and_the_emulator_measure_a_cell_the_same_way(text, width):
    """One rule for what a cell is, not two.

    What `chrome.py` spends and what `tests/frame.py` reports have to be the
    same statement, or a frame passes a width assertion the renderer had
    already broken. Both read `unicodedata`; this is what pins them together.
    """
    assert display_width(text) == width


# -- cutting, in cells, and never in silence -------------------------------


@pytest.mark.parametrize("width", range(0, 14))
def test_a_clipped_string_never_spends_more_cells_than_it_was_given(width):
    assert display_width(chrome.clip("日本語日本語", width)) <= width


def test_clip_never_splits_a_double_width_character_across_the_edge():
    """An odd number of cells comes back one short, never one over.

    Half an ideograph is not a character. A terminal handed one either drops it
    or spills it into the cell beyond — and the cell beyond is the reserved
    last column, or the pane in the next one.
    """
    assert chrome.clip("日本語", 6) == "日本語"       # an exact fit is not a cut
    assert chrome.clip("日本語", 5) == "日本…"
    assert chrome.clip("日本語", 4) == "日…"          # 語 cannot be halved
    assert chrome.clip("日本語", 3) == "日…"
    assert chrome.clip("日本語", 2) == "…"


#: Every width at which the ellipsis does not fit whole. The cut was silent at
#: all of them: `clip()` answered `text[:width]`, with no mark, so a row cut
#: with one cell to spare came back reading like a row nothing happened to.
@pytest.mark.parametrize("ellipsis,width,expected", [
    ("…", 1, "…"),
    ("…", 2, "a…"),
    ("...", 1, "."),
    ("...", 2, ".."),
    ("...", 3, "..."),
    ("...", 4, "a..."),
])
def test_a_cut_says_so_even_where_the_mark_is_all_that_fits(ellipsis, width,
                                                            expected):
    assert chrome.clip("abcdef", width, ellipsis) == expected


@pytest.mark.parametrize("ellipsis", ["…", "..."])
def test_no_cut_this_module_makes_is_ever_silent(ellipsis):
    """The named-absence rule, stated over every width there is.

    A row that lost characters and says nothing about it reads as a row its
    author wrote that way, which is the lie this project keeps refusing. The
    mark is taken out of the width *before* the text spends what is left, so
    there is no width at which the text can crowd the mark out.
    """
    text = "the quick brown fox"
    cuts = 0
    for width in range(1, len(text) + 4):
        cut = chrome.clip(text, width, ellipsis)
        assert display_width(cut) <= width, (width, cut)
        if cut == text:
            continue
        cuts += 1
        assert cut.endswith(ellipsis[-1]), (
            "%d cells of %r came back as %r, with no mark at all"
            % (width, text, cut))
    assert cuts, "no width cut this text at all — the sweep proves nothing"


def test_elide_left_keeps_the_tail_and_marks_the_front():
    # A synthetic path, not this machine's: the tail is what the assertion is
    # about, and a real home directory would make it one developer's.
    assert chrome.elide_left("/a/b/c/d/fixtures", 10) == "…/fixtures"
    assert chrome.elide_left("/short", 10) == "/short"


@pytest.mark.parametrize("width", range(1, 14))
def test_an_elided_path_never_spends_more_cells_than_it_was_given(width):
    assert display_width(chrome.elide_left("日本語日本語", width)) <= width


def test_wrap_breaks_double_width_text_on_the_cell_boundary():
    assert chrome.wrap("日本語日本語日本語", 6) == ["日本語", "日本語", "日本語"]


def test_wrap_joins_two_words_only_when_their_cells_fit():
    """The half of `wrap()` a hard break hides.

    `日本語 abcd` is eleven cells and does not fit in eight. Counted as
    characters it is eight, so the two words are joined — and the hard break
    that then cuts the joined line back to eight cells splits `abcd` in the
    middle, rather than breaking at the space that was there all along. The
    line is never *too wide* either way, so no width assertion can see it.
    """
    assert chrome.wrap("日本語 abcd", 8) == ["日本語", "abcd"]


#: Words of two cells and of four, so that `len() + 1 + len()` fits a width
#: `cell_width() + 1 + cell_width()` does not.
WRAP_WORDS = ["日本", "語彙", "abcd", "表現", "xy"]


@pytest.mark.parametrize("width", range(2, 12))
def test_a_word_that_fits_a_line_is_never_split_across_two(width):
    lines = chrome.wrap(" ".join(WRAP_WORDS), width)
    assert lines, width
    survived = " ".join(lines).split()
    for word in WRAP_WORDS:
        if display_width(word) <= width:
            assert word in survived, (width, lines)


@pytest.mark.parametrize("width", range(2, 12))
def test_no_wrapped_line_ends_in_a_blank(width):
    """A line ending in a space is a join that should not have happened.

    `wrap()` only ever joins on a space, and only when the join fits; a hard
    break falls inside a single word, which has none. So a trailing blank means
    two words were joined on a measure that was wrong and then cut apart again.
    """
    for line in chrome.wrap(" ".join(WRAP_WORDS), width):
        assert line == line.rstrip(), (width, line)


@pytest.mark.parametrize("width", range(1, 10))
def test_no_wrapped_line_is_wider_than_the_width_it_was_given(width):
    for line in chrome.wrap("日本語 abc 한국어 xyz", width):
        # The one exception is a single character wider than the whole line:
        # it keeps the line rather than being dropped, and `Canvas.write()`
        # cuts it to the mark. Dropping it would be a silent deletion.
        assert display_width(line) <= width or len(line) == 1, (width, line)


# -- the row fitter, moved here from three view modules --------------------


def test_the_row_fitter_marks_the_row_once_and_reserves_the_cell_first():
    """A row of several styled segments is cut as one thing, and marked once.

    Clipping the segments cannot do it: each one knows only its own width, so
    the row is either marked once per segment or — the case that shipped — not
    at all, because every segment fitted and the row did not.
    """
    parts = [("aaaa", theme_tokens.BODY), ("bbbb", theme_tokens.BODY)]
    fitted = chrome.fit_parts(parts, 6, "…")
    assert "".join(text for text, _ in fitted) == "aaaab…"
    assert [text for text, _ in fitted].count("…") == 1


def test_the_row_fitter_measures_its_segments_in_cells():
    parts = [("日本語", theme_tokens.BODY), ("abc", theme_tokens.BODY)]
    assert chrome.fit_parts(parts, 9, "…") == list(parts)   # 6 + 3, exactly
    assert "".join(t for t, _ in chrome.fit_parts(parts, 8, "…")) == "日本語a…"


@pytest.mark.parametrize("width", range(0, 14))
def test_a_fitted_row_never_spends_more_cells_than_it_was_offered(width):
    parts = [("日本", theme_tokens.BODY), ("語", theme_tokens.BODY),
             ("tail", theme_tokens.BODY)]
    fitted = chrome.fit_parts(parts, width, "…")
    assert sum(display_width(text) for text, _ in fitted) <= width


def test_exactly_one_module_in_the_package_fits_a_row_to_a_width():
    """`(parts, width, ellipsis)` was written three times, byte for byte.

    `runners.py`, `contract.py` and `models.py` each carried it, because three
    view legs across three stages were each forbidden to edit `chrome.py`, and
    each recorded the ask in a baton. Their bodies were identical — the same
    cut, the same reserved cell, the same theme ellipsis — so there was nothing
    to choose between them and the move was a move rather than a merge.

    Stated as a sweep of the package rather than as three `hasattr` checks, so
    that it also fails on a fourth copy under a fourth name.
    """
    modules = package_modules()
    assert modules, "the sweep found no modules — it proves nothing"
    definers = []
    for path in modules:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef) and [
                    arg.arg for arg in node.args.args
            ] == ["parts", "width", "ellipsis"]:
                definers.append((path.name, node.name))
    assert definers == [("chrome.py", "fit_parts")], definers


# -- what the panes and the chrome do with it, on a captured screen --------


def rule_cells(frame):
    """Where the box rules are, in screen *columns*, row by row.

    The rules are the panes' own edges, drawn by the layout from the terminal's
    size, and nothing a view draws may move one — so a row that overran its
    rectangle shows up here before it shows up anywhere else.

    Columns, not string indices: the cell after a double-width character is the
    empty string in `frame.lines`, so an index into a line carrying CJK is not
    the column the terminal drew at.
    """
    return [tuple(col for col, cell in enumerate(row) if cell in ("│", "─"))
            for row in frame.cells]


def last_column(frame):
    """What every row says in the column the chrome reserves.

    `assert_within_width()` makes the same claim, and this one reads the cells:
    a failure says what was drawn in the margin rather than only that something
    was.
    """
    return [row[frame.cols - 1] for row in frame.cells]


#: A goal of nothing but characters a terminal draws in two columns, and a
#: plain one of the same *cell* width — 60 ideographs is 120 cells, and so is
#: 120 `x`. Neither carries a space, so `wrap()` breaks both at the pane's edge
#: rather than at a word, and the two frames can differ only where the renderer
#: measured one of them wrong.
CJK_GOAL = "日本語で書かれた目標" * 6
PLAIN_GOAL = "x" * (2 * len(CJK_GOAL))


#: 40 columns at 24 rows rather than at 12: the Active Leg pane needs a body
#: row left over after the leg id row, or the goal is not drawn at all and
#: the comparison below is satisfied by two screens that both say nothing.
@pytest.mark.parametrize("size", [WIDE, STANDARD, (24, 40)])
def test_a_pane_of_double_width_text_stays_inside_its_rectangle(size, tmp_path):
    """The defect three view legs could see and none of them could reach.

    Both relays differ in one field. If the renderer counts cells they draw the
    same rectangle; if it counts characters the CJK one draws twice as wide.
    """
    plain = proved_frame_of(prose_relay(tmp_path / "plain", goal=PLAIN_GOAL,
                                        path=tmp_path), size=size)
    wide = proved_frame_of(prose_relay(tmp_path / "wide", goal=CJK_GOAL,
                                       path=tmp_path), size=size)

    assert wide.contains("日本語"), wide._message(
        "the double-width goal was not drawn at all — this proves nothing")
    assert rule_cells(wide) == rule_cells(plain), wide._message(
        "double-width text moved the panes' own edges: the pane overran its "
        "rectangle and painted over the rule beside it")
    assert last_column(wide) == [" "] * wide.rows, wide._message(
        "double-width text reached the column the chrome reserves")
    wide.assert_within_width()


#: Every size this leg certifies. The last two are the ones the status-bar
#: defect made impossible: `contract-view` and `models-view` both recorded that
#: below about ten columns `draw_status_bar()` reached the last cell on *every*
#: view with no keystroke sent, so the strict `assert_within_width()` could
#: certify no frame at all there and the row it named was the chrome's own.
KEYBAR_SIZES = [WIDE, STANDARD, (12, 40), (8, 30), (5, 20), (3, 12), (3, 8)]

#: Two sizes `session()` cannot reach: at two rows there is no keybar row, and
#: at four columns `_fit_bindings()` drops every binding, so `q Quit` — the
#: needle that means "the program has painted" everywhere else — is not drawn.
SILENT_SIZES = [(2, 6), (4, 4)]


@pytest.mark.parametrize("size", KEYBAR_SIZES)
def test_the_chrome_leaves_its_reserved_column_empty_at_every_size(size):
    term = session(FIXTURES / "agent-service", size=size)
    try:
        frame = repaint(term)
    finally:
        term.close()
    frame.assert_finished()
    assert last_column(frame) == [" "] * frame.rows, frame._message(
        "the chrome drew in the column it reserves, so no frame at this size "
        "can be certified by the strict assert_within_width()")
    frame.assert_within_width()
    assert "Traceback" not in frame.text, frame._message("the TUI raised")


@pytest.mark.parametrize("size", SILENT_SIZES)
def test_the_smallest_terminals_reserve_their_last_column_too(size):
    """The two sizes the status bar was reaching the margin at.

    The wait runs on the phase dot, which the status bar draws at every width
    down to two cells — and which is the very segment that was spaced against
    `canvas.width` while the bar and the count beside it used `width - 1`.
    """
    rows, cols = size
    term = TerminalSession(
        [sys.executable, str(ENTRY), str(FIXTURES / "agent-service")],
        rows=rows, cols=cols, env=UTF8_ENV)
    term.start()
    try:
        frame = term.send(IDLE_KEY, expect="●")
    finally:
        term.close()
    assert frame.paint_end == "synchronised", frame._message(
        "captured on %r — a repaint this program did not vouch for"
        % frame.paint_end)
    assert last_column(frame) == [" "] * frame.rows, frame._message(
        "the chrome drew in the column it reserves")
    frame.assert_within_width()
    assert "Traceback" not in frame.text, frame._message("the TUI raised")


#: Eight columns is where the Legs pane's header has exactly one cell left for
#: its count: the pane is seven cells wide, `Legs` takes four and the gap two.
NARROW_META = (16, 8)


@pytest.mark.parametrize("locale_name,mark", [("UTF-8", "…"), ("C", ".")])
def test_a_count_cut_to_one_cell_says_it_was_cut(locale_name, mark):
    """The silent cut, on the real fixture, at the width that reaches it.

    `clip()` answered `text[:width]` — no mark — whenever it was left fewer
    cells than the ellipsis is wide. The Legs pane's count has one cell here, so
    the header read `Legs  2`: a figure a reader has every reason to believe,
    against a relay that is thirty-six legs long. Under `LC_ALL=C` the ellipsis
    is three cells and the silence covered three widths rather than one.
    """
    if locale_name == "C":
        env = {key: value for key, value in os.environ.items()
               if key not in ("LC_ALL", "LC_CTYPE", "LANG")}
        env["LC_ALL"] = "C"
    else:
        env = UTF8_ENV
    rows, cols = NARROW_META
    term = TerminalSession(
        [sys.executable, str(ENTRY), str(FIXTURES / "agent-service")],
        rows=rows, cols=cols, env=env)
    term.start()
    term.wait_for(READY)
    try:
        frame = repaint(term)
    finally:
        term.close()
    frame.assert_finished()

    row = next((index for index, line in enumerate(frame.raw_lines)
                if line.startswith("Legs")), None)
    assert row is not None, frame._message("no Legs pane on this frame")
    meta = frame.raw_lines[row][len("Legs"):].strip()
    done, total = leg_figures(FIXTURES / "agent-service")
    count = "%d/%d" % (done, total)

    assert meta, frame._message("the pane's header lost its figure altogether")
    assert meta != count, frame._message(
        "this width holds the whole count, so it cannot show a silent cut")
    assert not count.startswith(meta), frame._message(
        "the count was cut to %r, which reads as a count and is not one" % meta)
    assert meta.endswith(mark), frame._message(
        "under LC_ALL=%s a cut is marked %r; this one says %r"
        % (locale_name, mark, meta))


# --------------------------------------------------------------------------
# ACC-TUI-006 — a blocked check is not a failed one
# --------------------------------------------------------------------------


#: The SGR foreground parameters ncurses emits for the two colours, spelled
#: here rather than read from `theme.py` so that a theme change fails a test
#: instead of being silently agreed to. ncurses emits 8-colour SGR, so these
#: are `31` and `35` and not `38;5;n`.
FAILED_FG, BLOCKED_FG = 31, 35


def two_states_relay(directory):
    """A relay carrying one failed check and one blocked one, and nothing else.

    Both reach the screen through `theme.status()`, which used to answer with
    one glyph *and* one attribute for the pair — so the two states were
    byte-identical cells, in colour and in monochrome, and only the word beside
    the check id told them apart. They are not the same thing to a supervisor:
    blocked needs a decision from them, failed needs a fix leg from the coach.
    """
    directory = Path(directory)
    (directory / "batons").mkdir(parents=True, exist_ok=True)
    (directory / "legs.json").write_text(json.dumps({
        "relay": "two-states",
        "stages": [{"id": "S1", "name": "Stage one", "legs": ["the-leg"]}],
        "legs": [{"id": "the-leg", "stage": "S1", "kind": "fix",
                  "goal": "a leg", "status": "running"}],
    }))
    (directory / "state.json").write_text(json.dumps({
        "relay": "two-states", "phase": "running", "currentStage": "S1",
        "currentLeg": "the-leg",
        "checks": {
            "ACC-AA-001": {"status": "failed", "stage": "S1",
                           "claimedBy": "the-leg", "round": 2,
                           "reason": "the write path still takes a bare string",
                           "fixLeg": "fix-the-write-path"},
            "ACC-AA-002": {"status": "blocked", "stage": "S1",
                           "claimedBy": "the-leg", "round": 1,
                           "reason": "no staging database was reachable"},
        },
    }))
    (directory / "dashboard.json").write_text(json.dumps({
        "title": "Two states", "path": str(directory)}))
    return directory


def two_states_frame(relay, **kwargs):
    """`(frame, failed_row, blocked_row)` off the Contract view.

    A check id is text only the Contract view paints, so it is a needle the
    repaint introduces rather than one the Overview already satisfies.
    """
    term = session(relay, size=WIDE, **kwargs)
    try:
        frame = term.send("C", expect="ACC-AA-001")
    finally:
        term.close()
    assert frame.paint_end == "synchronised", frame._message(
        "captured on %r — a repaint this program did not vouch for"
        % frame.paint_end)
    return frame, row_of(frame, "ACC-AA-001"), row_of(frame, "ACC-AA-002")


def test_a_blocked_check_and_a_failed_one_are_not_the_same_cells(tmp_path):
    frame, failed_row, blocked_row = two_states_frame(two_states_relay(tmp_path))
    frame.assert_attrs("failed", row=failed_row, fg=FAILED_FG, has=["bold"])
    frame.assert_attrs("blocked", row=blocked_row, fg=BLOCKED_FG, has=["bold"])
    # And the glyph carries its own state's attribute rather than the row's:
    # that is the half of ACC-TUI-006 `theme.status()` exists to hold, and it
    # is what makes the two rows differ at a glance and not only in the word.
    assert (frame.attrs_for("✗", row=failed_row)
            == frame.attrs_for("failed", row=failed_row)), frame._message(
        "the failed row's glyph and word were drawn differently")
    assert (frame.attrs_for("✗", row=blocked_row)
            == frame.attrs_for("blocked", row=blocked_row)), frame._message(
        "the blocked row's glyph and word were drawn differently")
    assert (frame.attrs_for("✗", row=failed_row)
            != frame.attrs_for("✗", row=blocked_row)), frame._message(
        "a blocked check and a failed one are the same cell on this screen")


def test_blocked_is_still_not_failed_on_a_terminal_with_no_colour(tmp_path):
    """Colour alone is not a distinction — a monochrome terminal has none.

    Both states are drawn `✗`, so on a terminal with no colour the weight is
    the whole of the difference. `status.failed` and `status.blocked` therefore
    carry different monochrome spellings as well as different pairs.
    """
    frame, failed_row, blocked_row = two_states_frame(
        two_states_relay(tmp_path), term="vt100")
    frame.assert_attrs("failed", row=failed_row, has=["bold", "reverse"],
                       lacks=["underline"])
    frame.assert_attrs("blocked", row=blocked_row, has=["bold", "underline"],
                       lacks=["reverse"])
    assert (frame.attrs_for("✗", row=failed_row)
            != frame.attrs_for("✗", row=blocked_row)), frame._message(
        "with no colour, a blocked check and a failed one are the same cell")


# --------------------------------------------------------------------------
# the chrome's own arithmetic, at inputs no fixture reaches
# --------------------------------------------------------------------------
#
# Everything above is a frame, and everything a reader sees stays a frame. This
# section is for the promises `chrome.py` makes to the view that has not been
# written yet: `Pane.right()` places a value by measuring it, `Pane.header()`
# places a meta by measuring it, `draw_keybar()` spaces its legend by measuring
# it — and no view puts double-width text through any of them *today*, so a
# screen cannot tell a module that measures cells from one that measures
# characters there. Left at that, the fix to those three would be a change no
# test could fail on, which is the same thing as no fix at all.
#
# `Canvas` and `Pane` are ordinary Python given a window: `write()` calls
# `addstr` and the rules call `hline`/`vline`, and nothing else in either class
# touches curses. So a window that records what it was asked to paint is enough
# to state the arithmetic directly, and the columns are read back with the
# emulator's measure rather than the renderer's — a renderer that miscounts
# cannot then certify itself.


class RecordingWindow:
    """A curses window that records what it was asked to paint.

    Raises `curses.error` outside its rectangle exactly as a real window does,
    so `Canvas.write()`'s "degrade, not crash" path is the one under test and
    not a stub of it.
    """

    def __init__(self, rows, cols):
        self.rows = rows
        self.cols = cols
        self.writes = []      # (row, col, text)
        self.rules = []       # (kind, row, col, length)

    def addstr(self, row, col, text, attr=0):
        if not (0 <= row < self.rows and 0 <= col < self.cols):
            raise curses.error("addstr outside the window")
        self.writes.append((row, col, text))

    def hline(self, row, col, ch, length, attr=0):
        self.rules.append(("h", row, col, length))

    def vline(self, row, col, ch, length, attr=0):
        self.rules.append(("v", row, col, length))


class PlainTheme:
    """The `Theme` interface with the attributes stubbed and the glyphs real.

    The glyph spellings come from `theme.GLYPHS` deliberately: what is under
    test here is the arithmetic *around* the glyphs, and a table of this file's
    own would be a second source of truth for what the chrome draws.
    """

    def __init__(self, glyphs=None):
        self._glyphs = dict(theme_tokens.GLYPHS, **(glyphs or {}))
        self.hline = "-"
        self.vline = "|"

    def attr(self, token):
        return 0

    def glyph(self, name):
        return self._glyphs.get(name, "?")


def paper_canvas(rows, cols, glyphs=None):
    """`(window, canvas)` — a canvas that draws onto a record, not a terminal."""
    win = RecordingWindow(rows, cols)
    return win, chrome.Canvas(win, PlainTheme(glyphs), 0, 0, rows, cols)


def painted_columns(win):
    """Every screen column the window was asked to paint.

    Measured with `display_width` — the emulator's rule — so a renderer that
    counted characters cannot supply the evidence that it counted cells.
    """
    columns = set()
    for _, col, text in win.writes:
        columns.update(range(col, col + display_width(text)))
    return columns


def rightmost(win):
    return max(painted_columns(win), default=-1)


#: Untrusted prose in every field of the chrome's own three rows. All of it is
#: double-width, so a row measured in characters is drawn at twice the cells it
#: was budgeted — and the chrome works in `width - 1`, so the overrun lands in
#: the reserved column before it lands anywhere else.
CJK_MODEL = {
    "relay": {"title": "日本語のリレー", "path": "/一/二/三/四五六",
              "phase": "実行中"},
    "metrics": {"elapsed": "四時間", "tokens": {"input": "十万", "output": "千"}},
    "legCounts": {"completed": 27, "total": 36},
}


@pytest.mark.parametrize("cols", range(2, 40))
def test_the_header_leaves_the_reserved_column_alone_at_every_width(cols):
    win, canvas = paper_canvas(1, cols)
    chrome.draw_header(canvas, CJK_MODEL)
    assert rightmost(win) <= cols - 2, (
        "the header painted column %d of %d" % (rightmost(win), cols))


@pytest.mark.parametrize("cols", range(2, 40))
def test_the_status_bar_leaves_the_reserved_column_alone_at_every_width(cols):
    """The defect two view legs recorded and neither could reach.

    The bar and the count were spaced against `width - 1`; the phase segments
    beside them were spaced against `canvas.width`, and `Canvas.write()` clips
    at the canvas rather than at the margin. So below about ten columns the
    status row was full-width on *every* view, with no keystroke sent, and the
    strict `assert_within_width()` could certify no frame at those sizes.
    """
    win, canvas = paper_canvas(1, cols)
    chrome.draw_status_bar(canvas, CJK_MODEL)
    assert rightmost(win) <= cols - 2, (
        "the status bar painted column %d of %d" % (rightmost(win), cols))


@pytest.mark.parametrize("cols", range(2, 60))
def test_the_keybar_leaves_the_reserved_column_alone_at_every_width(cols):
    win, canvas = paper_canvas(1, cols)
    chrome.draw_keybar(canvas, (("上下", "選択"), ("Tab", "次"), ("q", "Quit")))
    assert rightmost(win) <= cols - 2, (
        "the keybar painted column %d of %d" % (rightmost(win), cols))


#: A glyph table whose status marks are two cells each. Every real one is a
#: single cell today, which is exactly why this is here: the legend's width is
#: computed from the table, and a table is *data*. Counted as characters, a
#: table that grew a two-cell mark would space the legend at half its width and
#: run it off the row it is right-aligned against — silently, on every view.
WIDE_GLYPHS = {"completed": "済", "running": "走", "pending": "待",
               "failed": "失", "cancelled": "止"}


@pytest.mark.parametrize("cols", range(2, 90))
def test_the_keybars_legend_is_measured_in_the_cells_its_glyphs_take(cols):
    win, canvas = paper_canvas(1, cols, glyphs=WIDE_GLYPHS)
    chrome.draw_keybar(canvas, (("q", "Quit"),))
    assert rightmost(win) <= cols - 2, (
        "the legend painted column %d of %d" % (rightmost(win), cols))


def test_a_right_aligned_value_ends_on_the_panes_last_usable_cell():
    """`Pane.right()` places its text by measuring it, so the measure decides.

    Counting characters starts a six-cell value eight cells early and the row
    stops three cells short of where it says it ends — or, in a pane that runs
    to the margin, three cells past it.
    """
    win, canvas = paper_canvas(4, 21)
    pane = canvas.pane(0, 0, 4, 20, "Runner")
    assert pane.body_width == 20
    pane.right(0, "四時間")                       # three ideographs, six cells
    _, col, text = win.writes[-1]
    assert (col, text) == (14, "四時間")
    assert col + display_width(text) == pane.body_width


def test_a_panes_meta_is_placed_by_its_cells_and_not_its_characters():
    win, canvas = paper_canvas(4, 21)
    pane = canvas.pane(0, 0, 4, 20, "Legs")
    pane.header("三十六件")                        # four ideographs, eight cells
    _, col, text = win.writes[-1]
    assert (col, text) == (12, "三十六件")
    assert col + display_width(text) == pane.body_width


def test_a_panes_meta_is_budgeted_against_the_titles_cells():
    """The room left for the meta is the header's width less the *title's cells*.

    A seven-ideograph title is fourteen cells and seven characters. Counted as
    characters it leaves the meta eleven cells of a twenty-cell header — and the
    title is already using fourteen of them, so the meta is drawn on top of the
    title's last three. Right-aligning it correctly is not enough; the budget
    has to be right as well, and the two are separate arithmetic.
    """
    win, canvas = paper_canvas(4, 21)
    pane = canvas.pane(0, 0, 4, 20, "日本語のパネル")     # seven ideographs
    pane.header("1-2 of 30")
    (_, title_col, title), (_, meta_col, meta) = win.writes[-2:]
    assert (title_col, title) == (0, "日本語のパネル")
    assert meta_col >= display_width(title) + 2, (
        "the meta was drawn over the title: the title ends at cell %d and the "
        "meta starts at %d" % (display_width(title), meta_col))
    assert meta_col + display_width(meta) == pane.body_width


def test_a_write_reports_the_cells_it_spent_and_not_the_characters():
    """`Canvas.segments()` advances by what `write()` returns, so this is where
    a row of several styled segments starts overlapping itself."""
    win, canvas = paper_canvas(1, 20)
    assert canvas.write(0, 0, "日本語") == 6
    assert canvas.segments(0, [("日本", theme_tokens.BODY),
                               ("語", theme_tokens.BODY)]) == 6
    assert [col for _, col, _ in win.writes[-2:]] == [0, 4]


def test_a_write_starting_left_of_the_canvas_lands_on_its_own_column():
    win, canvas = paper_canvas(1, 20)
    canvas.write(0, -4, "日本語abc")               # 日本 are wholly off the edge
    assert win.writes[-1][1:] == (0, "語abc")


def test_a_double_width_character_straddling_the_edge_is_dropped_whole():
    """Half a character is not a character.

    Four cells of `日本語abc` are off the left edge and three are asked for, so
    `本` is half on and half off. It is dropped and its remaining cell left
    blank, rather than drawn a column early and pushing everything after it out
    of line.
    """
    win, canvas = paper_canvas(1, 20)
    canvas.write(0, -3, "日本語abc")
    assert win.writes[-1][1:] == (1, "語abc")


def test_a_clipped_write_is_marked_in_the_themes_own_ellipsis():
    """Not `clip()`'s literal default.

    Under a locale that cannot encode `…` curses drops that cell to a blank,
    and a truncation with a blank where its mark should be is a silent one —
    which is why three view modules were cutting their rows themselves.
    """
    win, canvas = paper_canvas(1, 6, glyphs={"ellipsis": "..."})
    canvas.write(0, 0, "abcdefgh")
    assert win.writes[-1][2] == "abc..."

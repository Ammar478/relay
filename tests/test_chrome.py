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
import json
import locale
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRIPTS = REPO / "scripts"
PACKAGE = SCRIPTS / "relay_control"
ENTRY = PACKAGE / "__main__.py"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))

from frame import TerminalSession  # noqa: E402

import relay_model  # noqa: E402
from relay_control import app  # noqa: E402

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

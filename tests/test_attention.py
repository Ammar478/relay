"""Frame tests for the attention band (ACC-TUI-005).

The band is the part of the dashboard that decides rather than reports, and
this file holds it to that: `NEEDS YOUR CALL` above the notes, drawn so that a
reader can tell the two apart *without reading them*, wrapped to the width the
terminal actually has, and never — at any size, on any model — an empty strip
of screen under the status bar.

Two rules from `.relay/skills/` this file follows:

* **"Visually distinct" is a claim about the attribute plane.** A test that
  read the labels would pass on a band that drew `NEEDS YOUR CALL` in exactly
  the styling of `NOTE`, which is the defect the check exists to prevent. Every
  distinctness assertion here goes through `attr_runs()` / `assert_attrs()`.
* **`send(keys, expect=NEEDLE)` only where `NEEDLE` is text the repaint
  introduces**, and a negative assertion is made behind such a barrier — the
  band's absence on another view is asserted on a frame that has already proved
  it is the other view's.

Figures are read from the fixture at assert time, through `relay_model.build()`
in process: how many `NOTE` items the agent-service fixture carries is the
fixture's business, and a test that hardcoded it would be certifying itself
after the next refresh.

The header's title fallback (`chrome.relay_title`) is tested here too. It is
not this check's subject; it is a defect the `tui-skeleton` runner flagged
about its own work — a relay with no title read `Relay Control`, the program's
name, rather than its own. It is fixed on this leg, and `tests/test_chrome.py`
belongs to a leg that is settled, so its guard lives here with this note.
"""

import json
import locale
import os
import re
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
from relay_control import attention, chrome  # noqa: E402

# The keybar is drawn on every view at every size, so it is the one needle that
# always means "the program has painted a screen".
READY = "q Quit"

WIDE = (48, 160)
STANDARD = (24, 80)

#: The band starts on the row under the status bar. Rows 0 and 1 are the
#: header and the status bar, which is what "directly under" means.
BAND_TOP = 2

AGENT_SERVICE = FIXTURES / "agent-service"

# ncurses emits 8-colour SGR on xterm-256color, so these are the foreground
# parameters `theme.ATTENTION` reaches the terminal as.
BAD_FG = 31        # attention.bad — red, bold
NOTE_FG = 36       # attention.note — cyan
CALM_FG = 32       # attention.calm — green


# --------------------------------------------------------------------------
# harness — the same shape as tests/test_chrome.py, which is the pattern
# --------------------------------------------------------------------------


def _utf8_env():
    """A child environment whose locale can encode the glyph table."""
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
    term = session(relay_dir, size=size, **kwargs)
    try:
        return term.frame()
    finally:
        term.close()


def row_of(frame, needle):
    row = frame.find(needle)
    if row is None:
        raise AssertionError(frame._message("no line contains %r" % needle))
    return row


# --------------------------------------------------------------------------
# the model, in process — never through JSON (ACC-DATA-003)
# --------------------------------------------------------------------------


def model_of(relay_dir):
    return relay_model.build(str(relay_dir))


def items_of(relay_dir, level=None):
    """The fixture's own attention items, from `build()` at assert time."""
    items = model_of(relay_dir)["attention"]
    return [i for i in items if level is None or i["level"] == level]


def band_of(frame, relay_dir):
    """`(height, lines)` — the rows the band claimed, off the captured frame.

    The height is what `height()` answers for this frame's width, so a band
    that painted more rows than it asked for is caught by the row *after* the
    band being something other than the first pane.
    """
    rows = attention.height(model_of(relay_dir), frame.cols)
    return rows, [frame.lines[row] for row in range(BAND_TOP, BAND_TOP + rows)]


def relay_at(directory, legs=None, dashboard=None):
    """A relay directory written for one test, with a path of its own.

    `path` is written so that the header's *title* is the only place a test's
    chosen directory name can come from: with the path elided to the same name
    there would be no telling which of the two the header was showing.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "legs.json").write_text(json.dumps(
        legs if legs is not None else {"legs": []}))
    if dashboard is not None:
        (directory / "dashboard.json").write_text(json.dumps(dashboard))
    return directory


# --------------------------------------------------------------------------
# ACC-TUI-005 — where the band is, and what order it is in
# --------------------------------------------------------------------------


def test_the_band_sits_directly_under_the_status_bar():
    frame = frame_of(AGENT_SERVICE)
    assert "RUNNING" in frame.lines[1], frame._message(
        "row 1 is not the status bar, so 'directly under' means nothing here")
    assert row_of(frame, "NEEDS YOUR CALL") == BAND_TOP, frame._message(
        "the worst item must be on the first row under the status bar")


def test_needs_your_call_is_drawn_above_every_note():
    frame = frame_of(AGENT_SERVICE)
    bad = row_of(frame, "NEEDS YOUR CALL")
    notes = [row for row, line in enumerate(frame.lines)
             if line.startswith("NOTE")]
    assert notes, frame._message("the band draws no NOTE row at all")
    assert bad < min(notes), frame._message(
        "a NOTE is drawn above the item that needs a human")


def test_the_evidence_item_names_the_check_it_is_about():
    """ACC-TUI-005's evidence: the `create_teams_channel` item, labelled."""
    frame = frame_of(AGENT_SERVICE)
    row = row_of(frame, "NEEDS YOUR CALL")
    assert "create_teams_channel" in frame.lines[row], frame._message(
        "the NEEDS YOUR CALL row does not carry its own text")


def test_every_note_the_model_carries_gets_a_row():
    frame = frame_of(AGENT_SERVICE)
    rows, lines = band_of(frame, AGENT_SERVICE)
    notes = [line for line in lines if line.startswith("NOTE")]
    assert len(notes) == len(items_of(AGENT_SERVICE, "note")), frame._message(
        "the band drew %d NOTE rows for %d NOTE items"
        % (len(notes), len(items_of(AGENT_SERVICE, "note"))))


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_the_band_occupies_exactly_the_rows_it_asked_for(size):
    """`height()` and `draw()` are one answer, and the panes prove it.

    If the band painted more rows than it claimed it would overwrite the pane
    below; fewer, and it would leave a blank strip that reads as a crash.
    """
    frame = frame_of(AGENT_SERVICE, size=size)
    rows, lines = band_of(frame, AGENT_SERVICE)
    assert rows > 0
    assert all(line.strip() for line in lines), frame._message(
        "the band claimed %d rows and left one of them blank" % rows)
    assert frame.lines[BAND_TOP + rows].startswith("Active Leg"), frame._message(
        "the first pane must start on the row after the band's %d" % rows)


# --------------------------------------------------------------------------
# ACC-TUI-005 — "visually distinct" is a claim about the attribute plane
# --------------------------------------------------------------------------


def test_needs_your_call_and_note_are_not_drawn_alike():
    frame = frame_of(AGENT_SERVICE)
    note_row = min(row for row, line in enumerate(frame.lines)
                   if line.startswith("NOTE"))
    bad = frame.run_with("NEEDS YOUR CALL", row=BAND_TOP)
    note = frame.run_with("NOTE", row=note_row)
    assert bad.attrs != note.attrs, frame._message(
        "NEEDS YOUR CALL and NOTE are both drawn %s" % bad.attrs.describe())


def test_the_two_labels_carry_their_own_colour_pairs():
    frame = frame_of(AGENT_SERVICE)
    note_row = min(row for row, line in enumerate(frame.lines)
                   if line.startswith("NOTE"))
    frame.assert_attrs("NEEDS YOUR CALL", fg=BAD_FG, has="bold", row=BAND_TOP)
    frame.assert_attrs("NOTE", fg=NOTE_FG, lacks="bold", row=note_row)


def test_the_two_labels_stay_distinct_on_a_terminal_with_no_colour():
    """The distinction cannot be carried by colour alone.

    A monochrome terminal is the one that would quietly lose the difference
    between "a human is needed" and "for the record", and it is the one no
    frame test would notice losing it.
    """
    frame = frame_of(AGENT_SERVICE, term="vt100")
    note_row = min(row for row, line in enumerate(frame.lines)
                   if line.startswith("NOTE"))
    bad = frame.run_with("NEEDS YOUR CALL", row=BAND_TOP)
    note = frame.run_with("NOTE", row=note_row)
    assert bad.attrs != note.attrs, frame._message(
        "with no colour both labels are drawn %s" % bad.attrs.describe())
    assert bad.attrs.flags, frame._message(
        "NEEDS YOUR CALL has no monochrome spelling at all")


def test_the_bad_item_is_distinct_along_its_whole_row_not_just_its_label():
    """The distinction survives a reader who is looking at the text.

    Asserted off the runs of the row itself: every attributed run on the
    `NEEDS YOUR CALL` row carries the level's colour, so the item is one
    visual object rather than a red word in front of ordinary prose.
    """
    frame = frame_of(AGENT_SERVICE)
    runs = [run for run in frame.attr_runs(BAND_TOP) if run.text.strip()]
    assert len(runs) >= 2, frame._message(
        "the band's first row is drawn as one undifferentiated run")
    assert all(run.attrs.fg == (BAD_FG,) for run in runs), frame._message(
        "the NEEDS YOUR CALL row mixes the level's colour with plain text: %s"
        % "; ".join("%r %s" % (run.text[:12], run.attrs.describe())
                    for run in runs))

    note_row = min(row for row, line in enumerate(frame.lines)
                   if line.startswith("NOTE"))
    note_runs = [run for run in frame.attr_runs(note_row) if run.text.strip()]
    assert not any(run.attrs.fg == (BAD_FG,) for run in note_runs), \
        frame._message("a NOTE row is drawn in the NEEDS YOUR CALL colour")


# --------------------------------------------------------------------------
# ACC-TUI-005 — wrapped to the pane width
# --------------------------------------------------------------------------


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_the_worst_item_is_wrapped_rather_than_cut_off(size):
    frame = frame_of(AGENT_SERVICE, size=size)
    text = items_of(AGENT_SERVICE, "bad")[0]["text"]
    # The item's own rows, read off the screen: a continuation row is indented,
    # a new item starts its label at column 0. Counting them from
    # `attention.TEXT_ROWS` would be the test asking the module what to expect.
    band = [frame.lines[BAND_TOP]]
    while frame.lines[BAND_TOP + len(band)].startswith(" "):
        band.append(frame.lines[BAND_TOP + len(band)])
    assert len(band) > 1, frame._message(
        "the item was cut to one row instead of wrapped")
    joined = " ".join(line.strip() for line in band)
    # The first row cannot hold the whole item at either width, so a word from
    # beyond the first row's worth of text is only there if the band wrapped.
    later = text.split()[len(band[0].split())]
    assert later in joined, frame._message(
        "the item is not wrapped onto its later rows: %r is missing" % later)
    assert len(band[0]) < frame.cols, frame._message(
        "the first band row runs the whole width")


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_the_wrapped_rows_line_up_under_the_text_not_under_the_label(size):
    frame = frame_of(AGENT_SERVICE, size=size)
    first = frame.lines[BAND_TOP]
    gutter = len(first) - len(first.lstrip())
    assert gutter == 0, frame._message("the label must start at column 0")
    second = frame.lines[BAND_TOP + 1]
    indent = len(second) - len(second.lstrip())
    assert indent == first.index("create_teams_channel"), frame._message(
        "a wrapped row must line up under the text it continues")
    # Literal, not `attention.GAP`: a test that asked the module what the gap
    # is would agree with the module about a gap of nothing.
    assert indent >= len("NEEDS YOUR CALL") + 2, frame._message(
        "the label and its text must not be run together")


def test_a_line_that_did_not_fit_ends_in_an_ellipsis():
    """Text the band could not spend a row on is cut visibly, not silently."""
    frame = frame_of(AGENT_SERVICE)
    rows, lines = band_of(frame, AGENT_SERVICE)
    notes = [line for line in lines if line.startswith("NOTE")]
    cut = [line for item, line in zip(items_of(AGENT_SERVICE, "note"), notes)
           if item["text"] not in line]
    assert cut, "no NOTE row was cut at this width, so this test proves nothing"
    for line in cut:
        assert line.rstrip().endswith("…"), frame._message(
            "a row that dropped text must say so: %r" % line)


@pytest.mark.parametrize("size", [WIDE, STANDARD, (30, 100), (16, 90)])
def test_the_band_leaves_the_reserved_last_column_empty(size):
    """The strict form of the width assertion, as the chrome intends.

    A passing `assert_within_width()` means "nothing was clipped" only while
    the margin is empty; `allow_full_width=True` would be this test agreeing
    not to look.
    """
    frame = frame_of(AGENT_SERVICE, size=size)
    frame.assert_within_width()
    assert "Traceback" not in frame.text, frame._message("the TUI raised")


# --------------------------------------------------------------------------
# the band decides — it is never empty, and it never eats the screen
# --------------------------------------------------------------------------


def test_a_quiet_relay_still_says_that_the_quiet_is_real():
    """A `calm` item is information: the alternative reads as a frozen screen."""
    relay = FIXTURES / "all-done"
    frame = frame_of(relay)
    calm = items_of(relay, "calm")
    assert calm, "the all-done fixture no longer carries a calm item"
    assert frame.lines[BAND_TOP].startswith(calm[0]["label"]), frame._message(
        "a relay with nothing to decide must still say so under the status bar")
    frame.assert_attrs(calm[0]["label"], fg=CALM_FG, row=BAND_TOP)
    assert calm[0]["text"].split()[0] in frame.lines[BAND_TOP]


def test_the_band_announces_what_it_could_not_fit(tmp_path):
    relay = relay_at(tmp_path / "many-notes", dashboard={
        "path": "~/many-notes",
        "notes": ["note number %d, which the coach wrote by hand" % n
                  for n in range(12)],
    })
    frame = frame_of(relay)
    rows, lines = band_of(frame, relay)
    assert rows == attention.MAX_ROWS, frame._message(
        "twelve notes must fill the band's budget, not overflow it")
    marker = [(row, line) for row, line in enumerate(lines)
              if re.match(r"^\+\d+ more$", line.strip())]
    assert marker, frame._message(
        "the band hid items without saying so: %r" % (lines,))
    shown = len([line for line in lines if line.startswith("NOTE")])
    row, text = marker[0]
    hidden = int(re.match(r"^\+(\d+) more$", text.strip()).group(1))
    assert shown + hidden == len(items_of(relay)), frame._message(
        "%d shown + %d hidden is not the %d items the model carries"
        % (shown, hidden, len(items_of(relay))))
    # The pane convention's marker, drawn the pane convention's way.
    frame.assert_attrs(text.strip(), has="dim", row=BAND_TOP + row)


def test_the_overflow_marker_owns_its_row():
    """`+N more` is the pane convention's marker, and it never shares a row."""
    frame = frame_of(AGENT_SERVICE, size=(12, 40))
    collision = frame.search(r"\+\d+ more\S")
    assert collision is None, frame._message(
        "row %s draws an overflow marker into another row's text" % (collision,))


def test_an_action_is_drawn_apart_from_the_text(tmp_path):
    """An action is what the human could *do* — not what happened."""
    relay = relay_at(tmp_path / "with-action", dashboard={
        "path": "~/with-action",
        "attention": [{
            "level": "bad", "label": "NEEDS YOUR CALL",
            "text": "the register route rewrites credentials it never read.",
            "action": "pause, then re-scope",
        }],
    })
    frame = frame_of(relay)
    rows, lines = band_of(frame, relay)
    assert any("pause, then re-scope" in line for line in lines), \
        frame._message("the band dropped the item's action")
    frame.assert_attrs_differ("pause, then re-scope", "register route")
    frame.assert_attrs("pause, then re-scope", has="bold")


def test_the_panes_survive_the_band_at_the_standard_terminal():
    """The band moves the panes down; it does not delete them."""
    frame = frame_of(AGENT_SERVICE, size=STANDARD)
    for title in ("Active Leg", "Legs", "Progress Log", "Active Runner"):
        assert frame.contains(title), frame._message(
            "%r is gone at 80x24 — the band has crowded out the Overview" % title)


@pytest.mark.parametrize("width", [12, 20, 40, 60, 80, 100, 160, 240])
def test_the_band_never_asks_for_more_rows_than_its_budget(width):
    """`height()` is not told how tall the terminal is, so it budgets itself.

    The literal bound is the point of the test: `app.paint()` clamps a greedy
    answer without sharing it, so a band that asked for a dozen rows would
    delete the panes on a twenty-four row terminal rather than compete for
    them. Bounding this against `attention.MAX_ROWS` alone would be the module
    agreeing with itself.
    """
    rows = attention.height(model_of(AGENT_SERVICE), width)
    assert 0 < rows <= attention.MAX_ROWS
    assert rows <= 8, "a band of %d rows leaves an 80x24 terminal no panes" % rows


@pytest.mark.parametrize("width", [0, 1, 2, 3, 4])
def test_a_terminal_too_narrow_for_a_word_gets_no_band(width):
    assert attention.height(model_of(AGENT_SERVICE), width) == 0


@pytest.mark.parametrize("width", [5, 6, 7, 8, 10, 12, 20, 41, 80, 160])
def test_every_row_the_band_lays_out_stops_short_of_the_last_column(width):
    """Every row, including the ones the band writes for itself.

    `+N more` is the band's own sentence rather than the model's, and at the
    widths where it is longer than the row it is the one row nothing else
    clips. A frame test cannot reach here: the terminal sizes where this bites
    are narrower than the band gets to draw at.
    """
    rows = attention._layout(model_of(AGENT_SERVICE), width)
    assert rows, "no band at all at %d columns" % width
    for parts in rows:
        drawn = sum(len(text) for text, _ in parts)
        assert drawn <= width - 1, (
            "a row of %d cells at width %d: %r"
            % (drawn, width, "".join(text for text, _ in parts)))
    if width < len("+99 more"):
        assert any(parts[0][0].startswith("+") for parts in rows), (
            "no overflow marker at %d columns, so the clipping is untested"
            % width)


# --------------------------------------------------------------------------
# degrade, do not crash
# --------------------------------------------------------------------------


@pytest.mark.parametrize("size", [(24, 80), (12, 40), (8, 30), (5, 20), (3, 12)])
def test_it_degrades_below_80x24_without_crashing(size):
    term = session(AGENT_SERVICE, size=size)
    try:
        frame = term.frame()
        assert "Traceback" not in frame.text, frame._message("the TUI raised")
        frame.assert_within_width()
        assert frame.text.strip(), frame._message("nothing was drawn at all")
        assert term.is_running, frame._message("the TUI exited instead of degrading")
    finally:
        term.close()


def test_a_narrow_terminal_gives_the_label_a_row_of_its_own():
    """Below the width prose needs, the label stops sharing its row.

    The alternative is a column of labels beside a two-word sliver of text,
    which is a band that has stopped being readable rather than one that has
    degraded.
    """
    frame = frame_of(AGENT_SERVICE, size=(12, 40))
    assert frame.lines[BAND_TOP].strip() == "NEEDS YOUR CALL", frame._message(
        "at 40 columns the label must own its row")
    assert frame.lines[BAND_TOP + 1].startswith("  "), \
        frame._message("the text must be indented under the label it belongs to")
    assert frame.lines[BAND_TOP + 1].strip(), frame._message(
        "the label was given a row and then no text followed it")


def test_a_resize_redraws_the_band_at_the_new_width():
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        wide = term.frame()
        assert wide.lines[BAND_TOP].startswith("NEEDS YOUR CALL")
        narrow = term.resize(*STANDARD, expect="NEEDS YOUR CALL")
        assert narrow.cols == 80
        narrow.assert_within_width()
        assert "Traceback" not in narrow.text
        assert narrow.lines[BAND_TOP].startswith("NEEDS YOUR CALL")
    finally:
        term.close()


def test_the_band_belongs_to_the_overview_and_to_no_other_view():
    """Asserted behind a barrier: the frame has already proved it is the Legs view.

    A bare `send()` returns whatever was on screen when the pty went quiet, and
    a stale frame satisfies every negative assertion for the wrong reason.
    """
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        legs = term.send("F", expect="Esc Overview")
        assert "Esc Overview" in legs.lines[legs.rows - 1]
        legs.assert_not_contains("NEEDS YOUR CALL")
        back = term.send("<Esc>", expect="Active Leg")
        assert back.lines[BAND_TOP].startswith("NEEDS YOUR CALL")
    finally:
        term.close()


# --------------------------------------------------------------------------
# untrusted input — `extras` is coach input passed through verbatim
# --------------------------------------------------------------------------


JUNK_MODELS = [
    None,
    {},
    {"attention": None},
    {"attention": "a string is not a list"},
    {"attention": []},
    {"attention": [None, 3, "not a dict", []]},
    {"attention": [{}]},
    {"attention": [{"level": ["bad"], "label": None, "text": "kept"}]},
    {"attention": [{"level": "nonsense", "label": 7, "text": "kept"}]},
    {"attention": [{"level": "bad", "label": "A\nB", "text": "x\ny",
                    "action": "do\nthis"}]},
    {"attention": [{"level": "bad", "label": "L" * 400, "text": "t" * 4000}]},
    {"attention": [{"level": "bad", "label": "NEEDS YOUR CALL",
                    "text": "text that fills every row it is given " * 12,
                    "action": "an action too long to share a row with it " * 2}]},
]


@pytest.mark.parametrize("model", JUNK_MODELS)
@pytest.mark.parametrize("width", [20, 80, 160])
def test_a_malformed_model_degrades_to_a_band_rather_than_a_traceback(model, width):
    rows = attention.height(model, width)
    assert 0 < rows <= attention.MAX_ROWS
    for parts in attention._layout(model, width):
        drawn = sum(len(text) for text, _ in parts)
        assert drawn <= width - 1, "a row of %d cells at width %d" % (drawn, width)
        assert "\n" not in "".join(text for text, _ in parts)


@pytest.mark.parametrize("model", [None, {}, {"attention": []},
                                   {"attention": [{"text": ""}]}])
def test_a_model_carrying_nothing_still_gets_a_row_that_says_so(model):
    """Never an empty band: a blank strip reads as a crash, not as quiet."""
    rows = attention._layout(model, 80)
    assert len(rows) == 1
    # Literals: asking `attention.QUIET` what it says would let a band that
    # said nothing at all certify itself.
    text = "".join(part for part, _ in rows[0])
    assert "QUIET" in text
    assert "no attention items" in text


def test_an_item_the_coach_left_unlabelled_is_still_labelled():
    rows = attention._layout({"attention": [
        {"level": "note", "text": "the coach wrote no label for this one"},
    ]}, 80)
    assert rows[0][0][0] == "NOTE", (
        "an unlabelled item must fall back to a label, not to a blank column: %r"
        % (rows[0],))


def test_an_item_with_no_text_is_not_given_a_row_of_labels():
    rows = attention._layout({"attention": [
        {"level": "bad", "label": "NEEDS YOUR CALL", "text": ""},
        {"level": "note", "label": "NOTE", "text": "this one has text"},
    ]}, 80)
    drawn = "".join(text for parts in rows for text, _ in parts)
    assert "NEEDS YOUR CALL" not in drawn
    assert "this one has text" in drawn


# --------------------------------------------------------------------------
# the header's title fallback — see this file's docstring for why it is here
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relay,expected", [
    ({"title": "Wave 2", "name": "wave-2", "relayDir": "/p/.relay"}, "Wave 2"),
    ({"title": None, "name": "wave-2", "relayDir": "/p/.relay"}, "wave-2"),
    ({"title": None, "name": None, "relayDir": "/p/payments/.relay"}, "payments"),
    ({"title": None, "name": None, "relayDir": "/tmp/cutover"}, "cutover"),
    ({"title": None, "name": None, "relayDir": "/"}, "Relay Control"),
    ({"title": None, "name": None, "relayDir": "/.relay"}, "Relay Control"),
    ({"title": None, "name": None, "relayDir": None}, "Relay Control"),
    ({}, "Relay Control"),
])
def test_the_header_falls_back_title_then_name_then_directory(relay, expected):
    assert chrome.relay_title({"relay": relay}) == expected


def test_the_header_names_a_nameless_relay_by_its_own_directory(tmp_path):
    relay = relay_at(tmp_path / "cutover-rehearsal",
                     dashboard={"path": "~/elsewhere"})
    frame = frame_of(relay)
    assert frame.lines[0].startswith("cutover-rehearsal"), frame._message(
        "the header does not name the relay by its directory")
    assert "Relay Control" not in frame.lines[0], frame._message(
        "the program's own name is the last resort, not the second")
    frame.assert_attrs("cutover-rehearsal", fg=36, has="bold", row=0)


def test_a_dot_relay_directory_is_named_by_the_project_it_sits_in(tmp_path):
    relay = relay_at(tmp_path / "payments-api" / ".relay",
                     dashboard={"path": "~/elsewhere"})
    frame = frame_of(relay)
    assert frame.lines[0].startswith("payments-api"), frame._message(
        "every relay directory is called .relay; the project above it is the name")


def test_a_relay_that_names_itself_is_named_that(tmp_path):
    relay = relay_at(tmp_path / "ignored-directory",
                     legs={"relay": "wave-3-cutover", "legs": []},
                     dashboard={"path": "~/elsewhere"})
    frame = frame_of(relay)
    assert frame.lines[0].startswith("wave-3-cutover"), frame._message(
        "legs.json names the relay; the directory is only the fallback")

"""Frame tests for navigation and filters (ACC-NAV-001..005).

One region per check in `.relay/contract.md`. Every visual claim is asserted
against a frame captured from a real curses process under a pty
(`tests/frame.py`); nothing here reaches into the program's state.

Five rules this file follows, each paid for by an earlier leg:

* **Every expectation is spelled out here, never read back out of
  `relay_control.*`.** The five Legs filters and their order, the four Runners
  filters, the six fields of a leg detail, the detail keybar and the module
  that is allowed to bind a key are all written down below. A test that asks
  the module what it did and then agrees with it cannot fail when the module
  changes — the failure class that has dominated this run.
* **Figures come from the fixture's own files at assert time.** `plan()` and
  `leg_counts()` parse `legs.json`; `checks()` parses `state.json`. Only the
  status *vocabulary* is shared with the model, because "what does `done` mean"
  is one decision and not two.
* **A negative assertion never rides on a bare `send()`.** "Esc on the Overview
  changes nothing" and "T on a view with no filter row changes nothing" are
  both satisfied by a repaint that simply started late, so each one is made
  against a frame the program *stated* was whole: the key, then a barrier key
  the loop must read after it, then `assert_finished()`. See
  `.relay/skills/driving-a-curses-child.md`.
* **`send(keys, expect=NEEDLE)` for a new screen**, with NEEDLE naming text the
  destination introduces — `"Runners ("`, not `"Runners"`, which the Overview's
  own keybar already carries.
* **The selection is read off the frame as an attribute**, never as a column
  position: `highlighted()` finds the reverse-video run, so a layout change
  moves these assertions rather than breaking them.
"""

import ast
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_chrome import (  # noqa: E402
    FIXTURES, OVERVIEW_KEYS, PACKAGE, READY, STANDARD, WIDE, repaint, session,
)

import relay_model  # noqa: E402

AGENT_SERVICE = FIXTURES / "agent-service"

#: Text only the named view paints, so `send(key, expect=...)` waits on a
#: screen the repaint introduced rather than on one that was already there.
#: The Overview's keybar carries the words `Legs`, `Runners`, `Models` and
#: `Contract`, which is why none of these is the bare word.
NEEDLES = {
    "overview": "Active Leg",
    "legs": "Stage/ID",
    "runners": "Runners (",
    "models": "Experimental toggles",
    "contract": "evidenced",
}

#: `Tab` order, spelled here. ACC-NAV-001 says Tab cycles forward through all
#: five and `Esc` goes back to the Overview, so the Overview is first.
TAB_ORDER = ("overview", "legs", "runners", "models", "contract")

#: The single-key jumps ACC-NAV-001 names, in the case the check writes them.
JUMPS = (("F", "legs"), ("W", "runners"), ("M", "models"), ("C", "contract"))

#: The Legs filter row, in order, with the leg state each entry selects.
#: ACC-NAV-003's evidence names this sequence and these figures.
LEGS_FILTERS = (("All", None), ("Pending", "pending"),
                ("In Progress", "running"), ("Completed", "completed"),
                ("Cancelled", "cancelled"))

#: The Runners filter row, in the order ACC-RUN-001 names it.
RUNNERS_FILTERS = (("All", None), ("Active", "running"),
                   ("Completed", "completed"), ("Failed", "failed"))

#: The six fields ACC-NAV-004's evidence names for a leg detail, as the screen
#: spells them.
LEG_DETAIL_FIELDS = ("Goal", "Fulfills", "Depends on", "Touches", "Boundaries",
                     "Verification")

#: What a detail's keybar says. A detail takes `Esc`, `Tab` and `q`; naming
#: `Up/Dn`, `Enter` or `T` on a screen that ignores them is a lie on every
#: frame, so these are asserted present *and* those absent.
DETAIL_KEYS = "Esc Back  Tab Next View  q Quit"
DETAIL_FORBIDDEN = ("Up/Dn", "T Filter", "Enter Detail", "Esc Overview")

#: A key nothing in the program binds, used as a barrier: the loop reads it,
#: changes nothing, and repaints — so a frame captured off it post-dates every
#: key sent before it. `test_chrome.repaint()` sends this one.
BARRIER = "z"

#: The one module allowed to name a navigation key. See
#: `test_only_one_module_names_a_navigation_key` for what this is worth.
KEY_OWNER = "navigation.py"


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------


def open_view(term, key, view, expect=None):
    """Switch to `view` and return the frame the program stated was whole.

    The needle is text the destination introduces; what makes the frame
    evidence is the closed DEC 2026 bracket, whose baseline is taken after the
    keys were read — so this assertion, and not the needle, is the statement
    that a repaint happened.
    """
    frame = term.send(key, expect=expect or NEEDLES[view])
    assert frame.paint_end == "synchronised", frame._message(
        "the %s view was captured on %r, which is not proof that the repaint "
        "finished" % (view, frame.paint_end))
    return frame


def settled(term, keys):
    """The screen after `keys`, proved whole by a barrier key read after them.

    Two different unsoundnesses, one answer.

    A frame taken straight off `send(keys)` is worthless for a *negative*
    assertion: a repaint that started later than the redraw window is
    indistinguishable from no repaint at all, and "nothing changed" passes on
    the stale screen for the wrong reason.

    And a frame taken off a *batch* of keys is early even when it is proved.
    ncurses reads the pty in chunks, so twelve keystrokes can be inside the
    program's own buffer while the pty's input queue is empty — the delivery
    barrier is satisfied, the DEC 2026 baseline is taken, and the wait ends on
    the bracket of the *first* of the twelve repaints. Measured: five `Down`s
    in one `send()` came back showing four of them.

    The barrier key answers both. It is written after `keys`, so it sits in the
    pty queue until the program reads again — which it does only once it has
    worked through everything it buffered. `send()`'s delivery barrier
    therefore does not clear until the last of `keys` has been acted on, the
    baseline is taken after that, and the bracket that ends the wait is the
    barrier's own repaint. `assert_finished()` is the statement that it closed.
    """
    term.send(keys)
    frame = repaint(term, expect=READY)
    frame.assert_finished()
    return frame


def body(frame):
    """Everything below the header and the status bar.

    Row 0 carries the relay's elapsed time and row 1 the progress bar, neither
    of which this leg is about; comparing them would make a "nothing changed"
    assertion flake on a clock rather than fail on a defect.
    """
    return frame.lines[2:]


def keybar(frame):
    return frame.lines[frame.rows - 1].rstrip()


def highlighted(frame):
    """`(row, text)` of the row drawn in reverse video — the selection.

    Read as an *attribute*, never as a column: the highlight is
    `theme.SELECTED` padded across the pane, so the longest reverse run on the
    screen is the selected row whatever the layout did with the columns. The
    active filter is also drawn selected, and is an order of magnitude shorter,
    which is why this answers with the longest and not with the first.
    """
    best = None
    for row in range(2, frame.rows - 1):
        for run in frame.attr_runs(row):
            # A blank run is the padding that carries the highlight across the
            # rest of the pane; on a row whose last cell keeps its own status
            # attribute that padding is a run of its own, and it is longer than
            # the text it follows. What names the row is the text.
            if not run.attrs.reverse or not run.text.strip():
                continue
            if best is None or len(run.text) > len(best[1]):
                best = (row, run.text)
    return best


def selected_text(frame):
    found = highlighted(frame)
    assert found is not None, frame._message(
        "no row on this frame is drawn as the selected row")
    return found[1].strip()


#: A `Stage/ID` cell as the Legs view spells it — `S2/credential-parity`.
REFERENCE = r"\b([A-Z]\w*/[\w.-]+)"


def selected_reference(frame):
    """The `Stage/ID` of the highlighted Legs row, read off the screen.

    Rows are identified by what they *say*, never by their position in
    `legs.json`: the model orders the plan by its own rule and the file's array
    order diverges from it at the tenth leg in this fixture, so a test that
    counted keystrokes into the file's list would be asserting against a plan
    the view never draws.
    """
    match = re.search(REFERENCE, selected_text(frame))
    assert match is not None, frame._message(
        "the selected row %r names no leg" % selected_text(frame))
    return match.group(1)


def leg_of(relay_dir, reference):
    """The leg record behind a `Stage/ID` cell."""
    identifier = reference.split("/", 1)[1]
    for leg in plan(relay_dir):
        if leg.get("id") == identifier:
            return leg
    raise AssertionError("no leg in legs.json is called %r" % identifier)


def marker(frame, word):
    """The figure on a `+N earlier` / `+N more` marker, or 0 when there is none."""
    match = frame.search(r"\+(\d+) %s" % word)
    if match is None:
        return 0
    return int(frame.lines[match].split("+")[1].split()[0])


# --------------------------------------------------------------------------
# figures, read from the fixture's own files at assert time
# --------------------------------------------------------------------------


def plan(relay_dir):
    data = json.loads((Path(relay_dir) / "legs.json").read_text())
    return [leg for leg in data.get("legs", []) if isinstance(leg, dict)]


def leg_counts(relay_dir):
    """`{filter label: count}` counted out of `legs.json`, not out of the model."""
    legs = plan(relay_dir)
    counts = {}
    for label, state in LEGS_FILTERS:
        counts[label] = len(legs) if state is None else sum(
            1 for leg in legs
            if relay_model.normalise_status(leg.get("status")) == state)
    return counts


def leg_reference(leg):
    """`S4/cutover-flip` — a leg said the way the Legs view heads it."""
    stage = leg.get("stage")
    identifier = leg.get("id") or "(unnamed leg)"
    return "%s/%s" % (stage, identifier) if stage else identifier


def checks(relay_dir):
    """`{check id: record}` out of `state.json`."""
    data = json.loads((Path(relay_dir) / "state.json").read_text())
    written = data.get("checks")
    return written if isinstance(written, dict) else {}


def synthetic_relay(directory, legs=(), check_records=None):
    """A relay whose files say exactly what this test needs them to say."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "legs.json").write_text(json.dumps({
        "relay": "synthetic",
        "stages": [{"id": "S1", "name": "Only stage",
                    "legs": [leg_id for leg_id, _ in legs]}],
        "legs": [{"id": leg_id, "stage": "S1", "status": status,
                  "goal": "a leg", "fulfills": []}
                 for leg_id, status in legs],
    }))
    (directory / "state.json").write_text(json.dumps({
        "relay": "synthetic", "phase": "running", "currentStage": "S1",
        "checks": check_records or {},
    }))
    return directory


# --------------------------------------------------------------------------
# ACC-NAV-001 — view switching
# --------------------------------------------------------------------------


def test_each_named_key_opens_its_view_in_either_case():
    """`F`, `W`, `M`, `C` — and their lowercase, which the program also takes."""
    for key, view in JUMPS:
        term = session(AGENT_SERVICE, size=WIDE)
        try:
            upper = open_view(term, key, view)
            assert upper.contains(NEEDLES[view])
            term.send("<Esc>", expect=NEEDLES["overview"])
            lower = open_view(term, key.lower(), view)
            assert lower.contains(NEEDLES[view]), lower._message(
                "%r did not open the %s view" % (key.lower(), view))
        finally:
            term.close()


def test_tab_cycles_forward_through_all_five_views_and_wraps():
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        for view in TAB_ORDER[1:] + TAB_ORDER[:1]:
            frame = term.send("<Tab>", expect=NEEDLES[view])
            assert frame.paint_end == "synchronised", frame._message(
                "captured on %r" % frame.paint_end)
            assert frame.contains(NEEDLES[view]), frame._message(
                "Tab did not reach the %s view" % view)
        # Five presses from the Overview land back on the Overview.
        assert keybar(frame).startswith(OVERVIEW_KEYS), frame._message(
            "five Tabs did not come back to the Overview: %r" % keybar(frame))
    finally:
        term.close()


def test_esc_returns_to_the_overview_from_every_view():
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        for key, view in JUMPS:
            open_view(term, key, view)
            home = term.send("<Esc>", expect=NEEDLES["overview"])
            assert home.paint_end == "synchronised", home._message(
                "captured on %r" % home.paint_end)
            assert keybar(home).startswith(OVERVIEW_KEYS), home._message(
                "Esc from the %s view left %r on the keybar" % (view,
                                                               keybar(home)))
    finally:
        term.close()


def test_esc_on_the_overview_changes_nothing():
    """A negative assertion, so it is made on a frame the program proved whole.

    `Esc` is documented as "returns to the Overview", and the Overview is where
    it already is. What must not happen is the screen changing — and a stale
    frame would say that for the wrong reason.
    """
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        before = repaint(term, expect=READY)
        before.assert_finished()
        after = settled(term, "<Esc>")
        assert body(after) == body(before), after._message(
            "Esc on the Overview redrew a different screen")
        assert keybar(after).startswith(OVERVIEW_KEYS)
    finally:
        term.close()


def test_a_key_nothing_binds_changes_nothing():
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        before = open_view(term, "F", "legs")
        after = settled(term, "x")
        assert body(after) == body(before), after._message(
            "an unbound key changed the Legs view")
    finally:
        term.close()


# --------------------------------------------------------------------------
# ACC-NAV-002 — row selection
# --------------------------------------------------------------------------


def test_down_moves_the_selection_and_up_moves_it_back():
    legs = plan(AGENT_SERVICE)
    assert len(legs) > 2, "the fixture must have rows for this to bite"

    term = session(AGENT_SERVICE, size=WIDE)
    try:
        first = open_view(term, "F", "legs")
        assert leg_reference(legs[0]) in selected_text(first), first._message(
            "the Legs view does not open on its first leg")
        down = term.send("<Down>", expect=NEEDLES["legs"])
        assert leg_reference(legs[1]) in selected_text(down), down._message(
            "Down did not move the selection to the second leg")
        up = term.send("<Up>", expect=NEEDLES["legs"])
        assert leg_reference(legs[0]) in selected_text(up), up._message(
            "Up did not move the selection back to the first leg")
    finally:
        term.close()


def test_up_on_the_first_row_stays_on_the_first_row():
    """The top bound, asserted on a proved frame: nothing may move, and a
    stale screen would agree for the wrong reason."""
    legs = plan(AGENT_SERVICE)
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        open_view(term, "F", "legs")
        after = settled(term, "<Up><Up><Up>")
        assert leg_reference(legs[0]) in selected_text(after), after._message(
            "Up at the top of the list moved the selection off the first leg")
        assert marker(after, "earlier") == 0, after._message(
            "the list scrolled above its own first row")
    finally:
        term.close()


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_fifty_downs_land_on_the_last_leg_and_scroll_the_list(size):
    """ACC-NAV-002's own evidence: 50 x Down on the 36-leg fixture.

    Three claims, and the third is the one a `paginate()` that ignored the
    selection would fail: the selection is on the last leg, it is *visible*,
    and the two markers plus the rows drawn account for every leg in the plan.
    """
    legs = plan(AGENT_SERVICE)
    assert len(legs) > 30, "this fixture must overflow for the test to bite"

    term = session(AGENT_SERVICE, size=size)
    try:
        open_view(term, "F", "legs")
        frame = settled(term, "<Down>" * 50)
        # The end of the list, said in two ways the screen can be asked for:
        # nothing is marked hidden below the window, and one more `Down` does
        # not move the selection. Which leg that is, is the plan's business.
        landed = selected_reference(frame)
        assert marker(frame, "more") == 0, frame._message(
            "50 x Down left %d rows below the window" % marker(frame, "more"))
        again = settled(term, "<Down>")
        assert selected_reference(again) == landed, again._message(
            "the selection moved past the last row of the plan: %r then %r"
            % (landed, selected_reference(again)))

        earlier, more = marker(frame, "earlier"), marker(frame, "more")
        drawn = sum(1 for leg in legs if frame.contains(leg_reference(leg)))
        assert earlier + drawn + more == len(legs), frame._message(
            "%d earlier + %d drawn + %d more is not the %d legs in the plan"
            % (earlier, drawn, more, len(legs)))
        if drawn < len(legs):
            # The list overflowed, so it had to scroll to keep the selection on
            # screen — and `chrome.paginate()`, which is what was here before
            # this leg, would have drawn the first screenful and marked the
            # selection's own row hidden.
            assert earlier > 0, frame._message(
                "the list did not scroll: the selection is past the pane edge "
                "and nothing is marked hidden above it")
        frame.assert_within_width()
        assert "Traceback" not in frame.text
    finally:
        term.close()


def test_the_selection_moves_in_the_runners_and_contract_views_too():
    """ACC-NAV-002 names three views, so all three are asserted."""
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        for key, view in (("W", "runners"), ("C", "contract")):
            open_view(term, key, view)
            first = repaint(term, expect=READY)
            first.assert_finished()
            moved = term.send("<Down>", expect=NEEDLES[view])
            assert selected_text(moved) != selected_text(first), moved._message(
                "Down did not move the selection in the %s view" % view)
            back = term.send("<Up>", expect=NEEDLES[view])
            assert selected_text(back) == selected_text(first), back._message(
                "Up did not move the selection back in the %s view" % view)
    finally:
        term.close()


#: The five status glyphs ACC-TUI-006 names, spelled here rather than read from
#: `theme.GLYPHS`: the child runs under a UTF-8 locale, so these are the shapes
#: that reach the screen.
GLYPHS = ("\u2713", "\u25cf", "\u25cb", "\u2717", "\u2212")


def test_the_selected_row_is_a_row_and_the_status_glyph_keeps_its_colour():
    """Two rules the highlight holds at once, and each shipped as a defect.

    It reads as a **row**: `theme.SELECTED` is reverse video and stops where its
    text does, so the bar starts within the leading gutter and runs to the
    pane's edge. A highlight that stopped at the end of the text is a
    highlighted *word*; one that started sixteen cells in — after an
    un-highlighted status column — is a bar that begins a third of the way
    across the row.

    And the status **glyph keeps its own state's colour** (ACC-TUI-006). The
    highlight says where the keyboard is; the glyph says what state that row is
    in; drawing the whole row in one attribute loses the second.
    """
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        frame = open_view(term, "F", "legs")
        row = highlighted(frame)[0]
        runs = frame.attr_runs(row)
        assert not runs[0].attrs.reverse, frame._message(
            "the whole selected row is one attribute — the status glyph lost "
            "its own: %r" % (runs,))
        assert any(glyph in runs[0].text for glyph in GLYPHS), frame._message(
            "the run the highlight leaves alone is not the status glyph: %r"
            % (runs[0],))
        bars = [run for run in runs if run.attrs.reverse]
        assert bars, frame._message("the selected row carries no highlight")
        assert runs[0].end <= 4, frame._message(
            "%d cells at the head of the selected row are not highlighted — "
            "the gutter is the glyph and its gap, not a whole column: %r"
            % (runs[0].end, runs[0]))
        assert bars[0].start <= runs[0].end, frame._message(
            "the highlight starts at column %d, not in the glyph's own gutter"
            % bars[0].start)
        assert max(run.end for run in bars) >= frame.cols - 2, frame._message(
            "the highlight stops at column %d of %d — it is a highlighted word, "
            "not a row" % (max(run.end for run in bars), frame.cols))
    finally:
        term.close()


@pytest.mark.parametrize("key,view", [("F", "legs"), ("W", "runners"),
                                     ("C", "contract")])
def test_a_long_list_scrolls_to_keep_the_selection_on_screen(key, view):
    """ACC-NAV-002's third clause, in all three views it names.

    Twenty-five rows down at 80x24 is past the pane edge in every one of them.
    `chrome.paginate()` — which is what each of these views used before this
    leg — always draws the *first* screenful, so the selection would be behind
    the `+N more` marker and there would be no highlighted row on the frame at
    all. That is what `selected_text()` refuses, and it is the whole difference
    between paginating a list and scrolling one.
    """
    term = session(AGENT_SERVICE, size=STANDARD)
    try:
        open_view(term, key, view)
        frame = settled(term, "<Down>" * 25)
        assert selected_text(frame), frame._message(
            "the %s view scrolled its selection off the screen" % view)
        assert marker(frame, "earlier") > 0, frame._message(
            "the %s view has a selection 25 rows down and nothing marked "
            "hidden above the window" % view)
        assert marker(frame, "more") >= 0
        frame.assert_within_width()
    finally:
        term.close()


#: A needle the Legs view paints at *every* height. `Stage/ID` is the column
#: head, and a pane too short for one does not draw it; the Overview's own Legs
#: pane is titled `Legs` with its count right-aligned, so `Legs (` is text only
#: the full-screen view introduces.
LEGS_ANY_HEIGHT = "Legs ("

#: Heights at which the Legs pane can still draw a leg. Below seven rows the
#: filter row takes the pane's only body row and there is no selection to see —
#: see the baton for what that pane does with its `+N more`.
SHORT = [24, 12, 9, 8, 7]


@pytest.mark.parametrize("rows", SHORT)
def test_the_window_keeps_the_selection_on_screen_at_every_height(rows):
    """Where the window's arithmetic is off by one, it is off by one *here*.

    Two of this leg's mutants live in a single row of the window: the guard
    that decides when the plain first-screenful answer is still right
    (`focus < height - 1`), and the clamp that stops the window scrolling past
    the row it is supposed to be showing (`min(start, focus)`). Both only bite
    at one particular height, and neither is visible at 160x48 where the whole
    plan fits.

    So the claim is made where it can fail: if the pane drew any leg at all, the
    leg it drew as *selected* is the one the keystrokes moved to.
    """
    legs = plan(AGENT_SERVICE)
    steps = 22
    assert len(legs) > steps

    term = session(AGENT_SERVICE, size=(rows, 60))
    try:
        open_view(term, "F", "legs", expect=LEGS_ANY_HEIGHT)
        # One press at a time, and the claim made after every one of them. The
        # boundary this is looking for is the press at which the selection
        # first reaches the pane's last row, and no test can name it: the
        # pane's height is arithmetic on the terminal that this file does not
        # do. So it walks past it and asserts at every step.
        seen = []
        for step in range(1, steps + 1):
            frame = settled(term, "<Down>")
            if not any(frame.contains(leg_reference(leg)) for leg in legs):
                # A pane too short for a single row is a `+N more` and nothing
                # else, and there is no selection to be visible. (Not the
                # `REFERENCE` pattern: the column head reads `Stage/ID`, which
                # is the shape of a reference and is not one.)
                assert marker(frame, "more"), frame._message(
                    "the pane drew no leg and did not say it was hiding any")
                return
            where = selected_reference(frame)
            assert frame.contains(where), frame._message(
                "step %d at %d rows: the selection is on a row the pane did "
                "not draw" % (step, rows))
            assert where not in seen, frame._message(
                "step %d at %d rows: Down came back to %r, which the selection "
                "has already been on — the window is not moving with it"
                % (step, rows, where))
            seen.append(where)
        assert len(seen) == steps
    finally:
        term.close()


#: An area heading in the Contract view: `CUTOVER 0/8 evidenced`.
AREA_HEADING = r"^[A-Z][A-Z0-9-]* \d+/\d+ evidenced\s*$"


def test_the_contract_window_never_opens_on_a_line_indented_under_nothing():
    """A cut between a check and its evidence is a line under no check.

    The Contract view's body is headings, check rows and wrapped prose, so a
    window chosen by row arithmetic alone can start in the middle of a check's
    evidence — and an indented line at the top of the pane reads as a check
    whose id failed to draw. The start walks forward to the next row that
    begins something.
    """
    term = session(AGENT_SERVICE, size=STANDARD)
    try:
        open_view(term, "C", "contract")
        for presses in (10, 12, 14, 16, 20):
            frame = settled(term, "<Down>" * (presses if presses == 10 else 2))
            above = frame.search(r"^\+\d+ earlier")
            assert above is not None, frame._message(
                "after %d Downs nothing is marked hidden above the window"
                % presses)
            first = frame.raw_lines[above + 1]
            assert first[:1] not in (" ", ""), frame._message(
                "the window opens on %r — a line indented under a check the "
                "reader cannot see" % first[:40])
    finally:
        term.close()


def two_area_relay(directory):
    """A relay whose Contract view is two areas of three plain checks.

    Six single-line rows and two headings, which is what makes the cut land on
    a heading at a height a test can name — against the agent-service fixture
    the same cut lands in the middle of a check's evidence at every height
    worth trying, and the claim is never put to the question.
    """
    return synthetic_relay(
        directory,
        legs=[("only-leg", "running")],
        check_records={"ACC-%s-00%d" % (area, n): {"status": "pending",
                                                   "stage": "S1"}
                       for area in ("AAA", "BBB") for n in (1, 2, 3)})


@pytest.mark.parametrize("rows", [5, 6, 7, 8, 9, 10, 11, 12, 16, 24])
def test_a_contract_area_heading_is_never_the_last_row_drawn(rows, tmp_path):
    """A heading is a label for the rows under it. Cut between the two and it
    labels the `+N more` marker, which is not what it says."""
    relay = two_area_relay(tmp_path / ("two-areas-%d" % rows))
    term = session(relay, size=(rows, 80))
    try:
        open_view(term, "C", "contract", expect="Contract")
        frame = repaint(term, expect=READY)
        frame.assert_finished()
        more = frame.search(r"^\+\d+ more")
        if more is None:
            return                       # nothing was hidden at this height
        above = frame.raw_lines[more - 1]
        assert not re.match(AREA_HEADING, above), frame._message(
            "at %d rows the pane's last row of content is the heading %r, with "
            "the overflow marker under it and nothing else" % (rows, above))
    finally:
        term.close()


def test_the_checks_enter_walks_are_the_ones_the_view_drew_not_the_models(
        tmp_path):
    """The list re-sorts on the state it *shows*; `Enter` follows that list.

    A check claiming `passed` with no evidence is drawn `blocked`, and blocked
    sorts above passed within its area (ACC-CONT-004) — so the row a reader
    sees first is not the check the model put first. A `rows` list taken
    straight from `checkGroups` puts the selection on one check and the reader
    on another, and nothing on either screen says so.
    """
    relay = synthetic_relay(
        tmp_path / "resorted",
        legs=[("only-leg", "running")],
        check_records={
            # Evidenced, so both are shown as claimed.
            "ACC-AAA-001": {"status": "passed", "stage": "S1",
                            "evidence": "a frame"},
            "ACC-AAA-002": {"status": "passed", "stage": "S1",
                            "evidence": "a frame"},
            # Claimed passed with nothing to show for it: drawn `blocked`,
            # and blocked leads its area.
            "ACC-AAA-003": {"status": "passed", "stage": "S1"},
        })
    term = session(relay, size=WIDE)
    try:
        listing = open_view(term, "C", "contract", expect="ACC-AAA")
        first = selected_text(listing)
        assert "ACC-AAA-003" in first, listing._message(
            "the unevidenced check does not lead its area, so this test "
            "cannot tell the two orders apart: %r" % first)
        detail = term.send("<Enter>", expect="Claimed by")
        assert detail.lines[2].split()[0] == "ACC-AAA-003", (
            detail._message(
                "the first row drew ACC-AAA-003 and Enter opened %r — the "
                "selection is indexing the model's order, not the view's"
                % detail.lines[2].split()[0]))
    finally:
        term.close()


def test_a_filter_that_narrows_the_list_brings_the_selection_back_into_it():
    """`selected()`'s clamp, which is not `move()`'s.

    Fifty rows down the plan and then `T` to a filter that keeps eight legs is
    an index no keystroke touched and that no longer names a row. It has to
    clamp to the last row of the list on screen — not vanish, which is what a
    view drawing rows it never highlights looks like.
    """
    states = {leg.get("id"): relay_model.normalise_status(leg.get("status"))
              for leg in plan(AGENT_SERVICE)}
    pending = [leg for leg in plan(AGENT_SERVICE)
               if states[leg.get("id")] == "pending"]
    assert 0 < len(pending) < 20, "the fixture must have a narrower filter"

    term = session(AGENT_SERVICE, size=WIDE)
    try:
        open_view(term, "F", "legs")
        settled(term, "<Down>" * 50)
        frame = settled(term, "T")                      # All -> Pending
        assert leg_reference(pending[-1]) in selected_text(frame), (
            frame._message(
                "after narrowing to %d legs the selection is %r, not the last "
                "of them %r" % (len(pending), selected_text(frame),
                                leg_reference(pending[-1]))))
    finally:
        term.close()


def test_the_index_a_move_stores_is_the_index_of_the_list_it_moved_in():
    """`move()`'s clamp, which is not `selected()`'s.

    Twenty Downs inside an eight-row filter must leave the *stored* index at
    the eighth row, not at twenty — because widening the filter again reads
    that stored index back, and a twenty that was never inside any list would
    put the reader most of the way down a plan they had not scrolled.
    """
    legs = plan(AGENT_SERVICE)
    states = {leg.get("id"): relay_model.normalise_status(leg.get("status"))
              for leg in plan(AGENT_SERVICE)}
    pending = [leg for leg in legs if states[leg.get("id")] == "pending"]
    assert 0 < len(pending) < len(legs)

    term = session(AGENT_SERVICE, size=WIDE)
    try:
        open_view(term, "F", "legs")
        term.send("T", expect=NEEDLES["legs"])          # All -> Pending
        settled(term, "<Down>" * 20)                    # further than it can go
        frame = settled(term, "T" * (len(LEGS_FILTERS) - 1))   # back to All
        assert view_count(frame, "Legs") == len(legs), frame._message(
            "the filter did not come back round to All")
        expected = leg_reference(legs[len(pending) - 1])
        assert expected in selected_text(frame), frame._message(
            "widening the filter put the selection on %r; the last row of the "
            "narrow list was index %d, so it should be %r"
            % (selected_text(frame), len(pending) - 1, expected))
    finally:
        term.close()


#: A leg id that is longer in characters than it is in cells: twelve `e` each
#: carrying a combining acute. `len()` counts 24, the terminal draws 12. The
#: same fact the other way round — CJK, two cells per character — cannot be
#: seen here, because a row that comes out too *long* is clipped by
#: `Canvas.write()` and the screen is identical. Too *short* is what shows.
COMBINING_ID = "e\u0301" * 12


def test_the_highlight_reaches_the_pane_edge_when_the_row_is_not_ascii(tmp_path):
    """The bar is padded in cells, not in characters (`chrome.cell_width`).

    A combining mark is a character the terminal draws in no cell of its own,
    so a row measured with `len()` is padded twelve cells short of the pane and
    the highlight stops in the middle of it — a bar that looks like it ran out
    rather than like a row.
    """
    relay = synthetic_relay(tmp_path / "combining",
                            legs=[(COMBINING_ID, "running"), ("plain", "pending")])
    term = session(relay, size=(24, 60))
    try:
        frame = open_view(term, "F", "legs", expect=LEGS_ANY_HEIGHT)
        row, _text = highlighted(frame)
        bars = [run for run in frame.attr_runs(row) if run.attrs.reverse]
        assert bars, frame._message("the selected row carries no highlight")
        reach = max(run.end for run in bars)
        assert reach >= frame.cols - 2, frame._message(
            "the highlight stops at column %d of %d on a row whose id is 24 "
            "characters and 12 cells wide" % (reach, frame.cols))
        frame.assert_within_width()
    finally:
        term.close()


def test_the_selection_survives_switching_away_and_back():
    legs = plan(AGENT_SERVICE)
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        open_view(term, "F", "legs")
        settled(term, "<Down><Down>")
        term.send("<Esc>", expect=NEEDLES["overview"])
        again = open_view(term, "F", "legs")
        assert leg_reference(legs[2]) in selected_text(again), again._message(
            "the Legs selection was reset by leaving the view")
    finally:
        term.close()


# --------------------------------------------------------------------------
# ACC-NAV-003 — filter cycling
# --------------------------------------------------------------------------


def filter_figure(frame, label):
    """`N` from the `Label (N)` entry of a filter row on this frame."""
    match = frame.search(r"%s \((\d+)\)" % label.replace(" ", r"\s"))
    assert match is not None, frame._message(
        "no filter row entry reads %r" % label)
    text = frame.lines[match]
    start = text.index("%s (" % label) + len(label) + 2
    return int(text[start:text.index(")", start)])


def view_count(frame, title):
    """`N` from a view's own `Title (N)` heading."""
    match = frame.search(r"%s \((\d+)\)" % title)
    assert match is not None, frame._message("no %s (N) heading" % title)
    text = frame.lines[match]
    start = text.index("%s (" % title) + len(title) + 2
    return int(text[start:text.index(")", start)])


def test_t_cycles_the_legs_filter_through_all_five_with_the_fixtures_counts():
    """ACC-NAV-003's own evidence, with every figure read off `legs.json`."""
    counts = leg_counts(AGENT_SERVICE)
    assert counts["All"] > counts["Completed"] > 0, (
        "the fixture must have a mixed plan for this to bite")

    term = session(AGENT_SERVICE, size=WIDE)
    try:
        frame = open_view(term, "F", "legs")
        for index, (label, _state) in enumerate(LEGS_FILTERS):
            if index:
                frame = term.send("T", expect=NEEDLES["legs"])
                assert frame.paint_end == "synchronised", frame._message(
                    "captured on %r" % frame.paint_end)
            assert view_count(frame, "Legs") == counts[label], frame._message(
                "under %s the header says %d and legs.json says %d"
                % (label, view_count(frame, "Legs"), counts[label]))
            for other, _ in LEGS_FILTERS:
                assert filter_figure(frame, other) == counts[other], (
                    frame._message("the filter row's %s (%d) is not the %d in "
                                   "legs.json"
                                   % (other, filter_figure(frame, other),
                                      counts[other])))
        # A sixth press wraps back to the first filter.
        wrapped = term.send("T", expect=NEEDLES["legs"])
        assert view_count(wrapped, "Legs") == counts["All"], wrapped._message(
            "the filter row does not wrap round to All")
    finally:
        term.close()


def test_the_active_filter_is_the_only_one_highlighted():
    """The highlight is what says which filter is on, so exactly one entry
    carries it and it is the one whose figure the header repeats."""
    counts = leg_counts(AGENT_SERVICE)
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        frame = open_view(term, "F", "legs")
        for index, (label, _state) in enumerate(LEGS_FILTERS):
            if index:
                frame = term.send("T", expect=NEEDLES["legs"])
            row = frame.search(r"All \(\d+\)")
            reverse = [run.text.strip() for run in frame.attr_runs(row)
                       if run.attrs.reverse and run.text.strip()]
            assert len(reverse) == 1, frame._message(
                "%d filter entries are highlighted, not one: %r"
                % (len(reverse), reverse))
            assert reverse[0] == "%s (%d)" % (label, counts[label]), (
                frame._message("the highlighted filter is %r, not %r"
                               % (reverse[0], label)))
    finally:
        term.close()


def test_t_cycles_the_runners_filter_and_the_header_follows_it():
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        frame = open_view(term, "W", "runners")
        seen = []
        for index, (label, _status) in enumerate(RUNNERS_FILTERS):
            if index:
                frame = term.send("T", expect=NEEDLES["runners"])
                assert frame.paint_end == "synchronised", frame._message(
                    "captured on %r" % frame.paint_end)
            figure = filter_figure(frame, label)
            seen.append(figure)
            assert view_count(frame, "Runners") == figure, frame._message(
                "under %s the header says %d and the filter row says %d"
                % (label, view_count(frame, "Runners"), figure))
        assert seen[0] > 0 and seen[0] >= max(seen), (
            "All must be the widest filter for this to have filtered anything: "
            "%r" % (seen,))
        assert len(set(seen)) > 1, (
            "every Runners filter showed the same figure — nothing filtered: "
            "%r" % (seen,))
    finally:
        term.close()


def test_the_selection_indexes_the_filtered_list_and_not_the_whole_plan():
    """`T` then `Dn` must move within the rows on screen.

    A selection taken over the unfiltered plan would highlight a leg the filter
    had removed — usually nothing at all, since the row is not on the screen —
    and `Enter` would open the detail of a leg the reader cannot see.
    """
    states = {leg.get("id"): relay_model.normalise_status(leg.get("status"))
              for leg in plan(AGENT_SERVICE)}
    pending = [leg for leg in plan(AGENT_SERVICE)
               if states[leg.get("id")] == "pending"]
    assert len(pending) > 2, "the fixture must have pending legs for this to bite"

    term = session(AGENT_SERVICE, size=WIDE)
    try:
        open_view(term, "F", "legs")
        term.send("T", expect=NEEDLES["legs"])          # All -> Pending
        frame = settled(term, "<Down><Down>")
        chosen = selected_text(frame)
        assert leg_reference(pending[2]) in chosen, frame._message(
            "under Pending, two Downs selected %r rather than the third "
            "pending leg %r" % (chosen, leg_reference(pending[2])))
        detail = term.send("<Enter>", expect="Verification")
        assert detail.contains(leg_reference(pending[2])), detail._message(
            "Enter under a filter opened the detail of another leg")
    finally:
        term.close()


def test_a_filter_that_selects_nothing_says_so_rather_than_drawing_an_empty_pane():
    counts = leg_counts(AGENT_SERVICE)
    empty = [index for index, (label, _) in enumerate(LEGS_FILTERS)
             if counts[label] == 0]
    assert empty, "the fixture must have an empty filter for this to bite"

    term = session(AGENT_SERVICE, size=WIDE)
    try:
        open_view(term, "F", "legs")
        frame = None
        for _ in range(empty[0]):
            frame = term.send("T", expect=NEEDLES["legs"])
        assert view_count(frame, "Legs") == 0
        assert "1-0" not in frame.text, frame._message(
            "an empty filter claims a range the pane did not draw")
        assert frame.search(r"no leg is [a-z]") is not None, frame._message(
            "an empty filter is a blank pane rather than a sentence")
    finally:
        term.close()


@pytest.mark.parametrize("key,view", [("C", "contract"), ("M", "models")])
def test_t_does_nothing_on_a_view_with_no_filter_row(key, view):
    """A view with no filter row does not take `T`, and does not name it.

    A negative assertion, so it is made against a proved frame. The pairing is
    the point: a keybar advertising `T Filter` over a screen that ignores it is
    a lie on every frame, and a view that quietly took `T` and cycled an
    invisible filter would be the same defect the other way round.
    """
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        before = open_view(term, key, view)
        assert "T Filter" not in keybar(before), before._message(
            "the %s view advertises T with no filter row to cycle" % view)
        after = settled(term, "T")
        assert body(after) == body(before), after._message(
            "T changed the %s view, which has no filter row" % view)
    finally:
        term.close()


def test_the_overview_neither_names_nor_takes_the_list_keys():
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        before = repaint(term, expect=READY)
        before.assert_finished()
        bar = keybar(before)
        for absent in ("T Filter", "Up/Dn", "Enter"):
            assert absent not in bar, before._message(
                "the Overview keybar names %r, which it does not take" % absent)
        after = settled(term, "T<Down><Up><Enter>")
        assert body(after) == body(before), after._message(
            "a list key changed the Overview, which has no list")
    finally:
        term.close()


# --------------------------------------------------------------------------
# ACC-NAV-004 — detail view
# --------------------------------------------------------------------------


def test_enter_opens_the_selected_legs_detail():
    """The six fields ACC-NAV-004's evidence names, and the leg they belong to."""
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        first = open_view(term, "F", "legs")
        opening = selected_reference(first)
        # Walk down to a leg that has all six fields to show, and never stop on
        # the first row: a detail that always opened on `rows[0]` would be the
        # right *shape* against the opening row and wrong about every other.
        target = None
        for _ in range(20):
            frame = settled(term, "<Down>")
            leg = leg_of(AGENT_SERVICE, selected_reference(frame))
            if leg.get("boundaries") and leg.get("verification") \
                    and leg.get("touches") and leg.get("fulfills"):
                target = leg
                break
        assert target is not None, "no leg within 20 rows has all six fields"
        reference = selected_reference(frame)
        assert reference != opening

        detail = term.send("<Enter>", expect="Verification")
        assert detail.paint_end == "synchronised", detail._message(
            "captured on %r" % detail.paint_end)
        assert detail.contains(reference), detail._message(
            "the detail does not name %r" % reference)
        assert not detail.contains(opening), detail._message(
            "the detail names the list's first leg rather than the selected one")
        for field in LEG_DETAIL_FIELDS:
            assert detail.contains(field), detail._message(
                "the leg detail has no %r field" % field)
        for check in target["fulfills"]:
            assert detail.contains(check), detail._message(
                "the detail omits the check %r the leg claims" % check)
        assert detail.contains(target["boundaries"][0][:30]), detail._message(
            "the detail omits the leg's first boundary")
        assert detail.contains(target["verification"][0][:30]), detail._message(
            "the detail omits the leg's first verification step")
        assert detail.contains(target["touches"][0].rsplit("/", 1)[-1][:20])
        detail.assert_within_width()
    finally:
        term.close()


def test_esc_leaves_the_detail_with_the_previous_selection_intact():
    """ACC-NAV-004's second half. The row is identified by what it *says*, so
    a selection that moved by one would fail as loudly as one that reset."""
    legs = plan(AGENT_SERVICE)
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        open_view(term, "F", "legs")
        before = settled(term, "<Down>" * 5)
        chosen = selected_text(before)
        assert leg_reference(legs[5]) in chosen

        term.send("<Enter>", expect=DETAIL_KEYS)
        back = term.send("<Esc>", expect=NEEDLES["legs"])
        assert back.paint_end == "synchronised", back._message(
            "captured on %r" % back.paint_end)
        assert selected_text(back) == chosen, back._message(
            "Esc came back with %r selected, not %r"
            % (selected_text(back), chosen))
    finally:
        term.close()


def test_enter_opens_a_runner_detail_naming_the_runners_leg():
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        listing = open_view(term, "W", "runners")
        first = selected_text(listing)
        detail = term.send("<Enter>", expect="Baton")
        assert detail.paint_end == "synchronised"
        assert detail.contains("Runner #"), detail._message(
            "the runner detail does not name the runner")
        leg = first.split()[1] if len(first.split()) > 1 else first
        assert detail.contains(leg), detail._message(
            "the runner detail names a different leg from the row it opened on")
        for field in ("Leg", "Stage", "Commit", "Baton"):
            assert detail.contains(field)
    finally:
        term.close()


def test_enter_opens_a_check_detail_showing_the_state_the_list_shows(tmp_path):
    """A check claiming `passed` with no evidence is *shown* blocked.

    The list applies the contract's opening rule; a detail that read the
    claimed status straight out of `state.json` would print `passed` over a row
    the reader just saw drawn `blocked`, which is the two-figures defect the
    Contract view exists to remove, one screen further in.
    """
    relay = synthetic_relay(
        tmp_path / "unevidenced",
        legs=[("only-leg", "running")],
        check_records={"ACC-FAKE-001": {"status": "passed", "stage": "S1",
                                        "claimedBy": "only-leg"}})
    term = session(relay, size=WIDE)
    try:
        listing = open_view(term, "C", "contract")
        assert listing.contains("blocked"), listing._message(
            "the list does not downgrade an unevidenced check")
        detail = term.send("<Enter>", expect="Claimed by")
        assert detail.paint_end == "synchronised"
        assert detail.contains("ACC-FAKE-001")
        # The Status field, read as the row under its own label.
        label = detail.find("Status")
        assert label is not None, detail._message("the detail has no Status field")
        assert detail.lines[label + 1].strip() == "blocked", detail._message(
            "the detail's Status reads %r where the list drew `blocked`"
            % detail.lines[label + 1].strip())
        assert detail.contains("no evidence"), detail._message(
            "the detail downgraded the check without saying why")
        # The pane's own right-hand figure, which is the other place a state
        # can be told: it is the shown one there too.
        assert detail.lines[2].rstrip().endswith("blocked"), detail._message(
            "the detail's own figure reads %r where the list drew `blocked`"
            % detail.lines[2].rstrip()[-20:])
    finally:
        term.close()


def test_enter_opens_the_detail_of_the_check_the_selected_row_names():
    """Which check, not just what a check detail looks like.

    Read off the frame in both directions: the id on the highlighted row is the
    id in the detail's title. A `rows` list in any other order than the one the
    view draws — the model's, say, rather than `shown_status()`'s — puts a
    reader on one row and a detail on another, and nothing on either screen
    says so.
    """
    term = session(AGENT_SERVICE, size=STANDARD)
    try:
        listing = open_view(term, "C", "contract")
        row = selected_text(listing).split()
        identifier = next(word for word in row if word.startswith("ACC-"))
        detail = term.send("<Enter>", expect="Claimed by")
        assert detail.paint_end == "synchronised"
        title = detail.lines[2].split()[0]
        assert title == identifier, detail._message(
            "the selected row named %r and the detail opened on %r"
            % (identifier, title))

        # And again, three rows further down, so a detail that happened to sit
        # on the first row cannot pass.
        term.send("<Esc>", expect="evidenced")
        listing = settled(term, "<Down>" * 3)
        row = selected_text(listing).split()
        identifier = next(word for word in row if word.startswith("ACC-"))
        detail = term.send("<Enter>", expect="Claimed by")
        assert detail.lines[2].split()[0] == identifier, detail._message(
            "after three Downs the row named %r and the detail opened on %r"
            % (identifier, detail.lines[2].split()[0]))
    finally:
        term.close()


def field_lines(frame, label):
    """The rows under a detail's `label`, up to the next label or the marker."""
    row = frame.find(label)
    assert row is not None, frame._message("the detail has no %r field" % label)
    out = []
    for line in frame.raw_lines[row + 1:]:
        if not line.startswith(" " * 2) or not line.strip():
            break
        out.append(line.strip())
    return out


def test_a_detail_says_none_for_a_field_the_row_has_no_value_for():
    """Three states, three spellings — `models-view`'s rule, one view over.

    A field with nothing in it draws the word `none`, in `theme.ABSENT`. Not a
    blank, which reads as a pane that failed to draw; not `None`, which is a
    Python object's name reaching a supervisor's screen; and not the label on
    its own, which is a heading pointing at nothing.
    """
    relay = FIXTURES / "agent-service"
    term = session(relay, size=WIDE)
    try:
        open_view(term, "C", "contract")
        detail = term.send("<Enter>", expect="Claimed by")
        empty = [label for label in ("Judged by", "Reason", "Fix leg")
                 if field_lines(detail, label) == ["none"]]
        assert empty, detail._message(
            "no field on this check reads `none`, so this proves nothing about "
            "how an absent field is drawn")
        assert "None" not in detail.text, detail._message(
            "a Python `None` reached the screen")
        for label in ("Area", "Status", "Claimed by", "Judged by", "Reason",
                      "Fix leg", "Evidence"):
            assert field_lines(detail, label), detail._message(
                "the %r field is a label with nothing under it" % label)
        detail.assert_attrs("none", has="dim", row=detail.find("Judged by") + 1)
    finally:
        term.close()


def test_a_list_field_draws_one_value_per_row():
    """A leg's `fulfills` is several check ids, not one sentence.

    Joined onto one line they would all still be *on the screen*, which is why
    this asserts the shape rather than the presence: one id per row, each on a
    row of its own, because a leg claiming nine checks is nine facts and not a
    paragraph.
    """
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        open_view(term, "F", "legs")
        for _ in range(20):
            frame = settled(term, "<Down>")
            leg = leg_of(AGENT_SERVICE, selected_reference(frame))
            if len(leg.get("fulfills") or []) > 2:
                break
        else:
            raise AssertionError("no leg within 20 rows claims three checks")
        detail = term.send("<Enter>", expect="Verification")
        rows = field_lines(detail, "Fulfills")
        assert rows == list(leg["fulfills"]), detail._message(
            "Fulfills drew %r for a leg claiming %r" % (rows, leg["fulfills"]))
    finally:
        term.close()


@pytest.mark.parametrize("rows", [24, 16, 12, 9])
def test_a_detail_too_long_for_the_pane_says_how_much_it_is_hiding(rows):
    """`+N more`, and a field label is never the last row it drew.

    A detail is one list of rows cut once, like every other multi-region pane
    in this package: what it cannot show it says it cannot show, and it does not
    spend its last row on a label for content there was no room for.
    """
    term = session(AGENT_SERVICE, size=(rows, 80))
    try:
        open_view(term, "C", "contract", expect="Contract")
        detail = term.send("<Enter>", expect=DETAIL_KEYS)
        detail.assert_finished()
        detail.assert_within_width()
        hidden = frame_marker = detail.search(r"^\+\d+ more")
        if frame_marker is None:
            assert rows >= 24, detail._message(
                "at %d rows the detail fitted nothing extra and said nothing "
                "was hidden" % rows)
            return
        above = detail.raw_lines[hidden - 1].strip()
        assert above not in ("Area", "Status", "Stage", "Claimed by",
                             "Judged by", "Reason", "Fix leg", "Evidence"), (
            detail._message("at %d rows the detail's last row of content is "
                            "the label %r, with the marker under it and "
                            "nothing else" % (rows, above)))
    finally:
        term.close()


def test_a_detail_advertises_only_the_keys_it_takes():
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        open_view(term, "F", "legs")
        detail = term.send("<Enter>", expect=DETAIL_KEYS)
        bar = keybar(detail)
        assert bar.startswith(DETAIL_KEYS), detail._message(
            "a detail's keybar reads %r, not %r" % (bar, DETAIL_KEYS))
        for absent in DETAIL_FORBIDDEN:
            assert absent not in bar, detail._message(
                "a detail names %r, which it does not take" % absent)
    finally:
        term.close()


def test_the_list_keys_do_nothing_while_a_detail_is_open():
    """The keybar says Esc, Tab and q; every other key it used to take must be
    inert, or the keybar is lying about the screen a reader is looking at."""
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        open_view(term, "F", "legs")
        settled(term, "<Down><Down>")
        before = term.send("<Enter>", expect=DETAIL_KEYS)
        after = settled(term, "<Down><Up><Enter>T")
        assert body(after) == body(before), after._message(
            "a key the detail does not advertise changed it")
    finally:
        term.close()


def test_tab_out_of_a_detail_lands_on_the_next_view_not_on_a_detail_of_it():
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        open_view(term, "F", "legs")
        term.send("<Enter>", expect=DETAIL_KEYS)
        nxt = term.send("<Tab>", expect=NEEDLES["runners"])
        assert nxt.contains(NEEDLES["runners"])
        assert not keybar(nxt).startswith(DETAIL_KEYS), nxt._message(
            "Tab out of a detail opened another detail")
    finally:
        term.close()


# --------------------------------------------------------------------------
# ACC-NAV-005 — quit is clean
# --------------------------------------------------------------------------


@pytest.mark.parametrize("keys", ["", "F", "W", "M", "C", "F<Enter>", "F<Down>T"])
def test_q_exits_zero_from_anywhere_in_the_program(keys):
    term = session(AGENT_SERVICE, size=STANDARD)
    try:
        if keys:
            term.send(keys)
        term.send("q")
        assert term.wait(timeout=10.0) == 0, (
            "q after %r did not exit 0" % keys)
    finally:
        term.close()


def test_quitting_restores_the_terminal_it_was_given():
    """ACC-NAV-005, read from the *master* fd.

    macOS revokes the slave the moment the session leader exits — which is
    exactly when this check wants to look — so `stty` on the child's own tty
    cannot answer. `termios_attrs()` reads the master side, which survives and
    carries the same struct: echo, canonical mode and the rest.
    """
    term = session(AGENT_SERVICE, size=STANDARD)
    try:
        before = term.initial_attrs
        assert before is not None, "the harness could not read the pty attributes"
        open_view(term, "F", "legs")
        term.send("<Enter>")                 # quit from a detail, not just a list
        term.send("q")
        assert term.wait(timeout=10.0) == 0
        assert term.termios_attrs() == before, (
            "the terminal was not restored:\n before %r\n after  %r"
            % (before, term.termios_attrs()))
        assert term.screen.cursor_visible, (
            "the cursor was left hidden after the program exited")
        assert not term.screen.synchronized_update, (
            "the program exited inside a DEC 2026 bracket it had opened")
    finally:
        term.close()


def test_quitting_gives_the_primary_screen_back():
    term = session(AGENT_SERVICE, size=STANDARD)
    try:
        open_view(term, "C", "contract")
        held = term.last_alt_frame()
        term.send("q")
        assert term.wait(timeout=10.0) == 0
        assert term.frame().contains(READY) is False, (
            "the program left its own screen behind on exit")
        held = term.last_alt_frame()
        assert held is not None and held.contains(READY), (
            "the program never took the alternate screen, so there is nothing "
            "for this check to be about")
    finally:
        term.close()


# --------------------------------------------------------------------------
# One key, one handler — the defect this leg exists to prevent
# --------------------------------------------------------------------------

#: The ordinals and key names that *are* navigating. A module naming one of
#: these is binding it, and exactly one module may.
NAVIGATION_KEYS = ("KEY_UP", "KEY_DOWN", "KEY_ENTER")
FILTER_LITERALS = ("t", "T")


def package_modules():
    return sorted(PACKAGE.rglob("*.py"))


def key_bindings(paths):
    """`{module name: [what it binds]}` and the AST node count walked.

    Two shapes, because there are two ways to name a key here: `curses.KEY_UP`
    and friends by attribute, and `ord("t")` by call. The node count comes back
    so a caller can prove the sweep looked at something — a filter that reached
    no file and a clean package report the same findings.
    """
    found = {}
    nodes = 0
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            nodes += 1
            named = None
            if isinstance(node, ast.Attribute) and node.attr in NAVIGATION_KEYS:
                named = node.attr
            elif (isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name)
                  and node.func.id == "ord"
                  and len(node.args) == 1
                  and isinstance(node.args[0], ast.Constant)
                  and node.args[0].value in FILTER_LITERALS):
                named = 'ord("%s")' % node.args[0].value
            if named is not None:
                found.setdefault(path.name, []).append(named)
    return found, nodes


def module_level_handlers(paths):
    """Every module-level `def handle(...)` in the package, by module."""
    found = {}
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "handle":
                    found.setdefault(path.name, []).append(node.lineno)
    return found


def test_only_one_module_names_a_navigation_key():
    """`T`, the arrows and `Enter` are bound in exactly one place.

    This is the check the leg exists for, and it is structural on purpose. Two
    handlers for one key are *behaviourally identical* while the first one
    still runs — the second is dead code that reads as live — so no frame on
    any screen can tell them apart. What separates them is that there are two,
    and that is a fact about the source.
    """
    modules = package_modules()
    found, nodes = key_bindings(modules)
    assert set(found) <= {KEY_OWNER}, (
        "a navigation key is bound outside %s: %r. Two handlers for one key is "
        "how a view stops responding for reasons nobody can find — delete the "
        "second, do not leave it to shadow the first." % (KEY_OWNER, found))
    # Every key, by the spelling the sweep can see. A key respelled as a bare
    # ordinal (`116` for `ord("t")`) is a key this sweep would stop watching,
    # and the module that owns them would still look clean.
    assert set(found.get(KEY_OWNER, ())) == {'ord("t")', 'ord("T")', "KEY_UP",
                                             "KEY_DOWN", "KEY_ENTER"}, (
        "%s names %r, not every navigation key by a spelling this sweep can "
        "see — a key respelled as a bare ordinal escapes it silently"
        % (KEY_OWNER, sorted(set(found.get(KEY_OWNER, ())))))
    assert len(modules) >= 7, "swept only %r" % ([p.name for p in modules],)
    assert {"app.py", "legs.py", "runners.py", "contract.py", "models.py",
            KEY_OWNER} <= {p.name for p in modules}
    assert nodes > 500, "the sweep walked only %d AST nodes" % nodes


def test_no_view_module_defines_a_key_handler():
    """The `handle()` hook is gone, not merely unused.

    A view that still defined one would be offered nothing by `app._run` — so
    it would be silent, and silence is what a shadowed handler looks like from
    every screen. The absence is asserted where it can be seen.
    """
    handlers = module_level_handlers(package_modules())
    assert not handlers, (
        "these modules still define a key handler: %r. `app._navigate` is the "
        "one handler; a second is dead code that reads as live." % handlers)


def test_the_sweep_finds_a_planted_second_handler(tmp_path):
    """Non-vacuity: both sweeps are shown failing on exactly the mutation this
    leg must keep out — a view module that binds `T` and hands it back through
    a `handle()`.

    The plant goes in `tmp_path` and not in `scripts/relay_control/`, which is
    where `test_chrome.py`'s reader sweep puts its own. That plant is sound and
    this file deliberately does not copy it: this repository is a shared tree
    with more than one runner in it, and a module that exists inside the package
    for ten milliseconds is a module another runner's sweep can collect. The two
    halves a plant inside the package proves at once are proved separately here,
    and neither can be vacuous on its own:

    * **the collection reaches the package** — `package_modules()` is asserted
      to return the real view modules, and the sweep over them is asserted to
      have walked a five-hundred-node tree, in the two tests above;
    * **the detector bites** — here, on a file that is a module like any other.
    """
    planted = tmp_path / "_navigation_shadow_probe.py"
    planted.write_text(
        "import curses\n"
        "\n"
        "\n"
        "def handle(key, state, model):\n"
        "    if key in (ord('t'), ord('T'), curses.KEY_UP):\n"
        "        return True\n"
        "    return False\n"
    )
    found, nodes = key_bindings([planted])
    assert nodes > 10, "the sweep did not walk the planted module"
    assert found.get(planted.name) == ['ord("t")', 'ord("T")', "KEY_UP"], (
        "the key sweep read %r out of a module binding ord('t'), ord('T') and "
        "curses.KEY_UP — it is vacuous, and would pass a second handler for T"
        % (found,))
    assert planted.name in module_level_handlers([planted]), (
        "the handler sweep passed a module defining handle() — it is vacuous, "
        "and would pass the hook coming back")


# --------------------------------------------------------------------------
# degradation — navigating a terminal too small for the thing being navigated
# --------------------------------------------------------------------------


@pytest.mark.parametrize("size", [(48, 160), (24, 80), (12, 40), (6, 20),
                                  (4, 12)])
def test_navigating_at_every_size_degrades_rather_than_crashing(size):
    """Every key this leg owns, on every view, at sizes down to 4x12.

    Nothing here asserts a layout — at 4x12 there is no pane to lay out. What
    is asserted is that the program is still painting whole frames afterwards,
    that no row wrapped, and that nothing on screen is a traceback.
    """
    term = session(AGENT_SERVICE, size=size)
    try:
        for key, _view in JUMPS:
            term.send(key)
            term.send("T<Down><Down><Up><Enter>")
            frame = settled(term, "<Esc>")
            assert "Traceback" not in frame.text, frame._message(
                "navigating the %s view at %r left a traceback" % (_view, size))
            frame.assert_within_width()
        term.send("q")
        assert term.wait(timeout=10.0) == 0, (
            "the program did not exit 0 after being navigated at %r" % (size,))
    finally:
        term.close()


def test_a_detail_whose_list_empties_under_it_falls_back_to_the_list(tmp_path):
    """The relay moves while a detail is open (ACC-LIVE-001's world).

    The row a detail shows is looked up out of the model *this repaint* was
    built from, never remembered from the keystroke that opened it — so a leg
    that has gone is a lookup with nothing to find, and `rows[selected]` on an
    empty list is an `IndexError` inside `curses.wrapper`, which takes the
    terminal down with it.
    """
    relay = synthetic_relay(tmp_path / "vanishing",
                            legs=[("only-leg", "running")])
    term = session(relay, size=STANDARD)
    try:
        open_view(term, "F", "legs")
        detail = term.send("<Enter>", expect=DETAIL_KEYS)
        assert detail.contains("only-leg")
        synthetic_relay(tmp_path / "vanishing")          # the plan is emptied
        frame = settled(term, BARRIER)
        assert "Traceback" not in frame.text, frame._message(
            "the detail's leg went away and took the program with it")
        term.send("q")
        assert term.wait(timeout=10.0) == 0, (
            "the program did not survive its detail's row disappearing")
    finally:
        term.close()


def test_a_relay_with_nothing_to_select_takes_the_keys_without_moving(tmp_path):
    """An empty list is the one case where every key this leg owns is a no-op.

    A `rows[selected]` written without the guard raises `IndexError` here, and
    a curses program that raises loses the terminal it was given.
    """
    relay = synthetic_relay(tmp_path / "bare")
    term = session(relay, size=STANDARD)
    try:
        for key, _view in JUMPS:
            term.send(key)
            frame = settled(term, "<Down><Enter>T<Up><Enter>")
            assert "Traceback" not in frame.text, frame._message(
                "an empty %s view crashed on a navigation key" % _view)
        term.send("q")
        assert term.wait(timeout=10.0) == 0
    finally:
        term.close()

"""Frame tests for the four Overview panes (ACC-OVER-001..005).

One region per check in `.relay/contract.md`. Every visual claim is asserted
against a frame captured from a real curses process under a pty; nothing here
inspects the program's internals, and no pane's geometry is known to this file —
where a pane starts and ends is *measured off the captured frame* by
`pane_view()`, so a layout change moves the assertions rather than breaking them.

Four rules this file follows, all of them paid for by an earlier leg:

* **Figures are read from the fixture's own files at assert time.** The
  agent-service fixture has been refreshed once already and every hardcoded
  figure in the contract had to be amended. `leg_entries()`, `leg_spec()`,
  `running_leg()`, `checks_of()`, `expected_result()` and `phase_of()` parse the
  fixture's `legs.json` and `state.json` here, so the view is checked against
  the relay and not against itself. Three things come from
  `relay_model.build()` instead — plan order, the size of the *derived*
  progress log, and the active runner's `n` — because each is a derivation the
  model owns and `tests/test_relay_model.py` and `tests/test_progress_log.py`
  already certify. Writing them out again here would be a second implementation
  rather than a second opinion; what is asserted against them is the *view's*
  property (drew a contiguous run, claimed the range it drew, took the model's
  own runner).
* **The harness comes from `tests/test_chrome.py`**, which is the settled
  pattern for driving the TUI. Imported rather than copied: two copies of
  `_utf8_env()` is two places for a locale bug to hide.
* **Box glyphs are asserted as box glyphs** (`│`, `─`). `curses` sends them
  through the alternate character set and the emulator translates.
* **`assert_within_width()` is used in its strict form.** The chrome reserves
  the screen's last column so it can be; a pane that filled that column would
  take the certification away from every other test in the repository.
"""

import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frame import display_width  # noqa: E402

from test_chrome import (  # noqa: E402
    FIXTURES, STANDARD, UTF8_ENV, WIDE, frame_of, leg_figures, repaint,
    session,
)

import relay_model  # noqa: E402

PANES = ("Active Leg", "Legs", "Progress Log", "Active Runner")

#: An age as `chrome.humanise_age()` spells it: `now`, `47m ago`, `27h ago`.
AGE = re.compile(r"^\s*(now|\d+[mhd] ago)\s+\S")

#: An elapsed time as `chrome.humanise_duration()` spells it.
ELAPSED = re.compile(r"(\d+h \d+m|\d+m \d+s|\d+s)$")


# --------------------------------------------------------------------------
# reading a pane off a captured frame
# --------------------------------------------------------------------------


class PaneView:
    """One Overview pane, located by measuring the frame it was drawn on.

    The left edge is where the pane's title was drawn, the right edge is the
    column rule beside it (or the screen's edge), and the last body row is the
    row before the rule underneath it (or before the keybar). Nothing here
    knows what `chrome.overview_frame()` computes, which is the point: these
    tests certify the screen a supervisor reads, not the arithmetic behind it.
    """

    def __init__(self, frame, title):
        self.frame = frame
        self.title = title
        self.top = frame.find(title)
        if self.top is None:
            raise AssertionError(frame._message("no pane titled %r" % title))
        self.left = frame.raw_lines[self.top].index(title)
        rule = frame.raw_lines[self.top].find("│", self.left)
        self.right = frame.cols if rule < 0 else rule
        self.rows = []
        for index in range(self.top + 1, frame.rows - 1):
            segment = frame.raw_lines[index][self.left:self.right]
            if segment.strip() and set(segment.strip()) == {"─"}:
                break
            self.rows.append(segment.rstrip())

    @property
    def width(self):
        return self.right - self.left

    @property
    def header(self):
        return self.frame.raw_lines[self.top][self.left:self.right].rstrip()

    @property
    def text(self):
        return "\n".join(self.rows)

    @property
    def filled(self):
        """The body rows with something on them."""
        return [row for row in self.rows if row.strip()]

    def contains(self, needle):
        return any(needle in row for row in self.rows)

    def index(self, needle):
        """The body row `needle` is on, counted from the first row under the
        title. Fails loudly rather than returning None."""
        for index, row in enumerate(self.rows):
            if needle in row:
                return index
        raise AssertionError(self.frame._message(
            "the %s pane does not show %r" % (self.title, needle)))

    def row(self, needle):
        return self.rows[self.index(needle)]

    def screen_row(self, needle):
        """The absolute frame row, for the attribute assertions."""
        return self.top + 1 + self.index(needle)

    def search(self, pattern):
        for row in self.rows:
            found = re.search(pattern, row)
            if found:
                return found
        return None

    def message(self, reason):
        return self.frame._message("%s pane: %s" % (self.title, reason))


def pane_view(frame, title):
    return PaneView(frame, title)


# --------------------------------------------------------------------------
# figures, read from the fixture at assert time
# --------------------------------------------------------------------------


def legs_file(relay_dir):
    path = Path(relay_dir) / "legs.json"
    return json.loads(path.read_text()) if path.exists() else {}


def state_file(relay_dir):
    path = Path(relay_dir) / "state.json"
    return json.loads(path.read_text()) if path.exists() else {}


def leg_entries(relay_dir):
    return [leg for leg in legs_file(relay_dir).get("legs", [])
            if isinstance(leg, dict)]


def plan_order(relay_dir):
    """Leg ids in plan order.

    Taken from the model on purpose. Plan order is a rule the model owns and
    `tests/test_relay_model.py` certifies — stages as declared, each stage's
    legs as its list declares them, then the legs that list forgot — and
    writing it out a second time here would be a second implementation rather
    than a second opinion. What this file checks with it is the view's own
    property: that the Legs pane draws a *contiguous run* of that order, having
    neither reordered nor quietly dropped a leg. The count it is checked
    against is read from `legs.json` itself.
    """
    return [leg["id"] for leg in relay_model.build(str(relay_dir))["legs"]]


def leg_spec(relay_dir, leg_id):
    for leg in leg_entries(relay_dir):
        if leg.get("id") == leg_id:
            return leg
    raise AssertionError("no leg %r in %s/legs.json" % (leg_id, relay_dir))


def running_leg(relay_dir):
    """The id of the leg the fixture's own `legs.json` marks running."""
    for leg in leg_entries(relay_dir):
        if relay_model.normalise_status(leg.get("status")) == "running":
            return leg.get("id")
    return None


def stage_name(relay_dir, leg_id):
    stage = leg_spec(relay_dir, leg_id).get("stage")
    for entry in legs_file(relay_dir).get("stages", []):
        if (entry or {}).get("id") == stage:
            return entry.get("name")
    return None


def phase_of(relay_dir):
    """The phase `state.json` declares, or `pending` where it declares none."""
    phase = state_file(relay_dir).get("phase")
    return phase.strip() if isinstance(phase, str) and phase.strip() else "pending"


def checks_of(relay_dir):
    checks = state_file(relay_dir).get("checks")
    return checks if isinstance(checks, dict) else {}


def expected_result(relay_dir, step):
    """What the fixture's own `state.json` says this step's last result was.

    A step naming a check the relay has judged carries that check's status; a
    step naming none — or naming one nobody has judged yet — has no result.
    """
    for cid, check in checks_of(relay_dir).items():
        if cid not in step or not isinstance(check, dict):
            continue
        status = check.get("status")
        if status == "pending" and check.get("round") is None:
            continue
        return status
    return "no result recorded"


def opening(text, words=4):
    """The first few words of a spec line — what survives wrapping."""
    return " ".join(str(text).split()[:words])


def leg_rows_of(pane):
    """`[leg id]` in the order the Legs pane drew them, markers excluded."""
    ids = []
    for row in pane.filled:
        if row.startswith("+"):                 # `+18 earlier` / `+8 more`
            continue
        parts = row.split(None, 1)
        if len(parts) == 2:
            ids.append(parts[1].strip())
    return ids


def marker(pane, pattern):
    found = pane.search(pattern)
    return int(found.group(1)) if found else 0


# --------------------------------------------------------------------------
# ACC-OVER-001 — the Active Leg pane shows the leg's spec
# --------------------------------------------------------------------------


def test_the_active_leg_pane_names_the_running_leg_and_its_stage():
    relay = FIXTURES / "agent-service"
    frame = frame_of(relay, size=WIDE)
    leg_id = running_leg(relay)
    assert leg_id, "the fixture no longer has a running leg"

    pane = pane_view(frame, "Active Leg")
    assert pane.contains(leg_id), pane.message("it does not name %r" % leg_id)
    assert pane.contains(stage_name(relay, leg_id)), pane.message(
        "it does not name the leg's stage")
    assert leg_spec(relay, leg_id)["stage"] in pane.header, pane.message(
        "the header does not carry the leg's stage id")
    # The id is the value the reader's eye should land on (`theme.EMPHASIS`).
    frame.assert_attrs(leg_id, has="bold", row=pane.screen_row(leg_id))


def test_the_active_leg_pane_shows_an_impl_legs_goal():
    relay = FIXTURES / "running-impl"
    frame = frame_of(relay, size=WIDE)
    leg_id = running_leg(relay)
    goal = leg_spec(relay, leg_id)["goal"]

    pane = pane_view(frame, "Active Leg")
    assert pane.contains(opening(goal, 8)), pane.message(
        "it does not show the running leg's goal")


def test_the_active_leg_pane_lists_boundaries_and_verification():
    relay = FIXTURES / "running-impl"
    frame = frame_of(relay, size=WIDE)
    spec = leg_spec(relay, running_leg(relay))
    assert spec["boundaries"] and spec["verification"], (
        "this fixture's running leg must carry both lists for the test to mean "
        "anything")

    pane = pane_view(frame, "Active Leg")
    for heading, items in (("Boundaries", spec["boundaries"]),
                           ("Verification", spec["verification"])):
        assert pane.contains(heading), pane.message("no %r heading" % heading)
        for item in items:
            assert pane.contains(opening(item)), pane.message(
                "%s omits %r" % (heading, item))
        # The heading is above the items it names, not below them.
        assert pane.index(heading) < pane.index(opening(items[0]))


def test_a_judge_leg_draws_no_empty_boundaries_or_verification_headings():
    """A judge leg carries neither list, and an empty heading invents one."""
    relay = FIXTURES / "agent-service"
    spec = leg_spec(relay, running_leg(relay))
    assert not spec.get("boundaries") and not spec.get("verification"), (
        "this fixture's running leg must carry neither list for the test to "
        "mean anything")

    pane = pane_view(frame_of(relay, size=WIDE), "Active Leg")
    assert not pane.contains("Boundaries"), pane.message(
        "a leg with no boundaries must not be given a Boundaries heading")
    assert not pane.contains("Verification"), pane.message(
        "a leg with no verification list must not be given the heading")
    # ...and the pane still says something rather than trailing off blank.
    assert len(pane.filled) >= 2, pane.message("it degenerated into a blank box")


def test_the_active_leg_pane_truncates_with_a_more_marker():
    """`+N more` when the pane is too short, and it accounts for the loss."""
    relay = FIXTURES / "running-impl"
    frame = frame_of(relay, size=STANDARD)
    spec = leg_spec(relay, running_leg(relay))
    items = list(spec["boundaries"]) + list(spec["verification"])

    pane = pane_view(frame, "Active Leg")
    missing = [item for item in items if not pane.contains(opening(item))]
    assert missing, pane.message(
        "the pane fitted everything at this size, so it proves no truncation")
    hidden = marker(pane, r"\+(\d+) more")
    assert hidden >= len(missing), pane.message(
        "%d spec lines were dropped and the marker claims %d"
        % (len(missing), hidden))


def test_a_truncated_pane_spends_no_row_on_whitespace():
    """Whitespace is the cheapest row in a pane, so it is the first to go.

    A pane that kept a blank separator and then wrote `+12 more` hid a line of
    the leg's spec in order to keep a gap.
    """
    pane = pane_view(frame_of(FIXTURES / "running-impl", size=STANDARD),
                     "Active Leg")
    found = pane.search(r"\+\d+ more")
    assert found, pane.message(
        "the pane fitted everything at this size, so it proves no truncation")
    above = pane.rows[:pane.index(found.group(0))]
    assert above and all(row.strip() for row in above), pane.message(
        "a truncated pane kept a blank row while hiding a line: %r" % (above,))


@pytest.mark.parametrize("relay,size", [
    ("running-impl", WIDE),
    ("running-impl", STANDARD),
    ("running-impl", (30, 100)),
    ("running-impl", (20, 70)),
    ("running-impl", (16, 60)),
    ("running-impl", (14, 50)),
    ("running-impl", (12, 40)),
    ("stale-currentleg", STANDARD),
])
def test_a_heading_is_never_the_last_thing_a_pane_drew(relay, size):
    """Truncating onto a heading draws the empty heading one row lower down."""
    frame = frame_of(FIXTURES / relay, size=size)
    for title in ("Active Leg", "Active Runner"):
        if frame.find(title) is None:           # dropped at this size
            continue
        pane = pane_view(frame, title)
        for heading in ("Boundaries", "Verification"):
            if not pane.contains(heading):
                continue
            below = pane.rows[pane.index(heading) + 1:]
            assert below, pane.message("%r is the last row of the pane" % heading)
            assert below[0].strip(), pane.message(
                "%r has a blank row under it" % heading)
            assert not below[0].strip().startswith("+"), pane.message(
                "%r has nothing under it but its own overflow marker" % heading)
            assert below[0].strip() not in ("Boundaries", "Verification"), \
                pane.message("%r is followed by another heading" % heading)


# --------------------------------------------------------------------------
# ACC-OVER-002 — the Active Leg pane when nothing is running
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relay", ["all-done", "empty"])
def test_with_no_running_leg_the_pane_states_the_phase(relay):
    relay_dir = FIXTURES / relay
    assert running_leg(relay_dir) is None, "this fixture has a running leg"
    pane = pane_view(frame_of(relay_dir, size=WIDE), "Active Leg")
    assert pane.contains(phase_of(relay_dir).upper()), pane.message(
        "it does not state the relay's phase")


@pytest.mark.parametrize("relay", ["all-done", "empty"])
def test_with_no_running_leg_the_pane_says_what_is_waited_on(relay):
    """Never a blank box: a titled pane with an empty body reads as a crash."""
    pane = pane_view(frame_of(FIXTURES / relay, size=WIDE), "Active Leg")
    assert len(pane.filled) >= 2, pane.message(
        "it states a phase and then nothing about what is being waited on")
    assert pane.rows[0].strip(), pane.message("its first body row is blank")
    words = " ".join(pane.filled[1:]).lower()
    assert any(word in words for word in ("waiting", "waited", "planned")), \
        pane.message("nothing in it says what the relay is waiting on")


def test_a_finished_relay_reads_as_finished_and_counts_its_legs():
    relay = FIXTURES / "all-done"
    pane = pane_view(frame_of(relay, size=WIDE), "Active Leg")
    done, total = leg_figures(relay)
    assert pane.contains("COMPLETE")
    assert pane.contains("%d legs" % total), pane.message(
        "a complete relay should still say how many legs it ran")
    assert pane.contains("%d done" % done), pane.message(
        "the tally does not agree with the fixture's own legs.json")


# --------------------------------------------------------------------------
# ACC-OVER-003 — the Legs pane
# --------------------------------------------------------------------------


def test_the_legs_pane_header_counts_done_over_total():
    relay = FIXTURES / "agent-service"
    pane = pane_view(frame_of(relay, size=WIDE), "Legs")
    done, total = leg_figures(relay)
    assert pane.header.endswith("%d/%d" % (done, total)), pane.message(
        "the header does not carry the fixture's own done/total")


@pytest.mark.parametrize("relay", ["agent-service", "running-impl"])
def test_the_legs_pane_draws_legs_in_plan_order(relay):
    relay_dir = FIXTURES / relay
    pane = pane_view(frame_of(relay_dir, size=WIDE), "Legs")
    order = plan_order(relay_dir)
    drawn = leg_rows_of(pane)
    assert drawn, pane.message("no leg rows at all")
    for leg_id in drawn:
        assert leg_id in order, pane.message("%r is not a planned leg" % leg_id)
    start = order.index(drawn[0])
    assert drawn == order[start:start + len(drawn)], pane.message(
        "the legs are not a run of the plan: %r" % (drawn,))


def test_the_legs_pane_highlights_the_running_leg():
    relay = FIXTURES / "agent-service"
    frame = frame_of(relay, size=WIDE)
    leg_id = running_leg(relay)
    pane = pane_view(frame, "Legs")

    assert pane.contains(leg_id), pane.message(
        "the running leg is not on screen at all")
    frame.assert_attrs(leg_id, has="reverse", row=pane.screen_row(leg_id))
    # ...and it is the *running* leg that is highlighted, not every row.
    others = [row for row in leg_rows_of(pane) if row != leg_id]
    assert others, pane.message("only one leg was drawn; nothing to compare")
    frame.assert_attrs(others[0], lacks="reverse",
                       row=pane.screen_row(others[0]))


def test_the_legs_pane_keeps_the_running_leg_on_screen_when_it_overflows():
    """A relay 27 legs into 36 may not answer "which leg?" with `+22 more`."""
    relay = FIXTURES / "agent-service"
    pane = pane_view(frame_of(relay, size=WIDE), "Legs")
    order = plan_order(relay)
    leg_id = running_leg(relay)
    assert len(order) > len(pane.rows), (
        "this fixture no longer overflows the pane, so it proves no windowing")
    assert order.index(leg_id) > len(pane.rows), (
        "the running leg is inside the first screenful, so it proves no "
        "windowing either")
    assert pane.contains(leg_id), pane.message(
        "the running leg was scrolled off the pane")


def test_the_legs_pane_markers_account_for_every_leg_it_did_not_draw():
    relay = FIXTURES / "agent-service"
    pane = pane_view(frame_of(relay, size=WIDE), "Legs")
    drawn = leg_rows_of(pane)
    above = marker(pane, r"\+(\d+) earlier")
    below = marker(pane, r"\+(\d+) more")
    assert above and below, pane.message(
        "a window with legs hidden at both ends must say so at both ends")
    total = len(leg_entries(relay))             # counted out of legs.json
    assert above + len(drawn) + below == total, pane.message(
        "%d above + %d drawn + %d below is not the fixture's %d legs"
        % (above, len(drawn), below, total))


def test_a_relay_with_no_legs_says_so():
    pane = pane_view(frame_of(FIXTURES / "empty", size=WIDE), "Legs")
    assert pane.contains("no legs planned yet"), pane.message(
        "emptiness is a sentence, never a blank body")
    assert pane.header.endswith("0/0")


# --------------------------------------------------------------------------
# ACC-OVER-004 — the Progress Log pane
# --------------------------------------------------------------------------


def log_rows(pane):
    return [row for row in pane.filled if not row.strip().startswith("+")]


def test_the_progress_log_shows_the_newest_entries_with_a_relative_age():
    relay = FIXTURES / "agent-service"
    pane = pane_view(frame_of(relay, size=WIDE), "Progress Log")
    entries = relay_model.build(str(relay))["log"]
    assert entries, "the fixture's log is empty; nothing to show"

    rows = log_rows(pane)
    assert rows, pane.message("no log entries were drawn")
    for row in rows:
        assert AGE.match(row), pane.message(
            "%r does not start with a relative age column" % row)
    # Newest first: the first row is the newest entry the model derived.
    assert entries[0]["m"][:40] in rows[0], pane.message(
        "the first row is not the newest entry")


def test_the_progress_log_header_names_the_range_it_actually_drew():
    relay = FIXTURES / "agent-service"
    pane = pane_view(frame_of(relay, size=WIDE), "Progress Log")
    total = len(relay_model.build(str(relay))["log"])
    found = re.search(r"1-(\d+) of (\d+)", pane.header)
    assert found, pane.message("the header carries no range: %r" % pane.header)
    assert int(found.group(1)) == len(log_rows(pane)), pane.message(
        "the header claims %s rows and the pane drew %d"
        % (found.group(1), len(log_rows(pane))))
    assert int(found.group(2)) == total, pane.message(
        "the header claims %s entries and the model derived %d"
        % (found.group(2), total))


@pytest.mark.parametrize("size", [WIDE, STANDARD, (12, 40)])
def test_an_empty_log_reads_none_and_never_a_range_of_nothing(size):
    frame = frame_of(FIXTURES / "empty", size=size)
    assert frame.search(r"1-0 of ") is None, frame._message(
        "`1-0 of 0` is the defect ACC-OVER-004 exists to prevent")
    if frame.find("Progress Log") is None:      # dropped at this size
        return
    pane = pane_view(frame, "Progress Log")
    assert pane.header.endswith("none"), pane.message(
        "an empty log's header must read `none`: %r" % pane.header)
    assert pane.contains("nothing recorded yet"), pane.message(
        "an empty log must say so in the body as well as the header")


def test_the_progress_log_marks_the_entries_it_could_not_draw():
    """A pane that dropped eleven entries in silence claims a log it has not got.

    Three sizes, because how many entries fit is the layout's business and not
    this test's: what is asserted at each is that drawn + hidden is the whole
    log, and at least one of them has to have overflowed or the marker was
    never exercised at all.
    """
    relay = FIXTURES / "agent-service"
    total = len(relay_model.build(str(relay))["log"])
    overflowed = 0
    for size in [STANDARD, (20, 100), (16, 120)]:
        frame = frame_of(relay, size=size)
        if frame.find("Progress Log") is None:   # dropped at this size
            continue
        pane = pane_view(frame, "Progress Log")
        drawn, hidden = len(log_rows(pane)), marker(pane, r"\+(\d+) more")
        assert drawn + hidden == total, pane.message(
            "%d drawn + %d hidden is not the %d entries the model derived"
            % (drawn, hidden, total))
        # ...and the header names the range this pane drew, not the range it
        # would have drawn given room. `1-12 of 12` over one visible entry is
        # `1-0 of 0` wearing a different number.
        found = re.search(r"1-(\d+) of (\d+)", pane.header)
        assert found, pane.message("no range in the header: %r" % pane.header)
        assert (int(found.group(1)), int(found.group(2))) == (drawn, total), \
            pane.message("the header claims %r over %d drawn of %d"
                         % (found.group(0), drawn, total))
        overflowed += 1 if hidden else 0
    assert overflowed, (
        "no size overflowed the Progress Log, so the marker was never drawn")


def test_a_log_too_short_to_draw_one_entry_never_claims_a_range():
    """`1-0 of 12` is `1-0 of 0`'s twin: a range the pane did not draw."""
    frame = frame_of(FIXTURES / "agent-service", size=(12, 40))
    assert frame.search(r"1-0 of ") is None, frame._message(
        "the Progress Log claims a range with nothing in it")


# --------------------------------------------------------------------------
# ACC-OVER-005 — the Active Runner pane
# --------------------------------------------------------------------------


def runner_id(pane):
    """The leg id the Active Runner pane names, beside its `#N`."""
    found = pane.search(r"#(\d+|\?)\s+(\S+)")
    assert found, pane.message("no `#N leg` row")
    return found.group(2)


def active_leg_id(pane):
    """The leg id the Active Leg pane names, after its status glyph."""
    parts = pane.filled[0].split(None, 1)
    assert len(parts) == 2, pane.message("no `glyph id` row")
    return parts[1].split()[0]


@pytest.mark.parametrize("relay,size", [
    ("agent-service", WIDE),
    ("running-impl", WIDE),
    ("running-impl", STANDARD),
    ("stale-currentleg", WIDE),
])
def test_the_two_panes_name_one_leg_in_the_same_frame(relay, size):
    """ACC-DATA-003 in the one place a supervisor would see it break."""
    relay_dir = FIXTURES / relay
    frame = frame_of(relay_dir, size=size)
    leg = active_leg_id(pane_view(frame, "Active Leg"))
    runner = runner_id(pane_view(frame, "Active Runner"))
    assert leg == runner, frame._message(
        "the Active Leg pane says %r and the Active Runner pane says %r"
        % (leg, runner))
    assert leg == running_leg(relay_dir), frame._message(
        "both panes agree on %r, which is not the leg legs.json marks running"
        % leg)


def test_the_active_runner_is_the_models_runner_and_not_an_id_lookup(tmp_path):
    """Two legs answering to one id: identity and equality disagree.

    This is the shape that made the original dashboard's two panes name
    different legs. `model["activeRunner"]` *is* the active leg's row, so the
    pane draws `#3`; a pane that looked the leg up by id would find the first
    row answering to `twin` and draw `#1`.
    """
    (tmp_path / "legs.json").write_text(json.dumps({
        "relay": "twins",
        "stages": [{"id": "S1", "name": "Only stage",
                    "legs": ["twin", "other", "twin"]}],
        "legs": [
            {"id": "twin", "stage": "S1", "kind": "impl",
             "goal": "the first twin.", "status": "done"},
            {"id": "other", "stage": "S1", "kind": "impl",
             "goal": "not a twin.", "status": "done"},
            {"id": "twin", "stage": "S1", "kind": "impl",
             "goal": "the second twin, the one that is running.",
             "verification": ["python3 -m pytest -q"], "status": "running"},
        ],
    }))
    model = relay_model.build(str(tmp_path))
    active = model["activeRunner"]
    lookup = next(row for row in model["runners"]
                  if row["leg"] == model["activeLeg"]["id"])
    assert active is not lookup, (
        "the model no longer distinguishes identity from a matching id, so "
        "this test can no longer see the defect it exists for")

    pane = pane_view(frame_of(tmp_path, size=WIDE), "Active Runner")
    assert pane.contains("#%d" % active["n"]), pane.message(
        "the pane does not draw the model's own active runner #%d" % active["n"])
    assert not pane.contains("#%d" % lookup["n"]), pane.message(
        "the pane drew #%d — the runner found by looking the leg id up, not "
        "the one the model derived from the active leg" % lookup["n"])


@pytest.mark.parametrize("relay", ["agent-service", "running-impl"])
def test_the_active_runner_right_aligns_the_elapsed_time(relay):
    relay_dir = FIXTURES / relay
    pane = pane_view(frame_of(relay_dir, size=WIDE), "Active Runner")
    row = pane.row("#")
    assert ELAPSED.search(row), pane.message(
        "%r does not end with an elapsed time" % row)
    assert len(row) >= pane.width - 1, pane.message(
        "the elapsed time ends at column %d of a %d-wide pane, so it is not "
        "right-aligned" % (len(row), pane.width))


def test_the_active_runner_shows_each_step_with_its_last_known_result():
    relay = FIXTURES / "running-impl"
    pane = pane_view(frame_of(relay, size=WIDE), "Active Runner")
    steps = leg_spec(relay, running_leg(relay))["verification"]
    assert len(steps) > 1, "one step proves nothing about a result column"

    seen = set()
    for step in steps:
        row = pane.row(opening(step))
        expected = expected_result(relay, step)
        seen.add(expected)
        assert row.rstrip().endswith(expected), pane.message(
            "%r ends %r; state.json's last result for it is %r"
            % (step, row.rstrip()[-24:], expected))
    assert len(seen) > 1, (
        "every step in this fixture has the same result, so the test cannot "
        "tell a real result column from a constant")


def test_a_step_the_relay_has_never_judged_says_so_rather_than_guessing():
    relay = FIXTURES / "running-impl"
    pane = pane_view(frame_of(relay, size=WIDE), "Active Runner")
    steps = [step for step in leg_spec(relay, running_leg(relay))["verification"]
             if expected_result(relay, step) == "no result recorded"]
    assert steps, "this fixture no longer has an unjudged step"
    for step in steps:
        assert pane.row(opening(step)).rstrip().endswith("no result recorded"), \
            pane.message("%r is reported as having a result" % step)


def test_a_leg_with_no_verification_steps_says_so_instead_of_a_heading():
    relay = FIXTURES / "agent-service"
    assert not leg_spec(relay, running_leg(relay)).get("verification")
    pane = pane_view(frame_of(relay, size=WIDE), "Active Runner")
    assert pane.contains("no verification steps recorded"), pane.message(
        "a judge leg's runner pane must say why it lists no steps")
    assert not pane.contains("Verification"), pane.message(
        "a heading was drawn over an empty list")


def test_with_no_runner_at_all_the_pane_says_so():
    pane = pane_view(frame_of(FIXTURES / "all-done", size=WIDE), "Active Runner")
    assert pane.contains("no runner is on a leg right now"), pane.message(
        "emptiness is a sentence, never a blank body")


# --------------------------------------------------------------------------
# degradation — the panes at every size, and the reserved last column
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relay,size", [
    ("agent-service", WIDE),
    ("agent-service", STANDARD),
    ("agent-service", (24, 79)),
    ("agent-service", (16, 60)),
    ("agent-service", (12, 40)),
    ("agent-service", (8, 30)),
    ("agent-service", (5, 20)),
    ("agent-service", (3, 12)),
    ("running-impl", STANDARD),
    ("running-impl", (14, 50)),
    ("all-done", STANDARD),
    ("empty", (12, 40)),
])
def test_the_overview_degrades_without_crashing(relay, size):
    term = session(FIXTURES / relay, size=size)
    try:
        frame = term.frame()
        assert "Traceback" not in frame.text, frame._message("the TUI raised")
        assert term.is_running, frame._message("the TUI exited instead")
        assert frame.text.strip(), frame._message("nothing was drawn at all")
        # Strict: the chrome reserves the last column, and a pane that filled
        # it would take that certification away from every other frame test.
        assert not frame.full_width_rows(), frame._message(
            "rows %r reach the reserved last column"
            % (frame.full_width_rows(),))
        frame.assert_within_width()
    finally:
        term.close()


@pytest.mark.parametrize("relay,size", [
    ("agent-service", WIDE),
    ("running-impl", WIDE),
    ("running-impl", STANDARD),
])
def test_the_highlighted_row_stops_at_the_pane_edge(relay, size):
    """The running leg's highlight is padded across its pane, and no further.

    A pane that padded into the column rule beside it would draw over the
    layout; one that padded into the screen's last column would break
    `assert_within_width()` for every test in the repository.
    """
    relay_dir = FIXTURES / relay
    frame = frame_of(relay_dir, size=size)
    pane = pane_view(frame, "Legs")
    leg_id = running_leg(relay_dir)
    row = pane.screen_row(leg_id)
    run = frame.run_with(leg_id, row=row)
    assert run.attrs.reverse, frame._message("the running row is not highlighted")
    reversed_cells = [col for col in range(frame.cols)
                      if frame.attrs_at(row, col).reverse]
    assert min(reversed_cells) >= pane.left, frame._message(
        "the highlight starts left of the pane")
    assert max(reversed_cells) < pane.right, frame._message(
        "the highlight runs past the pane's right edge")
    assert max(reversed_cells) < frame.cols - 1, frame._message(
        "the highlight reaches the reserved last column")


# --------------------------------------------------------------------------
# Width is measured in cells, not in characters
#
# `chrome.cell_width()` is the package's one measure. Two sites in this module
# were still counting Python characters — the highlight the Legs pane pads
# across its width, and the gap `_step_row` leaves between a verification step
# and its result — and neither could be seen by any width assertion: a `Pane`
# clips every write to its own rectangle, so a row built twice as wide as the
# pane reaches the screen *truncated* rather than overrunning it.
#
# What sees it is a control relay. The two relays below differ in one thing:
# whether their prose is drawn in one cell per character or two. They carry the
# same number of *cells*, so a pane that measures cells draws them into the
# same columns, and a pane that measures characters draws the wide one at half
# the width it paints.
# --------------------------------------------------------------------------

#: Nine ideographs — eighteen cells, nine characters — and an ASCII leg id of
#: the same eighteen cells.
CJK_ID = "日本語で書かれた脚"
PLAIN_ID = "x" * 18

#: A verification step naming a check `state.json` records as passed, so the
#: row carries a result to be pushed off the end of. Twenty-three cells either
#: way; eighteen characters against twenty-three.
CELL_CHECK = "ACC-CELL-001"
CJK_STEP = CELL_CHECK + " の検証手順"
PLAIN_STEP = CELL_CHECK + " " + "y" * 10

#: A step far wider than any pane, so the row has to be cut and the result kept.
LONG_CJK_STEP = CELL_CHECK + " " + "検証" * 60

#: The result `state.json` gives that check, as the Active Runner pane spells
#: it. Written out here rather than read from the module.
CELL_RESULT = "passed"

#: The theme's mark for a cut, under the UTF-8 locale `session()` forces.
ELLIPSIS = "…"


def cells_relay(directory, leg_id, step, goal="a goal with nothing odd in it"):
    """A one-leg relay whose leg id and verification step are what is passed.

    Everything else is held constant, so two of these differ only in the one
    field a test is measuring — which is what makes "the same cells, the same
    columns" a statement about the renderer rather than about the fixture.
    """
    directory = Path(directory)
    (directory / "batons").mkdir(parents=True, exist_ok=True)
    (directory / "legs.json").write_text(json.dumps({
        "relay": "cells",
        "stages": [{"id": "S1", "name": "Only stage", "legs": [leg_id]}],
        "legs": [{"id": leg_id, "stage": "S1", "goal": goal,
                  "fulfills": [CELL_CHECK], "verification": [step],
                  "status": "running"}],
    }))
    (directory / "state.json").write_text(json.dumps({
        "relay": "cells", "phase": "running", "currentStage": "S1",
        "currentLeg": leg_id,
        "checks": {CELL_CHECK: {"status": "passed", "stage": "S1"}},
    }))
    (directory / "dashboard.json").write_text(json.dumps(
        {"title": "Cells relay", "path": str(directory)}))
    return directory


def highlight_span(frame):
    """`(row, first, last)` — the one reverse-video run on this screen.

    `theme.SELECTED` is the only token that reaches a colour terminal as
    reverse video, so the run is the highlight and nothing else. Read as
    *columns* off `attrs_at()`: a string index into a line carrying
    double-width text is not the column the terminal drew at.
    """
    found = [(row, col) for row in range(frame.rows)
             for col in range(frame.cols) if frame.attrs_at(row, col).reverse]
    assert found, frame._message("nothing on this screen is highlighted")
    rows = sorted({row for row, _ in found})
    assert len(rows) == 1, frame._message(
        "rows %r are highlighted; the Overview highlights the running leg and "
        "nothing else" % rows)
    columns = [col for _, col in found]
    return rows[0], min(columns), max(columns)


def painted_columns(frame, row):
    """Every column of `row` with something in it — columns, not characters."""
    return [col for col, cell in enumerate(frame.cells[row]) if cell.strip()]


def last_row_containing(frame, needle):
    """The *last* row carrying `needle`.

    A leg's verification step is drawn twice: once in the Active Leg pane's
    `Verification` list and once in the Active Runner pane, which is the one
    that carries the step's result. The Active Runner pane is the bottom one at
    every size, so the last match is its copy.
    """
    rows = [row for row in range(frame.rows) if needle in frame.lines[row]]
    assert rows, frame._message("no row carries %r" % needle)
    return rows[-1]


def test_a_double_width_leg_id_pads_its_highlight_to_the_pane_and_no_further(
        tmp_path):
    """ACC-OVER-003's highlight, on a leg id a terminal draws two cells wide.

    The Legs pane padded the running row with `str.ljust()`, which counts
    characters: nine ideographs are eighteen cells, so the row was padded out
    to the pane's width in *characters* and reached it in twice as many cells.
    `Canvas.write()` cut the row back, and the running leg's row — the one
    thing on the Overview that says where the relay is — ended in an ellipsis
    marking the truncation of its own blank padding.
    """
    wide = frame_of(cells_relay(tmp_path / "wide", CJK_ID, CJK_STEP), size=WIDE)
    plain = frame_of(cells_relay(tmp_path / "plain", PLAIN_ID, PLAIN_STEP),
                     size=WIDE)
    assert wide.contains(CJK_ID), wide._message(
        "the double-width leg id was not drawn at all — this proves nothing")

    row, first, last = highlight_span(wide)
    assert (row, first, last) == highlight_span(plain), wide._message(
        "the highlight covers %r on a relay whose leg id is eighteen cells "
        "wide and %r on one whose id is the same eighteen cells of ASCII"
        % ((row, first, last), highlight_span(plain)))
    assert ELLIPSIS not in wide.lines[row], wide._message(
        "the highlighted row was cut: the padding was measured in characters "
        "and spent in cells, so the row ran past the pane and came back with "
        "a mark on it")
    assert last == wide.cols - 2, wide._message(
        "the highlight ends at column %d; the pane runs to the column before "
        "the one the chrome reserves, which is %d"
        % (last, wide.cols - 2))
    wide.assert_within_width()


@pytest.mark.parametrize("size", [WIDE, (48, 60)])
def test_a_double_width_verification_step_keeps_its_result_on_the_row(
        size, tmp_path):
    """ACC-OVER-005: the step, and its last known result beside it.

    `_step_row` spends the row on the lead, the step and the result and puts
    what is left between the last two. Measured with `len()` the step is
    charged half what it costs, so the gap is written twice as wide as the row
    has room for — and what `Canvas.write()` then cuts off the end is the
    result, which is the one thing the row exists to report.
    """
    wide = frame_of(cells_relay(tmp_path / "wide", CJK_ID, CJK_STEP), size=size)
    plain = frame_of(cells_relay(tmp_path / "plain", PLAIN_ID, PLAIN_STEP),
                     size=size)
    wide_row = last_row_containing(wide, CELL_CHECK)
    plain_row = last_row_containing(plain, CELL_CHECK)
    assert wide.lines[wide_row].rstrip().endswith(CELL_RESULT), wide._message(
        "the Active Runner's step row reads %r — the result was pushed off "
        "the end of a row measured in characters"
        % wide.lines[wide_row].rstrip())
    assert (painted_columns(wide, wide_row)[-1]
            == painted_columns(plain, plain_row)[-1]), wide._message(
        "the step row ends at column %d against %d for the same row in cells "
        "of ASCII" % (painted_columns(wide, wide_row)[-1],
                      painted_columns(plain, plain_row)[-1]))
    assert wide_row == plain_row, wide._message(
        "double-width prose moved the Active Runner pane's rows")
    wide.assert_within_width()


def test_a_step_too_wide_for_the_pane_is_cut_and_still_reports_its_result(
        tmp_path):
    """The cut is the step's, never the result's.

    A step of a hundred and twenty cells cannot fit any pane here, so the row
    has to be cut — and `_step_row` reserves the result before it spends the
    row on the step, so what carries the mark is the step.
    """
    frame = frame_of(cells_relay(tmp_path / "long", CJK_ID, LONG_CJK_STEP),
                     size=WIDE)
    row = last_row_containing(frame, CELL_CHECK)
    line = frame.lines[row].rstrip()
    assert line.endswith(CELL_RESULT), frame._message(
        "the cut took the result: %r" % line)
    assert ELLIPSIS in line, frame._message(
        "a step of a hundred and twenty cells was drawn with no mark saying "
        "it had been cut: %r" % line)
    frame.assert_within_width()


#: Every size the Overview is swept at with double-width prose. `session()`
#: waits on `q Quit`, which the keybar still draws at five rows and twenty
#: columns; below that the wait has nothing to land on and `tests/test_chrome.py`
#: makes the same claim through a bare `TerminalSession`.
CELLS_SIZES = [WIDE, STANDARD, (12, 40), (8, 30), (5, 20)]


@pytest.mark.parametrize("size", CELLS_SIZES)
def test_double_width_prose_degrades_without_reaching_the_reserved_column(
        size, tmp_path):
    """Degrade, not crash — with every rectangle measured in cells.

    The relay's goal, leg id and verification step are all drawn two columns
    per character, so every width computation in this module is spending twice
    what `len()` would have said. The claim is the strict one: nothing wrapped,
    and the column the chrome reserves is empty, which is what lets every other
    frame test in the repository be certified rather than waved through.
    """
    relay = cells_relay(tmp_path / "small", CJK_ID, LONG_CJK_STEP,
                        goal="日本語で書かれた目標" * 6)
    term = session(relay, size=size)
    try:
        frame = repaint(term)
    finally:
        term.close()
    frame.assert_finished()
    assert frame.paint_end == "synchronised", frame._message(
        "captured on %r — a repaint this program did not vouch for"
        % frame.paint_end)
    assert "Traceback" not in frame.text, frame._message("the TUI raised")
    assert [row[frame.cols - 1] for row in frame.cells] == [" "] * frame.rows, (
        frame._message("something reached the column the chrome reserves"))
    frame.assert_within_width()


def test_a_step_the_view_cut_itself_says_so_under_a_locale_without_the_mark(
        tmp_path):
    """`LC_ALL=C` is the only screen that separates the two cuts.

    Under UTF-8 a cut made by `Canvas.write()` and a cut made by `_step_row`
    both end in `…` and no assertion can tell them apart. Under a locale that
    cannot encode it, curses drops the canvas's default mark to a *blank* —
    a silent truncation wearing a mark's clothes — while the theme's degrades
    to `...`. The row is the view's own cut, so it has to carry the theme's.
    """
    env = {key: value for key, value in os.environ.items()
           if key not in ("LC_ALL", "LC_CTYPE", "LANG")}
    env["LC_ALL"] = "C"
    relay = cells_relay(tmp_path / "ascii-mark", "plain-leg",
                        CELL_CHECK + " " + "y" * 400)
    term = session(relay, size=WIDE, env=env)
    try:
        frame = repaint(term)
    finally:
        term.close()
    row = last_row_containing(frame, CELL_CHECK)
    line = frame.lines[row].rstrip()
    assert line.endswith(CELL_RESULT), frame._message(
        "the cut took the result rather than the step: %r" % line)
    assert "..." in line, frame._message(
        "the step was cut with no mark this locale can draw: %r" % line)
    assert UTF8_ENV["LC_ALL"] != "C", (
        "the UTF-8 environment this file's other tests run under is itself C, "
        "so this test says nothing about the difference between the two")
    frame.assert_within_width()


#: The glyphs this module measures, spelled here rather than read from
#: `theme.GLYPHS`. The child runs under a UTF-8 locale, so these are what
#: reaches the screen.
PANE_GLYPHS = ("✓", "●", "○", "✗", "−", "·")

#: Every word the Active Runner pane can put beside a verification step. The
#: first four are what the model normalises a check's status to (ACC-DATA-004);
#: the last is this view's own literal for a step nothing has judged.
STEP_RESULTS = ("passed", "failed", "blocked", "pending", "no result recorded")


def test_the_two_measures_this_pane_cannot_tell_apart_are_one_cell_wide(
        tmp_path):
    """Where `len()` and `chrome.cell_width()` provably agree, and why.

    Two of this module's widths are measured over text that cannot be
    double-width, so swapping the measure there is a mutation no frame can
    fail on. They are equivalent — and this is the check that keeps them
    equivalent rather than an argument that they are:

    * **the glyphs come from `theme.GLYPHS`, which is data.** A table that grew
      a two-cell mark would make the two measures differ, and the row a step or
      a boundary is drawn on would be one cell longer than the pane. That is a
      failure here rather than a truncation on a screen.
    * **the word beside a verification step is a check's status**, and the
      model normalises every spelling a coach can write to one of four ASCII
      words. Asserted against a relay whose `state.json` says `済んだ判定`,
      which is exactly the input that would otherwise reach `_step_row` as
      prose.
    """
    for glyph in PANE_GLYPHS:
        assert display_width(glyph) == len(glyph) == 1, (
            "%r is %d cells and %d characters, so the rows this module spaces "
            "from it are measured two different ways"
            % (glyph, display_width(glyph), len(glyph)))
    for word in STEP_RESULTS:
        assert display_width(word) == len(word), (
            "%r is not one cell per character" % word)

    directory = tmp_path / "cjk-status"
    (directory / "batons").mkdir(parents=True, exist_ok=True)
    (directory / "legs.json").write_text(json.dumps({
        "relay": "cells",
        "stages": [{"id": "S1", "name": "Only stage", "legs": ["leg"]}],
        "legs": [{"id": "leg", "stage": "S1", "goal": "a goal",
                  "fulfills": [CELL_CHECK], "verification": [CJK_STEP],
                  "status": "running"}]}))
    (directory / "state.json").write_text(json.dumps({
        "relay": "cells", "phase": "running", "currentStage": "S1",
        "currentLeg": "leg",
        "checks": {CELL_CHECK: {"status": "済んだ判定", "stage": "S1",
                                "round": 1}}}))
    statuses = {check.get("status")
                for check in relay_model.build(str(directory))["checks"]}
    assert statuses <= set(STEP_RESULTS), (
        "the model let %r through to the view, so the word this pane measures "
        "is coach prose after all and `len()` is not safe on it"
        % sorted(statuses - set(STEP_RESULTS)))


#: A boundary of one-character words, so `wrap()` fills each line to the last
#: cell it is given rather than stopping short at a word break. Two cells of
#: bullet in front of a line wrapped to the *pane's* width instead of to what
#: is left of it is two cells past the pane, and the difference between the two
#: is invisible on prose whose words happen not to land on the edge.
BOUNDARY_MARK = "START"
BOUNDARY = BOUNDARY_MARK + " " + " ".join("abcdefghij"[index % 10]
                                          for index in range(150))

#: 80 columns stacks the Overview into one column, so a screen row belongs to
#: exactly one pane and can be measured whole.
STACKED = (48, 80)


def boundary_relay(directory):
    directory = Path(directory)
    (directory / "batons").mkdir(parents=True, exist_ok=True)
    (directory / "legs.json").write_text(json.dumps({
        "relay": "cells",
        "stages": [{"id": "S1", "name": "Only stage", "legs": ["leg"]}],
        "legs": [{"id": "leg", "stage": "S1", "goal": "short goal",
                  "status": "running", "fulfills": [],
                  "boundaries": [BOUNDARY]}]}))
    (directory / "state.json").write_text(json.dumps({
        "relay": "cells", "phase": "running", "currentStage": "S1",
        "currentLeg": "leg", "checks": {}}))
    (directory / "dashboard.json").write_text(json.dumps(
        {"title": "Cells relay", "path": str(directory)}))
    return directory


def test_a_wrapped_boundary_stays_inside_the_pane_and_under_its_own_bullet(
        tmp_path):
    """ACC-OVER-001's list, and the two cells the bullet costs it.

    The bullet's width is subtracted from the line and spent again as the
    indent under it, so the row is the pane's width exactly — on the bullet's
    row and on every row that continues it. Wrap the line to the whole pane and
    each row is two cells too long; indent a continuation by anything else and
    the sentence steps sideways halfway through. `Pane` clips either one back,
    so what is left on the screen is a mark on a row that had nothing cut from
    it, and a list that no longer reads as a list.
    """
    frame = frame_of(boundary_relay(tmp_path / "boundary"), size=STACKED)
    head = frame.find("Boundaries")
    assert head is not None, frame._message("the pane drew no Boundaries list")
    rows = []
    for row in range(head + 1, frame.rows):
        if not frame.lines[row].strip() or set(frame.lines[row].strip()) == {"─"}:
            break
        rows.append(row)
    assert len(rows) >= 3, frame._message(
        "the boundary occupies %d rows; it has to wrap at least twice for "
        "this to say anything" % len(rows))

    for row in rows:
        assert ELLIPSIS not in frame.lines[row], frame._message(
            "row %d carries a mark saying it was cut, on a boundary that was "
            "wrapped to fit: %r" % (row, frame.lines[row]))

    text_at = frame.lines[rows[0]].index(BOUNDARY_MARK)
    for row in rows[1:]:
        assert painted_columns(frame, row)[0] == text_at, frame._message(
            "row %d continues the boundary from column %d; the sentence "
            "starts at column %d on the row above"
            % (row, painted_columns(frame, row)[0], text_at))
    frame.assert_within_width()

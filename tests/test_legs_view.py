"""Frame tests for the Legs view (ACC-LEGS-001..004).

One region per check in `.relay/contract.md`. Every visual claim is asserted
against a frame captured from a real curses process under a pty
(`tests/frame.py`); nothing here inspects the program's internals, and no
column position is known to this file — where the three columns start is
*measured off the captured frame* by `Table`, so a layout change moves the
assertions rather than breaking them.

Four rules this file follows, each paid for by an earlier leg:

* **Figures come from the fixture's own files at assert time.** `plan()`,
  `leg_counts()` and `leg_states()` parse `legs.json` here rather than asking
  `relay_model.build()`, so the view is checked against the relay and not
  against a model that could miscount in the same direction. Only the *status
  vocabulary* is shared (`normalise_status`, `kind_of`): "what does `done`
  mean" is one decision and not two.
* **Every expectation is spelled out here, never read back out of
  `relay_control.legs` or `relay_control.theme`.** The word a status cell
  should carry, the glyph in front of it, the separator in the filter row and
  the shape of a `Stage/ID` cell are all written down below. A test that asks
  the module what it drew and then agrees with it cannot fail when the module
  changes, and that shape has already survived 18 mutations elsewhere in this
  relay.
* **The harness comes from `tests/test_chrome.py`**, which is the settled way
  to drive this TUI. Imported rather than copied — two copies of `_utf8_env()`
  is two places for a locale bug to hide.
* **`assert_within_width()` is used in its strict form**, and never on its own.
  A pass there means "nothing wrapped"; it is not evidence that nothing was
  clipped, because ncurses clips in software and an exact fit is byte-identical
  to a truncation. ACC-LEGS-003 is asserted on what the row actually *says* at
  the column boundary — the ellipsis — with the width helper beside it.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frame import display_width  # noqa: E402

from test_chrome import (  # noqa: E402
    FIXTURES, STANDARD, WIDE, row_of, session,
)

import relay_model  # noqa: E402

AGENT_SERVICE = FIXTURES / "agent-service"

#: Text only the Legs view paints, so `send("F", expect=...)` waits on a screen
#: the repaint introduced rather than on one that was already there.
LEGS_NEEDLE = "Stage/ID"

#: The five status glyphs, spelled here rather than read from `theme.GLYPHS`.
#: The child runs under a UTF-8 locale (`test_chrome.UTF8_ENV`), so these are
#: the shapes that reach the screen.
GLYPHS = {
    "completed": "✓",
    "running": "●",
    "pending": "○",
    "cancelled": "−",
    "blocked": "✗",
}

#: What the Status column must call each of the four display states — the same
#: five words the filter row uses, so a reader can match a row to a filter
#: without translating. `In Progress` is Mission Control's word for `running`.
STATE_WORDS = {
    "completed": "Completed",
    "running": "In Progress",
    "pending": "Pending",
    "cancelled": "Cancelled",
}

#: The filter row, in order, and the state each entry selects.
FILTERS = (("All", None), ("Pending", "pending"), ("In Progress", "running"),
           ("Completed", "completed"), ("Cancelled", "cancelled"))

ELLIPSIS = "…"


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------


def open_legs(term, expect=LEGS_NEEDLE):
    """The Legs view, off a frame the program stated was whole.

    The needle is text the repaint introduces; what makes the frame evidence
    is the closed DEC 2026 bracket, whose baseline is taken after the keys were
    read — so the assertion below, and not the needle, is the statement that a
    repaint happened.
    """
    frame = term.send("F", expect=expect)
    assert frame.paint_end == "synchronised", frame._message(
        "the Legs view was captured on %r, which is not proof that the "
        "repaint finished" % frame.paint_end)
    return frame


def legs_frame(relay_dir, size=WIDE, expect=LEGS_NEEDLE, **kwargs):
    """One finished frame of the Legs view against `relay_dir`, at `size`."""
    term = session(relay_dir, size=size, **kwargs)
    try:
        return open_legs(term, expect=expect)
    finally:
        term.close()


def synthetic_relay(directory, legs, relay="synthetic"):
    """A relay directory whose `legs.json` says exactly what `legs` says.

    `legs` is `[(id, status)]` — the *coach's* word for the status, written
    into the file verbatim, which is the input ACC-LEGS-004 is about.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "legs.json").write_text(json.dumps({
        "relay": relay,
        "stages": [{"id": "S1", "name": "Only stage",
                    "legs": [leg_id for leg_id, _ in legs]}],
        "legs": [{"id": leg_id, "stage": "S1", "status": status,
                  "goal": "a leg", "fulfills": []}
                 for leg_id, status in legs],
    }))
    return directory


# --------------------------------------------------------------------------
# figures, read from the fixture at assert time
# --------------------------------------------------------------------------


def cell_column(frame, row, needle):
    """The grid column `needle` starts at on `row`, measured off the text.

    Not `test_chrome.column_of()`: that answers with the start of the
    *attribute run* the text sits in, and a table's three column labels are
    drawn alike, so all three would answer 0. What is being measured here is
    the grid, which is a fact about the characters.
    """
    index = frame.raw_lines[row].find(needle)
    if index < 0:
        raise AssertionError(frame._message(
            "row %d does not contain %r" % (row, needle)))
    return index


def plan(relay_dir):
    """Every leg record in the fixture's own `legs.json`."""
    path = Path(relay_dir) / "legs.json"
    data = json.loads(path.read_text())
    return [leg for leg in data.get("legs", []) if isinstance(leg, dict)]


def leg_counts(relay_dir):
    """`{filter key: count}` counted out of `legs.json`, not out of the model."""
    legs = plan(relay_dir)
    counts = {"all": len(legs)}
    for state in ("pending", "running", "completed", "cancelled"):
        counts[state] = sum(
            1 for leg in legs
            if relay_model.normalise_status(leg.get("status")) == state)
    return counts


def leg_kinds(relay_dir):
    """`{leg id: impl|fix|judge}` from the fixture."""
    return {leg.get("id"): relay_model.kind_of(leg) for leg in plan(relay_dir)}


def leg_states(relay_dir):
    """`{leg id: (display state, the coach's own word)}` from the fixture."""
    out = {}
    for leg in plan(relay_dir):
        raw = leg.get("status")
        out[leg.get("id")] = (relay_model.normalise_status(raw),
                              raw if isinstance(raw, str) else None)
    return out


def leg_fulfills(relay_dir):
    """`{leg id: [check id]}` from the fixture."""
    return {leg.get("id"): [c for c in (leg.get("fulfills") or [])
                            if isinstance(c, str)]
            for leg in plan(relay_dir)}


# --------------------------------------------------------------------------
# reading the table off a captured frame
# --------------------------------------------------------------------------


class Table:
    """The Legs table as it was drawn, located by measuring the frame.

    The head row is the one carrying `Stage/ID`; the three columns begin where
    their labels begin. Nothing here knows a width the program computed, which
    is the point: what is certified is the grid a supervisor reads.
    """

    def __init__(self, frame):
        self.frame = frame
        self.head = row_of(frame, "Stage/ID")
        # The Status column keeps its glyph after it has given up its label,
        # and the glyph is the first thing on the row.
        self.status = (cell_column(frame, self.head, "Status")
                       if "Status" in frame.raw_lines[self.head] else 0)
        self.stage = cell_column(frame, self.head, "Stage/ID")
        self.fulfills = (cell_column(frame, self.head, "Fulfills")
                         if "Fulfills" in frame.raw_lines[self.head] else None)
        self.rows = {}
        for index in range(self.head + 1, frame.rows - 1):
            cells = self._cells(frame.raw_lines[index])
            if cells[1]:
                self.rows[index] = cells

    def _cells(self, line):
        end = self.frame.cols if self.fulfills is None else self.fulfills
        return (line[self.status:self.stage].rstrip(),
                line[self.stage:end].rstrip(),
                "" if self.fulfills is None else line[self.fulfills:].rstrip())

    @property
    def ids(self):
        """The leg ids drawn, top to bottom."""
        return [leg_id(cells[1]) for _, cells in sorted(self.rows.items())]

    def cells_for(self, wanted):
        """`(status, stage/id, fulfills)` for one leg, matched on the id itself.

        Matched on the parsed cell rather than with `frame.find()`, because
        `code-judge-S3` is a substring of `code-judge-S3-r2` and the first row
        containing the text is not necessarily the row about that leg.
        """
        for _, cells in sorted(self.rows.items()):
            if leg_id(cells[1]) == wanted:
                return cells
        raise AssertionError(self.frame._message(
            "no row of the Legs table is about %r" % wanted))

    def row_for(self, wanted):
        for index, cells in sorted(self.rows.items()):
            if leg_id(cells[1]) == wanted:
                return index
        raise AssertionError(self.frame._message(
            "no row of the Legs table is about %r" % wanted))


def leg_id(stage_cell):
    """The leg id out of a `S2/register-route-credential-guard  fix` cell."""
    reference = stage_cell.split()[0] if stage_cell.split() else ""
    _, _, identifier = reference.partition("/")
    return identifier or reference


def kind_marker(stage_cell):
    """The kind marker out of a `Stage/ID` cell — `""` for an impl leg."""
    parts = stage_cell.split()
    return parts[1] if len(parts) > 1 else ""


# --------------------------------------------------------------------------
# ACC-LEGS-001 — header, filter row, columns
# --------------------------------------------------------------------------


def test_the_view_names_itself_and_counts_its_legs():
    frame = legs_frame(AGENT_SERVICE)
    total = leg_counts(AGENT_SERVICE)["all"]
    frame.assert_contains("Legs (%d)" % total)


def test_the_filter_row_names_every_filter_with_its_count():
    frame = legs_frame(AGENT_SERVICE)
    counts = leg_counts(AGENT_SERVICE)
    row = row_of(frame, "All (")
    columns = []
    for label, state in FILTERS:
        entry = "%s (%d)" % (label, counts["all" if state is None else state])
        assert entry in frame.raw_lines[row], frame._message(
            "the filter row does not carry %r" % entry)
        columns.append(cell_column(frame, row, entry))
    assert columns == sorted(columns), frame._message(
        "the five filters are not in the order the contract names them")
    whole = " | ".join("%s (%d)" % (label, counts["all" if s is None else s])
                       for label, s in FILTERS)
    assert frame.raw_lines[row].rstrip() == whole, frame._message(
        "the filter row is not `%s`" % whole)


def test_the_active_filter_is_told_apart_from_the_ones_beside_it():
    frame = legs_frame(AGENT_SERVICE)
    counts = leg_counts(AGENT_SERVICE)
    frame.assert_attrs_differ("All (%d)" % counts["all"],
                              "Completed (%d)" % counts["completed"])


def test_cycling_the_filter_moves_the_highlight_and_the_rows_with_it():
    """`T` is bound here so the filter row can be seen working at all.

    The states each frame must contain are read from `legs.json`, so a filter
    that selected the wrong legs would have to miscount in the same direction
    as the file to pass.
    """
    counts = leg_counts(AGENT_SERVICE)
    states = leg_states(AGENT_SERVICE)
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        frame = open_legs(term)
        for label, wanted in FILTERS[1:] + FILTERS[:1]:
            key = "all" if wanted is None else wanted
            frame = term.send("T", expect="Legs (%d)" % counts[key])
            entry = "%s (%d)" % (label, counts[key])
            other = next("%s (%d)" % (name, counts["all" if s is None else s])
                         for name, s in FILTERS if name != label)
            row = row_of(frame, "All (")
            assert frame.attrs_for(entry, row=row) != \
                frame.attrs_for(other, row=row), frame._message(
                    "%r is the active filter and is drawn exactly like %r"
                    % (entry, other))
            if not counts[key]:
                # An empty filter is not an empty pane, and it is never a
                # range the pane did not draw.
                frame.assert_contains("no leg is %s" % label.lower())
                assert "none" in frame.raw_lines[row_of(frame, "Legs (")], \
                    frame._message("an empty filter's meta is not `none`")
                assert not re.search(r"\d+-\d+ of \d+", frame.text), \
                    frame._message("an empty filter claims a range it did "
                                   "not draw")
                continue
            drawn = Table(frame).ids
            assert len(drawn) == counts[key], frame._message(
                "%s selects %d legs and the view drew %d"
                % (label, counts[key], len(drawn)))
            if wanted is not None:
                assert all(states[leg][0] == wanted for leg in drawn), \
                    frame._message("a leg of another state is under %s" % label)
    finally:
        term.close()


def test_the_view_heads_the_three_columns_the_contract_names():
    frame = legs_frame(AGENT_SERVICE)
    head = row_of(frame, "Stage/ID")
    columns = [cell_column(frame, head, label)
               for label in ("Status", "Stage/ID", "Fulfills")]
    assert columns == sorted(columns), frame._message(
        "the columns are not in the order Status, Stage/ID, Fulfills")


def test_every_cell_sits_under_the_column_that_heads_it():
    """The head is a label for the cells under it, so it has to line up."""
    frame = legs_frame(AGENT_SERVICE)
    table = Table(frame)
    for index, (status, stage, fulfills) in sorted(table.rows.items()):
        line = frame.raw_lines[index]
        assert line[:table.status].strip() == "", frame._message(
            "row %d draws something to the left of the Status column" % index)
        assert line[table.stage - 1] == " ", frame._message(
            "row %d runs the Status cell into the Stage/ID column" % index)
        assert status and stage, frame._message(
            "row %d has an empty Status or Stage/ID cell" % index)
        assert not stage.startswith(" "), frame._message(
            "row %d starts its Stage/ID cell late" % index)
        if table.fulfills is not None:
            assert line[table.fulfills - 1] == " ", frame._message(
                "row %d runs the Stage/ID cell into the Fulfills column" % index)
            assert not fulfills.startswith(" "), frame._message(
                "row %d starts its Fulfills cell late" % index)


def fields(line):
    """`[(column, text)]` — the cells on one row, split on two or more spaces.

    Two spaces is what separates one column from the next, so this recovers the
    grid from the characters without being told where any column is.
    """
    found, column = [], 0
    for part in re.split(r"( {2,})", line.rstrip()):
        if part and not part.startswith("  "):
            found.append((column, part))
        column += len(part)
    return found


@pytest.mark.parametrize("size", [WIDE, STANDARD, (24, 60), (24, 54),
                                  (12, 40), (10, 30), (10, 20)])
def test_every_column_drawn_carries_its_own_label_whole(size):
    """A column narrower than its heading is not a column, it is noise.

    Either the heading is cut to something that labels nothing (`Fulfi…`, `F`)
    or it is dropped and the cells stand under nothing saying what they are. So
    a column that cannot carry its label whole is not drawn at all, and the two
    that can go — `Fulfills`, then `Stage/ID` — give way in that order. The
    Status column is the one exception: below a certain width it is the status
    glyph alone, which the keybar's legend names, and its word is gone with the
    label. It is checked here as the *leftmost* field, which is why the test
    compares the rightmost.
    """
    frame = legs_frame(AGENT_SERVICE, size=size, expect="q Quit")
    order = ["Status", "Stage/ID", "Fulfills"]
    # The head row is the one under the filter row — located that way and not
    # by looking for `Stage/ID`, because a mutation that drops a label would
    # otherwise take this test with it.
    head = row_of(frame, "All (") + 1
    if head >= frame.rows - 1:
        return
    labels = [text for _, text in fields(frame.raw_lines[head])]
    if not any(label in labels for label in order):
        return                          # too short for a head row at all
    assert [label for label in order if label in labels] == labels, \
        frame._message("the column heads at %dx%d are %r"
                       % (size[1], size[0], labels))
    body = [index for index in range(head + 1, frame.rows - 1)
            if frame.raw_lines[index].strip()]
    assert body, frame._message("no rows to check the heads against")
    rightmost = max(fields(frame.raw_lines[index])[-1][0] for index in body)
    heading = fields(frame.raw_lines[head])[-1][0]
    assert rightmost == heading, frame._message(
        "at %dx%d the last column of content starts at %d and the last "
        "column head at %d — something is drawn where nothing names it"
        % (size[1], size[0], rightmost, heading))


def test_the_stage_id_column_names_the_stage_as_well_as_the_leg():
    """`Stage/ID` is two facts, and the plan's shape is the first of them.

    Four legs in the fixture are called `code-judge-S<n>`; without the stage in
    front of it a reader has the plan's order and nothing that groups it.
    """
    frame = legs_frame(AGENT_SERVICE, size=WIDE)
    table = Table(frame)
    stages = {leg.get("id"): leg.get("stage") for leg in plan(AGENT_SERVICE)}
    for identifier, stage in sorted(stages.items()):
        cell = table.cells_for(identifier)[1].split()[0]
        assert cell == "%s/%s" % (stage, identifier), frame._message(
            "%s is in stage %s and its Stage/ID cell reads %r"
            % (identifier, stage, cell))


def test_the_running_leg_is_told_apart_from_the_rest_of_the_plan():
    frame = legs_frame(AGENT_SERVICE, size=WIDE)
    table = Table(frame)
    states = leg_states(AGENT_SERVICE)
    live = next(leg for leg, (state, _) in sorted(states.items())
                if state == "running")
    done = next(leg for leg, (state, _) in sorted(states.items())
                if state == "completed")
    running = frame.attrs_for(live, row=table.row_for(live))
    finished = frame.attrs_for(done, row=table.row_for(done))
    assert running != finished, frame._message(
        "the leg a runner is on and a leg that landed are both drawn %s"
        % running.describe())


def test_the_view_lists_every_leg_the_fixture_plans():
    """At 160x48 the whole plan fits, so nothing may be missing from it."""
    frame = legs_frame(AGENT_SERVICE, size=WIDE)
    table = Table(frame)
    assert sorted(table.ids) == sorted(leg_kinds(AGENT_SERVICE)), frame._message(
        "the view does not draw one row per planned leg")


def test_a_relay_with_no_legs_says_so_in_words(tmp_path):
    """Never an empty box: a pane with a title and a blank body reads as one
    that crashed, and a filter row of five zeroes is filler, not information."""
    frame = legs_frame(synthetic_relay(tmp_path / "no-legs", []),
                       expect="no legs planned yet")
    frame.assert_contains("no legs planned yet")
    frame.assert_not_contains("All (0)")


# --------------------------------------------------------------------------
# ACC-LEGS-002 — judge legs and fix legs are marked
# --------------------------------------------------------------------------


def test_a_judge_leg_and_a_fix_leg_each_carry_their_kind():
    frame = legs_frame(AGENT_SERVICE)
    table = Table(frame)
    kinds = leg_kinds(AGENT_SERVICE)
    assert kinds["code-judge-S2"] == "judge"
    assert kind_marker(table.cells_for("code-judge-S2")[1]) == "judge"
    a_fix = next(leg for leg, kind in sorted(kinds.items()) if kind == "fix")
    assert kind_marker(table.cells_for(a_fix)[1]) == "fix"


def test_every_leg_carries_its_kind_and_an_impl_leg_carries_none():
    """Swept over all 36, so a marker on the wrong row is a failure too."""
    frame = legs_frame(AGENT_SERVICE, size=WIDE)
    table = Table(frame)
    kinds = leg_kinds(AGENT_SERVICE)
    for identifier, kind in sorted(kinds.items()):
        marker = kind_marker(table.cells_for(identifier)[1])
        expected = "" if kind == "impl" else kind
        assert marker == expected, frame._message(
            "%s is a %s leg and its Stage/ID cell reads %r"
            % (identifier, kind, table.cells_for(identifier)[1]))


def test_a_tight_column_gives_up_the_id_before_it_gives_up_the_kind():
    """At 40 columns `S1/behaviour-judge-S1 judge` does not fit its column.

    What must give is the id — a leg id cut in the middle still names the leg
    to a reader who has the plan in front of them, and `judge` is one word that
    cannot be inferred from what is left of it.
    """
    frame = legs_frame(AGENT_SERVICE, size=(12, 40))
    table = Table(frame)
    kinds = leg_kinds(AGENT_SERVICE)
    references = {leg.get("id"): "%s/%s" % (leg.get("stage"), leg.get("id"))
                  for leg in plan(AGENT_SERVICE)}
    clipped = 0
    for _, cells in sorted(table.rows.items()):
        head = cells[1].split()[0].rstrip(ELLIPSIS)
        named = [leg for leg, ref in references.items() if ref.startswith(head)]
        if len(named) != 1 or kinds[named[0]] == "impl":
            continue
        assert kind_marker(cells[1]) == kinds[named[0]], frame._message(
            "%s is a %s leg and its cell reads %r at 40 columns"
            % (named[0], kinds[named[0]], cells[1]))
        clipped += cells[1].split()[0] != references[named[0]]
    assert clipped, frame._message(
        "no judge or fix leg had its id cut at 40 columns, so this case "
        "proves nothing")


def test_the_kind_marker_is_not_drawn_like_the_leg_id_beside_it():
    frame = legs_frame(AGENT_SERVICE)
    table = Table(frame)
    row = table.row_for("code-judge-S2")
    line = frame.raw_lines[row]
    marker = line.rindex("judge", 0, table.fulfills or frame.cols)
    identifier = line.index("code-judge-S2")
    assert frame.attrs_at(row, marker) != frame.attrs_at(row, identifier), \
        frame._attr_message(
            "the kind marker is drawn exactly like the leg id, so a judge leg "
            "is not visibly distinguished from an impl leg", row)


# --------------------------------------------------------------------------
# ACC-LEGS-003 — Fulfills truncates without breaking the grid
# --------------------------------------------------------------------------


def widest_fulfills(relay_dir):
    """`(leg id, the whole cell)` for the leg claiming the most checks."""
    cells = {leg: ", ".join(checks)
             for leg, checks in leg_fulfills(relay_dir).items() if checks}
    identifier = max(sorted(cells), key=lambda leg: len(cells[leg]))
    return identifier, cells[identifier]


def test_the_widest_fulfills_cell_truncates_with_an_ellipsis_at_eighty():
    frame = legs_frame(AGENT_SERVICE, size=STANDARD)
    identifier, whole = widest_fulfills(AGENT_SERVICE)
    table = Table(frame)
    cell = table.cells_for(identifier)[2]
    assert cell, frame._message("%s draws no Fulfills cell at all, so this "
                                "case proves nothing" % identifier)
    assert len(cell) < len(whole), frame._message(
        "%s claims %d cells of checks and the view drew all of them at 80 "
        "columns" % (identifier, len(whole)))
    assert cell.endswith(ELLIPSIS), frame._message(
        "the Fulfills cell for %s was cut to %r with no ellipsis to say so"
        % (identifier, cell))
    assert whole.startswith(cell[:-len(ELLIPSIS)]), frame._message(
        "the Fulfills cell for %s is not the head of what it fulfils" % identifier)


def test_no_row_is_wider_than_eighty_columns_and_none_wraps():
    frame = legs_frame(AGENT_SERVICE, size=STANDARD)
    # Strict: the chrome reserves the screen's last column so that a captured
    # frame carries no full-width row and this helper can certify it.
    frame.assert_within_width()


def test_every_leg_the_view_draws_at_eighty_is_exactly_one_line():
    frame = legs_frame(AGENT_SERVICE, size=STANDARD)
    table = Table(frame)
    drawn = table.ids
    assert drawn, frame._message("the view drew no leg rows at 80 columns")
    assert len(drawn) == len(set(drawn)), frame._message(
        "a leg is on two rows, so a row wrapped: %r" % drawn)
    rows = sorted(table.rows)
    assert rows == list(range(rows[0], rows[0] + len(rows))), frame._message(
        "the leg rows are not contiguous — something wrapped between them")


def test_a_leg_that_claims_no_check_gets_a_mark_and_not_a_blank():
    """A blank cell reads as a table that failed to draw; `0`, `none` and an
    em-dash each read as a value somebody measured. The convention is
    `runners-view`'s, one view over: one dim mark per empty cell."""
    frame = legs_frame(AGENT_SERVICE, size=WIDE)
    table = Table(frame)
    claims = leg_fulfills(AGENT_SERVICE)
    silent = [leg for leg, checks in sorted(claims.items()) if not checks]
    assert silent, "the fixture has no leg that claims nothing"
    marks = {table.cells_for(leg)[2] for leg in silent}
    assert len(marks) == 1, frame._message(
        "legs claiming no check draw %r — one absence, one spelling" % marks)
    mark = marks.pop()
    assert mark and len(mark) == 1 and not mark.isalnum(), frame._message(
        "a leg claiming no check draws %r, which is not one dim mark" % mark)
    frame.assert_attrs(mark, has="dim", row=table.row_for(silent[0]))


def test_the_table_is_laid_out_to_the_pane_and_not_one_cell_short():
    """The strict width helper cannot see a table laid out narrower than it may
    be — every row simply fits. The widest row reaching the pane's own last
    column is the other half of the claim."""
    frame = legs_frame(AGENT_SERVICE, size=STANDARD)
    table = Table(frame)
    # Measured over the table's own rows: the header and the status bar are
    # already full width, so a maximum over the whole frame would pass on a
    # table five cells narrow.
    widest = max(len(frame.raw_lines[index].rstrip()) for index in table.rows)
    # The chrome reserves the screen's last column for every view.
    assert widest == frame.cols - 1, frame._message(
        "the widest leg row is %d cells of a %d-column screen"
        % (widest, frame.cols))


def test_a_cut_cell_still_says_it_was_cut_under_a_locale_without_utf8():
    """`chrome.clip()`'s default `…` is dropped to a blank by curses under
    `LC_ALL=C` — a silent truncation wearing a mark's clothes. Every cell here
    is cut with the theme's ellipsis, which has an ASCII spelling."""
    import os

    from relay_control import theme as theme_tokens
    env = {k: v for k, v in os.environ.items()
           if k not in ("LC_ALL", "LC_CTYPE", "LANG")}
    env["LC_ALL"] = "C"
    ascii_ellipsis = theme_tokens.ASCII_GLYPHS["ellipsis"]
    frame = legs_frame(AGENT_SERVICE, size=STANDARD, env=env)
    identifier, whole = widest_fulfills(AGENT_SERVICE)
    cell = Table(frame).cells_for(identifier)[2]
    assert len(cell) < len(whole), frame._message(
        "nothing was cut, so this case proves nothing")
    assert cell.endswith(ascii_ellipsis), frame._message(
        "under LC_ALL=C the cut cell reads %r, with no mark to say it was cut"
        % cell)


def test_the_fulfills_column_never_runs_into_the_screen_edge():
    """A cell that filled the last column would take `assert_within_width()`
    away from every other test in the repository."""
    frame = legs_frame(AGENT_SERVICE, size=STANDARD)
    assert frame.full_width_rows() == [], frame._message(
        "rows reach the last column, which a terminal cannot tell from "
        "content truncated to fit")


# --------------------------------------------------------------------------
# ACC-LEGS-004 — a blocked leg does not read as merely queued
# --------------------------------------------------------------------------


def blocked_leg(relay_dir):
    """The fixture's leg whose coach-written status is `blocked`."""
    for identifier, (_, raw) in sorted(leg_states(relay_dir).items()):
        if (raw or "").strip().lower() == "blocked":
            return identifier
    raise AssertionError("%s has no leg whose raw status is 'blocked'"
                         % relay_dir)


def test_a_blocked_leg_says_blocked_and_not_pending():
    frame = legs_frame(AGENT_SERVICE, size=WIDE)
    identifier = blocked_leg(AGENT_SERVICE)
    assert identifier == "cutover-flip"
    status, _, _ = Table(frame).cells_for(identifier)
    assert status == "%s  blocked" % GLYPHS["pending"], frame._message(
        "%s is blocked and its Status cell reads %r" % (identifier, status))


def test_the_blocked_word_is_not_drawn_like_an_ordinary_pending_row():
    """Both rows are `pending` to the model, so the word carries the difference
    and the styling has to carry it too — a supervisor scans, and does not read
    thirty-six cells."""
    frame = legs_frame(AGENT_SERVICE, size=WIDE)
    table = Table(frame)
    states = leg_states(AGENT_SERVICE)
    queued = next(leg for leg, (state, raw) in sorted(states.items())
                  if state == "pending" and (raw or "").strip().lower() != "blocked")
    blocked = frame.attrs_for("blocked", row=table.row_for(blocked_leg(AGENT_SERVICE)))
    ordinary = frame.attrs_for(STATE_WORDS["pending"], row=table.row_for(queued))
    assert blocked != ordinary, frame._message(
        "a blocked leg and a queued one are both drawn %s" % blocked.describe())


def test_none_of_the_legs_the_coach_called_done_carries_an_extra_word():
    """The check exists because `rawStatus != status` is the wrong rule.

    27 of the fixture's 36 legs say `done`, which differs from `completed` and
    carries nothing the display state has not already said.
    """
    frame = legs_frame(AGENT_SERVICE, size=WIDE)
    table = Table(frame)
    states = leg_states(AGENT_SERVICE)
    said_done = [leg for leg, (_, raw) in sorted(states.items())
                 if (raw or "").strip().lower() == "done"]
    assert len(said_done) == leg_counts(AGENT_SERVICE)["completed"]
    for identifier in said_done:
        status, _, _ = table.cells_for(identifier)
        assert status == "%s  %s" % (GLYPHS["completed"],
                                     STATE_WORDS["completed"]), frame._message(
            "%s says `done`, which is what `completed` says — its Status cell "
            "reads %r" % (identifier, status))


def test_a_running_leg_whose_coach_word_is_running_says_only_in_progress():
    """`running` differs from `In Progress` as text and not as information."""
    frame = legs_frame(AGENT_SERVICE, size=WIDE)
    table = Table(frame)
    states = leg_states(AGENT_SERVICE)
    live = [leg for leg, (state, _) in sorted(states.items())
            if state == "running"]
    assert live, "the fixture has no running leg"
    for identifier in live:
        status, _, _ = table.cells_for(identifier)
        assert status == "%s  %s" % (GLYPHS["running"],
                                     STATE_WORDS["running"]), frame._message(
            "%s's Status cell reads %r" % (identifier, status))


QUIET_AND_LOUD = (
    # (leg id, what the coach wrote, what the Status cell must say)
    ("said-done", "done", STATE_WORDS["completed"]),
    ("said-shipped", "Shipped", STATE_WORDS["completed"]),
    ("said-wip", "wip", STATE_WORDS["running"]),
    ("said-in-progress", "in progress", STATE_WORDS["running"]),
    ("said-todo", "todo", STATE_WORDS["pending"]),
    ("said-skipped", "skipped", STATE_WORDS["cancelled"]),
    ("said-blocked", "blocked", "blocked"),
    ("said-waiting", "waiting", "waiting"),
    ("said-failed", "failed", "failed"),
    ("said-needs-a-human", "needs-a-human", "needs-a-human"),
)


def test_the_column_speaks_only_for_a_word_the_four_states_lose(tmp_path):
    """Ten coach words on one screen: six quiet, four that carry something.

    The quiet six are ordinary spellings of the state they map to — including
    `wip` and `in progress`, which differ from `In Progress` as text. The loud
    four are the two words that mean more than the state they mapped to and two
    the model had to fall back on, where every trace of the coach's word would
    otherwise be gone.
    """
    relay = synthetic_relay(tmp_path / "vocabulary",
                            [(leg, raw) for leg, raw, _ in QUIET_AND_LOUD])
    frame = legs_frame(relay, size=WIDE)
    table = Table(frame)
    for identifier, raw, expected in QUIET_AND_LOUD:
        status, _, _ = table.cells_for(identifier)
        word = status.split(None, 1)[1] if " " in status.strip() else ""
        assert word == expected, frame._message(
            "the coach wrote %r for %s and the Status cell says %r, not %r"
            % (raw, identifier, word, expected))


def test_a_coach_s_essay_cannot_take_the_grid_with_it(tmp_path):
    """`legs.json` is hand-written, and `status` is a string like any other.

    A two-hundred-character status would otherwise size the Status column to
    two hundred cells and leave the rest of the grid with nothing — untrusted
    prose deciding the layout. It is cut like any other cell, and the columns
    beside it stay where they were.
    """
    essay = "blocked " + "on a decision nobody has made yet " * 8
    relay = synthetic_relay(tmp_path / "essay", [("short", "done"),
                                                 ("long-winded", essay)])
    frame = legs_frame(relay, size=STANDARD)
    frame.assert_within_width()
    table = Table(frame)
    status = table.cells_for("long-winded")[0]
    assert len(status) < 40, frame._message(
        "one leg's status took %d cells of an 80-column screen" % len(status))
    assert status.endswith(ELLIPSIS), frame._message(
        "the status was cut to %r with nothing to say so" % status)
    assert table.cells_for("short")[1].startswith("S1/short"), frame._message(
        "the essay moved the Stage/ID column out from under its head")


# --------------------------------------------------------------------------
# degradation — the view is drawn, or it is dropped; it never crashes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("size", [WIDE, STANDARD, (12, 40), (8, 30), (6, 60),
                                  (5, 20), (3, 12)])
def test_the_view_degrades_at_every_size_it_is_given(size):
    """Below three rows there is no keybar and below two no view canvas at all.

    The keybar is the one needle that means "a screen was painted" at every
    size this program still draws one at, and the closed bracket is what makes
    the frame evidence rather than a guess.
    """
    term = session(AGENT_SERVICE, size=size)
    try:
        frame = term.send("F", expect="q Quit")
        frame.assert_finished()
        frame.assert_within_width()
        frame.assert_not_contains("Traceback")
        assert term.is_running, frame._message("the TUI exited instead")
        # A heading is never the last row drawn: column labels with no leg
        # under them point at nothing, and the row was the content's.
        head = frame.find("Stage/ID")
        if head is not None:
            below = [line for line in frame.lines[head + 1:frame.rows - 1]
                     if line.strip()]
            assert below, frame._message(
                "the column heads are the last thing drawn at %dx%d"
                % (size[1], size[0]))
    finally:
        term.close()


@pytest.mark.parametrize("size", [WIDE, STANDARD, (12, 40), (10, 30)])
def test_the_filter_row_keeps_the_active_filter_whole_and_on_screen(size):
    """Narrow enough and the row cannot hold five filters. What it must not do
    is cut one in half, or drop the one whose highlight says which is on."""
    frame = legs_frame(AGENT_SERVICE, size=size)
    counts = leg_counts(AGENT_SERVICE)
    whole = ["%s (%d)" % (label, counts["all" if s is None else s])
             for label, s in FILTERS]
    row = row_of(frame, "All (")
    frame.assert_contains(whole[0])
    for part in frame.raw_lines[row].strip().split(" | "):
        assert part in whole or part == ELLIPSIS, frame._message(
            "the filter row at %dx%d drew %r, which is neither a filter nor "
            "the mark that says one is hidden" % (size[1], size[0], part))


def test_what_the_view_hides_it_says_it_is_hiding():
    """`+N more` and the header's range are the same claim, twice.

    At 80x24 the 36-leg plan does not fit. Every leg is then either drawn or
    counted in the marker — a marker that omitted the difference would be the
    same lie as a range the pane did not draw.
    """
    frame = legs_frame(AGENT_SERVICE, size=STANDARD)
    total = leg_counts(AGENT_SERVICE)["all"]
    drawn = Table(frame).ids
    assert len(drawn) < total, frame._message(
        "the whole plan fits at 80x24, so this case proves nothing")
    marker = re.search(r"\+(\d+) more", frame.text)
    assert marker, frame._message(
        "the view hid %d legs and drew no marker to say so"
        % (total - len(drawn)))
    hidden = int(marker.group(1))
    assert len(drawn) + hidden == total, frame._message(
        "%d legs drawn and %d counted as hidden, out of %d"
        % (len(drawn), hidden, total))
    frame.assert_contains("1-%d of %d" % (len(drawn), total))


def test_the_view_consumes_its_own_key_and_no_other():
    """A view that swallowed a key it does not own would take `q` with it.

    Quitting is not a view's decision, and neither is `Esc`.
    """
    term = session(AGENT_SERVICE, size=WIDE)
    try:
        open_legs(term)
        term.send("<Esc>", expect="Active Runner")      # back to the Overview
        open_legs(term)
        term.send("q")
        assert term.wait(timeout=5) == 0, "`q` did not quit from the Legs view"
    finally:
        term.close()


# --------------------------------------------------------------------------
# Width is measured in cells, not in characters
#
# Every one of this table's three widths is measured over untrusted prose — a
# leg id, a stage, a coach's status word — and a terminal draws a CJK character
# in two columns. `len()` therefore sized `Stage/ID` at half what its widest id
# needs, and `str.ljust()` then padded a clipped cell out to that many
# *characters* rather than cells.
#
# Neither is visible to a width assertion: `Pane` clips every write to its own
# rectangle, so a row built twice as wide as the pane reaches the screen
# truncated rather than overrunning it. What is visible is what the row says —
# an id cut where the column had room for it — and where the grid lands, which
# is what the "content starts under its own head" assertion below measures.
# --------------------------------------------------------------------------

#: Nine ideographs: eighteen cells, nine characters.
CJK_LEG = "日本語で書かれた脚"
CJK_LEG_TWO = "二番目の日本語の脚"

#: The reference the `Stage/ID` column builds out of it.
CJK_REFERENCE = "S1/" + CJK_LEG


def cells_relay(directory, legs, relay="cells"):
    """A relay whose leg ids are whatever is passed, each fulfilling a check.

    `fulfills` is populated so that the rightmost column has content of its
    own: a column of absence markers would still line up with its heading
    whatever the arithmetic did, and would prove nothing about the grid.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "legs.json").write_text(json.dumps({
        "relay": relay,
        "stages": [{"id": "S1", "name": "Only stage",
                    "legs": [leg_id for leg_id, _ in legs]}],
        "legs": [{"id": leg_id, "stage": "S1", "status": status,
                  "goal": "a leg", "fulfills": ["ACC-CELL-%03d" % index]}
                 for index, (leg_id, status) in enumerate(legs)],
    }))
    return directory


CELLS_LEGS = ((CJK_LEG, "running"), (CJK_LEG_TWO, "pending"),
              ("plain-leg", "pending"))


def column_cells(frame, row):
    """One screen row as a list of cells, so an index *is* a column.

    `frame.lines` joins the grid and the cell after a double-width character is
    the empty string in it, so a string index into a line carrying CJK is not
    the column the terminal drew at.
    """
    return list(frame.cells[row])


def head_columns(frame, head):
    """`{label: column}` for the table's heads.

    The head row is ASCII by construction — the three labels are literals in
    this file — so a string index into it *is* a column, which is exactly what
    stops being true one row further down.
    """
    line = frame.lines[head]
    return {label: line.index(label) for label in ("Status", "Stage/ID",
                                                   "Fulfills")}


def test_a_double_width_leg_id_is_drawn_whole_where_the_column_has_room(
        tmp_path):
    """`Stage/ID` is as wide as its widest id, measured in cells.

    Sized with `len()` the column was nine cells wide for an id that needs
    eighteen, so a wide terminal with nothing else on it clipped an id it had a
    hundred and forty columns to spare for — and marked the cut, which reads as
    a relay whose leg ids are too long rather than as a renderer that measured
    them wrong.
    """
    frame = legs_frame(cells_relay(tmp_path / "wide", CELLS_LEGS), size=WIDE)
    assert frame.contains(CJK_REFERENCE), frame._message(
        "the Stage/ID column cut %r, which fits the terminal twice over"
        % CJK_REFERENCE)
    row = frame.find(CJK_REFERENCE)
    assert ELLIPSIS not in frame.lines[row], frame._message(
        "the row carries a mark saying something was cut: %r"
        % frame.lines[row])
    frame.assert_within_width()


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_every_column_of_a_double_width_row_starts_under_its_own_head(
        size, tmp_path):
    """The grid is one grid, on every row, whatever the row is spelled in.

    The heads are spaced from the layout and the cells are padded to it, so the
    two agree only while both are measured the same way. Padded in characters,
    a cell clipped to eighteen *cells* was then padded out to eighteen
    characters — thirty-six cells — and every column to its right was drawn out
    from under its own heading.
    """
    frame = legs_frame(cells_relay(tmp_path / "grid", CELLS_LEGS), size=size)
    head = frame.find("Stage/ID")
    assert head is not None, frame._message("the table drew no head row")
    columns = head_columns(frame, head)
    body = [row for row in range(head + 1, frame.rows - 1)
            if frame.lines[row].strip()]
    assert body, frame._message("the table drew no rows under its heads")

    for row in body:
        cells = column_cells(frame, row)
        for label, col in sorted(columns.items(), key=lambda item: item[1]):
            assert cells[col].strip(), frame._message(
                "row %d has nothing in column %d, where the %s head starts — "
                "the cells to its left were padded in characters and pushed "
                "it right" % (row, col, label))
            if col:
                assert not cells[col - 1].strip(), frame._message(
                    "row %d puts content in column %d, immediately left of "
                    "the %s head — the cell before it overran its column"
                    % (row, col - 1, label))
    frame.assert_within_width()


def test_the_words_this_table_is_spaced_from_are_one_cell_per_character():
    """Where `len()` and `chrome.cell_width()` provably agree, and why.

    Four of this table's widths are measured over text that cannot be
    double-width — the three column heads, the four state words, the five
    filter labels and the status glyph in the gutter — so swapping the measure
    there is a mutation no frame can fail on. They are equivalent, and this is
    what keeps them equivalent rather than an argument that they are: a head or
    a glyph that stopped being one cell per character makes the two measures
    disagree, and the column it is the threshold for would be drawn at half the
    width it needs.

    Nothing here is read out of `relay_control.legs` or `relay_control.theme`;
    every word and mark is one this file already spells for its own assertions.
    """
    words = (("Status", "Stage/ID", "Fulfills")
             + tuple(STATE_WORDS.values())
             + tuple(label for label, _ in FILTERS)
             + tuple(GLYPHS.values()) + (ELLIPSIS,))
    for text in words:
        assert display_width(text) == len(text), (
            "%r is %d cells and %d characters, so the widths this table is "
            "spaced from are measured two different ways"
            % (text, display_width(text), len(text)))

"""Frame tests for the Runners view (ACC-RUN-001..003).

Every claim below is asserted against a frame captured from a real curses
process under a pty. Nothing here imports `relay_control.runners`, and nothing
here reads a width, a column order, a label or a marker out of the program:
a test that took its expectation from the module could not fail when the module
changed, which is the failure mode that has dominated this relay — a judge ran
21 mutations against the model and 18 left the suite green, nearly all of that
shape. So:

* **The figures come from the fixture's own files at assert time.**
  `runner_legs()` and `baton_files()` parse `legs.json` and `batons/` here. The
  only thing borrowed from `relay_model` is `normalise_status`, because "what
  does the word `done` mean" is one decision and not two — the same line
  `tests/test_chrome.py` already draws.
* **The layout is measured off the screen.** `Table` finds the view's title
  row, the filter row and the column-header row on the captured frame, and
  reads every column's position out of the header row. No column width, gap or
  order is known to this file except the eight labels the contract names.
* **The spellings asserted are the contract's**, not the module's: `Runners
  (N)`, `All | Active | Completed | Failed`, and the eight column labels are
  written out here as literals on purpose. A test that read `runners.COLUMNS`
  would agree with any table the module happened to draw.
* **`assert_within_width()` in its strict form**, plus the right edge asserted
  directly: eight columns at 80 cells is tight, so what the row actually says
  at the margin is checked rather than only certified.

The one non-ASCII literal spelled here rather than read from the theme is the
absence marker `·` (and the status glyphs, which `tests/test_chrome.py` already
spells the same way). The session forces a UTF-8 locale, so this is the mark
the screen carries; reading it from `theme.GLYPHS` would make the assertion
agree with whatever the theme was changed to.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frame import display_width  # noqa: E402

from test_chrome import (  # noqa: E402
    FIXTURES, STANDARD, WIDE, repaint, session,
)

import relay_model  # noqa: E402

#: The eight columns ACC-RUN-001 names, in the order it names them.
COLUMNS = ("#", "Leg", "Stage", "Start", "Duration", "Commit", "Baton", "Status")

#: The four filters ACC-RUN-001 names, in the order it names them.
FILTERS = ("All", "Active", "Completed", "Failed")

#: Text only the Runners view paints. The Overview's keybar carries the word
#: `Runners` (`W Runners`), so `expect="Runners"` would be satisfied by the
#: screen already showing; the parenthesised count is introduced by the repaint.
OPENED = "Runners ("

#: How the view spells "this cell has no source" — one dim cell, in the theme's
#: bullet. Spelled out rather than read from `theme.GLYPHS` so that a theme
#: change is a test failure and not a silently agreed-on new answer.
ABSENT = "·"

#: What a runner row's Status cell may say. The model's own vocabulary; a cell
#: ending in anything else is a truncated word, which is what the right-edge
#: assertions are looking for.
STATUS_WORDS = ("completed", "running", "partial", "failed", "cancelled",
                "pending")

#: The glyph each of those carries (ACC-TUI-006), as `tests/test_chrome.py`
#: spells them.
STATUS_GLYPHS = {"completed": "✓", "running": "●", "pending": "○",
                 "failed": "✗", "cancelled": "−"}


# --------------------------------------------------------------------------
# reading the view off a captured frame
# --------------------------------------------------------------------------


class Table:
    """The Runners view, located by measuring the frame it was drawn on.

    The title row is the row carrying `Runners (N)`; the body is everything
    between it and the keybar; the filter row is the first body row; the
    column-header row is the first body row carrying the `Leg` label; and every
    column's position is where its label starts on that row. A layout change
    moves these assertions rather than breaking them.
    """

    def __init__(self, frame):
        self.frame = frame
        self.top = frame.search(r"Runners \(\d+\)")
        if self.top is None:
            raise AssertionError(frame._message("no Runners view on this frame"))
        self.left = frame.raw_lines[self.top].index("Runners (")
        self.rows = [frame.raw_lines[index][self.left:]
                     for index in range(self.top + 1, frame.rows - 1)]

    # -- the header row --------------------------------------------------

    @property
    def title_row(self):
        return self.frame.raw_lines[self.top][self.left:].rstrip()

    @property
    def count(self):
        """The `N` of `Runners (N)`."""
        return int(re.search(r"Runners \((\d+)\)", self.title_row).group(1))

    @property
    def meta(self):
        """The pane's right-hand figure — what follows the title."""
        return self.title_row[len("Runners (%d)" % self.count):].strip()

    # -- the body --------------------------------------------------------

    @property
    def filled(self):
        return [row for row in self.rows if row.strip()]

    @property
    def filter_row(self):
        if not self.filled:
            raise AssertionError(self.message("the body is blank"))
        return self.rows[0].rstrip()

    def filter_counts(self):
        """`{label: count}` read off the filter row, in the order drawn."""
        found = re.findall(r"([A-Za-z]+) \((\d+)\)", self.filter_row)
        return [(label, int(number)) for label, number in found]

    @property
    def header_index(self):
        """The body row the column labels are on, or None when none was drawn."""
        for index, row in enumerate(self.rows):
            if re.search(r"(?<!\S)Leg(?!\S)", row):
                return index
        return None

    @property
    def columns(self):
        """`[(label, column)]` for the columns actually drawn, left to right."""
        index = self.header_index
        if index is None:
            return []
        return [(match.group(), match.start())
                for match in re.finditer(r"\S+", self.rows[index])]

    @property
    def labels(self):
        return [label for label, _ in self.columns]

    @property
    def data_rows(self):
        """The runner rows: everything under the labels that is not a marker."""
        index = self.header_index
        if index is None:
            return []
        return [row for row in self.rows[index + 1:]
                if row.strip() and not row.lstrip().startswith("+")]

    def cells(self, row):
        """`{label: text}` for one data row, split at the column positions."""
        edges = self.columns + [("", len(row) + 1)]
        return {label: row[start:edges[index + 1][1] - 1].strip()
                for index, (label, start) in enumerate(self.columns)}

    def column(self, label):
        """Every data row's cell in one column."""
        return [self.cells(row)[label] for row in self.data_rows]

    def row_for(self, leg):
        for row in self.data_rows:
            if self.cells(row)["Leg"] == leg:
                return row
        raise AssertionError(self.message("no row for leg %r" % leg))

    def screen_row(self, row):
        """The absolute frame row a body row is on, for attribute assertions."""
        return self.top + 1 + self.rows.index(row)

    def marker(self, pattern=r"\+(\d+) more"):
        for row in self.rows:
            found = re.search(pattern, row)
            if found:
                return int(found.group(1))
        return 0

    @property
    def text(self):
        return "\n".join(self.rows)

    def message(self, reason):
        return self.frame._message("Runners view: %s" % reason)


def open_runners(term):
    """Switch to the Runners view and return the frame it painted."""
    return term.send("W", expect=OPENED)


def runners_frame(relay_dir, size=WIDE, **kwargs):
    term = session(relay_dir, size=size, **kwargs)
    try:
        return open_runners(term)
    finally:
        term.close()


def table_of(relay_dir, size=WIDE, **kwargs):
    return Table(runners_frame(relay_dir, size=size, **kwargs))


def filtered(term, presses):
    """The view after `presses` × `T`, as a table."""
    frame = None
    for _ in range(presses):
        frame = term.send("T", expect=OPENED)
    return Table(frame)


# --------------------------------------------------------------------------
# figures, read from the fixture's own files at assert time
# --------------------------------------------------------------------------


def legs_of(relay_dir):
    path = Path(relay_dir) / "legs.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [leg for leg in data.get("legs", []) if isinstance(leg, dict)]


def runner_legs(relay_dir):
    """`(required, running)` — the legs that must have a row, out of `legs.json`.

    A runner has been on every completed leg and on every running one, so both
    must appear in the view. Only the status vocabulary is shared with the
    model: "what does the word `done` mean" is one decision and not two.
    """
    required, running = [], []
    for leg in legs_of(relay_dir):
        status = relay_model.normalise_status(leg.get("status"))
        if status in ("completed", "running"):
            required.append(leg.get("id"))
        if status == "running":
            running.append(leg.get("id"))
    return required, running


def allowed_legs(relay_dir):
    """Every leg a row may legitimately name.

    The legs above, plus any baton whose leg `legs.json` forgot: the relay
    reports what happened rather than what was planned, and this fixture
    carries exactly such a baton on purpose. Stated as a *bound* rather than as
    an exact list, so that this file asserts the view's own property — it draws
    the legs that ran and invents none — rather than re-deciding a question the
    model owns and `tests/test_relay_model.py` certifies.
    """
    required, _ = runner_legs(relay_dir)
    return set(required) | set(baton_files(relay_dir))


def baton_files(relay_dir):
    """`{leg id: path}` for every baton on the fixture's disk."""
    directory = Path(relay_dir) / "batons"
    if not directory.is_dir():
        return {}
    return {path.stem: path for path in sorted(directory.glob("*.md"))}


def baton_lines(path):
    """How many lines that baton is, counted here rather than asked of anyone."""
    return path.read_text(errors="replace").count("\n") + 1


def legs_without_a_baton(relay_dir):
    required, running = runner_legs(relay_dir)
    batons = baton_files(relay_dir)
    return [leg for leg in required if leg not in batons and leg not in running]


# --------------------------------------------------------------------------
# ACC-RUN-001 — header, filter row, columns
# --------------------------------------------------------------------------


def test_the_view_names_itself_runners_with_the_number_of_runners():
    relay = FIXTURES / "agent-service"
    required, _ = runner_legs(relay)
    assert len(required) > 1, "this fixture must have runners for the test to bite"

    table = table_of(relay)
    assert not table.marker(), table.message(
        "everything must fit at 160x48 for the count to be checkable here")
    assert table.count == len(table.data_rows), table.message(
        "the view says %d runners and draws %d rows"
        % (table.count, len(table.data_rows)))
    assert table.count >= len(required), table.message(
        "the view says %d runners; legs.json has %d legs a runner has been on"
        % (table.count, len(required)))


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_the_filter_row_names_the_four_filters_in_order_with_counts(size):
    relay = FIXTURES / "agent-service"
    required, running = runner_legs(relay)

    table = table_of(relay, size=size)
    counts = table.filter_counts()
    assert [label for label, _ in counts] == list(FILTERS), table.message(
        "the filter row reads %r, not %r" % (table.filter_row, list(FILTERS)))
    figures = dict(counts)
    assert figures["All"] == table.count >= len(required), table.message(
        "All (%d) against a view of %d runners and %d legs that ran"
        % (figures["All"], table.count, len(required)))
    assert figures["Active"] == len(running), table.message(
        "Active (%d) against %d running legs" % (figures["Active"], len(running)))
    assert figures["Completed"] + figures["Failed"] <= figures["All"], (
        table.message("the buckets add up to more than the whole: %r" % counts))
    # The separators the contract spells: `All | Active | Completed | Failed`.
    assert table.filter_row.count("|") == len(FILTERS) - 1, table.message(
        "the filter row is not separated as `%s`" % " | ".join(FILTERS))


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_the_eight_columns_are_drawn_in_the_order_the_contract_names(size):
    table = table_of(FIXTURES / "agent-service", size=size)
    assert table.labels == list(COLUMNS), table.message(
        "the columns are %r, not %r" % (table.labels, list(COLUMNS)))


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_every_cell_sits_under_its_own_column_header(size):
    """A table is only a table if the columns line up on every row.

    Asserted as two facts a reader would notice: the cell under each label
    starts where the label does, and the gap column before each label is blank
    on every row — which is also what stops two cells running together.
    """
    table = table_of(FIXTURES / "agent-service", size=size)
    assert table.data_rows, table.message("no runner rows were drawn")
    for row in table.data_rows:
        for label, start in table.columns[1:]:
            assert row[start - 1] == " ", table.message(
                "the %s column has no gap before it on %r" % (label, row))
        cells = table.cells(row)
        for label, start in table.columns:
            assert cells[label], table.message(
                "the %s cell is blank on %r — an unmeasured field is a marker, "
                "not an empty cell" % (label, row))
            assert row[start] != " ", table.message(
                "the %s cell does not start under its label on %r" % (label, row))


def test_the_leg_column_carries_the_legs_the_fixture_says_ran():
    relay = FIXTURES / "agent-service"
    required, running = runner_legs(relay)

    table = table_of(relay)
    drawn = table.column("Leg")
    assert len(drawn) == len(set(drawn)), table.message(
        "a leg is listed twice: %r" % drawn)
    missing = [leg for leg in required if leg not in drawn]
    assert not missing, table.message(
        "legs.json says these legs ran and they have no row: %r" % missing)
    invented = set(drawn) - allowed_legs(relay)
    assert not invented, table.message(
        "rows name legs nothing on disk says ran: %r" % sorted(invented))
    for leg in running:
        assert leg in drawn, table.message("the running leg %r has no row" % leg)


def test_the_number_column_counts_the_rows_from_one():
    table = table_of(FIXTURES / "agent-service")
    numbers = [int(cell) for cell in table.column("#")]
    assert numbers == list(range(1, len(numbers) + 1)), table.message(
        "the # column reads %r" % numbers)


def test_the_running_leg_is_drawn_with_its_status_and_its_glyph():
    relay = FIXTURES / "agent-service"
    _, running = runner_legs(relay)
    assert running, "this fixture must have a running leg"

    table = table_of(relay)
    row = table.row_for(running[0])
    status = table.cells(row)["Status"]
    assert "running" in status, table.message(
        "the running leg's Status cell reads %r" % status)
    assert STATUS_GLYPHS["running"] in status, table.message(
        "the Status cell carries no status glyph: %r" % status)
    # The glyph keeps its own status colour (ACC-TUI-006).
    table.frame.assert_attrs(STATUS_GLYPHS["running"], fg=33,
                             row=table.screen_row(row))


def test_the_active_runners_leg_is_the_value_the_eye_lands_on():
    relay = FIXTURES / "agent-service"
    _, running = runner_legs(relay)
    table = table_of(relay)
    row = table.row_for(running[0])
    table.frame.assert_attrs(running[0], has="bold", row=table.screen_row(row))


def test_the_pane_never_claims_a_range_it_did_not_draw():
    """`1-N of M` names the rows the pane drew, and `+K more` accounts for the rest."""
    relay = FIXTURES / "agent-service"
    required, _ = runner_legs(relay)
    table = table_of(relay, size=STANDARD)

    drawn = len(table.data_rows)
    hidden = table.marker()
    assert hidden, table.message(
        "everything fitted at 80x24, so this proves no truncation")
    assert table.count >= len(required), table.message(
        "the view claims fewer runners than legs.json says ran")
    assert drawn + hidden == table.count, table.message(
        "%d rows drawn and %d claimed hidden, out of %d runners"
        % (drawn, hidden, table.count))
    assert table.meta == "1-%d of %d" % (drawn, table.count), table.message(
        "the header reads %r for %d rows drawn" % (table.meta, drawn))


def test_a_row_says_when_it_started_and_how_long_it_took_or_neither():
    """`start` and `duration` are one measurement in the model — a runner
    starts when the previous baton lands, and its duration is measured from
    that same instant. A row that has one and not the other is a row drawing
    some other field."""
    table = table_of(FIXTURES / "agent-service")
    pairs = [(cells["Start"], cells["Duration"])
             for cells in (table.cells(row) for row in table.data_rows)]
    assert any(start != ABSENT for start, _ in pairs), table.message(
        "no row has a start time at all")
    assert any(start == ABSENT for start, _ in pairs), table.message(
        "every row has a start time, so this proves nothing")
    for start, duration in pairs:
        assert (start == ABSENT) == (duration == ABSENT), table.message(
            "a row reads Start %r and Duration %r" % (start, duration))


def test_no_row_claims_an_impossible_duration():
    """A leg that has been running for a thousand hours is a clock read as an
    elapsed time, not a leg."""
    table = table_of(FIXTURES / "agent-service")
    for cell in table.column("Duration"):
        if cell == ABSENT:
            continue
        assert re.fullmatch(r"(\d+h \d\dm|\d+m \d\ds|\d+s)", cell), (
            table.message("a Duration cell reads %r" % cell))
        hours = re.match(r"(\d+)h", cell)
        assert not hours or int(hours.group(1)) < 1000, table.message(
            "a Duration cell reads %r" % cell)


# --------------------------------------------------------------------------
# ACC-RUN-001 — the right edge, where eight columns at 80 cells run out
# --------------------------------------------------------------------------


@pytest.mark.parametrize("size", [WIDE, STANDARD, (30, 100), (24, 60), (12, 40)])
def test_no_row_of_the_runners_view_reaches_the_last_column(size):
    """Certified, not waved through.

    ncurses clips in software, so a row ending exactly at the margin is
    byte-identical to one truncated to fit and `assert_within_width()` refuses
    to certify it. The chrome reserves the last column so the strict form
    applies; a view that filled it would take that certification away from
    every other test in the repository.
    """
    frame = runners_frame(FIXTURES / "agent-service", size=size)
    assert not frame.full_width_rows(), frame._message(
        "rows %r reach the reserved last column" % (frame.full_width_rows(),))
    frame.assert_within_width()
    assert "Traceback" not in frame.text


def test_at_80_columns_every_row_ends_in_a_whole_status_word():
    """What the row actually says at the right edge, not merely that it fits.

    `assert_within_width()` cannot tell an exact fit from a truncation —
    ncurses clips in software, so both look the same on the screen — and a
    table built one cell too wide loses that cell silently. So the last column
    is read: a Status cell that lost its tail is a truncation, whatever the
    width helper says about the row.
    """
    frame = runners_frame(FIXTURES / "agent-service", size=STANDARD)
    table = Table(frame)
    for row in table.data_rows:
        assert row.rstrip().endswith(STATUS_WORDS), table.message(
            "the last column was clipped: %r" % row.rstrip())
    assert "…" not in table.rows[table.header_index], table.message(
        "a column label was clipped at 80 columns")
    # ...and the table spends every cell it is allowed and not one more: the
    # widest row reaches the column before the reserved one.
    widest = max(len(row.rstrip()) for row in table.data_rows)
    assert widest == frame.cols - 1, table.message(
        "the widest row is %d cells of the %d a pane may use"
        % (widest, frame.cols - 1))


def test_at_80_columns_the_leg_column_is_the_one_that_gives_way():
    """Something has to give at 80 cells; it is the elastic column, visibly.

    The longest leg id in the fixture is wider than the table can afford, and
    the row that carries it ends in an ellipsis rather than losing a column or
    running into its neighbour.
    """
    relay = FIXTURES / "agent-service"
    wide = table_of(relay)
    whole = wide.column("Leg")
    longest = max(whole, key=len)
    assert len(longest) > 20, wide.message(
        "no leg id is long enough here to make 80 columns tight")

    table = table_of(relay, size=STANDARD)
    assert table.labels == list(COLUMNS), table.message(
        "a column was dropped at 80 columns instead of narrowing the Leg column")
    drawn = table.column("Leg")
    clipped = [cell for cell in drawn if cell.endswith("…")]
    assert clipped or longest in drawn, table.message(
        "the Leg column neither fitted %r nor marked it as cut" % longest)
    for cell in drawn:
        head = cell.rstrip("…")
        assert any(leg.startswith(head) for leg in whole), table.message(
            "the Leg cell %r is not the head of any leg the view listed" % cell)


# --------------------------------------------------------------------------
# ACC-RUN-002 — the Active count is the running legs
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", ["agent-service", "running-impl",
                                     "legs-only", "all-done", "no-dashboard"])
def test_the_active_filter_count_equals_the_legs_that_are_running(fixture):
    """The original Mission Control defect: `Active (0)` while legs ran.

    The expectation is counted out of the fixture's own `legs.json`, so the
    view is checked against the relay and not against itself. `all-done` is in
    the list on purpose: with nothing running the answer is 0, and a count
    hardcoded to the running case would pass everywhere else.
    """
    relay = FIXTURES / fixture
    _, running = runner_legs(relay)

    table = table_of(relay)
    figures = dict(table.filter_counts())
    assert figures.get("Active") == len(running), table.message(
        "Active (%r) while legs.json marks %d legs running: %r"
        % (figures.get("Active"), len(running), running))


def test_the_active_filter_lists_exactly_the_legs_that_are_running():
    """The count and the rows behind it are the same fact, or one of them lies."""
    relay = FIXTURES / "agent-service"
    _, running = runner_legs(relay)

    term = session(relay, size=WIDE)
    try:
        open_runners(term)
        table = filtered(term, 1)                  # All -> Active
        assert table.filter_counts()[1][0] == "Active"
        assert sorted(table.column("Leg")) == sorted(running), table.message(
            "the Active filter lists %r for running legs %r"
            % (table.column("Leg"), running))
        assert dict(table.filter_counts())["Active"] == len(table.data_rows), (
            table.message("Active (%d) over %d rows"
                          % (dict(table.filter_counts())["Active"],
                             len(table.data_rows))))
        for row in table.data_rows:
            assert "running" in table.cells(row)["Status"], table.message(
                "the Active filter drew a row that is not running: %r" % row)
    finally:
        term.close()


def test_the_completed_filter_lists_no_running_leg():
    relay = FIXTURES / "agent-service"
    _, running = runner_legs(relay)

    term = session(relay, size=WIDE)
    try:
        open_runners(term)
        table = filtered(term, 2)                  # All -> Active -> Completed
        drawn = table.column("Leg")
        assert drawn, table.message("the Completed filter drew nothing")
        assert not set(drawn) & set(running), table.message(
            "a running leg is listed as completed: %r" % (set(drawn) & set(running)))
        figures = dict(table.filter_counts())
        # Against `All`, not against the view's own heading: since
        # `navigation-and-filters` the heading is the *filtered* count
        # (ACC-NAV-003), so `len(drawn) < table.count` would now be asking
        # whether the Completed filter drew fewer rows than it says it has —
        # which is not what this test is about. `All` is still the whole list.
        assert len(drawn) < figures["All"], table.message(
            "the Completed filter drew every runner — it filtered nothing")
        assert figures["Completed"] == len(drawn) == table.count, (
            table.message("Completed (%d) over %d rows under a heading of %d"
                          % (figures["Completed"], len(drawn), table.count)))
    finally:
        term.close()


def test_a_filter_that_matches_nothing_says_so_in_words():
    relay = FIXTURES / "agent-service"
    term = session(relay, size=WIDE)
    try:
        open_runners(term)
        table = filtered(term, 3)                  # ... -> Failed
        assert dict(table.filter_counts())["Failed"] == 0, (
            "this fixture must have no failed runner for the test to bite")
        assert table.header_index is None, table.message(
            "column labels were drawn over an empty list")
        body = [row for row in table.filled[1:]]
        assert body, table.message("an empty filter left a blank body")
        assert "Failed" in body[0] or "failed" in body[0], table.message(
            "the empty filter does not say which filter is empty: %r" % body[0])
        assert "0/0" not in table.text and "1-0" not in table.text, table.message(
            "a range nothing was drawn for")
    finally:
        term.close()


def test_the_active_filter_is_highlighted_and_the_others_are_not():
    relay = FIXTURES / "agent-service"
    term = session(relay, size=WIDE)
    try:
        frame = open_runners(term)
        table = Table(frame)
        row = table.top + 1
        counts = dict(table.filter_counts())
        frame.assert_attrs("All (%d)" % counts["All"], has="reverse", row=row)
        frame.assert_attrs("Completed (%d)" % counts["Completed"],
                           lacks="reverse", row=row)

        table = filtered(term, 1)
        row = table.top + 1
        counts = dict(table.filter_counts())
        table.frame.assert_attrs("Active (%d)" % counts["Active"],
                                 has="reverse", row=row)
        table.frame.assert_attrs("All (%d)" % counts["All"],
                                 lacks="reverse", row=row)
    finally:
        term.close()


# --------------------------------------------------------------------------
# ACC-RUN-003 — unavailable fields are visibly unmeasured
# --------------------------------------------------------------------------


def test_a_field_with_no_source_is_a_dim_marker_and_not_a_blank_or_a_zero():
    """17 of this fixture's completed legs left no baton. That is the normal
    case here, and every one of those cells has to say so."""
    relay = FIXTURES / "agent-service"
    orphans = legs_without_a_baton(relay)
    assert orphans, "this fixture must have a leg with no baton"

    table = table_of(relay)
    row = table.row_for(orphans[0])
    cells = table.cells(row)
    for label in ("Start", "Duration", "Commit", "Baton"):
        assert cells[label] == ABSENT, table.message(
            "%r has no baton, so its %s cell must be the absence marker, not %r"
            % (orphans[0], label, cells[label]))
    # Dim, and one cell — never a word, a zero or an em-dash.
    column = dict(table.columns)["Commit"]
    assert table.frame.attrs_at(table.screen_row(row), column).dim, table.message(
        "the absence marker is not dim")
    assert "—" not in table.text, table.message("an em-dash was invented")
    assert not re.search(r"(?<!\S)(0|n/a|N/A|none|null|None)(?!\S)", table.text), (
        table.message("a placeholder value was invented"))


def test_a_field_with_a_source_carries_its_real_value():
    """The other half: the marker means absent, so it may not stand for data."""
    relay = FIXTURES / "agent-service"
    batons = baton_files(relay)
    worked, _ = runner_legs(relay)
    landed = [leg for leg in worked if leg in batons]
    assert landed, "this fixture must have a leg that left a baton"

    table = table_of(relay)
    checked = 0
    for leg in landed:
        cells = table.cells(table.row_for(leg))
        assert cells["Baton"] == "%d ln" % baton_lines(batons[leg]), table.message(
            "%r's Baton cell reads %r for a %d-line baton"
            % (leg, cells["Baton"], baton_lines(batons[leg])))
        if cells["Commit"] != ABSENT:
            assert re.fullmatch(r"[0-9a-f]{7}", cells["Commit"]), table.message(
                "%r's Commit cell reads %r" % (leg, cells["Commit"]))
            checked += 1
    assert checked, table.message("no row carried a commit at all")


def test_a_column_with_no_data_for_any_row_is_not_drawn():
    relay = FIXTURES / "all-done"
    assert not baton_files(relay), (
        "this fixture must have no batons for the test to bite")
    worked, _ = runner_legs(relay)
    assert worked, "this fixture must have runners"

    table = table_of(relay)
    for label in ("Start", "Duration", "Commit", "Baton"):
        assert label not in table.labels, table.message(
            "the %s column has no data for any row and was drawn anyway" % label)
    for label in ("#", "Leg", "Status"):
        assert label in table.labels, table.message(
            "the %s column has data and was dropped" % label)
    assert ABSENT not in table.text, table.message(
        "a column of nothing but markers was drawn instead of being dropped")


def test_no_column_drawn_is_empty_for_every_row():
    """The general form of the rule, over every column the view chose to draw."""
    table = table_of(FIXTURES / "agent-service")
    for label in table.labels:
        values = [cell for cell in table.column(label) if cell != ABSENT]
        assert values, table.message(
            "the %s column is the marker on every row and should have been "
            "dropped" % label)


# --------------------------------------------------------------------------
# emptiness and degradation
# --------------------------------------------------------------------------


def test_a_relay_no_runner_has_worked_says_so_in_words():
    relay = FIXTURES / "empty"
    worked, _ = runner_legs(relay)
    assert not worked and not baton_files(relay), (
        "this fixture must have no runners for the test to bite")

    table = table_of(relay)
    assert table.count == 0
    assert table.meta == "", table.message(
        "a pane with nothing to count wrote %r beside its title" % table.meta)
    assert table.header_index is None, table.message(
        "column labels were drawn over an empty view")
    assert table.filled, table.message("an empty view is a blank box")
    assert re.search(r"[a-z]{3,} [a-z]{2,}", table.filled[0]), table.message(
        "emptiness is stated in words, not in punctuation: %r" % table.filled[0])
    assert "0/0" not in table.text and "1-0" not in table.text


@pytest.mark.parametrize("size", [(12, 40), (8, 30), (5, 20), (3, 12)])
def test_the_runners_view_degrades_below_80x24_without_crashing(size):
    term = session(FIXTURES / "agent-service", size=size)
    try:
        term.send("W")
        frame = repaint(term)
        frame.assert_finished()
        assert "Traceback" not in frame.text, frame._message("the view raised")
        frame.assert_within_width()
        assert not frame.full_width_rows(), frame._message(
            "rows %r reach the reserved last column" % (frame.full_width_rows(),))
        assert frame.text.strip(), frame._message("nothing was drawn at all")
        assert term.is_running, frame._message("the TUI exited instead")
    finally:
        term.close()


def test_a_narrow_terminal_drops_columns_rather_than_overflowing():
    relay = FIXTURES / "agent-service"
    table = table_of(relay, size=(24, 40))
    assert table.labels, table.message("no columns survived at 40 columns")
    assert set(table.labels) < set(COLUMNS), table.message(
        "eight columns cannot fit 40 cells, yet %r were drawn" % table.labels)
    for label in ("Leg", "Status"):
        assert label in table.labels, table.message(
            "%s is the column a narrow terminal keeps" % label)
    assert "Baton" not in table.labels, table.message(
        "the Baton column outlived wider ones at 40 columns")
    for row in table.data_rows:
        for label, start in table.columns[1:]:
            assert row[start - 1] == " ", table.message(
                "columns ran together at 40 cells: %r" % row)
    # Columns give way so that the leg id stays readable; a table of `re…`
    # in five columns is worse than the same table in four.
    for cell in table.column("Leg"):
        assert len(cell.rstrip("…")) >= 8, table.message(
            "the Leg column was narrowed to %r rather than dropping a column"
            % cell)


def test_a_pane_too_short_for_a_row_counts_rather_than_claiming_a_range():
    """Six rows: the filter row and the overflow marker, and nothing else fits.

    Two things go wrong here if they are allowed to. `1-0 of 29` is a range the
    pane did not draw (ACC-OVER-004's rule, one view over), and a column-label
    row with no row under it is a heading pointing at nothing.
    """
    relay = FIXTURES / "agent-service"
    term = session(relay, size=(6, 80))
    try:
        table = Table(open_runners(term))
        assert table.header_index is None, table.message(
            "column labels were drawn with no room for a row under them")
        assert not table.data_rows
        assert "1-" not in table.meta, table.message(
            "the header claims the range %r over rows it did not draw"
            % table.meta)
        assert str(table.count) in table.meta, table.message(
            "the header says %r instead of how many runners there are"
            % table.meta)
        assert table.marker() == table.count, table.message(
            "%d runners, and the marker accounts for %d"
            % (table.count, table.marker()))
    finally:
        term.close()


@pytest.mark.parametrize("cols", [40, 30, 50])
def test_a_filter_row_too_wide_for_the_terminal_is_cut_visibly(cols):
    """Four labels and four counts do not fit 40 cells, and a row that quietly
    lost `Failed (0)` reads as a view with three filters."""
    table = table_of(FIXTURES / "agent-service", size=(24, cols))
    row = table.filter_row
    assert len(row) <= cols - 1, table.message(
        "the filter row runs into the reserved last column: %r" % row)
    shown = [label for label, _ in table.filter_counts()]
    if shown != list(FILTERS):
        assert row.endswith("…"), table.message(
            "the filter row lost %r with nothing to say it was cut: %r"
            % ([f for f in FILTERS if f not in shown], row))
    assert row.count("…") <= 1, table.message(
        "the cut is marked more than once: %r" % row)


def test_the_filter_row_survives_a_terminal_too_short_for_the_table():
    """The counts are the one thing a single body row can still say."""
    relay = FIXTURES / "agent-service"
    _, running = runner_legs(relay)
    term = session(relay, size=(8, 80))
    try:
        frame = open_runners(term)
        table = Table(frame)
        figures = dict(table.filter_counts())
        assert figures["Active"] == len(running), table.message(
            "the filter row lost its counts on a short terminal")
    finally:
        term.close()


# --------------------------------------------------------------------------
# Width is measured in cells, not in characters
#
# `_widths()` sizes every column from the values it just rendered, and those
# values are prose: a leg id, a stage, a coach's status word. A terminal draws
# a CJK character in two columns, so `len()` sized the elastic `Leg` column at
# half what its widest id needs — and `str.ljust()` then padded each clipped
# cell out to that many *characters*, which is twice the column it belongs to.
#
# Neither shows up in `assert_within_width()`: `Pane` clips every write to its
# own rectangle, so a row built twice as wide as the table reaches the screen
# truncated rather than overrunning it. What shows up is an id cut where the
# table had room for it, and a grid whose cells no longer stand under their
# own heads.
# --------------------------------------------------------------------------

#: Nine ideographs: eighteen cells, nine characters.
CJK_LEG = "日本語で書かれた脚"

#: Three legs the model will build runner rows for — two landed, one running.
CELLS_LEGS = ((CJK_LEG, "done"), ("二番目の日本語の脚", "done"),
              ("三番目の日本語の脚", "running"))

ELLIPSIS = "…"


def cells_relay(directory, legs=CELLS_LEGS, relay="cells"):
    """A relay whose leg ids are double-width, so its runner rows are too."""
    directory = Path(directory)
    (directory / "batons").mkdir(parents=True, exist_ok=True)
    (directory / "legs.json").write_text(json.dumps({
        "relay": relay,
        "stages": [{"id": "S1", "name": "Only stage",
                    "legs": [leg_id for leg_id, _ in legs]}],
        "legs": [{"id": leg_id, "stage": "S1", "status": status,
                  "goal": "a leg", "fulfills": []}
                 for leg_id, status in legs],
    }))
    (directory / "state.json").write_text(json.dumps({
        "relay": relay, "phase": "running", "currentStage": "S1",
        "checks": {}}))
    return directory


def head_columns(frame, head):
    """`{label: column}` for the heads this table actually drew.

    The head row is ASCII by construction — the eight labels are literals in
    this file — so a string index into it *is* a column, which is exactly what
    stops being true one row further down, where the cell after a double-width
    character is the empty string in `frame.lines`.
    """
    line = frame.lines[head]
    return {label: line.index(label) for label in COLUMNS if label in line}


def test_a_double_width_leg_id_is_drawn_whole_where_the_table_has_room(
        tmp_path):
    """The elastic column is as wide as its widest value, measured in cells.

    Sized with `len()` it was nine cells wide for an id that needs eighteen, so
    a hundred-and-sixty-column terminal with four columns on it clipped an id
    it had the room to draw twice over — and marked the cut, which reads as a
    relay whose leg ids are too long rather than as a table that measured them
    wrong.
    """
    frame = runners_frame(cells_relay(tmp_path / "wide"))
    assert frame.contains(CJK_LEG), frame._message(
        "the Leg column cut %r, which fits this terminal several times over"
        % CJK_LEG)
    row = frame.find(CJK_LEG)
    assert ELLIPSIS not in frame.lines[row], frame._message(
        "the row carries a mark saying something was cut: %r"
        % frame.lines[row])
    frame.assert_within_width()


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_every_cell_of_a_double_width_row_starts_under_its_own_head(
        size, tmp_path):
    """The heads and the cells are spaced from one layout, in one measure.

    A cell clipped to `widths[key]` *cells* and then padded out to that many
    *characters* is twice its column wide, and every column to its right stands
    somewhere its heading does not.
    """
    frame = runners_frame(cells_relay(tmp_path / "grid"), size=size)
    head = frame.find(COLUMNS[1])           # the row carrying `Leg`
    assert head is not None, frame._message("the table drew no head row")
    columns = head_columns(frame, head)
    assert len(columns) >= 3, frame._message(
        "only %r survived, which is too few to say anything about a grid"
        % sorted(columns))
    body = [row for row in range(head + 1, frame.rows - 1)
            if frame.lines[row].strip()]
    assert body, frame._message("the table drew no rows under its heads")

    for row in body:
        cells = list(frame.cells[row])
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

    A column is as wide as its label or its widest value. The values are prose
    and are measured in cells; the labels are the eight the contract names, and
    they cannot be double-width — so swapping the measure on the *label* is a
    mutation no frame can fail on. It is equivalent, and this keeps it so: a
    label that stopped being one cell per character makes the two measures
    disagree, and the column would be spaced at half what its head needs.

    Nothing here is read out of `relay_control.runners`; every word and mark is
    one this file already spells for its own assertions.
    """
    words = COLUMNS + FILTERS + STATUS_WORDS + tuple(STATUS_GLYPHS.values()) \
        + (ABSENT,)
    for text in words:
        assert display_width(text) == len(text), (
            "%r is %d cells and %d characters, so the widths this table is "
            "spaced from are measured two different ways"
            % (text, display_width(text), len(text)))

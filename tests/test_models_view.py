"""Frame tests for the Models view (ACC-MODEL-001..003).

Every claim below is asserted against a frame captured from a real curses
process under a pty. **Nothing here imports `relay_control.models` or
`relay_control.theme`**, and nothing reads a label, a width, a default or a
marker out of the program. A test that took its expectation from the module it
tests cannot fail when the module changes, and that shape has dominated this
relay — a judge ran 21 mutations against the model and 18 left the suite green,
nearly all of them this. So:

* **The spellings asserted are the contract's and SKILL.md's.** `Coach`,
  `Runner`, `Judge`, `Skip code judge`, `Skip behaviour judge`, `read-only`,
  `dashboard.json` are written out here as literals on purpose, and the role
  guidance is checked against the Models table parsed out of `SKILL.md` at
  assert time rather than against a string in `models.py`.
* **The values come from the fixture's own `dashboard.json` at assert time.**
  `pinned_models()` parses the file; no model name or effort is hardcoded.
* **The layout is measured off the screen.** `View` finds the pane, the column
  heads and every row on the captured frame, and reads each column's position
  out of the head row. No width, gap or order is known to this file except the
  three column labels and the three roles the contract names.

The non-ASCII literals spelled here rather than read from the theme are the
absence marker `·`, the failure glyph `✗` and the ellipsis `…` — the session
forces a UTF-8 locale, so those are the marks the screen carries, and reading
them from `theme.GLYPHS` would make every assertion agree with whatever the
theme was last changed to.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frame import TerminalSession  # noqa: E402
from test_chrome import (  # noqa: E402
    ENTRY, FIXTURES, REPO, STANDARD, UTF8_ENV, WIDE, repaint, session,
)

#: The three roles ACC-MODEL-001 names, in the order it names them.
ROLES = ("Coach", "Runner", "Judge")

#: The columns this view draws. `Role` and `Model` are ACC-MODEL-001's two
#: facts; `Effort` is the reasoning effort it also names.
COLUMNS = ("Role", "Model", "Effort")

#: The two toggles ACC-MODEL-003 names, spelled as it spells them.
TOGGLES = ("Skip code judge", "Skip behaviour judge")

#: Text only the Models view paints. The Overview's keybar carries the word
#: `Models` (`M Models`), so `expect="Models"` would be satisfied by the screen
#: already showing; the parenthesised figure is introduced by the repaint.
OPENED = "Models ("

#: How a pane says "this cell has no source" — one dim cell, in the theme's
#: bullet — and how it marks a cut. Spelled rather than imported, so a theme
#: change is a failure here and not a silently agreed-on new answer.
ABSENT = "·"
ELLIPSIS = "…"
FAILED = "✗"

#: Text only the Enter message introduces. The pane's own figure already reads
#: `read-only`, so that word alone is on screen before any key is pressed —
#: `expect=MESSAGE` ends a wait on the frame that was already there, which
#: is how this file first flaked. `is read-only` is the message's own phrase and
#: it survives the clip at 40 columns.
MESSAGE = "is read-only"

#: The words the view must use for the three states of a role's model, and for
#: a toggle nobody wrote. `not configured` is ACC-MODEL-001's "documented
#: default when absent".
NOT_CONFIGURED = "not configured"
UNREADABLE = "unreadable"
DEFAULT_OFF = "off (default)"


# --------------------------------------------------------------------------
# reading the view off a captured frame
# --------------------------------------------------------------------------


class View:
    """The Models view, located by measuring the frame it was drawn on.

    The title row is the row carrying `Models (n/m)`; the body is everything
    between it and the keybar; the column-head row is the first body row
    carrying the `Role` label; a role's row is the body row whose first token
    is that role's name; and its guidance is the indented row under it. A
    layout change moves these assertions rather than breaking them.
    """

    def __init__(self, frame):
        self.frame = frame
        self.top = frame.search(r"Models \(\d+/\d+\)")
        if self.top is None:
            raise AssertionError(frame._message("no Models view on this frame"))
        self.left = frame.raw_lines[self.top].index("Models (")
        self.rows = [frame.raw_lines[index][self.left:]
                     for index in range(self.top + 1, frame.rows - 1)]

    # -- the title row ---------------------------------------------------

    @property
    def title_row(self):
        return self.frame.raw_lines[self.top][self.left:].rstrip()

    @property
    def configured(self):
        """The `n` of `Models (n/m)` — how many roles have a model."""
        return int(re.search(r"Models \((\d+)/(\d+)\)", self.title_row).group(1))

    @property
    def roles_offered(self):
        """The `m` of `Models (n/m)`."""
        return int(re.search(r"Models \((\d+)/(\d+)\)", self.title_row).group(2))

    @property
    def meta(self):
        """The pane's right-hand figure — whatever follows the title."""
        title = re.search(r"Models \(\d+/\d+\)", self.title_row).group()
        return self.title_row[len(title):].strip()

    # -- the body --------------------------------------------------------

    @property
    def filled(self):
        return [row for row in self.rows if row.strip()]

    @property
    def text(self):
        return "\n".join(self.rows)

    @property
    def head_index(self):
        """The body row the column labels are on, or None when none was drawn."""
        for index, row in enumerate(self.rows):
            if re.match(r"Role(?!\S)", row):
                return index
        return None

    @property
    def columns(self):
        """`[(label, column)]` for the heads actually drawn, left to right."""
        index = self.head_index
        if index is None:
            return []
        return [(match.group(), match.start())
                for match in re.finditer(r"\S+", self.rows[index])]

    @property
    def labels(self):
        return [label for label, _ in self.columns]

    def column_at(self, label):
        for name, start in self.columns:
            if name == label:
                return start
        raise AssertionError(self.message("no %r column head is drawn" % label))

    def index_of(self, role):
        """The body row `role` names itself on."""
        for index, row in enumerate(self.rows):
            if re.match(r"%s(?!\S)" % re.escape(role), row):
                return index
        raise AssertionError(self.message("no row for the %s role" % role))

    def role_row(self, role):
        return self.rows[self.index_of(role)]

    def cells(self, row):
        """`{label: text}` for one role row, split at the head positions."""
        edges = self.columns + [("", len(row) + 1)]
        return {label: row[start:edges[index + 1][1] - 1].strip()
                for index, (label, start) in enumerate(self.columns)}

    def model_of(self, role):
        return self.cells(self.role_row(role))["Model"]

    def effort_of(self, role):
        return self.cells(self.role_row(role))["Effort"]

    def guidance(self, role):
        """The one-line reason under a role's row (ACC-MODEL-002)."""
        index = self.index_of(role) + 1
        if index >= len(self.rows):
            raise AssertionError(self.message("no guidance row under %s" % role))
        row = self.rows[index]
        if not row.startswith("  ") or not row.strip():
            raise AssertionError(self.message(
                "the row under %s is not an indented reason: %r" % (role, row)))
        return row.strip()

    def toggle_index(self, label):
        for index, row in enumerate(self.rows):
            if row.strip().startswith(label):
                return index
        raise AssertionError(self.message("no row for the %r toggle" % label))

    def toggle_row(self, label):
        return self.rows[self.toggle_index(label)]

    def toggle_state(self, label):
        return self.toggle_row(label).strip()[len(label):].strip()

    def screen_row(self, index):
        """The absolute frame row a body row is on, for attribute assertions."""
        return self.top + 1 + index

    def marker(self, pattern=r"\+(\d+) more"):
        for row in self.rows:
            found = re.search(pattern, row)
            if found:
                return int(found.group(1))
        return 0

    def message(self, reason):
        return self.frame._message("Models view: %s" % reason)


def open_models(term):
    """Switch to the Models view and return the frame it painted."""
    return term.send("M", expect=OPENED)


def models_frame(relay_dir, size=WIDE, **kwargs):
    term = session(relay_dir, size=size, **kwargs)
    try:
        return open_models(term)
    finally:
        term.close()


def view_of(relay_dir, size=WIDE, **kwargs):
    return View(models_frame(relay_dir, size=size, **kwargs))


# --------------------------------------------------------------------------
# figures, read from the fixture's own files at assert time
# --------------------------------------------------------------------------


AGENT_SERVICE = FIXTURES / "agent-service"
CONFIGURED = FIXTURES / "models-configured"


def dashboard_of(relay_dir):
    path = Path(relay_dir) / "dashboard.json"
    return json.loads(path.read_text()) if path.exists() else {}


def pinned_models(relay_dir):
    """`{role: (model, effort)}` as the fixture's own `dashboard.json` writes it."""
    written = dashboard_of(relay_dir).get("models") or {}
    return {role: (entry.get("model"), entry.get("effort"))
            for role, entry in written.items() if isinstance(entry, dict)}


def written_toggles(relay_dir):
    return dashboard_of(relay_dir).get("toggles") or {}


def relay_with(tmp_path, dashboard, name="relay"):
    """A relay directory whose `dashboard.json` is exactly `dashboard`.

    A fixture is a relay dir, never a directory containing `.relay/` — that
    path is gitignored at any depth (`.relay/skills/relay-fixtures.md`).
    """
    directory = tmp_path / name
    directory.mkdir()
    (directory / "dashboard.json").write_text(json.dumps(dashboard))
    (directory / "legs.json").write_text(json.dumps({
        "stages": [{"id": "S1", "name": "Only stage"}],
        "legs": [{"id": "only-leg", "stage": "S1", "kind": "impl",
                  "goal": "Exist.", "status": "running"}],
    }))
    return directory


def skill_guidance():
    """`{role: what it needs}` parsed out of SKILL.md's Models table.

    ACC-MODEL-002 says the view's one-line reason must *match* that table, so
    the table is the expectation and this file reads it at assert time. A copy
    of the sentences here would agree with a view that had drifted from the
    document it is quoting.
    """
    text = (REPO / "SKILL.md").read_text()
    found = {}
    for row in re.findall(r"^\|\s*(Coach|Runner|Judge)\s*\|(.+?)\|\s*$",
                          text, re.M):
        found[row[0]] = row[1].strip()
    assert set(found) == set(ROLES), (
        "SKILL.md's Models table no longer names %r; it names %r"
        % (list(ROLES), sorted(found)))
    return found


def significant(text):
    """The words of `text` long enough to carry its meaning, lowercased."""
    return {word.lower() for word in re.findall(r"[A-Za-z-]{5,}", text)}


# --------------------------------------------------------------------------
# ACC-MODEL-001 — the three roles, their models and their efforts
# --------------------------------------------------------------------------


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_m_opens_a_view_listing_the_three_relay_roles_in_order(size):
    view = view_of(AGENT_SERVICE, size=size)
    drawn = [role for role in ROLES if any(
        re.match(r"%s(?!\S)" % role, row) for row in view.rows)]
    assert drawn == list(ROLES), view.message(
        "the roles drawn are %r, not %r" % (drawn, list(ROLES)))
    positions = [view.index_of(role) for role in ROLES]
    assert positions == sorted(positions), view.message(
        "the roles are out of order: %r" % positions)
    assert view.roles_offered == len(ROLES), view.message(
        "the title offers %d roles" % view.roles_offered)


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_the_column_heads_name_the_role_its_model_and_its_effort(size):
    view = view_of(CONFIGURED, size=size)
    assert view.labels == list(COLUMNS), view.message(
        "the column heads are %r, not %r" % (view.labels, list(COLUMNS)))


def test_a_relay_with_no_models_key_reads_not_configured_for_every_role():
    """This repository's own `dashboard.json` has no `models` key, and so does
    the agent-service fixture. That is the common case, and it has to read as a
    deliberate screen rather than as a broken one: every role is named, every
    role carries its reason, and the cell says in words that nothing was
    written."""
    assert "models" not in dashboard_of(AGENT_SERVICE), (
        "this test needs a fixture with no `models` key")

    view = view_of(AGENT_SERVICE)
    for role in ROLES:
        assert view.model_of(role) == NOT_CONFIGURED, view.message(
            "%s reads %r rather than %r"
            % (role, view.model_of(role), NOT_CONFIGURED))
    assert view.configured == 0, view.message(
        "the title claims %d roles are configured" % view.configured)
    assert "0/0" not in view.text and "1-0" not in view.text, view.message(
        "emptiness is stated in words, never as a filler figure")
    # Nothing written is not the same as something wrong: a key nobody wrote
    # may not raise the alarm a mistyped one does, or the alarm means nothing
    # on the screen a supervisor sees every day.
    assert UNREADABLE not in view.text, view.message(
        "a relay that configured nothing is reported as unreadable")
    assert FAILED not in view.text, view.message(
        "a failure mark is drawn on a screen with nothing wrong with it")


def test_a_fixture_with_explicit_models_shows_exactly_those_values():
    written = pinned_models(CONFIGURED)
    assert set(written) == {"coach", "runner", "judge"}, (
        "this fixture must pin all three roles")

    view = view_of(CONFIGURED)
    for role in ROLES:
        model, effort = written[role.lower()]
        assert view.model_of(role) == model, view.message(
            "%s reads %r; dashboard.json says %r"
            % (role, view.model_of(role), model))
        assert view.effort_of(role) == effort, view.message(
            "%s's effort reads %r; dashboard.json says %r"
            % (role, view.effort_of(role), effort))
    assert view.configured == len(ROLES), view.message(
        "three roles are pinned and the title says %d" % view.configured)


def test_a_role_written_as_a_bare_model_name_is_read_as_that_model(tmp_path):
    """`"coach": "Opus 4.6"` is a spelling a coach will use, and it says
    unambiguously which model. It is read, not called malformed."""
    relay = relay_with(tmp_path, {"models": {"coach": "Opus 4.6"}})
    view = view_of(relay)
    assert view.model_of("Coach") == "Opus 4.6", view.message(
        "a bare model name read as %r" % view.model_of("Coach"))
    assert view.configured == 1, view.message(
        "the title counts %d configured roles" % view.configured)


def test_a_role_with_no_effort_shows_one_dim_mark_not_a_blank(tmp_path):
    relay = relay_with(tmp_path, {"models": {
        "coach": {"model": "Opus 4.6", "effort": "high"},
        "runner": {"model": "Sonnet 4.5"},
    }})
    view = view_of(relay)
    assert view.effort_of("Coach") == "high"
    assert view.effort_of("Runner") == ABSENT, view.message(
        "an unwritten effort reads %r, not the absence mark"
        % view.effort_of("Runner"))


def test_an_effort_column_no_role_fills_is_dropped_rather_than_drawn_empty(
        tmp_path):
    """A column whose every row is the marker is not a column, it is a
    statement about the relay — the rule `runners-view` states as ACC-RUN-003,
    one view over."""
    relay = relay_with(tmp_path, {"models": {
        "coach": {"model": "Opus 4.6"},
        "runner": {"model": "Sonnet 4.5"},
        "judge": {"model": "GPT-5.1"},
    }})
    view = view_of(relay)
    assert "Effort" not in view.labels, view.message(
        "an empty Effort column was drawn: %r" % view.labels)
    assert view.labels == ["Role", "Model"], view.message(
        "the heads are %r" % view.labels)
    for role in ROLES:
        assert ABSENT not in view.role_row(role), view.message(
            "%s still carries an absence mark: %r" % (role, view.role_row(role)))


# --------------------------------------------------------------------------
# ACC-MODEL-002 — the one-line reason each role wants that kind of model
# --------------------------------------------------------------------------


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_every_role_carries_its_own_one_line_reason(size):
    view = view_of(AGENT_SERVICE, size=size)
    lines = {role: view.guidance(role) for role in ROLES}
    for role, line in lines.items():
        assert len(line) > 20, view.message(
            "%s's reason is %r" % (role, line))
    assert len(set(lines.values())) == len(ROLES), view.message(
        "two roles share a reason: %r" % lines)


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_the_judge_line_says_it_must_be_a_different_provider(size):
    """ACC-MODEL-002's evidence: the frame contains the judge's
    different-provider line. The phrase has to survive on one row, because a
    reason folded across two rows is not one the frame can be read for."""
    view = view_of(AGENT_SERVICE, size=size)
    line = view.guidance("Judge")
    assert "different provider from the runner" in line, view.message(
        "the judge's reason reads %r" % line)
    for role in ("Coach", "Runner"):
        assert "different provider" not in view.guidance(role), view.message(
            "%s's reason claims the judge's requirement" % role)


def test_each_reason_matches_the_models_table_in_skill_md():
    """The document is the expectation, parsed here at assert time.

    Each row of SKILL.md's Models table says what that role needs; the view's
    line has to be that sentence and not a different opinion about the role.
    """
    table = skill_guidance()
    view = view_of(AGENT_SERVICE)
    for role in ROLES:
        shared = significant(view.guidance(role)) & significant(table[role])
        assert len(shared) >= 3, view.message(
            "%s's reason %r shares only %r with SKILL.md's %r"
            % (role, view.guidance(role), sorted(shared), table[role]))
    assert "provider" in significant(table["Judge"]), (
        "SKILL.md's judge row no longer mentions the provider")


def test_the_reason_survives_a_narrow_terminal_as_a_marked_cut():
    """At 40 columns the reasons do not fit. What a pane may never do is drop
    the tail silently — the cut is marked, once, in the theme's own ellipsis."""
    view = view_of(AGENT_SERVICE, size=(24, 40))
    for role in ROLES:
        line = view.guidance(role)
        assert line, view.message("%s lost its reason entirely" % role)
        if not line.endswith(ELLIPSIS):
            continue
        assert line.count(ELLIPSIS) == 1, view.message(
            "%s's reason is marked cut twice: %r" % (role, line))
    cut = [role for role in ROLES if view.guidance(role).endswith(ELLIPSIS)]
    assert cut, view.message("nothing was cut at 40 columns, so nothing is proved")


# --------------------------------------------------------------------------
# ACC-MODEL-003 — the toggles, and that they are read-only
# --------------------------------------------------------------------------


#: What ACC-MODEL-003 calls the pair, and therefore what has to label them.
TOGGLES_HEADING = "Experimental toggles"


@pytest.mark.parametrize("size", [WIDE, STANDARD])
def test_both_experimental_toggles_are_listed_under_a_heading_that_names_them(
        size):
    view = view_of(CONFIGURED, size=size)
    order = [view.toggle_index(label) for label in TOGGLES]
    assert order == sorted(order), view.message(
        "the toggles are out of order: %r" % order)
    heading = [index for index, row in enumerate(view.rows)
               if row.strip() == TOGGLES_HEADING]
    assert heading, view.message(
        "no %r heading over the toggles" % TOGGLES_HEADING)
    assert heading[0] < order[0], view.message(
        "the heading is at %d and the first toggle at %d" % (heading[0], order[0]))


def test_each_toggle_shows_the_state_dashboard_json_gives_it():
    written = written_toggles(CONFIGURED)
    assert set(written.values()) == {True, False}, (
        "this fixture must write one toggle on and one off")

    view = view_of(CONFIGURED)
    for key, label in zip(("skipCodeJudge", "skipBehaviourJudge"), TOGGLES):
        expected = "on" if written[key] else "off"
        assert view.toggle_state(label) == expected, view.message(
            "%s reads %r; dashboard.json says %r"
            % (label, view.toggle_state(label), written[key]))


def test_a_toggle_nobody_wrote_says_so_rather_than_claiming_a_state():
    """An unwritten toggle is off — but "off" and "off because nobody wrote it"
    are different facts, and a screen that spells them the same way invents the
    first one."""
    assert "toggles" not in dashboard_of(AGENT_SERVICE), (
        "this test needs a fixture with no toggles written")
    view = view_of(AGENT_SERVICE)
    for label in TOGGLES:
        assert view.toggle_state(label) == DEFAULT_OFF, view.message(
            "%s reads %r, not %r"
            % (label, view.toggle_state(label), DEFAULT_OFF))


def test_enter_states_that_the_tui_is_read_only_and_names_the_file():
    term = session(CONFIGURED)
    try:
        open_models(term)
        frame = term.send("<Enter>", expect=MESSAGE)
        frame.assert_finished()
        view = View(frame)
        assert "dashboard.json" in view.text, view.message(
            "the read-only message never names the file to edit")
        assert TOGGLES[0] in view.text
        said = [index for index, row in enumerate(view.rows)
                if "read-only" in row]
        assert len(said) == 1, view.message(
            "the read-only message is drawn %d times" % len(said))
        assert TOGGLES[0] in view.rows[said[0]], view.message(
            "the message does not name the toggle Enter was pressed on: %r"
            % view.rows[said[0]])
        # The answer goes under the thing it is about, and it does not cost the
        # view its content: everything that was on screen before is still there.
        assert said[0] > view.toggle_index(TOGGLES[-1]), view.message(
            "the message is drawn at body row %d, above the toggles at %d"
            % (said[0], view.toggle_index(TOGGLES[-1])))
        for role in ROLES:
            assert view.model_of(role), view.message(
                "%s's row went to make room for the message" % role)
        for label in TOGGLES:
            assert view.toggle_state(label), view.message(
                "%s went to make room for the message" % label)
    finally:
        term.close()


def test_enter_leaves_dashboard_json_exactly_as_it_found_it(tmp_path):
    """ACC-MODEL-003's second half, and the one that matters: the dashboard is
    a view, never a gate. Asserted on a copy so that a write would be visible
    rather than destructive."""
    relay = tmp_path / "models-configured"
    shutil.copytree(CONFIGURED, relay)
    path = relay / "dashboard.json"
    before, mtime = path.read_bytes(), os.stat(path).st_mtime_ns

    term = session(relay)
    try:
        open_models(term)
        term.send("<Enter>", expect=MESSAGE)
        term.send("<Down>", expect=OPENED)
        term.send("<Enter>", expect=MESSAGE)
    finally:
        term.close()

    assert path.read_bytes() == before, "the TUI rewrote dashboard.json"
    assert os.stat(path).st_mtime_ns == mtime, "the TUI touched dashboard.json"


def read_only_line(frame):
    """The one row carrying the read-only message, or a loud failure."""
    view = View(frame)
    said = [row.strip() for row in view.rows if "read-only" in row]
    assert len(said) == 1, view.message(
        "the read-only message is drawn %d times: %r" % (len(said), said))
    return said[0]


def test_the_selection_moves_between_the_toggles_and_enter_names_the_one_on_it():
    """Down, Enter, Up, Enter — and the message names a different toggle each
    time. Without this the selection could be a decoration."""
    term = session(CONFIGURED)
    try:
        open_models(term)
        # One key per send: a repaint the program finished after the *first*
        # of several keys can end a wait, and the frame it hands back is then
        # a screen from the middle of the script.
        term.send("<Down>", expect=OPENED)
        second = read_only_line(term.send("<Enter>", expect=MESSAGE))
        assert second.startswith(TOGGLES[1]), (
            "Down then Enter named %r" % second)
        term.send("<Up>", expect=OPENED)
        first = read_only_line(term.send("<Enter>", expect=MESSAGE))
        assert first.startswith(TOGGLES[0]), (
            "Up then Enter named %r" % first)
    finally:
        term.close()


def test_the_selection_does_not_run_off_either_end_of_the_toggles():
    """Clamped, not wrapped, and never out of range: an index past the last
    toggle would be an IndexError in the middle of a repaint."""
    term = session(CONFIGURED)
    try:
        open_models(term)
        for _ in range(6):
            term.send("<Down>", expect=OPENED)
        frame = term.send("<Enter>", expect=MESSAGE)
        frame.assert_finished()
        frame.assert_not_contains("Traceback")
        assert read_only_line(frame).startswith(TOGGLES[-1])
        for _ in range(6):
            term.send("<Up>", expect=OPENED)
        frame = term.send("<Enter>", expect=MESSAGE)
        frame.assert_not_contains("Traceback")
        assert read_only_line(frame).startswith(TOGGLES[0])
    finally:
        term.close()


def test_moving_the_selection_takes_back_a_message_about_the_other_row():
    """The message names one toggle. Leaving it on screen while the keyboard
    has moved on makes it a statement about a row nobody is on."""
    term = session(CONFIGURED)
    try:
        open_models(term)
        term.send("<Enter>", expect=MESSAGE)
        frame = term.send("<Down>", expect=OPENED)
        frame.assert_finished()
        frame.assert_not_contains(MESSAGE)
    finally:
        term.close()


def test_the_row_the_keyboard_is_on_is_highlighted_across_the_pane():
    """The highlight is a row, not a word: reverse video that stops where its
    text does reads as an emphasised phrase rather than as a cursor."""
    frame = models_frame(CONFIGURED)
    view = View(frame)
    on_row = view.screen_row(view.toggle_index(TOGGLES[0]))
    off_row = view.screen_row(view.toggle_index(TOGGLES[1]))
    assert (frame.attrs_for(TOGGLES[0], row=on_row)
            != frame.attrs_for(TOGGLES[1], row=off_row)), frame._message(
        "the selected toggle is drawn exactly like the unselected one")

    run = frame.run_with(TOGGLES[0], row=on_row)
    assert run.start == view.left, frame._message(
        "the highlight starts at %d, the pane's left edge at %d"
        % (run.start, view.left))
    assert run.end >= frame.cols - 1, frame._message(
        "the highlight ends at %d of %d columns" % (run.end, frame.cols))
    assert run.end <= frame.cols - 1, frame._message(
        "the highlight runs into the reserved last column")
    other = frame.run_with(TOGGLES[1], row=off_row)
    assert other.end < run.end, frame._message(
        "the unselected row is padded like the selected one")


def test_esc_returns_to_the_overview_and_q_still_quits():
    """A `handle()` that fell out of its `if` with `return True` swallows Tab,
    Esc and q, and the view becomes a room with no door. Nothing else kills
    that mutation."""
    term = session(CONFIGURED)
    try:
        open_models(term)
        frame = term.send("<Esc>", expect="Active Leg")
        frame.assert_finished()
        assert frame.search(r"Models \(\d+/\d+\)") is None, frame._message(
            "Esc did not leave the Models view")
        open_models(term)
        term.send("q")
        assert term.wait(timeout=5) == 0, "q did not quit from the Models view"
    finally:
        term.close()


def test_the_pane_says_it_is_read_only_before_anything_is_pressed():
    view = view_of(CONFIGURED)
    assert "read-only" in view.meta, view.message(
        "the pane's own figure reads %r" % view.meta)


# --------------------------------------------------------------------------
# untrusted input — the coach wrote it by hand, so assume they mistyped it
# --------------------------------------------------------------------------


def test_a_role_written_as_a_list_is_named_unreadable_not_rendered(tmp_path):
    """The defect this guards: a view that spells a malformed entry as though
    it were valid tells the coach their typo worked."""
    relay = relay_with(tmp_path, {"models": {"runner": ["Sonnet 4.5"]}})
    view = view_of(relay)
    cell = view.model_of("Runner")
    assert UNREADABLE in cell, view.message(
        "a list-shaped entry reads %r" % cell)
    assert "list" in cell, view.message(
        "the cell does not say what was written instead: %r" % cell)
    assert "Sonnet" not in view.text, view.message(
        "a malformed entry was rendered as a model name anyway")
    assert view.configured == 0, view.message(
        "the title counted an unreadable entry as configured")


def test_a_model_field_of_the_wrong_type_is_named_unreadable(tmp_path):
    relay = relay_with(tmp_path, {"models": {
        "judge": {"model": 5, "effort": "high"}}})
    view = view_of(relay)
    cell = view.model_of("Judge")
    assert UNREADABLE in cell and "model" in cell and "int" in cell, (
        view.message("a numeric model name reads %r" % cell))
    assert not re.search(r"(?<!\d)5(?!\d)", cell), view.message(
        "the typo was rendered as the model: %r" % cell)
    # The field beside it is readable and is still read.
    assert view.effort_of("Judge") == "high", view.message(
        "a bad `model` took the readable `effort` down with it")


def test_an_effort_field_of_the_wrong_type_does_not_spoil_the_model(tmp_path):
    relay = relay_with(tmp_path, {"models": {
        "coach": {"model": "Opus 4.6", "effort": ["high"]}}})
    view = view_of(relay)
    assert view.model_of("Coach") == "Opus 4.6", view.message(
        "a bad `effort` took the readable `model` down with it: %r"
        % view.model_of("Coach"))
    cell = view.effort_of("Coach")
    assert UNREADABLE in cell and "effort" in cell, view.message(
        "a list-shaped effort reads %r" % cell)


def test_a_models_key_that_is_not_an_object_is_named_rather_than_ignored(
        tmp_path):
    """Silently reading `not configured` off a `models` the coach did write is
    the same lie one level up."""
    relay = relay_with(tmp_path, {"models": ["coach", "runner"]})
    view = view_of(relay)
    said = [row.strip() for row in view.rows
            if "models" in row and UNREADABLE in row]
    assert said, view.message("a list-shaped `models` was ignored in silence")
    assert "list" in said[0], view.message(
        "the note does not say what was written: %r" % said[0])
    for role in ROLES:
        assert view.model_of(role) == NOT_CONFIGURED


def test_a_toggle_written_as_a_string_is_unreadable_not_on(tmp_path):
    relay = relay_with(tmp_path, {"toggles": {"skipCodeJudge": "yes"}})
    view = view_of(relay)
    state = view.toggle_state(TOGGLES[0])
    assert UNREADABLE in state and "str" in state, view.message(
        "`\"yes\"` reads %r" % state)
    assert state.startswith(FAILED), view.message(
        "the unreadable state carries no failure mark: %r" % state)
    assert state.split()[-1] not in ("on", "off"), view.message(
        "a string was read as a boolean: %r" % state)


def test_a_toggles_key_that_is_not_an_object_is_named_rather_than_ignored(
        tmp_path):
    relay = relay_with(tmp_path, {"toggles": "skipCodeJudge"})
    view = view_of(relay)
    said = [row.strip() for row in view.rows
            if "toggles" in row and UNREADABLE in row]
    assert said, view.message("a string-shaped `toggles` was ignored in silence")
    assert "str" in said[0], view.message("the note reads %r" % said[0])


def test_a_model_name_of_prose_cannot_size_the_column_past_its_cap(tmp_path):
    """A table sizes its columns from the model, and the model is coach prose
    verbatim (ACC-DATA-001/007). Two hundred characters in `model` must not
    leave the rest of the grid nothing."""
    relay = relay_with(tmp_path, {"models": {
        "coach": {"model": "x" * 200, "effort": "high"},
        "runner": {"model": "Sonnet 4.5", "effort": "medium"},
    }})
    view = view_of(relay)
    assert view.labels == list(COLUMNS), view.message(
        "prose in one cell cost the table its columns: %r" % view.labels)
    cell = view.model_of("Coach")
    assert cell.endswith(ELLIPSIS), view.message(
        "the long name was cut without a mark: %r" % cell)
    assert len(cell) <= 32, view.message(
        "the Model column grew to %d cells" % len(cell))
    assert view.effort_of("Runner") == "medium", view.message(
        "the Effort column was pushed off the screen by one long name")
    view.frame.assert_within_width()


def test_a_reasoning_effort_of_prose_cannot_size_its_column_either(tmp_path):
    """`effort` is coach prose too, and it is the *last* column: uncapped, it
    runs the row to the terminal's edge and takes the table with it."""
    relay = relay_with(tmp_path, {"models": {
        "coach": {"model": "Opus 4.6", "effort": "y" * 200},
        "runner": {"model": "Sonnet 4.5", "effort": "medium"},
    }})
    view = view_of(relay)
    assert view.labels == list(COLUMNS), view.message(
        "prose in one effort cell cost the table its columns: %r" % view.labels)
    cell = view.effort_of("Coach")
    assert cell.endswith(ELLIPSIS), view.message(
        "the long effort was cut without a mark: %r" % cell)
    assert len(cell) <= 20, view.message(
        "the Effort column grew to %d cells" % len(cell))
    assert view.model_of("Runner") == "Sonnet 4.5", view.message(
        "one long effort pushed the Model column off the screen")
    view.frame.assert_within_width()
    assert not view.frame.full_width_rows(), view.frame._message(
        "rows %r reach the reserved last column"
        % (view.frame.full_width_rows(),))


#: A model name a coach pasted out of a terminal. `\x1b[31m` is an SGR colour
#: sequence, `\r` returns the cursor to the window's left margin, and `\x00`
#: used to take the TUI down with a `ValueError` from `addstr`.
POISONED = "Son\x1b[31mnet\r\x00 4.5"

#: The same name with every control character replaced by an ordinary one, so
#: that the two relays differ in *what* those cells say and in nothing else —
#: same length, same column widths, same table.
CLEAN = POISONED.replace("\x1b", "E").replace("\r", "R").replace("\x00", "N")


def test_a_control_character_in_a_model_name_costs_exactly_one_cell(tmp_path):
    """`chrome.Canvas.write()` replaces every control character with a one-cell
    mark (ACC-ROBUST-006). The claim asserted here is the width one: the same
    relay with clean prose and with poisoned prose draws rows of identical
    length, exactly one row differs, and no caret notation reaches the screen."""
    assert len(CLEAN) == len(POISONED)
    clean = relay_with(tmp_path, {"models": {
        "runner": {"model": CLEAN, "effort": "medium"}}}, name="clean")
    dirty = relay_with(tmp_path, {"models": {
        "runner": {"model": POISONED, "effort": "medium"}}}, name="dirty")

    good = models_frame(clean)
    poisoned = models_frame(dirty)
    poisoned.assert_finished()
    poisoned.assert_within_width()
    assert "Traceback" not in poisoned.text
    assert "^[" not in poisoned.text and "^?" not in poisoned.text, (
        poisoned._message("a control character reached the screen as caret notation"))
    # Row 0 is chrome's header, which carries the relay's own path — two
    # directories by construction, so it is the one row allowed to differ.
    below = slice(1, None)
    assert ([len(line.rstrip()) for line in good.lines[below]]
            == [len(line.rstrip()) for line in poisoned.lines[below]]), (
        poisoned._message("the poisoned name changed the width of a row"))
    differing = [index for index, (a, b)
                 in enumerate(zip(good.lines[below], poisoned.lines[below]))
                 if a != b]
    assert len(differing) == 1, poisoned._message(
        "a control character took effect on rows %r" % differing)


MALFORMED = [
    {"models": "coach"},
    {"models": 7},
    {"models": {"coach": 4.5, "runner": True, "judge": []}},
    {"models": {"coach": {"model": {"name": "Opus"}}}},
    {"models": {"coach": {}}},
    {"models": {"coach": ""}},
    {"models": {"coach": {"model": "   "}}},
    {"models": {"Coach": {"model": "Opus 4.6"}}},
    {"toggles": []},
    {"toggles": {"skipCodeJudge": 1, "skipBehaviourJudge": None}},
    {"models": None, "toggles": None},
]


@pytest.mark.parametrize("dashboard", MALFORMED,
                         ids=range(len(MALFORMED)))
def test_every_malformed_shape_degrades_without_crashing(tmp_path, dashboard):
    relay = relay_with(tmp_path, dashboard)
    term = session(relay)
    try:
        frame = open_models(term)
        frame.assert_finished()
        frame.assert_within_width()
        frame.assert_not_contains("Traceback")
        assert term.is_running, frame._message("the view took the TUI down")
        view = View(frame)
        for role in ROLES:
            assert view.model_of(role), view.message(
                "%s's cell is blank, which reads as a pane that failed" % role)
    finally:
        term.close()


def test_an_empty_dict_entry_is_not_configured_rather_than_unreadable(tmp_path):
    """`"coach": {}` claims nothing, so it is the absent case and not a typo.
    An entry that names nothing may not be counted as configured either."""
    relay = relay_with(tmp_path, {"models": {"coach": {}}})
    view = view_of(relay)
    assert view.model_of("Coach") == NOT_CONFIGURED, view.message(
        "an empty entry reads %r" % view.model_of("Coach"))
    assert view.configured == 0


# --------------------------------------------------------------------------
# the grid, the reserved last column, and degradation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("size", [WIDE, STANDARD, (30, 100), (24, 60),
                                  (24, 40)])
def test_the_rightmost_column_of_content_starts_where_its_head_starts(size):
    """One assertion that four separate width mutations cannot survive: a cell
    built one cell too wide is clamped by `Pane` and stays invisible to
    `assert_within_width()`, but it moves the column under its head."""
    view = view_of(CONFIGURED, size=size)
    # Asserted rather than skipped: at every size in this list the table has
    # room for at least two columns, and a skip here would quietly turn the
    # narrow cases — the ones the assertion exists for — into no test at all.
    assert len(view.labels) >= 2, view.message(
        "only %r survived at %r" % (view.labels, size))
    for label in view.labels[1:]:
        head = view.column_at(label)
        for role in ROLES:
            row = view.role_row(role)
            cell = view.cells(row)[label]
            if not cell:
                continue
            assert row.index(cell, head - 1) == head, view.message(
                "%s's %s cell starts at %d, its head at %d: %r"
                % (role, label, row.index(cell, head - 1), head, row))


@pytest.mark.parametrize("size", [WIDE, STANDARD, (24, 40), (12, 40), (8, 30)])
def test_no_row_of_the_view_reaches_the_reserved_last_column(size):
    term = session(CONFIGURED, size=size)
    try:
        term.send("M")
        frame = repaint(term)
        frame.assert_finished()
        frame.assert_within_width()
        assert not frame.full_width_rows(), frame._message(
            "rows %r reach the reserved last column" % (frame.full_width_rows(),))
    finally:
        term.close()


#: Every size below is a different branch of the layout, and two of them are
#: the ones that only look redundant. At **four rows** the view is handed a
#: canvas exactly one row tall, which is one less than a pane needs, so
#: `full_pane()` answers `None`; at **four columns** it answers `None` for the
#: width instead, because the pane stops one column short of the margin. Those
#: two are the only sizes at which the `pane is None` check does anything, and
#: without them a view that never made it is indistinguishable from one that
#: did.
DEGRADED = [WIDE, STANDARD, (12, 40), (8, 30), (5, 20), (4, 40), (4, 12),
            (3, 12)]


@pytest.mark.parametrize("size", DEGRADED)
def test_the_view_degrades_at_every_size_it_is_given(size):
    term = session(AGENT_SERVICE, size=size)
    try:
        term.send("M")
        frame = repaint(term)
        frame.assert_finished()
        frame.assert_within_width()
        frame.assert_not_contains("Traceback")
        assert frame.text.strip(), frame._message("nothing was drawn at all")
        assert term.is_running, frame._message("the TUI exited instead")
    finally:
        term.close()


@pytest.mark.parametrize("rows", [24, 16, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5])
def test_a_heading_is_never_the_last_row_the_pane_draws(rows):
    """`Role` labels a table and `Experimental toggles` labels two rows. Either
    one drawn with nothing under it is a heading pointing at nothing — the rule
    the Active Leg pane's `Boundaries` states, two views over."""
    term = session(CONFIGURED, size=(rows, 80))
    try:
        term.send("M")
        frame = repaint(term)
        frame.assert_finished()
        for heading in ("Role", TOGGLES_HEADING):
            index = frame.find(heading)
            if index is None:
                continue
            # `+N more` is not a row under the heading — it is the pane saying
            # the rows the heading labelled are the ones it could not draw.
            below = [line for line in frame.lines[index + 1:frame.rows - 1]
                     if line.strip() and not line.strip().startswith("+")]
            assert below, frame._message(
                "%r is the last row drawn at %d rows" % (heading, rows))
    finally:
        term.close()


def test_a_pane_too_short_for_everything_says_how_much_it_hid():
    """`+N more` counts rows a reader would count as a line, and it owns its
    own row rather than overwriting one."""
    term = session(CONFIGURED, size=(9, 80))
    try:
        frame = open_models(term)
        view = View(frame)
        hidden = view.marker()
        assert hidden, view.message(
            "nothing fits at 9 rows, yet nothing says so: %r" % view.filled)
        drawn = [row for row in view.filled if "+%d more" % hidden not in row]
        # Three roles with a reason each, two toggles, and the two headings
        # over them: what the contract asks for is this many lines, and the
        # marker plus what was drawn has to account for all of them.
        whole = len(ROLES) * 2 + len(TOGGLES) + 2
        assert hidden + len(drawn) >= whole, view.message(
            "%d rows drawn and %d hidden does not account for %d lines"
            % (len(drawn), hidden, whole))
    finally:
        term.close()


def test_a_role_nobody_configured_is_drawn_as_an_absence_not_as_a_value():
    """`not configured` is the documented default, and it has to *look* like
    one: a default drawn in the same ink as a model somebody chose is a value
    this view invented."""
    view = view_of(CONFIGURED)
    frame = view.frame
    pinned = pinned_models(CONFIGURED)["coach"][0]
    absent = view_of(AGENT_SERVICE)
    chosen = frame.attrs_for(pinned, row=view.screen_row(view.index_of("Coach")))
    default = absent.frame.attrs_for(
        NOT_CONFIGURED, row=absent.screen_row(absent.index_of("Coach")))
    assert chosen != default, frame._message(
        "a pinned model and an unpinned one are drawn identically")


def test_an_unreadable_entry_is_drawn_differently_from_a_readable_one(tmp_path):
    """The third state needs a third appearance as well as a third word: a
    typo drawn in body text is a typo that reads as a value."""
    relay = relay_with(tmp_path, {"models": {
        "coach": {"model": "Opus 4.6"}, "runner": ["Sonnet 4.5"]}})
    view = view_of(relay)
    frame = view.frame
    good = frame.attrs_for("Opus 4.6", row=view.screen_row(view.index_of("Coach")))
    bad = frame.attrs_for(UNREADABLE,
                          row=view.screen_row(view.index_of("Runner")))
    assert good != bad, frame._message(
        "an unreadable entry is drawn exactly like a model somebody pinned")
    assert FAILED in view.model_of("Runner"), view.message(
        "the unreadable cell carries no failure mark: %r"
        % view.model_of("Runner"))


def test_the_effort_column_is_judged_over_every_role_and_not_the_first(tmp_path):
    """The first row of this table is exactly the one a coach is likeliest to
    have half-filled, so a column judged on it drops a column two roles below
    still have something to say in."""
    relay = relay_with(tmp_path, {"models": {
        "coach": {"model": "Opus 4.6"},
        "runner": {"model": "Sonnet 4.5"},
        "judge": {"model": "GPT-5.1", "effort": "high"},
    }})
    view = view_of(relay)
    assert "Effort" in view.labels, view.message(
        "the Effort column was dropped although the Judge has one: %r"
        % view.labels)
    assert view.effort_of("Judge") == "high"
    assert view.effort_of("Coach") == ABSENT


def test_the_read_only_message_never_overwrites_a_row_it_shares():
    """Short pane, message showing: the marker, the message and the content
    are three different rows, and the marker still accounts for what went."""
    term = session(CONFIGURED, size=(10, 80))
    try:
        open_models(term)
        frame = term.send("<Enter>", expect=MESSAGE)
        frame.assert_finished()
        view = View(frame)
        said = [index for index, row in enumerate(view.rows)
                if "read-only" in row]
        assert len(said) == 1, view.message("the message is drawn %r" % said)
        assert view.marker(), view.message(
            "nothing fits at 10 rows, and nothing says so")
        marker_rows = [index for index, row in enumerate(view.rows)
                       if re.search(r"\+\d+ more", row)]
        assert said[0] not in marker_rows, view.message(
            "the message and the overflow marker share a row")
        assert TOGGLES[0] in view.rows[said[0]], view.message(
            "the message lost the toggle it names at 10 rows: %r"
            % view.rows[said[0]])
    finally:
        term.close()


@pytest.mark.parametrize("rows", [10, 8, 7, 6, 5])
def test_the_message_and_the_overflow_marker_never_write_over_each_other(rows):
    """One body row and a message in it is the case where a marker and an
    answer both want the same cell. `+1 moreMeasured stage` is what that looks
    like when nobody decided which wins."""
    term = session(CONFIGURED, size=(rows, 40))
    try:
        term.send("M")
        frame = term.send("<Enter>", expect=MESSAGE)
        frame.assert_finished()
        frame.assert_within_width()
        frame.assert_not_contains("Traceback")
        assert not frame.full_width_rows(), frame._message(
            "rows %r reach the reserved last column" % (frame.full_width_rows(),))
        for line in frame.lines:
            assert not re.search(r"\+\d+ more\S", line), frame._message(
                "the marker shares its row with %r" % line.strip())
            assert MESSAGE not in line or "more" not in line, (
                frame._message("the message and the marker share %r"
                               % line.strip()))
    finally:
        term.close()


@pytest.mark.parametrize("written", ["", "   ", "\t"])
def test_a_blank_model_name_claims_nothing_and_is_not_a_typo(tmp_path, written):
    """A key whose value is whitespace says nothing about a model, so it is the
    absent case. Calling it unreadable puts a failure mark on a screen where
    nothing failed; reading it as a name draws an empty cell."""
    relay = relay_with(tmp_path, {"models": {"coach": {"model": written}}})
    view = view_of(relay)
    assert view.model_of("Coach") == NOT_CONFIGURED, view.message(
        "a blank model name reads %r" % view.model_of("Coach"))
    assert UNREADABLE not in view.text and FAILED not in view.text, (
        view.message("whitespace was reported as a typo"))
    assert view.configured == 0


@pytest.mark.parametrize("written", ["yes", 1, 0, [], {"on": True}, 1.0])
def test_only_a_json_boolean_is_read_as_a_toggle_state(tmp_path, written):
    """`1` is a coach reaching for `true` and missing. Reading it as `on`
    reports a judge as skipped on the strength of a typo — and reading `0` as
    `off` is the same coercion wearing the harmless answer's clothes."""
    relay = relay_with(tmp_path, {"toggles": {"skipCodeJudge": written}})
    view = view_of(relay)
    state = view.toggle_state(TOGGLES[0])
    assert UNREADABLE in state, view.message(
        "%r was read as a state: %r" % (written, state))
    assert state.startswith(FAILED), view.message(
        "the unreadable state carries no failure mark: %r" % state)
    assert not state.endswith("on") and not state.endswith("off"), (
        view.message("%r was coerced to a boolean: %r" % (written, state)))
    # The toggle beside it was not written at all, and still says so.
    assert view.toggle_state(TOGGLES[1]) == DEFAULT_OFF


def test_a_column_is_its_widest_cell_and_one_space_and_no_more():
    """Measured off the frame with no width known to this file: the next column
    starts one cell after the widest thing in this one. A table built a cell
    wider than its content moves *every* column together, heads included, so
    "the cells line up under their heads" cannot see it — this can."""
    view = view_of(CONFIGURED)
    labels = view.labels
    assert len(labels) >= 2, view.message("one column proves nothing")
    starts = [view.column_at(label) for label in labels]
    rows = ([view.rows[view.head_index]]
            + [view.role_row(role) for role in ROLES])
    for index in range(len(labels) - 1):
        widest = max(len(row[starts[index]:starts[index + 1]].rstrip())
                     for row in rows)
        assert starts[index + 1] == starts[index] + widest + 1, view.message(
            "the %s column is %d cells wide for content of %d"
            % (labels[index], starts[index + 1] - starts[index], widest))


def test_the_last_cell_of_a_row_is_never_padded(tmp_path):
    """`Canvas.write()` clips with an `…` whenever it cut something — trailing
    spaces included. A row padded out to its column therefore ends in a mark
    saying a value was truncated when the value is whole and only its padding
    was lost. `legs-view` shipped exactly that as `Stage/ID    …`."""
    relay = relay_with(tmp_path, {"models": {
        "coach": {"model": "Opus"},
        "runner": {"model": "Sonnet 4.5"},
    }})
    view = view_of(relay, size=(24, 14))
    row = view.role_row("Coach")
    assert "Opus" in row, view.message("the short name did not survive: %r" % row)
    assert not row.rstrip().endswith(ELLIPSIS), view.message(
        "a whole row is marked cut because its padding did not fit: %r" % row)


def test_a_blank_separator_is_dropped_before_any_line_of_content():
    """Whitespace is the cheapest row in a pane. A pane that kept a gap and
    then wrote `+2 more` hid two lines in order to keep a blank."""
    term = session(CONFIGURED, size=(14, 80))
    try:
        frame = open_models(term)
        frame.assert_finished()
        view = View(frame)
        assert view.marker() == 0, view.message(
            "%d lines were hidden at 14 rows: %r" % (view.marker(), view.filled))
        for role in ROLES:
            assert view.guidance(role), view.message(
                "%s lost its reason to a blank row" % role)
        for label in TOGGLES:
            assert view.toggle_state(label), view.message(
                "%s was hidden to keep a blank row" % label)
    finally:
        term.close()


def test_a_terminal_too_narrow_for_the_table_drops_a_column_rather_than_cutting():
    """Narrowing and dropping are two different answers and they are ordered.
    A row cut by the clipper loses the tail of whatever cell it landed in; a
    column dropped loses one fact and keeps the rest whole."""
    wide = view_of(CONFIGURED)
    assert "Effort" in wide.labels, wide.message(
        "this fixture must have an Effort column for the test to bite")

    view = view_of(CONFIGURED, size=(24, 24))
    assert "Effort" not in view.labels, view.message(
        "the table was cut instead of giving up a column: %r" % view.labels)
    for role in ROLES:
        row = view.role_row(role)
        assert not row.rstrip().endswith(ELLIPSIS), view.message(
            "%s's row was clipped although a column could have gone: %r"
            % (role, row))
        assert view.model_of(role) == pinned_models(CONFIGURED)[role.lower()][0], (
            view.message("%s's model lost its tail: %r" % (role, row)))


@pytest.mark.parametrize("size", [(24, 4), (24, 3), (10, 4), (4, 4)])
def test_a_terminal_too_narrow_for_any_pane_draws_no_view_and_does_not_crash(
        size):
    """The other half of `full_pane()` answering `None`: not too few rows, too
    few columns. A pane stops one column short of the margin, so below five
    columns there is nothing left to put a title in.

    `session()` cannot be used here — it waits for `q Quit`, and the keybar has
    no room for it — so the program is started directly and asked only the
    question this size can answer: did it draw nothing, and stay alive.

    `assert_within_width()` is deliberately not called: at six columns and
    below `chrome.draw_status_bar()` reaches the screen's reserved last column,
    so the strict helper refuses every frame at these sizes whatever a view
    does. That is chrome's row, not this view's — reported in this leg's baton.
    """
    rows, cols = size
    term = TerminalSession([sys.executable, str(ENTRY), str(CONFIGURED)],
                           rows=rows, cols=cols, env=UTF8_ENV)
    term.start()
    try:
        frame = term.send("M")
        frame.assert_not_contains("Traceback")
        frame.assert_not_contains("Error")
        # The weaker of the two width claims, which is the one that still means
        # something here: nothing wrapped.
        assert frame.overlong_lines() == [], frame._message(
            "rows %r are wider than the terminal" % (frame.overlong_lines(),))
        assert term.is_running, frame._message(
            "the TUI exited at %dx%d instead of drawing nothing" % (cols, rows))
        # Rows 0 and 1 are chrome's header and status bar; everything from
        # there down is the view's, and there is no pane it could have drawn.
        assert not any(line.strip() for line in frame.lines[2:]), (
            frame._message("a pane was drawn in %d columns" % cols))
    finally:
        term.close()

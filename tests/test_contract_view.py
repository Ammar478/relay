"""Frame tests for the Contract view (ACC-CONT-001..004).

Every claim below is asserted against a frame captured from a real curses
process under a pty. **Nothing here imports `relay_control.contract` or
`relay_control.theme`**, and nothing here reads a width, an indent, a marker, a
label or a sort order out of the program. A test that took its expectation from
the module it is testing cannot fail when the module changes, and that shape has
dominated this relay: a judge ran 21 mutations against the model and 18 left the
suite green, nearly all of them exactly this. So:

* **Figures come from the fixture's own files at assert time.** `areas_of()` and
  `evidenced()` parse `state.json` here, applying the contract's own rule — *a
  check passes only with the evidence it names* — rather than asking the view
  what it counted.
* **The layout is measured off the screen.** `View` finds the pane's title row
  on the captured frame and reads the indent, the columns and the row kinds out
  of the pixels. The only numbers this file knows are the terminal's own.
* **The spellings asserted are the contract's**, written out as literals on
  purpose: `AREA N/M evidenced`, `N/M passed`, `+N more`, the four state words
  and the five status glyphs ACC-TUI-006 names.
* **The right edge is read, not merely certified.** `assert_within_width()`
  passes on a row built one cell too wide, because `Pane` clamps the write in
  software; `EXACT_FILL` gives the view an evidence string with no word breaks
  in it, so a row that is one cell wide or one cell narrow is visible in what
  the last column says.
"""

import json
import math
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frame import TerminalSession  # noqa: E402

from test_chrome import (  # noqa: E402
    ENTRY, FIXTURES, STANDARD, UTF8_ENV, WIDE, session,
)

#: The four states a check can be shown in — the contract's vocabulary, not the
#: module's. A word on a check row that is not one of these is a truncated word
#: or an invented one.
STATES = ("passed", "failed", "blocked", "pending")

#: The glyphs ACC-TUI-006 names, spelled here rather than read from
#: `theme.GLYPHS` so that a theme change fails a test instead of being silently
#: agreed to. The session forces a UTF-8 locale, so these are the marks the
#: screen carries. `passed` shares `✓` with `completed` and `blocked` shares `✗`
#: with `failed`; that collision is why the state *word* is asserted everywhere
#: below and the glyph alone never is.
GLYPHS = {"passed": "✓", "pending": "○", "failed": "✗", "blocked": "✗"}
ANY_GLYPH = "✓✗○●−"

#: The theme's ellipsis under a UTF-8 locale, and its spelling when the locale
#: cannot encode that. A row cut with neither — with nothing at all — is the
#: silent truncation this package never does. Both are spelled here rather than
#: read from `theme.GLYPHS`, so a theme change fails a test instead of being
#: silently agreed to.
ELLIPSIS = "…"
ASCII_ELLIPSIS = "..."

#: The needle every Contract frame is captured on. It is *not* text the repaint
#: introduces — the Overview's own keybar carries `C Contract` — and it does not
#: have to be: `app.py` brackets each repaint in DEC 2026 and `wait_for` takes
#: its baseline after the delivery barrier, so only a bracket the program closed
#: after it read the key can end the wait. The needle is the trigger for the
#: capture; `paint_end == "synchronised"` is the statement that a repaint
#: happened, and `open_contract()` asserts it on every frame this file takes.
#: See `.relay/skills/driving-a-curses-child.md`.
OPENED = "Contract"

HEADING = re.compile(r"^(?P<area>\S+) (?P<passed>\d+)/(?P<total>\d+) evidenced$")
CHECK = re.compile(r"^(?P<glyph>[%s])  (?P<id>\S+) +(?P<word>\S+)$" % ANY_GLYPH)
MARKER = re.compile(r"^\+(?P<hidden>\d+) more$")


# --------------------------------------------------------------------------
# what the fixture says, read at assert time
# --------------------------------------------------------------------------


def checks_of(relay_dir):
    """`state.json`'s checks, as written. No model, no view."""
    raw = json.loads((Path(relay_dir) / "state.json").read_text())["checks"]
    return {cid: body for cid, body in raw.items()}


def area_of(check_id):
    """The area segment of a check id — `ACC-CRED-001` is `CRED`."""
    parts = check_id.split("-")
    return parts[1] if len(parts) > 2 else "GENERAL"


def is_evidenced(check):
    """The contract's opening rule, applied to the file rather than to the view.

    "A check passes only with the evidence it names. No evidence means blocked,
    not passed." So a check counts towards `N/M evidenced` when it claims
    `passed` *and* names something.
    """
    return (check.get("status") == "passed"
            and bool(str(check.get("evidence") or "").strip()))


def areas_of(relay_dir):
    """`{area: (evidenced, total)}` counted out of the fixture's own file."""
    counts = {}
    for cid, check in checks_of(relay_dir).items():
        done, total = counts.get(area_of(cid), (0, 0))
        counts[area_of(cid)] = (done + int(is_evidenced(check)), total + 1)
    return counts


def overall_of(relay_dir):
    """`(evidenced, total)` over the whole fixture."""
    checks = checks_of(relay_dir)
    return sum(1 for c in checks.values() if is_evidenced(c)), len(checks)


def ids_in_area(relay_dir, area):
    return sorted(cid for cid in checks_of(relay_dir) if area_of(cid) == area)


# --------------------------------------------------------------------------
# building a relay whose checks are whatever the test needs
# --------------------------------------------------------------------------


def checks_relay(directory, checks, leg="the-leg"):
    """A one-leg relay carrying exactly `checks`.

    `state.json` is hand-written in a real relay, which is the whole reason this
    view re-applies the evidence rule: a test can write `"status": "passed"`
    with no evidence here because a coach can write it there.
    """
    directory = Path(directory)
    (directory / "batons").mkdir(parents=True, exist_ok=True)
    (directory / "legs.json").write_text(json.dumps({
        "relay": "contract-fixture",
        "stages": [{"id": "S1", "name": "Stage one", "legs": [leg]}],
        "legs": [{"id": leg, "stage": "S1", "kind": "impl",
                  "goal": "one leg", "fulfills": sorted(checks),
                  "boundaries": ["nothing"], "verification": ["nothing"],
                  "status": "running"}],
    }))
    (directory / "state.json").write_text(json.dumps({
        "relay": "contract-fixture", "phase": "running", "currentStage": "S1",
        "currentLeg": leg, "checks": checks,
    }))
    (directory / "dashboard.json").write_text(json.dumps({
        "title": "Checks fixture", "path": str(directory)}))
    return directory


def passed(evidence):
    return {"status": "passed", "stage": "S1", "claimedBy": "the-leg",
            "round": 1, "evidence": evidence}


def claimed_passed_without_evidence():
    """What a coach writes when a check was ticked and never evidenced."""
    return {"status": "passed", "stage": "S1", "claimedBy": "the-leg",
            "round": 1}


def pending():
    return {"status": "pending", "stage": "S1", "claimedBy": "the-leg"}


def failed(reason, fix_leg):
    return {"status": "failed", "stage": "S1", "claimedBy": "the-leg",
            "round": 2, "reason": reason, "fixLeg": fix_leg}


def blocked(reason):
    return {"status": "blocked", "stage": "S1", "claimedBy": "the-leg",
            "round": 1, "reason": reason}


# --------------------------------------------------------------------------
# reading the view off a captured frame
# --------------------------------------------------------------------------


class View:
    """The Contract view, located by measuring the frame it was drawn on.

    The title row is the row carrying `Contract` at the pane's left edge; the
    body is every row between it and the keybar. Every other position — where a
    check id starts, where its prose is indented to — is read off the rows
    rather than known in advance.
    """

    def __init__(self, frame):
        self.frame = frame
        # The *last* row that begins with the pane's title: the chrome header on
        # row 0 carries the relay's own name, and a relay may legitimately be
        # called something beginning with `Contract`.
        rows = [index for index, line in enumerate(frame.raw_lines)
                if re.match(r"^Contract(\s|$)", line)]
        if not rows:
            raise AssertionError(frame._message("no Contract view on this frame"))
        self.top = rows[-1]
        self.body = [frame.raw_lines[index]
                     for index in range(self.top + 1, frame.rows - 1)]

    # -- the pane's own header -------------------------------------------

    @property
    def title_row(self):
        return self.frame.raw_lines[self.top].rstrip()

    @property
    def meta(self):
        """The pane's right-hand figure — what follows the title."""
        return self.title_row[len("Contract"):].strip()

    # -- the body --------------------------------------------------------

    @property
    def rows(self):
        return [row.rstrip() for row in self.body]

    @property
    def drawn(self):
        """Every body row that has something on it, with its index."""
        return [(index, row) for index, row in enumerate(self.rows) if row]

    def headings(self):
        """`[(row, area, evidenced, total)]` for every group heading drawn."""
        found = []
        for index, row in self.drawn:
            match = HEADING.match(row)
            if match:
                found.append((index, match.group("area"),
                              int(match.group("passed")),
                              int(match.group("total"))))
        return found

    def checks(self):
        """`[(row, glyph, id, word)]` for every check row drawn."""
        found = []
        for index, row in self.drawn:
            match = CHECK.match(row)
            if match:
                found.append((index, match.group("glyph"), match.group("id"),
                              match.group("word")))
        return found

    def check(self, check_id):
        for entry in self.checks():
            if entry[2] == check_id:
                return entry
        raise AssertionError(self.frame._message(
            "no row for %s on this frame" % check_id))

    def prose_under(self, check_id):
        """The indented rows belonging to `check_id`, in the order drawn."""
        start = self.check(check_id)[0]
        out = []
        for index in range(start + 1, len(self.rows)):
            row = self.rows[index]
            if not row or not row.startswith(" "):
                break
            out.append(row)
        return out

    @property
    def marker(self):
        """`N` of the pane's own `+N more`, or None when it drew none.

        The pane's marker starts at column 0; a marker belonging to one check's
        prose is indented under that check, and is not this one.
        """
        for index, row in self.drawn:
            match = MARKER.match(row)
            if match:
                return int(match.group("hidden"))
        return None


def open_contract(term):
    """Switch to the Contract view and hand back a frame the program vouched for."""
    frame = term.send("C", expect=OPENED)
    assert frame.paint_end == "synchronised", frame._message(
        "captured on %r — a repaint this program did not vouch for"
        % frame.paint_end)
    return frame


def contract_frame(relay_dir, size=WIDE):
    term = session(relay_dir, size=size)
    try:
        return open_contract(term)
    finally:
        term.close()


# --------------------------------------------------------------------------
# ACC-CONT-001 — grouped by area, with per-area counts
# --------------------------------------------------------------------------


def test_the_two_groups_the_contract_names_are_drawn_with_their_counts():
    """ACC-CONT-001's own evidence line: `CRED 6/6`, `CUTOVER 0/8`, `25/39 passed`.

    The figures are counted out of `state.json` here rather than typed, because
    the agent-service fixture has been refreshed once already.
    """
    relay = FIXTURES / "agent-service"
    areas = areas_of(relay)
    frame = contract_frame(relay, size=WIDE)
    frame.assert_finished()

    for area in ("CRED", "CUTOVER"):
        done, total = areas[area]
        frame.assert_contains("%s %d/%d" % (area, done, total))
        frame.assert_contains("%s %d/%d evidenced" % (area, done, total))

    done, total = overall_of(relay)
    assert View(frame).meta == "%d/%d passed" % (done, total), frame._message(
        "the pane meta is not the overall evidenced count")


def test_every_group_drawn_is_headed_by_its_own_area_and_its_own_count():
    relay = FIXTURES / "agent-service"
    areas = areas_of(relay)
    view = View(contract_frame(relay, size=WIDE))
    drawn = view.headings()
    assert drawn, "no group heading was drawn at all"
    for _, area, done, total in drawn:
        assert (done, total) == areas[area], \
            "heading %s claims %d/%d, the fixture says %s" % (
                area, done, total, areas[area])
    # Areas run in alphabetical order, and what is drawn is a prefix of them:
    # the pane cuts at the bottom, never in the middle.
    order = [area for _, area, _, _ in drawn]
    assert order == sorted(areas)[:len(order)]


def test_every_check_row_sits_under_the_heading_for_its_own_area():
    view = View(contract_frame(FIXTURES / "agent-service", size=WIDE))
    headings = view.headings()
    for row, _, check_id, _ in view.checks():
        above = [area for index, area, _, _ in headings if index < row]
        assert above, "a check row was drawn above every heading"
        assert above[-1] == area_of(check_id), \
            "%s was drawn under the %s heading" % (check_id, above[-1])


def test_the_group_count_and_the_header_are_the_same_arithmetic(tmp_path):
    """Sum the headings, get the pane's own figure. One fact, drawn twice."""
    relay = checks_relay(tmp_path / "sums", {
        "ACC-AA-001": passed("measured"),
        "ACC-AA-002": pending(),
        "ACC-BB-001": passed("measured"),
        "ACC-BB-002": failed("it did not hold", "fix-bb"),
        "ACC-CC-001": blocked("the environment was not available"),
    })
    view = View(contract_frame(relay, size=WIDE))
    headings = view.headings()
    assert len(headings) == 3
    assert view.meta == "%d/%d passed" % (
        sum(done for _, _, done, _ in headings),
        sum(total for _, _, _, total in headings))


# --------------------------------------------------------------------------
# The honesty rule: never `passed` without the evidence it names
# --------------------------------------------------------------------------


def test_a_check_claiming_passed_with_no_evidence_is_shown_blocked(tmp_path):
    """The one thing this view exists to refuse to do.

    `state.json` says `passed`. There is no evidence. The row must not carry the
    passed glyph, must not carry the word `passed`, and must not be counted into
    either figure — and it must say on the screen that a claim was made, because
    a check that quietly became `blocked` is a figure that quietly shrank.
    """
    relay = checks_relay(tmp_path / "claimed", {
        "ACC-HH-001": passed("a real measurement"),
        "ACC-HH-002": claimed_passed_without_evidence(),
        "ACC-HH-003": pending(),
    })
    frame = contract_frame(relay, size=WIDE)
    frame.assert_finished()
    view = View(frame)

    _, glyph, _, word = view.check("ACC-HH-002")
    assert word == "blocked", "a check with no evidence was shown as %r" % word
    assert glyph == GLYPHS["blocked"]
    assert glyph != GLYPHS["passed"]

    # It is out of both figures, and the divergence is named rather than lost.
    assert view.headings() == [(0, "HH", 1, 3)]
    assert "1/3 passed" in view.meta
    assert "1 unevidenced" in view.meta

    said = " ".join(view.prose_under("ACC-HH-002"))
    assert "passed" in said and "no evidence" in said, \
        "the row says nothing about the claim it refused: %r" % said


def test_an_unevidenced_claim_sorts_with_the_blocked_checks_not_the_passed(tmp_path):
    relay = checks_relay(tmp_path / "sorted", {
        "ACC-HH-001": passed("a real measurement"),
        "ACC-HH-002": claimed_passed_without_evidence(),
        "ACC-HH-003": pending(),
    })
    view = View(contract_frame(relay, size=WIDE))
    assert [entry[2] for entry in view.checks()] == [
        "ACC-HH-002", "ACC-HH-003", "ACC-HH-001"]


def test_checks_shown_in_one_state_run_in_id_order(tmp_path):
    """Two checks land in `blocked` by two different routes; id decides which leads.

    One is blocked in `state.json`; one claims `passed` and evidences nothing.
    They reach the view in that order — the model sorts blocked above passed —
    so a view that leant on the order it was handed would draw them the wrong
    way round, and only the id tie-break puts `001` above `009`.
    """
    relay = checks_relay(tmp_path / "tiebreak", {
        "ACC-ZZ-001": claimed_passed_without_evidence(),
        "ACC-ZZ-009": blocked("no staging database was reachable"),
    })
    view = View(contract_frame(relay, size=WIDE))
    assert [(cid, word) for _, _, cid, word in view.checks()] == [
        ("ACC-ZZ-001", "blocked"), ("ACC-ZZ-009", "blocked")]


def test_the_header_says_nothing_about_claims_when_there_are_none(tmp_path):
    """`· N unevidenced` is a statement, not furniture: it appears only when true."""
    relay = checks_relay(tmp_path / "clean", {
        "ACC-HH-001": passed("a real measurement"),
        "ACC-HH-002": pending(),
    })
    frame = contract_frame(relay, size=WIDE)
    frame.assert_finished()
    assert View(frame).meta == "1/2 passed"
    frame.assert_not_contains("unevidenced")


# --------------------------------------------------------------------------
# ACC-CONT-002 — evidence renders as readable wrapped text
# --------------------------------------------------------------------------


def test_the_evidence_the_contract_names_is_wrapped_and_indented():
    """ACC-CONT-002's own evidence line: the multi-line ACC-MERGE-002 evidence."""
    relay = FIXTURES / "agent-service"
    evidence = " ".join(checks_of(relay)["ACC-MERGE-002"]["evidence"].split())
    frame = contract_frame(relay, size=WIDE)
    view = View(frame)

    check_row, _, _, _ = view.check("ACC-MERGE-002")
    lines = view.prose_under("ACC-MERGE-002")
    assert len(lines) > 1, "the evidence was not wrapped onto several rows"

    # Indented *past* where a check id starts, not merely to it. Relay evidence
    # routinely begins with a check id of its own, and a wrapped line starting
    # in the id column would be indistinguishable from a check row that lost
    # its glyph — which is the two-line broken-glyph defect ACC-CONT-003 names,
    # arriving from the other direction.
    id_column = view.rows[check_row].index("ACC-MERGE-002")
    for line in lines:
        assert len(line) - len(line.lstrip()) > id_column, \
            "an evidence row is not indented past the check id column: %r" % line

    # Wrapped, not cut: the rows read back as the evidence the fixture holds.
    assert evidence.startswith(" ".join(" ".join(lines).split()))


def test_evidence_too_long_for_its_block_says_how_many_lines_it_did_not_show(tmp_path):
    """`+N more`, and N is the count of lines a reader would have counted.

    The evidence is one unbroken token, so how many rows it needs is arithmetic
    this test can do for itself off the terminal's width — no wrapping rule is
    borrowed from the program.
    """
    body = "X" * 1000
    relay = checks_relay(tmp_path / "long", {"ACC-LL-001": passed(body)})
    frame = contract_frame(relay, size=WIDE)
    frame.assert_finished()
    view = View(frame)

    lines = view.prose_under("ACC-LL-001")
    assert lines, "no evidence was drawn at all"
    marker = MARKER.match(lines[-1].strip())
    assert marker, "long evidence did not end in a marker: %r" % lines[-1]

    shown = lines[:-1]
    assert shown, "a marker was drawn where a readable line would have fitted"
    indent = len(shown[0]) - len(shown[0].lstrip())
    room = frame.cols - 1 - indent
    needed = math.ceil(len(body) / room)
    assert len(shown) + int(marker.group("hidden")) == needed, (
        "%d rows drawn and %s hidden does not account for the %d the evidence "
        "needs at %d cells" % (len(shown), marker.group("hidden"), needed, room))


#: The most rows the view may spend on one piece of a check's prose, marker
#: included, and the most pieces a check has (its fix leg, its reason, its
#: evidence). The pane holds twenty body rows at 80x24 against thirty-nine
#: checks, so a block that could grow without bound would answer "which checks
#: are unevidenced?" with `+N more`. Both are stated here as literals rather
#: than read from the module: the budget is the property, and a test that
#: imported it could not fail when the budget changed.
MAX_PROSE_ROWS = 3
MAX_PROSE_PIECES = 3


def test_no_check_spends_more_than_its_share_of_the_pane_on_prose(tmp_path):
    """Every block is bounded, and it is the same bound for every check.

    Three checks with one piece of prose each, and one with all three, so the
    per-piece budget and the whole-check ceiling are both stated.
    """
    relay = checks_relay(tmp_path / "budget", {
        "ACC-BB-001": passed("word " * 400),
        "ACC-BB-002": passed("word " * 400),
        "ACC-BB-003": blocked("reason " * 200),
        "ACC-BB-004": dict(failed("reason " * 200, "fix-bb"),
                           evidence="word " * 400),
    })
    single = ("ACC-BB-001", "ACC-BB-002", "ACC-BB-003")
    for size in (WIDE, STANDARD):
        view = View(contract_frame(relay, size=size))
        for _, _, cid, _ in view.checks():
            block = view.prose_under(cid)
            assert block, "%s drew no prose at all at %r" % (cid, size)
            ceiling = (MAX_PROSE_ROWS if cid in single
                       else MAX_PROSE_ROWS * MAX_PROSE_PIECES)
            assert len(block) <= ceiling, (
                "%s spent %d rows on prose at %r; the budget is %d"
                % (cid, len(block), size, ceiling))
        # And the budget really binds — asserted where the pane drew the whole
        # block, since at 80x24 the pane's own cut lands inside it.
        for cid in single:
            block = view.prose_under(cid)
            if len(block) == MAX_PROSE_ROWS:
                assert MARKER.match(block[-1].strip()), \
                    "%s: prose that did not fit was not marked as cut" % cid
    view = View(contract_frame(relay, size=WIDE))
    for cid in single:
        assert len(view.prose_under(cid)) == MAX_PROSE_ROWS, \
            "%s: prose long enough to be cut was not cut" % cid


def test_the_fix_leg_is_not_swept_up_by_a_marker_that_was_not_counting_it(tmp_path):
    """A row below a `+N more` reads as one of the rows the marker counted.

    So the fix leg goes directly under the check id, above anything that can be
    cut. Its position is the claim: a leg id printed after a marker is a leg id
    a reader has no reason to believe is not part of the truncated prose.
    """
    relay = checks_relay(tmp_path / "order", {
        "ACC-BB-001": dict(failed("reason " * 200, "fix-bb"),
                           evidence="word " * 400),
    })
    block = View(contract_frame(relay, size=WIDE)).prose_under("ACC-BB-001")
    assert block[0].strip() == "fix leg: fix-bb", \
        "the fix leg is not the first thing under the id: %r" % block[:2]
    cut = [index for index, row in enumerate(block) if MARKER.match(row.strip())]
    assert cut, "nothing in this block was cut, so the ordering proves nothing"
    assert cut[0] > 0


def test_an_evidence_row_fills_the_pane_and_stops_one_column_short(tmp_path):
    """The width claim read off the last column, which is the only thing that sees it.

    `assert_within_width()` cannot catch a row built one cell too wide — `Pane`
    clamps the write in software and the helper stays green — so this asserts
    what the row *says* at the margin. The evidence has no word breaks in it, so
    a full row is exactly `room` X's: one cell too wide and `Canvas.write()`
    clips it and leaves an ellipsis; one cell too narrow and the row stops a
    column early.
    """
    relay = checks_relay(tmp_path / "edge", {"ACC-EE-001": passed("X" * 1000)})
    for size in (WIDE, STANDARD, (12, 40)):
        frame = contract_frame(relay, size=size)
        frame.assert_finished()
        frame.assert_within_width()
        view = View(frame)
        full = [row for row in view.prose_under("ACC-EE-001")
                if row.strip().startswith("X")]
        assert full, "no evidence row at %r" % (size,)
        for row in full:
            assert ELLIPSIS not in row, \
                "an evidence row was clipped by the canvas: %r" % row
            assert set(row.strip()) == {"X"}, \
                "an evidence row carries something the fixture did not: %r" % row
            assert len(row) == frame.cols - 1, (
                "an evidence row ends at column %d; the pane's last usable "
                "column is %d" % (len(row) - 1, frame.cols - 2))
        # And the reserved column really is empty.
        assert frame.raw_lines[View(frame).top + 1 + view.rows.index(full[0])][-1] == " "


def test_a_row_too_wide_for_the_pane_is_cut_once_with_the_themes_ellipsis(tmp_path):
    """A check id nothing can shorten still leaves a mark where it was cut."""
    long_id = "ACC-EE-" + "B" * 60
    relay = checks_relay(tmp_path / "wide", {long_id: passed("short")})
    frame = contract_frame(relay, size=(12, 40))
    frame.assert_finished()
    frame.assert_within_width()

    row = [line for line in View(frame).rows if line.startswith(GLYPHS["passed"])]
    assert row, frame._message("no check row was drawn")
    assert row[0].endswith(ELLIPSIS), \
        "a row was cut with no mark at all: %r" % row[0]
    assert len(row[0]) == frame.cols - 1, \
        "a cut row does not reach the pane's last usable column: %r" % row[0]


def test_a_cut_row_is_still_marked_where_the_locale_cannot_encode_the_mark(tmp_path):
    """The one screen that separates the view's own cut from the canvas's.

    `Canvas.write()` clips an over-long segment with `chrome.clip()`'s literal
    `…` default, and under a locale that cannot encode it curses drops that cell
    to a blank — a truncation wearing no mark at all. Under UTF-8 the two are
    indistinguishable, so this is the only place the difference is observable:
    the view cuts the row itself, with the theme's own ellipsis, which has an
    ASCII spelling.
    """
    long_id = "ACC-EE-" + "B" * 60
    relay = checks_relay(tmp_path / "ascii", {long_id: passed("short")})
    env = {key: value for key, value in os.environ.items()
           if key not in ("LC_ALL", "LC_CTYPE", "LANG")}
    env["LC_ALL"] = "C"
    term = TerminalSession(
        [sys.executable, str(ENTRY), str(relay)], rows=12, cols=40, env=env)
    term.start()
    term.wait_for("q Quit")
    try:
        frame = term.send("C", expect=OPENED)
    finally:
        term.close()
    frame.assert_finished()
    frame.assert_within_width()

    row = next((line.rstrip() for line in frame.raw_lines
                if "ACC-EE-BBB" in line), None)
    assert row is not None, frame._message("no check row was drawn")
    assert ELLIPSIS not in row, \
        "a UTF-8 mark reached an ASCII terminal: %r" % row
    assert row.endswith(ASCII_ELLIPSIS), \
        "a row was cut with no mark at all under LC_ALL=C: %r" % row
    assert len(row) == frame.cols - 1, \
        "a cut row does not reach the pane's last usable column: %r" % row


# --------------------------------------------------------------------------
# ACC-CONT-003 — pending checks render on one line
# --------------------------------------------------------------------------


def test_the_eight_cutover_checks_are_eight_consecutive_single_line_rows():
    """ACC-CONT-003's own evidence line. The two-line broken glyph does not recur."""
    relay = FIXTURES / "agent-service"
    wanted = ids_in_area(relay, "CUTOVER")
    assert len(wanted) == 8, "the fixture no longer has eight CUTOVER checks"

    view = View(contract_frame(relay, size=WIDE))
    drawn = [(row, cid, word) for row, _, cid, word in view.checks()
             if area_of(cid) == "CUTOVER"]
    assert [cid for _, cid, _ in drawn] == wanted
    assert [row for row, _, _ in drawn] == list(
        range(drawn[0][0], drawn[0][0] + 8)), "the eight rows are not consecutive"
    for _, cid, word in drawn:
        assert word == "pending"
        assert view.prose_under(cid) == [], \
            "%s spent a second row on nothing" % cid


def test_no_pending_check_anywhere_on_the_frame_costs_a_second_row():
    view = View(contract_frame(FIXTURES / "agent-service", size=WIDE))
    checks = checks_of(FIXTURES / "agent-service")
    for _, _, cid, word in view.checks():
        if word == "pending" and not str(checks[cid].get("evidence") or "").strip():
            assert view.prose_under(cid) == [], "%s is not one row" % cid


def test_a_pending_row_carries_the_pending_glyph_in_the_pending_colour():
    """The glyph and the word come from one call, so they cannot come apart."""
    frame = contract_frame(FIXTURES / "agent-service", size=WIDE)
    view = View(frame)
    row, glyph, cid, word = next(entry for entry in view.checks()
                                 if entry[3] == "pending")
    assert glyph == GLYPHS["pending"]
    screen_row = view.top + 1 + row
    frame.assert_attrs(GLYPHS["pending"], row=screen_row, fg=37, has="dim")
    frame.assert_attrs("pending", row=screen_row, fg=37, has="dim")


# --------------------------------------------------------------------------
# ACC-CONT-004 — failed checks lead, and name their fix leg
# --------------------------------------------------------------------------


@pytest.fixture
def four_states(tmp_path):
    """One area holding one check in each of the four states."""
    return checks_relay(tmp_path / "states", {
        "ACC-XX-001": passed("a real measurement"),
        "ACC-XX-002": pending(),
        "ACC-XX-003": failed("the write path still accepts a bare string",
                             "fix-the-write-path"),
        "ACC-XX-004": dict(blocked("no staging database was reachable"),
                           fixLeg="fix-the-staging-database"),
    })


def test_a_failed_check_is_the_first_row_of_its_group(four_states):
    view = View(contract_frame(four_states, size=WIDE))
    heading_row, area, _, _ = view.headings()[0]
    assert area == "XX"
    first = view.checks()[0]
    assert first[0] == heading_row + 1, "a heading is not followed by a check row"
    assert first[2] == "ACC-XX-003"
    assert first[3] == "failed"


def test_failed_leads_then_blocked_then_pending_then_passed(four_states):
    view = View(contract_frame(four_states, size=WIDE))
    assert [(cid, word) for _, _, cid, word in view.checks()] == [
        ("ACC-XX-003", "failed"),
        ("ACC-XX-004", "blocked"),
        ("ACC-XX-002", "pending"),
        ("ACC-XX-001", "passed"),
    ]


def test_a_failed_check_shows_its_reason_and_names_its_fix_leg(four_states):
    frame = contract_frame(four_states, size=WIDE)
    said = " ".join(" ".join(View(frame).prose_under("ACC-XX-003")).split())
    assert "the write path still accepts a bare string" in said
    assert "fix-the-write-path" in said
    assert "fix leg" in said, \
        "the fix leg is on screen but nothing says that is what it is: %r" % said


def test_a_blocked_check_is_not_shown_as_a_failed_check(four_states):
    """Different states, different sentences — a supervisor acts differently.

    Blocked needs a decision from them; failed needs a fix leg from the coach.
    The theme gives both the same glyph, so the word is the whole distinction.
    """
    view = View(contract_frame(four_states, size=WIDE))
    _, failed_glyph, _, failed_word = view.check("ACC-XX-003")
    _, blocked_glyph, _, blocked_word = view.check("ACC-XX-004")
    assert failed_word == "failed" and blocked_word == "blocked"
    assert failed_glyph == blocked_glyph == GLYPHS["failed"]

    said = " ".join(view.prose_under("ACC-XX-004"))
    assert "no staging database was reachable" in said, \
        "a blocked check does not say why a supervisor has to decide something"


def test_a_fix_leg_is_drawn_wherever_the_relay_recorded_one(four_states):
    """A recorded fact is not suppressed to keep the view's categories tidy.

    ACC-CONT-004 requires the fix leg on a failed check. This fixture also
    records one against a *blocked* check, and it is drawn there too: the state
    word already separates "needs your decision" from "needs a fix leg", and a
    view that hid a leg id the coach wrote down would be the second source of
    truth this whole relay exists to remove.
    """
    view = View(contract_frame(four_states, size=WIDE))
    for cid, leg in (("ACC-XX-003", "fix-the-write-path"),
                     ("ACC-XX-004", "fix-the-staging-database")):
        said = " ".join(" ".join(view.prose_under(cid)).split())
        assert "fix leg: %s" % leg in said, \
            "%s does not name the fix leg the fixture records: %r" % (cid, said)
    for cid in ("ACC-XX-001", "ACC-XX-002"):
        assert "fix leg" not in " ".join(view.prose_under(cid)), \
            "%s was given a fix leg the fixture does not record" % cid


def test_a_failed_rows_glyph_and_word_carry_one_attribute(four_states):
    frame = contract_frame(four_states, size=WIDE)
    view = View(frame)
    row = view.check("ACC-XX-003")[0]
    screen_row = view.top + 1 + row
    frame.assert_attrs(GLYPHS["failed"], row=screen_row, fg=31, has="bold")
    frame.assert_attrs("failed", row=screen_row, fg=31, has="bold")


def test_every_word_on_a_check_row_is_one_of_the_four_states(four_states):
    for size in (WIDE, STANDARD):
        view = View(contract_frame(four_states, size=size))
        for _, _, cid, word in view.checks():
            assert word in STATES, "%s says %r" % (cid, word)


# --------------------------------------------------------------------------
# Overflow, emptiness and degradation
# --------------------------------------------------------------------------


def content_rows(view):
    """The body rows that are not the pane's own overflow marker."""
    return [row for _, row in view.drawn if not MARKER.match(row)]


def test_the_pane_marker_counts_the_rows_it_actually_hid():
    """`drawn + hidden` is the whole list — and the whole list is *measured*.

    The total is taken from a terminal tall enough to draw every row and no
    marker at all, so it is a number this test read off a screen rather than a
    number the view agreed with itself about. A marker that was consistently
    wrong by a constant satisfies conservation between two short terminals and
    fails here.
    """
    relay = FIXTURES / "agent-service"
    whole = View(contract_frame(relay, size=(130, 80)))
    assert whole.marker is None, \
        "130 rows was not enough to draw the whole list; raise the height"
    total = len(content_rows(whole))
    assert total > 100, "the fixture no longer produces a list worth cutting"

    for rows in (24, 30, 38, 90):
        view = View(contract_frame(relay, size=(rows, 80)))
        hidden = view.marker
        assert hidden, "nothing was hidden at %d rows on a 39-check relay" % rows
        assert len(content_rows(view)) + hidden == total, (
            "at %d rows the view drew %d and claimed %d hidden, of %d"
            % (rows, len(content_rows(view)), hidden, total))
    # More room really does hide less.
    assert (View(contract_frame(relay, size=(38, 80))).marker
            < View(contract_frame(relay, size=(24, 80))).marker)


def test_the_marker_sits_on_the_row_the_content_stopped_at():
    """`+N more` belongs to the region that overflowed, not to the pane.

    When a heading has to be given back the content stops short of the pane's
    last body row, and a marker pinned to the bottom leaves a blank strip
    between the list and the statement about it — a gap a reader reads as the
    end of the list.
    """
    for rows in (24, 31, 48):
        view = View(contract_frame(FIXTURES / "agent-service", size=(rows, 160)))
        marker_rows = [index for index, row in view.drawn if MARKER.match(row)]
        assert marker_rows, "nothing was hidden at %d rows" % rows
        content = [index for index, row in view.drawn if not MARKER.match(row)]
        assert marker_rows[0] == content[-1] + 1, (
            "at %d rows the marker is %d rows below the last content row"
            % (rows, marker_rows[0] - content[-1]))


def test_a_pane_that_hid_something_always_says_so(tmp_path):
    """No height draws part of the list and reports nothing hidden.

    The smallest pane the layout will build has a single body row, and the
    tempting arithmetic — spend it on content, put the marker below — puts the
    marker outside the pane, where `Pane` drops it. The screen is then one row
    of a hundred and nineteen with nothing saying so, which is the same lie as
    `1-0 of 12`.
    """
    for rows in range(5, 20):
        view = View(contract_frame(FIXTURES / "agent-service", size=(rows, 80)))
        assert view.marker, (
            "at %d rows the pane drew %r and claimed nothing was hidden"
            % (rows, content_rows(view)))


def test_a_heading_is_never_the_last_thing_drawn():
    """At every height, including the ones where only the marker fits."""
    for rows in range(6, 40, 3):
        view = View(contract_frame(FIXTURES / "agent-service", size=(rows, 80)))
        body = [row for _, row in view.drawn if not MARKER.match(row)]
        if body:
            assert not HEADING.match(body[-1]), (
                "at %d rows the pane ends on an empty heading: %r"
                % (rows, body[-1]))


def test_a_relay_with_no_checks_says_so_in_words(tmp_path):
    relay = checks_relay(tmp_path / "none", {})
    term = session(relay, size=WIDE)
    try:
        frame = term.send("C", expect="no acceptance checks")
    finally:
        term.close()
    frame.assert_finished()
    frame.assert_contains("no acceptance checks recorded")
    frame.assert_not_contains("0/0")
    frame.assert_not_contains("evidenced")
    assert View(frame).meta == "", \
        "a pane with nothing to count still drew a figure: %r" % View(frame).meta


def test_the_view_degrades_at_every_size_down_to_a_line(tmp_path):
    """Down to 1x4 the program draws something and never raises.

    The width claim is made in two strengths, because below about ten columns
    `chrome.draw_status_bar()` itself runs to the last column — a chrome defect
    this leg may not fix, recorded in the baton. Where a Contract pane was drawn
    at all, the strict form applies and the view is certified; where it was not,
    what is asserted is that nothing wrapped, which is the whole of "degrade,
    not crash" at four columns.
    """
    relay = checks_relay(tmp_path / "small", {
        "ACC-XX-001": passed("a real measurement " * 20),
        "ACC-XX-002": pending(),
        "ACC-XX-003": failed("it did not hold", "fix-it"),
        "ACC-YY-001": claimed_passed_without_evidence(),
    })
    drew = 0
    for size in ((48, 160), (24, 80), (12, 40), (8, 30), (6, 20), (4, 10),
                 (3, 8), (2, 6), (1, 4)):
        term = _quiet_session(relay, size)
        try:
            before = term.screen.synchronized_updates
            frame = term.send("C")
            assert "Traceback" not in frame.text, frame._message("the TUI raised")
            assert term.screen.synchronized_faults == 0, frame._message(
                "the program's repaint brackets did not balance")
            assert term.screen.synchronized_updates > before, frame._message(
                "the program never finished a repaint at %r" % (size,))
            assert frame.overlong_lines() == [], frame._message(
                "content wrapped at %r" % (size,))
            if frame.contains(OPENED):
                drew += 1
                frame.assert_within_width()
        finally:
            term.close()
    assert drew >= 4, "the view was never drawn at all — nothing was certified"


def _quiet_session(relay_dir, size):
    """A session started without waiting for a keybar that may not fit.

    Below four rows there is no keybar at all, so `session()`'s own wait for
    `q Quit` cannot be used at the smallest sizes — and a wait that can never
    end is not a degradation test, it is a hang.
    """
    rows, cols = size
    term = TerminalSession(
        [sys.executable, str(ENTRY), str(relay_dir)],
        rows=rows, cols=cols, env=UTF8_ENV,
    )
    term.start()
    return term

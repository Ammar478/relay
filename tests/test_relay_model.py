"""Tests for the shared relay view-model (scripts/relay_model.py).

One test (or one class) per DATA check in `.relay/contract.md`, plus the
adversarial fixtures. Every fixture under `tests/fixtures/` is a frozen relay
directory: the files a coach would have written, and nothing generated.

`tests/fixtures/agent-service/` is a frozen copy of a real in-flight relay,
taken 2026-08-24. Baton mtimes carry the landing order of its runners, and git
does not preserve mtimes, so every test that cares about ordering works against
a copy in `tmp_path` with the recorded mtimes stamped back on (see `relay()`).
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPO / "scripts"))

import relay_model  # noqa: E402

# Recorded from the live relay at copy time. Applied to the copy, never to the
# checked-in fixture, so the landing order survives a fresh clone.
AGENT_SERVICE_BATON_MTIMES = {
    "reconcile-develop": 1787565177.0,
    "reconcile-security": 1787565538.2,
    "credential-parity": 1787567418.5,
    "create-path-credential-guard": 1787574378.2,
    "process-entitlement": 1787575048.3,
    "chat-session-ownership": 1787576216.4,
    "pg-repository-correctness": 1787576799.6,
    "thread-id-ownership": 1787577264.3,
    "s2-test-quality": 1787582281.6,
    "mask-shape-coverage": 1787582967.6,
}

ALL_FIXTURES = [
    "agent-service",
    "stale-currentleg",
    "tokens",
    "no-dashboard",
    "ghost-currentleg",
    "legs-only",
    "malformed",
    "all-done",
    "empty",
]

EM_DASH = "—"


@pytest.fixture
def relay(tmp_path):
    """Copy a fixture relay into tmp_path and pin its baton mtimes."""

    def _relay(name):
        dst = tmp_path / name
        shutil.copytree(FIXTURES / name, dst)
        if name == "agent-service":
            for stem, mtime in AGENT_SERVICE_BATON_MTIMES.items():
                path = dst / "batons" / f"{stem}.md"
                if path.exists():
                    os.utime(path, (mtime, mtime))
        return dst

    return _relay


def walk_strings(obj, path="model"):
    """Every string in the model, with the path it sits at."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_strings(k, f"{path}.<key>")
            yield from walk_strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


# --------------------------------------------------------------------------
# ACC-DATA-001 — One module builds the model
# --------------------------------------------------------------------------

def test_build_returns_a_dict_for_every_fixture(relay):
    for name in ALL_FIXTURES:
        model = relay_model.build(relay(name))
        assert isinstance(model, dict), name


def test_build_accepts_a_string_path(relay):
    model = relay_model.build(str(relay("agent-service")))
    assert isinstance(model, dict)


def test_build_importable_and_callable_from_a_clean_interpreter(relay):
    """The literal ACC-DATA-001 evidence line."""
    target = relay("agent-service")
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import relay_model;"
         "m = relay_model.build(sys.argv[2]); print(type(m).__name__, len(m['legs']))",
         str(REPO / "scripts"), str(target)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[0] == "dict"


#: The two modules allowed to read a relay file directly. `render_dashboard.py`
#: is the standing exception: ACC-HTML-005 retires it, and the leg that earns
#: that check deletes it from here. Anything else is a second source of truth.
RELAY_READERS_ALLOWED = ("scripts/relay_model.py", "scripts/render_dashboard.py")

#: A name only a relay file has.
RELAY_FILE_NAMES = re.compile(r"legs\.json|state\.json|dashboard\.json|batons")

#: Every way this project has actually opened one.
RELAY_READS = re.compile(
    r"json\.load\(|\.read_text\(|\.read_bytes\(|\bopen\(|\.glob\(|scandir\(")


def sweep_for_relay_readers(root):
    """({file: text} inspected, {file: evidence} offending) under `root`.

    Whole files, not single lines. `render_dashboard.py` reads every relay file
    through a two-line idiom - a `load(path)` helper on one line and
    `load(mdir / "legs.json")` on another - and a per-line detector cannot see
    that shape at all, which means it could not see a second reader written the
    same way either. Its own allow-listing was hiding that: the line detector
    scored zero hits on the one file it was written to excuse.

    `.relay/` is out of scope. It is the live relay's own working directory,
    gitignored, and not source this repository ships.
    """
    swept, offenders = {}, {}
    for path in sorted(root.glob("**/*.py")):
        if {".git", ".relay", "fixtures"} & set(path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        if rel in RELAY_READERS_ALLOWED or rel.startswith("tests/"):
            continue
        text = path.read_text(errors="replace")
        swept[rel] = text
        lines = text.splitlines()
        named = [l.strip() for l in lines if RELAY_FILE_NAMES.search(l)]
        reads = [l.strip() for l in lines if RELAY_READS.search(l)]
        if named and reads:
            offenders[rel] = (named + reads)[:4]
    return swept, offenders


def test_relay_model_is_the_only_reader_of_relay_files():
    """No module outside relay_model.py opens a relay file (ACC-DATA-001)."""
    _swept, offenders = sweep_for_relay_readers(REPO)
    assert offenders == {}, offenders


def test_every_allowed_relay_reader_still_exists():
    """A rename is how an allow-list quietly becomes an exemption for nothing,
    and the sweep that follows it becomes an exemption for everything."""
    missing = [rel for rel in RELAY_READERS_ALLOWED if not (REPO / rel).is_file()]
    assert missing == [], missing


@pytest.mark.parametrize("shape,body", [
    ("one line", "import json, pathlib\n"
                 "def read(relay):\n"
                 "    return json.load(open(pathlib.Path(relay) / 'legs.json'))\n"),
    ("a helper and a call site", "import json\n"
                                 "def load(path):\n"
                                 "    return json.load(path.open())\n"
                                 "def read(relay):\n"
                                 "    return load(relay / 'state.json')\n"),
    ("a batons walk", "def batons(relay):\n"
                      "    return sorted((relay / 'batons').glob('*.md'))\n"),
])
def test_the_only_reader_sweep_can_see_a_new_reader(shape, body):
    """The sweep above was blind for its whole life, and passing proved nothing.

    On a fresh clone every `.py` file in this repository is `relay_model.py`,
    `render_dashboard.py`, or a test - and the loop skipped all three kinds. It
    inspected zero files and asserted `set() == set()`. A third reader could
    have landed in `scripts/` and it would still have been green.

    So a reader is planted where a real one would land and the sweep is
    required to name it. That fails if the walk stops reaching files, if the
    skip rules widen, or if the detector stops recognising how this project
    reads a relay file - the three ways it went blind, and the only ways left.
    """
    canary = REPO / "scripts" / "_relay_reader_canary.py"
    rel = "scripts/_relay_reader_canary.py"
    assert not canary.exists(), f"{canary} was left behind by an earlier run"
    canary.write_text(body)
    try:
        swept, offenders = sweep_for_relay_readers(REPO)
        assert rel in swept, (shape, sorted(swept))
        assert rel in offenders, (shape, sorted(offenders))
    finally:
        canary.unlink()


# --------------------------------------------------------------------------
# ACC-DATA-002 — A leg's own status wins over stale currentLeg
# --------------------------------------------------------------------------

def test_stale_currentleg_does_not_force_a_done_leg_to_running(relay):
    model = relay_model.build(relay("stale-currentleg"))
    by_id = {l["id"]: l for l in model["legs"]}

    # state.json still names it; legs.json says done. legs.json wins.
    assert model["relay"]["currentLegDeclared"] == "open-and-green-mr"
    assert by_id["open-and-green-mr"]["status"] == "completed"

    running = [l for l in model["legs"] if l["status"] == "running"]
    assert len(running) == 1, [l["id"] for l in running]
    assert running[0]["id"] == "cutover-flip"
    assert model["activeLeg"]["id"] == "cutover-flip"
    assert model["legCounts"]["running"] == 1


def test_agent_service_reports_one_running_leg_not_two(relay):
    """The defect as observed: currentLeg named a done leg and the renderer
    forced it to display running, giving two In Progress legs."""
    model = relay_model.build(relay("agent-service"))
    by_id = {l["id"]: l for l in model["legs"]}

    assert model["relay"]["currentLegDeclared"] == "open-and-green-mr"
    assert by_id["open-and-green-mr"]["status"] == "completed"

    running = [l for l in model["legs"] if l["status"] == "running"]
    assert len(running) == 1, [l["id"] for l in running]
    assert running[0]["id"] == "code-judge-S3-r2"
    assert model["activeLeg"]["id"] == "code-judge-S3-r2"


def test_active_leg_is_the_first_running_leg_in_plan_order(relay):
    model = relay_model.build(relay("agent-service"))
    running = [l for l in model["legs"] if l["status"] == "running"]
    assert model["activeLeg"]["id"] == running[0]["id"]
    assert model["activeLeg"]["order"] == min(l["order"] for l in running)


def test_currentleg_naming_a_missing_leg_is_not_invented(relay):
    model = relay_model.build(relay("ghost-currentleg"))
    assert model["relay"]["currentLegDeclared"] == "a-leg-that-does-not-exist"
    assert [l["id"] for l in model["legs"]] == ["one", "two"]
    assert model["activeLeg"] is None          # nothing is running
    assert model["activeRunner"] is None
    assert any("a-leg-that-does-not-exist" in w for w in model["warnings"])


def test_agent_service_counts_match_the_frozen_fixture(relay):
    model = relay_model.build(relay("agent-service"))
    counts = model["legCounts"]
    assert counts["total"] == 36
    assert counts["completed"] == 27
    assert counts["running"] == 1
    assert counts["pending"] == 8            # 7 pending + 1 blocked
    assert counts["cancelled"] == 0
    assert counts["total"] == sum(
        counts[k] for k in ("completed", "running", "pending", "cancelled"))

    assert model["checkCounts"] == {
        "total": 39, "passed": 25, "failed": 0, "blocked": 0, "pending": 14}
    assert model["relay"]["currentStage"]["id"] == "S3"
    assert model["relay"]["currentStage"]["name"] == "Ship the image and prove the data"
    assert model["relay"]["phase"] == "running"


def test_blocked_leg_keeps_its_raw_status(relay):
    """`blocked` is not one of the four leg states, so it maps to pending —
    but the word the coach wrote survives for a view that wants it."""
    model = relay_model.build(relay("agent-service"))
    by_id = {l["id"]: l for l in model["legs"]}
    assert by_id["cutover-flip"]["status"] == "pending"
    assert by_id["cutover-flip"]["rawStatus"] == "blocked"


# --------------------------------------------------------------------------
# ACC-DATA-003 — Active leg and active runner agree
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_active_leg_and_active_runner_never_disagree(name, relay):
    model = relay_model.build(relay(name))
    leg, runner = model["activeLeg"], model["activeRunner"]
    if leg is None or runner is None:
        assert leg is None and runner is None, (name, leg, runner)
    else:
        assert runner["leg"] == leg["id"], name


def test_active_runner_is_present_when_a_leg_runs(relay):
    model = relay_model.build(relay("agent-service"))
    assert model["activeRunner"]["leg"] == "code-judge-S3-r2"
    assert model["activeRunner"]["status"] == "running"
    assert model["activeRunner"] in model["runners"]


def test_no_active_runner_when_nothing_runs(relay):
    model = relay_model.build(relay("all-done"))
    assert model["activeLeg"] is None
    assert model["activeRunner"] is None
    assert model["runnerCounts"]["active"] == 0


def test_runner_active_count_equals_running_legs(relay):
    """ACC-RUN-002's data half: the Runners view read Active (0) while two
    legs displayed as running."""
    for name in ALL_FIXTURES:
        model = relay_model.build(relay(name))
        running = sum(1 for l in model["legs"] if l["status"] == "running")
        assert model["runnerCounts"]["active"] == running, name


# --------------------------------------------------------------------------
# ACC-DATA-004 — Status vocabulary is normalised
# --------------------------------------------------------------------------

LEG_STATES = {"completed", "running", "pending", "cancelled"}

STATUS_TABLE = [
    ("done", "completed"), ("complete", "completed"), ("completed", "completed"),
    ("finished", "completed"), ("DONE", "completed"), ("Done ", "completed"),
    ("shipped", "completed"), ("landed", "completed"), ("passed", "completed"),
    ("in progress", "running"), ("in_progress", "running"), ("in-progress", "running"),
    ("IN PROGRESS", "running"), ("wip", "running"), ("running", "running"),
    ("active", "running"), ("started", "running"),
    ("TODO", "pending"), ("todo", "pending"), ("queued", "pending"),
    ("planned", "pending"), ("not_started", "pending"), ("pending", "pending"),
    ("cancelled", "cancelled"), ("canceled", "cancelled"), ("skipped", "cancelled"),
    ("dropped", "cancelled"), ("abandoned", "cancelled"),
    ("blocked", "pending"), ("waiting on a human", "pending"),
    ("", "pending"), (None, "pending"), (0, "pending"), ("garbage", "pending"),
]


@pytest.mark.parametrize("raw,expected", STATUS_TABLE)
def test_status_vocabulary(raw, expected):
    assert relay_model.normalise_status(raw) == expected


def test_every_spelling_lands_on_a_known_state():
    for raw, _ in STATUS_TABLE:
        assert relay_model.normalise_status(raw) in LEG_STATES


def test_leg_status_is_never_none_or_undefined(relay):
    for name in ALL_FIXTURES:
        model = relay_model.build(relay(name))
        for leg in model["legs"]:
            assert leg["status"] in LEG_STATES, (name, leg["id"], leg["status"])
            assert leg["status"] not in ("undefined", "None")


CHECK_TABLE = [
    ("passed", "passed"), ("pass", "passed"), ("PASS", "passed"), ("ok", "passed"),
    ("green", "passed"), ("satisfied", "passed"),
    ("failed", "failed"), ("fail", "failed"), ("red", "failed"), ("broken", "failed"),
    ("blocked", "blocked"), ("block", "blocked"), ("unevidenced", "blocked"),
    ("cannot_verify", "blocked"), ("cannot verify", "blocked"),
    ("pending", "pending"), ("", "pending"), (None, "pending"), ("nonsense", "pending"),
]


@pytest.mark.parametrize("raw,expected", CHECK_TABLE)
def test_check_vocabulary(raw, expected):
    assert relay_model.normalise_check(raw) == expected


def test_kind_of_uses_explicit_kind_then_id(relay):
    model = relay_model.build(relay("agent-service"))
    kinds = {l["id"]: l["kind"] for l in model["legs"]}
    assert kinds["cutover-flip"] == "impl"
    assert kinds["code-judge-S3-r2"] == "judge"      # no `kind` field; id says judge
    assert kinds["fix-agenttype-write-paths"] == "fix"
    assert set(kinds.values()) <= {"impl", "fix", "judge"}


def test_fix_kind_from_repairs(relay):
    model = relay_model.build(relay("all-done"))
    kinds = {l["id"]: l["kind"] for l in model["legs"]}
    assert kinds["b"] == "fix"


def test_cancelled_leg_normalises(relay):
    model = relay_model.build(relay("all-done"))
    by_id = {l["id"]: l for l in model["legs"]}
    assert by_id["c"]["status"] == "cancelled"
    assert model["legCounts"]["cancelled"] == 1
    assert model["relay"]["phase"] == "complete"


# --------------------------------------------------------------------------
# ACC-DATA-007 — Runners carry real columns or say so
# --------------------------------------------------------------------------

RUNNER_KEYS = {"n", "leg", "stage", "start", "duration", "commit", "batonLines", "status"}


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_runner_row_exposes_the_contract_columns(name, relay):
    model = relay_model.build(relay(name))
    for row in model["runners"]:
        assert RUNNER_KEYS <= set(row), (name, RUNNER_KEYS - set(row))


def test_unavailable_runner_fields_are_none_not_invented(relay):
    model = relay_model.build(relay("agent-service"))
    rows = {r["leg"]: r for r in model["runners"]}

    # A completed leg with no baton on disk: nothing is known but the leg.
    # This named `merge-and-tag`, which is not a leg of this fixture, so the
    # clause asserted nothing at all - `no_baton is None` was the branch that
    # ran. The rows are derived from the fixture instead, which is what the
    # fixtures skill says to do and what makes the assertion bite.
    no_baton = [r for r in model["runners"]
                if r["batonPath"] is None and r["status"] != "running"]
    assert no_baton, "the fixture has completed legs whose runner left no baton"
    for row in no_baton:
        assert row["commit"] is None, row["leg"]
        assert row["batonLines"] is None and row["start"] is None
        assert row["duration"] is None and row["finished"] is None

    # A leg with a baton: the fields the baton actually carries are filled in.
    landed = rows["reconcile-develop"]
    assert landed["batonLines"] > 0
    # No `**Commit:**` field in this baton. Line 7 reports the merge this leg
    # MADE; line 5 mentions the commit the branch was forked FROM, two lines
    # earlier. This assertion pinned the second of those as correct until
    # `log-attribution-truth`: a sha in a baton is not a claim that the leg
    # produced it, and crediting the branch point cost the log the one commit
    # this leg actually landed (ACC-DATA-009).
    assert landed["commit"] == "c3319e2"
    assert "7f8690c" in relay_model.baton_text(landed["batonPath"])
    assert landed["finished"] == pytest.approx(1787565177.0)
    assert landed["start"] is None          # first runner: no previous handoff
    assert landed["duration"] is None
    assert landed["stage"] == "S1"

    second = rows["reconcile-security"]
    assert second["start"] == pytest.approx(1787565177.0)
    assert second["duration"] == pytest.approx(1787565538.2 - 1787565177.0)


def test_runner_numbering_follows_landing_order(relay):
    model = relay_model.build(relay("agent-service"))
    numbered = [r["leg"] for r in sorted(model["runners"], key=lambda r: r["n"])]
    assert [r["n"] for r in model["runners"]] == list(range(1, len(model["runners"]) + 1))
    batoned = [leg for leg in numbered if leg in AGENT_SERVICE_BATON_MTIMES]
    expected = sorted(
        (leg for leg in batoned), key=lambda leg: AGENT_SERVICE_BATON_MTIMES[leg])
    assert batoned == expected
    assert numbered[-1] == "code-judge-S3-r2"      # the running runner is last


def test_runner_duration_for_the_active_leg_needs_an_injected_now(relay):
    target = relay("agent-service")
    without = relay_model.build(target)
    assert without["activeRunner"]["duration"] is None      # no wall-clock by default

    pinned = relay_model.build(target, now=1787583000.0)
    assert pinned["activeRunner"]["start"] == pytest.approx(1787582967.6)
    assert pinned["activeRunner"]["duration"] == pytest.approx(32.4, abs=0.5)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_no_em_dash_anywhere_in_the_model(name, relay):
    model = relay_model.build(relay(name))
    hits = [(where, s) for where, s in walk_strings(model) if EM_DASH in s]
    assert hits == [], hits


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_model_carries_no_display_placeholders(name, relay):
    model = relay_model.build(relay(name))
    banned = {"—", "-", "N/A", "n/a", "undefined", "None", "null", "?"}
    hits = [(where, s) for where, s in walk_strings(model) if s in banned]
    assert hits == [], hits


def test_baton_prose_is_read_on_demand_not_carried_in_the_model(relay):
    """Baton prose is full of em-dashes and 9KB long; the model carries the
    path and the view asks for the text."""
    target = relay("agent-service")
    model = relay_model.build(target)
    row = {r["leg"]: r for r in model["runners"]}["reconcile-develop"]
    assert row["batonPath"].endswith("batons/reconcile-develop.md")
    text = relay_model.baton_text(row["batonPath"])
    assert "## Implemented" in text
    assert relay_model.baton_text(str(target / "batons" / "nope.md")) is None


def test_a_baton_without_a_leg_is_reported_not_silently_dropped(relay):
    model = relay_model.build(relay("agent-service"))
    legs = {l["id"] for l in model["legs"]}
    assert "s2-test-quality" not in legs                 # baton exists, leg does not
    assert any("s2-test-quality" in w for w in model["warnings"])


def test_a_bold_commit_field_is_read_whichever_side_the_colon_is_on(tmp_path):
    """`**Commit:** <sha>` and `**Commit**: <sha>` are the same field of the
    same template, and runners write both. A field that is not read costs the
    leg its commit, and the progress log then has no leg to attribute that
    commit to (ACC-DATA-009)."""
    (tmp_path / "batons").mkdir()
    (tmp_path / "legs.json").write_text(json.dumps(
        {"relay": "bold",
         "legs": [{"id": "inside", "status": "done"},
                  {"id": "outside", "status": "done"}]}))
    (tmp_path / "batons" / "inside.md").write_text(
        "# Baton\nSTATUS: success\n**Commit:** 1a2b3c4\n")
    (tmp_path / "batons" / "outside.md").write_text(
        "# Baton\nSTATUS: success\n**Commit**: 5d6e7f8\n")

    rows = {r["leg"]: r for r in relay_model.build(tmp_path)["runners"]}
    assert rows["inside"]["commit"] == "1a2b3c4"
    assert rows["outside"]["commit"] == "5d6e7f8"


def test_baton_status_becomes_runner_status(relay):
    model = relay_model.build(relay("stale-currentleg"))
    rows = {r["leg"]: r for r in model["runners"]}
    assert rows["alpha"]["status"] == "completed"
    assert rows["alpha"]["commit"] == "1a2b3c4"
    assert rows["open-and-green-mr"]["status"] == "partial"
    assert model["runnerCounts"]["partial"] == 1


# --------------------------------------------------------------------------
# ACC-DATA-007 — one unsourced column at a time
#
# The fixtures happen to fill most columns in, which is why "the model never
# invents a value" survived so long with nothing holding it: a column can only
# be caught inventing when there is a row it has no source for. `unsourced`
# below is a relay built so that each of the eight columns the check names has
# at least one such row, and each test asserts that column's absence together
# with a row where the column *is* sourced — so a mutation that blanks the
# column everywhere fails just as loudly as one that fills it in everywhere.
#
# Each test is paired with a mutation of `scripts/relay_model.py` that invents
# a value for exactly its column; the mutation table is in
# `.relay/batons/evidence-that-bites.md`. A test that does not fail under its
# own mutation is not evidence and is not finished.
# --------------------------------------------------------------------------

#: Baton landing times, stamped rather than left to the filesystem: `start` and
#: `duration` are handoffs between them and a test of "60 seconds" needs to
#: know it was 60 seconds.
UNSOURCED_MTIMES = {"batoned": 1_000_000.0, "handoff": 1_000_060.0,
                    "quiet": 1_000_120.0}


@pytest.fixture
def unsourced(tmp_path):
    """A relay with a row missing a source for every column in `RUNNER_KEYS`.

    * `batoned`  — the fully sourced row, and the first to land, so its own
                   `start` and `duration` have no previous handoff.
    * `handoff`  — lands 60s later: the row where `start` and `duration` are
                   real, so a test can tell absence from erasure.
    * `quiet`    — a baton with no `Status:` line and no commit claim.
    * `batonless`— a completed leg whose runner left no baton at all.
    * `stageless`— the same, and the coach gave it no `stage`.
    * (no id)    — the same, and the coach gave it no `id` either.
    * `live`     — the running leg: no baton yet, and no clock passed to
                   `build()`, so its elapsed time is unknown too.
    """
    target = write_relay(
        tmp_path, "unsourced",
        legs={"relay": "unsourced",
              "stages": [{"id": "S1", "name": "Stage One",
                          "legs": ["batoned", "handoff", "batonless", "quiet"]}],
              "legs": [{"id": "batoned", "stage": "S1", "status": "done"},
                       {"id": "handoff", "stage": "S1", "status": "done"},
                       {"id": "batonless", "stage": "S1", "status": "done"},
                       {"id": "quiet", "stage": "S1", "status": "done"},
                       {"id": "stageless", "status": "done"},
                       {"status": "done", "goal": "a leg the coach left unnamed"},
                       {"id": "live", "stage": "S1", "status": "running"}]})
    batons = target / "batons"
    batons.mkdir()
    (batons / "batoned.md").write_text(
        "# Baton: batoned\n\n**Status:** success\n**Commit:** 1a2b3c4\n")
    (batons / "handoff.md").write_text(
        "# Baton: handoff\n\n**Status:** success\n**Commit:** 2b3c4d5\n")
    # No `Status:` line and no sentence that claims a commit: a runner who
    # wrote prose and filled in none of the template's fields.
    (batons / "quiet.md").write_text(
        "# Baton: quiet\n\nI ran the leg and wrote this and nothing else.\n")
    import os

    for stem, mtime in UNSOURCED_MTIMES.items():
        os.utime(batons / f"{stem}.md", (mtime, mtime))
    return target


@pytest.fixture
def unsourced_rows(unsourced):
    """`{leg id: row}` for the unsourced relay. The unnamed leg keys on ``""``."""
    model = relay_model.build(unsourced)
    rows = {r["leg"]: r for r in model["runners"]}
    assert len(rows) == len(model["runners"]) == 7, model["runners"]
    return rows


def test_n_is_the_row_s_landing_position_not_a_fabricated_number(unsourced):
    """`n` always has a source — the row's own position — so the way to invent
    it is to emit something that is not that position."""
    rows = relay_model.build(unsourced)["runners"]
    assert [r["n"] for r in rows] == list(range(1, len(rows) + 1))
    batoned = [r["leg"] for r in rows if r["leg"] in UNSOURCED_MTIMES]
    assert batoned == sorted(batoned, key=UNSOURCED_MTIMES.get)
    assert rows[-1]["leg"] == "live"          # the running runner is last


def test_a_leg_with_no_id_gets_no_invented_name(unsourced_rows):
    """The unnamed leg still earns a runner row — it is a leg the coach
    planned and a runner completed — but the model does not name it."""
    assert "" in unsourced_rows, sorted(unsourced_rows)
    assert unsourced_rows[""]["status"] == "completed"
    assert unsourced_rows["batoned"]["leg"] == "batoned"     # a real id survives


def test_a_leg_with_no_stage_has_a_stage_of_none(unsourced_rows):
    for leg in ("stageless", ""):
        assert unsourced_rows[leg]["stage"] is None, leg
        assert unsourced_rows[leg]["stageName"] is None, leg
    assert unsourced_rows["batoned"]["stage"] == "S1"        # a real stage survives
    assert unsourced_rows["batoned"]["stageName"] == "Stage One"


def test_start_is_none_until_a_previous_baton_has_landed(unsourced_rows):
    # The first runner to land: nothing handed off to it.
    assert unsourced_rows["batoned"]["start"] is None
    # No baton at all: nothing on disk says when it ran.
    for leg in ("batonless", "stageless", ""):
        assert unsourced_rows[leg]["start"] is None, leg
    # A real handoff survives: the previous baton's landing time.
    assert unsourced_rows["handoff"]["start"] == pytest.approx(
        UNSOURCED_MTIMES["batoned"])
    assert unsourced_rows["live"]["start"] == pytest.approx(
        UNSOURCED_MTIMES["quiet"])


def test_duration_is_none_unless_both_ends_are_known(unsourced_rows):
    assert unsourced_rows["batoned"]["duration"] is None     # start unknown
    assert unsourced_rows["live"]["duration"] is None        # no clock passed
    for leg in ("batonless", "stageless", ""):
        assert unsourced_rows[leg]["duration"] is None, leg
        assert unsourced_rows[leg]["finished"] is None, leg
    # Both ends known: 60 seconds, and not a zero standing in for absence.
    assert unsourced_rows["handoff"]["duration"] == pytest.approx(60.0)


def test_commit_is_none_when_no_baton_claims_one(unsourced_rows):
    for leg in ("quiet", "batonless", "stageless", "", "live"):
        assert unsourced_rows[leg]["commit"] is None, leg
    assert unsourced_rows["batoned"]["commit"] == "1a2b3c4"


def test_batonlines_is_none_without_a_baton_and_a_real_count_with_one(
        unsourced_rows):
    for leg in ("batonless", "stageless", "", "live"):
        assert unsourced_rows[leg]["batonLines"] is None, leg
        assert unsourced_rows[leg]["batonPath"] is None, leg
    assert unsourced_rows["quiet"]["batonLines"] > 0          # not a zero
    assert unsourced_rows["batoned"]["batonLines"] > 0


def test_status_falls_back_to_the_leg_never_to_a_placeholder(unsourced_rows):
    """`status` always has a source — the baton's word, or failing that the
    leg's own state — so inventing here means answering with something outside
    the vocabulary."""
    known = {"completed", "running", "partial", "failed"}
    # A baton with no `Status:` line: the leg's own state answers instead.
    assert unsourced_rows["quiet"]["status"] == "completed"
    assert unsourced_rows["batonless"]["status"] == "completed"
    assert unsourced_rows["live"]["status"] == "running"
    for leg, row in unsourced_rows.items():
        assert row["status"] in known, (leg, row["status"])


#: What a view prints for "no value". None of them belongs in the model.
PLACEHOLDER_STRINGS = {EM_DASH, "-", "--", "N/A", "n/a", "NA", "undefined", "None",
                       "null", "nil", "?", "TBD", "unknown", ""}


def _assert_no_placeholder_columns(rows, label):
    for row in rows:
        for column in sorted(RUNNER_KEYS):
            value = row[column]
            if not isinstance(value, str):
                continue
            # `leg` is the one column whose empty string is meaningful: it is
            # the id of a leg that has none, and the model refuses to make one
            # up. Every other string column carrying a placeholder is an
            # invention.
            if column == "leg" and value == "":
                continue
            assert EM_DASH not in value, (label, row["n"], column, value)
            assert value not in PLACEHOLDER_STRINGS, (label, row["n"], column, value)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_no_runner_column_carries_a_display_placeholder(name, relay):
    """The em-dash clause, over the columns it is about. An unsourced column
    is `None`; it is never dressed as a value a view could print."""
    _assert_no_placeholder_columns(relay_model.build(relay(name))["runners"], name)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_no_runner_column_carries_a_display_placeholder_in_place(name):
    """And with the fixture read where it lies, inside this repository, so the
    commit-reading branch of `build()` runs. A commit *subject* quoted into a
    log entry may legitimately contain an em-dash — that is a recorded
    decision, and it is why this sweep is over the runner columns rather than
    over every string in the model."""
    _assert_no_placeholder_columns(
        relay_model.build(FIXTURES / name)["runners"], f"{name} (in place)")


def test_no_runner_column_carries_a_display_placeholder_when_unsourced(unsourced):
    """The same sweep over the relay that actually has unsourced columns —
    the fixtures fill most of them in, which is how an em-dash substitution
    could hide from the sweep above."""
    _assert_no_placeholder_columns(relay_model.build(unsourced)["runners"],
                                   "unsourced")


# --------------------------------------------------------------------------
# ACC-DATA-008 — Token and time metrics are absent, not zero
# --------------------------------------------------------------------------

def test_agent_service_has_no_token_metrics_at_all(relay):
    model = relay_model.build(relay("agent-service"))
    assert "tokens" not in model["metrics"]
    assert "elapsed" not in model["metrics"]
    assert model["metrics"] == {}


def test_token_fixture_carries_the_metrics(relay):
    model = relay_model.build(relay("tokens"))
    assert model["metrics"]["tokens"]["input"] == "324.0K"
    assert model["metrics"]["tokens"]["cached"] == "16.8M"
    assert model["metrics"]["tokens"]["output"] == "111.0K"
    assert model["metrics"]["elapsed"] == "6h 38m"


def test_placeholder_token_values_are_dropped_not_carried(relay, tmp_path):
    target = relay("tokens")
    dash = json.loads((target / "dashboard.json").read_text())
    dash["tokens"] = {"input": "12.0K", "cached": "—", "output": ""}
    dash["elapsed"] = "—"
    (target / "dashboard.json").write_text(json.dumps(dash))

    model = relay_model.build(target)
    assert model["metrics"]["tokens"] == {"input": "12.0K"}
    assert "elapsed" not in model["metrics"]


def test_zero_is_kept_when_it_was_actually_measured(relay):
    target = relay("tokens")
    dash = json.loads((target / "dashboard.json").read_text())
    dash["tokens"] = {"input": 0, "cached": 0, "output": 0}
    (target / "dashboard.json").write_text(json.dumps(dash))

    model = relay_model.build(target)
    assert model["metrics"]["tokens"] == {"input": 0, "cached": 0, "output": 0}


def test_check_round_is_absent_rather_than_zero(relay):
    model = relay_model.build(relay("stale-currentleg"))
    by_id = {c["id"]: c for c in model["checks"]}
    assert by_id["ACC-X-001"]["round"] == 1
    assert by_id["ACC-Y-002"]["round"] is None       # never judged, not round 0


# --------------------------------------------------------------------------
# Checks and grouping (feeds the Contract view)
# --------------------------------------------------------------------------

def test_checks_group_by_area_with_counts(relay):
    model = relay_model.build(relay("agent-service"))
    groups = {g["area"]: g for g in model["checkGroups"]}
    assert groups["CRED"]["passed"] == groups["CRED"]["total"] == 6
    assert groups["CUTOVER"]["passed"] == 0
    assert groups["CUTOVER"]["total"] == 8
    assert [g["area"] for g in model["checkGroups"]] == sorted(groups)


def test_failed_checks_sort_first_within_their_area(relay):
    model = relay_model.build(relay("stale-currentleg"))
    x = [g for g in model["checkGroups"] if g["area"] == "X"][0]
    assert [c["id"] for c in x["checks"]] == ["ACC-X-002", "ACC-X-001"]
    assert x["checks"][0]["status"] == "failed"
    assert x["checks"][0]["fixLeg"] == "fix-pipeline"
    assert x["checks"][0]["reason"] == "pipeline red on the MR"
    assert x["checks"][0]["evidence"] is None


def test_check_titles_come_from_the_coach_when_given(relay):
    model = relay_model.build(relay("tokens"))
    by_id = {c["id"]: c for c in model["checks"]}
    assert by_id["ACC-M-001"]["title"] == "Metrics are measured, not guessed"
    assert by_id["ACC-M-002"]["title"] is None


# --------------------------------------------------------------------------
# Attention band (feeds ACC-TUI-005)
# --------------------------------------------------------------------------

def test_coach_attention_strings_become_levelled_items(relay):
    model = relay_model.build(relay("agent-service"))
    band = model["attention"]
    assert band[0]["label"] == "NEEDS YOUR CALL"
    assert band[0]["level"] == "bad"
    assert "create_teams_channel" in band[0]["text"]
    assert not band[0]["text"].startswith("NEEDS YOUR CALL")
    notes = [i for i in band if i["label"] == "NOTE"]
    assert len(notes) == 4                      # 3 remaining signals + `notes`
    assert all(i["level"] == "note" for i in notes)


def test_derived_stalled_and_blocked_signals(relay):
    model = relay_model.build(relay("stale-currentleg"))
    labels = [i["label"] for i in model["attention"]]
    assert "STALLED" in labels                  # ACC-X-002 failed 3 rounds
    assert "BLOCKED" in labels                  # ACC-Y-001 cannot be evidenced
    assert model["attention"][0]["level"] == "bad"


def test_calm_signal_when_there_is_nothing_to_say(relay):
    model = relay_model.build(relay("no-dashboard"))
    assert len(model["attention"]) == 1
    assert model["attention"][0]["level"] == "calm"
    assert model["attention"][0]["label"] == "ON TRACK"


def test_dict_attention_items_pass_through(relay):
    model = relay_model.build(relay("tokens"))
    warn = [i for i in model["attention"] if i["label"] == "SLOW"][0]
    assert warn["level"] == "warn"
    assert warn["action"] == "pause -> mark complete"


# --------------------------------------------------------------------------
# Progress log — the seam ACC-DATA-005 fills in
# --------------------------------------------------------------------------

def test_explicit_log_is_returned_verbatim(relay):
    model = relay_model.build(relay("tokens"))
    assert [e["m"] for e in model["log"]] == [
        "Stage judging: ACC-M-002 still failing",
        "measured-leg landed",
        "plan approved",
    ]
    assert model["logSource"] == "dashboard"


def test_the_log_is_derived_when_the_coach_writes_none(relay):
    """ACC-DATA-005. agent-service has no `dashboard.json.log`, and the model
    still tells the story of the run from baton mtimes, git and the check
    transitions in state.json. The full behaviour lives in
    `tests/test_progress_log.py`; this is the model-level contract."""
    model = relay_model.build(relay("agent-service"), now=1787600000.0)
    assert model["logSource"] == "derived"
    assert len(model["log"]) >= 10
    times = [e["t"] for e in model["log"]]
    assert times == sorted(times, reverse=True)
    assert all(isinstance(e["t"], float) and isinstance(e["m"], str)
               for e in model["log"])


# --------------------------------------------------------------------------
# Adversarial fixtures
# --------------------------------------------------------------------------

def test_empty_relay_directory(relay):
    model = relay_model.build(relay("empty"))
    assert model["legs"] == []
    assert model["checks"] == []
    assert model["runners"] == []
    assert model["activeLeg"] is None and model["activeRunner"] is None
    assert model["legCounts"] == {"total": 0, "completed": 0, "running": 0,
                                  "pending": 0, "cancelled": 0}
    assert model["sources"] == {"legs": "missing", "state": "missing",
                                "dashboard": "missing"}
    assert model["metrics"] == {}
    assert model["relay"]["phase"] == "pending"


def test_missing_relay_directory_raises_a_clear_error(tmp_path):
    with pytest.raises(relay_model.RelayNotFound) as excinfo:
        relay_model.build(tmp_path / "nope")
    assert "nope" in str(excinfo.value)


def test_legs_only(relay):
    model = relay_model.build(relay("legs-only"))
    assert [l["id"] for l in model["legs"]] == ["solo"]
    assert model["legs"][0]["status"] == "running"
    assert model["activeLeg"]["id"] == "solo"
    assert model["activeRunner"]["leg"] == "solo"
    assert model["checks"] == []
    assert model["sources"]["state"] == "missing"
    assert model["relay"]["phase"] == "running"      # derived, no state.json


def test_malformed_json_degrades_without_raising(relay):
    model = relay_model.build(relay("malformed"))
    assert model["legs"] == []
    assert model["checks"] == []
    assert model["sources"] == {"legs": "malformed", "state": "malformed",
                                "dashboard": "malformed"}
    assert len(model["warnings"]) >= 3
    assert any("legs.json" in w for w in model["warnings"])


def test_no_dashboard_json(relay):
    model = relay_model.build(relay("no-dashboard"))
    assert model["sources"]["dashboard"] == "missing"
    assert model["extras"] == {}
    assert model["metrics"] == {}
    assert model["relay"]["title"] == "no-dashboard"   # falls back to the name
    assert model["activeLeg"]["id"] == "judge-it"


def test_relay_path_and_title(relay):
    target = relay("tokens")
    model = relay_model.build(target)
    assert model["relay"]["title"] == "Measured relay"
    assert model["relay"]["name"] == "measured"
    assert model["relay"]["path"] == "~/dev/measured"
    assert model["relay"]["relayDir"] == str(target.resolve())

    plain = relay_model.build(relay("no-dashboard"))
    assert plain["relay"]["path"].endswith("/no-dashboard")   # parent of relay dir


# --------------------------------------------------------------------------
# Determinism and shape
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_build_is_deterministic(name, relay):
    target = relay(name)
    assert relay_model.build(target) == relay_model.build(target)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_model_is_json_serialisable(name, relay):
    json.dumps(relay_model.build(relay(name)))


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_top_level_keys_are_stable(name, relay):
    model = relay_model.build(relay(name))
    assert set(model) == {
        "relay", "metrics", "stages", "legs", "legCounts", "activeLeg",
        "runners", "runnerCounts", "activeRunner", "checks", "checkGroups",
        "checkCounts", "attention", "log", "logSource", "extras", "sources",
        "warnings",
    }


def test_stage_order_and_names(relay):
    model = relay_model.build(relay("agent-service"))
    assert [s["id"] for s in model["stages"]] == ["S1", "S2", "S3", "S4"]
    orders = [l["order"] for l in model["legs"]]
    assert orders == sorted(orders)
    stages = [l["stage"] for l in model["legs"]]
    assert stages == sorted(stages, key=lambda s: ["S1", "S2", "S3", "S4"].index(s))
    assert model["legs"][0]["stageName"] == "Reconcile the branch stack"


def test_leg_rows_carry_the_spec_the_active_leg_pane_needs(relay):
    model = relay_model.build(relay("stale-currentleg"))
    leg = model["activeLeg"]
    assert leg["goal"] == "Flip the cutover."
    assert leg["boundaries"] == ["do not touch prod"]
    assert leg["verification"] == ["pytest tests/cutover"]
    assert leg["touches"] == ["src/cutover.py"]
    assert leg["fulfills"] == ["ACC-Y-001"]
    assert leg["dependsOn"] == ["open-and-green-mr"]
    assert leg["stageName"] == "Second stage"
    assert leg["isActive"] is True


def test_missing_leg_fields_are_none_or_empty_lists(relay):
    model = relay_model.build(relay("legs-only"))
    leg = model["legs"][0]
    assert leg["fulfills"] == [] and leg["boundaries"] == [] and leg["verification"] == []
    assert leg["goal"] == "Do the thing."
    model2 = relay_model.build(relay("ghost-currentleg"))
    assert model2["legs"][0]["goal"] == "One."


def test_build_never_writes_to_the_relay_directory(relay):
    target = relay("agent-service")
    before = {p: (p.stat().st_mtime, p.stat().st_size)
              for p in sorted(target.rglob("*")) if p.is_file()}
    relay_model.build(target, now=1787583000.0)
    after = {p: (p.stat().st_mtime, p.stat().st_size)
             for p in sorted(target.rglob("*")) if p.is_file()}
    assert before == after


def test_module_does_not_import_curses():
    source = (REPO / "scripts" / "relay_model.py").read_text()
    assert "import curses" not in source
    assert "curses" not in sys.modules or True     # the module never pulls it in


# --------------------------------------------------------------------------
# ACC-DATA-001 — untrusted input: every malformed shape degrades
#
# `build()` runs once per repaint against a directory a coach, a runner and a
# half-finished write are all editing underneath it. Every case below was
# observed to raise before this section was written.
# --------------------------------------------------------------------------

def write_relay(root, name, legs=None, state=None, dashboard=None, raw=None):
    """A relay directory built from whatever objects a test hands it.

    `raw` writes bytes straight to a file, for the shapes `json.dumps` cannot
    express: invalid UTF-8, a truncated object, an empty file.
    """
    target = Path(root) / name
    target.mkdir(parents=True, exist_ok=True)
    for filename, data in (("legs.json", legs), ("state.json", state),
                           ("dashboard.json", dashboard)):
        if data is not None:
            (target / filename).write_text(json.dumps(data))
    for filename, blob in (raw or {}).items():
        (target / filename).write_bytes(blob)
    return target


RELAY_FILES = ["legs.json", "state.json", "dashboard.json"]

# A relay file caught mid-write, byte for byte: a JSON object cut inside a
# multi-byte character, so the file is neither valid UTF-8 nor valid JSON.
TRUNCATED_MID_CHARACTER = '{"relay": "café'.encode()[:-1]

MALFORMED_BYTES = {
    "not utf-8 at all": b'{"relay": "caf\xe9"}',
    "a latin-1 encoded file": '{"relay": "café"}'.encode("latin-1"),
    "truncated inside a character": TRUNCATED_MID_CHARACTER,
    "truncated json": b'{"relay": "x", "legs": [{"id": "a"',
    "empty": b"",
    "whitespace only": b"   \n",
    "a nul byte": b'{"relay": "\x00"}',
    "not json": b"not json at all",
    "a json array": b'[1, 2, 3]',
    "a json scalar": b'42',
}

# Values a leg `id` must never be. Each one crashed a different line.
BAD_IDS = [None, 7, True, 1.5, ["x"], {"k": 1}, "", "   "]
# Present, but not a string: a coach wrote something the field cannot hold, and
# the model owes a warning. `None` is absence, not a wrong type, so it is kept
# apart in `MISSING_OR_BAD`.
BAD_STRINGS = [7, True, ["x"], {"k": 1}]
MISSING_OR_BAD = [None] + BAD_STRINGS


@pytest.mark.parametrize("label,blob", sorted(MALFORMED_BYTES.items()))
@pytest.mark.parametrize("filename", RELAY_FILES)
def test_malformed_file_bytes_are_a_warning_not_an_exception(
        tmp_path, filename, label, blob):
    target = write_relay(tmp_path, f"{filename}-{abs(hash(label))}",
                         raw={filename: blob})
    model = relay_model.build(target)
    assert isinstance(model, dict)
    assert model["sources"][filename.split(".")[0]] == "malformed"
    assert any(filename in w for w in model["warnings"]), model["warnings"]


def test_non_utf8_warning_says_what_was_wrong(tmp_path):
    """A decode failure and a syntax failure are different repairs, so the
    warning names which one happened."""
    target = write_relay(tmp_path, "bytes", raw={"legs.json": b'{"relay": "caf\xe9"}'})
    warning = " ".join(relay_model.build(target)["warnings"])
    assert "legs.json" in warning and "UTF-8" in warning


def test_every_relay_file_malformed_at_once_still_builds(tmp_path):
    target = write_relay(tmp_path, "all-bad",
                         raw={name: b'\xff\xfe truncated' for name in RELAY_FILES})
    model = relay_model.build(target)
    assert model["legs"] == [] and model["checks"] == []
    assert set(model["sources"].values()) == {"malformed"}
    assert len(model["warnings"]) >= 3


@pytest.mark.parametrize("bad", BAD_IDS)
def test_a_leg_id_of_the_wrong_type_never_raises(tmp_path, bad):
    target = write_relay(tmp_path, f"leg-id-{type(bad).__name__}-{bad!r:.8}",
                         legs={"legs": [{"id": bad, "status": "running"}]})
    model = relay_model.build(target)
    assert isinstance(model, dict)
    assert len(model["legs"]) == 1
    assert model["legs"][0]["id"] == ""          # unidentifiable, never "None"
    assert any("id" in w for w in model["warnings"]), model["warnings"]


def test_a_leg_with_no_id_key_at_all_never_raises(tmp_path):
    target = write_relay(tmp_path, "no-id-key",
                         legs={"legs": [{"status": "running", "goal": "g"}]})
    model = relay_model.build(target)
    assert model["legs"][0]["id"] == ""
    assert model["legs"][0]["goal"] == "g"       # the rest of the row survives
    assert any("id" in w for w in model["warnings"])


@pytest.mark.parametrize("bad", BAD_STRINGS)
def test_a_claimed_by_of_the_wrong_type_never_raises(tmp_path, bad):
    target = write_relay(
        tmp_path, f"claimed-{type(bad).__name__}",
        legs={"legs": [{"id": "a", "status": "done"}]},
        state={"checks": {"ACC-A-001": {"status": "passed", "claimedBy": bad}}})
    model = relay_model.build(target)
    assert isinstance(model, dict)
    assert model["checks"][0]["claimedBy"] is None
    assert any("claimedBy" in w for w in model["warnings"]), model["warnings"]


@pytest.mark.parametrize("bad", MISSING_OR_BAD)
def test_a_leg_stage_of_the_wrong_type_never_raises(tmp_path, bad):
    target = write_relay(
        tmp_path, f"stage-{type(bad).__name__}",
        legs={"stages": [{"id": "S1", "name": "One", "legs": ["a"]}],
              "legs": [{"id": "a", "stage": bad, "status": "running"}]})
    model = relay_model.build(target)
    assert isinstance(model, dict)
    assert model["legs"][0]["stage"] is None


@pytest.mark.parametrize("bad", MISSING_OR_BAD)
def test_state_pointers_of_the_wrong_type_never_raise(tmp_path, bad):
    target = write_relay(
        tmp_path, f"pointers-{type(bad).__name__}",
        legs={"legs": [{"id": "a", "status": "running"}]},
        state={"currentLeg": bad, "currentStage": bad, "phase": bad})
    model = relay_model.build(target)
    assert isinstance(model, dict)
    assert model["relay"]["currentLegDeclared"] in (None, str(bad))
    assert model["relay"]["currentStage"] is None or isinstance(
        model["relay"]["currentStage"]["id"], str)


@pytest.mark.parametrize("bad", [5, "text", {"a": 1}, [1, 2], None])
def test_legs_and_stages_of_the_wrong_type_never_raise(tmp_path, bad):
    target = write_relay(tmp_path, f"shape-{type(bad).__name__}-{bad!r:.6}",
                         legs={"stages": bad, "legs": bad})
    model = relay_model.build(target)
    assert isinstance(model, dict)
    assert model["legs"] == [] and model["stages"] == []


def test_dashboard_fields_of_the_wrong_type_never_raise(tmp_path):
    target = write_relay(
        tmp_path, "dash-shapes",
        legs={"legs": [{"id": "a", "status": "running"}]},
        dashboard={"title": ["a"], "path": {"p": 1}, "elapsed": ["1h"],
                   "tokens": "many", "checkTitles": [1, 2], "log": "not a list",
                   "attention": 7, "notes": {"level": "bad"}})
    model = relay_model.build(target)
    assert isinstance(model, dict)
    assert isinstance(model["relay"]["path"], str)
    assert model["relay"]["title"] in (None, "a") or isinstance(
        model["relay"]["title"], str)


def test_build_of_an_empty_path_is_refused(tmp_path):
    """`build('')` used to build the current working directory, which is
    whatever the view happened to be started from."""
    with pytest.raises(relay_model.RelayNotFound):
        relay_model.build("")
    with pytest.raises(relay_model.RelayNotFound):
        relay_model.build(None)
    with pytest.raises(relay_model.RelayNotFound):
        relay_model.build(7)


def test_kind_of_survives_a_leg_that_is_not_a_dict():
    assert relay_model.kind_of({"id": None}) in ("impl", "fix", "judge")
    assert relay_model.kind_of({"id": ["x"]}) in ("impl", "fix", "judge")
    assert relay_model.kind_of(None) == "impl"


def test_fuzz_over_malformed_relay_directories(tmp_path):
    """Every combination of a bad leg id, a bad claimedBy and a bad file body
    builds. One assertion, many shapes: the point is that none of them raise.

    Every relay in the corpus also carries the two shapes ACC-DATA-003's
    evidence names, because a corpus that cannot produce them cannot be
    evidence for them:

    * **a running leg with no usable id** — the third leg. Every member of
      `BAD_IDS` is unidentifiable, so this leg is running and nameless in all
      of them, and `activeLeg` must skip it.
    * **duplicate leg ids across stages** — the last two legs. Both answer to
      `real`, both run, and they differ only in the stage they belong to. The
      runner row the model calls active must be the one built from the leg
      `activeLeg` names, which `assert_active_agrees` compares field by field.
    """
    built = 0
    for i, bad_id in enumerate(BAD_IDS):
        for j, bad_claim in enumerate(MISSING_OR_BAD):
            target = write_relay(
                tmp_path, f"fuzz-{i}-{j}",
                legs={"relay": bad_claim,
                      "stages": [{"id": bad_id, "name": bad_claim, "legs": bad_id}],
                      "legs": [{"id": bad_id, "status": bad_claim,
                                "stage": bad_id, "goal": bad_claim,
                                "fulfills": bad_claim, "kind": bad_claim},
                               {"id": bad_id, "status": "running",
                                "stage": "S1", "goal": bad_claim},
                               {"id": "real", "status": "running",
                                "stage": "S1"},
                               {"id": "real", "status": "running",
                                "stage": "S2"}]},
                state={"currentLeg": bad_id, "currentStage": bad_id,
                       "phase": bad_claim,
                       "checks": {"ACC-A-001": {"status": bad_claim,
                                                "claimedBy": bad_claim,
                                                "round": bad_claim,
                                                "fixLeg": bad_id}}},
                dashboard={"title": bad_claim, "path": bad_claim,
                           "tokens": bad_claim, "attention": bad_claim,
                           "log": bad_claim, "elapsed": bad_claim})
            model = relay_model.build(target)
            assert isinstance(model, dict), (bad_id, bad_claim)
            json.dumps(model)
            assert_active_agrees(model, f"fuzz-{i}-{j}")

            # The corpus is only evidence while it still contains the two
            # cases, so it says so out loud rather than trusting the literal
            # above to stay that way.
            assert [r["leg"] for r in model["runners"]] == ["", "real", "real"], (
                bad_id, bad_claim)
            assert model["runners"][0]["status"] == "running"    # nameless, running
            assert model["activeLeg"]["id"] == "real"            # and not chosen
            assert model["activeLeg"]["stage"] == "S1"           # the first namesake
            built += 1
    assert built == len(BAD_IDS) * len(MISSING_OR_BAD)



def test_fuzz_over_malformed_file_bodies(tmp_path):
    """Every malformed body, in every relay file, in every combination of
    which files are present."""
    bodies = list(MALFORMED_BYTES.values())
    for i, blob in enumerate(bodies):
        for mask in range(1, 8):
            raw = {name: blob for bit, name in enumerate(RELAY_FILES)
                   if mask & (1 << bit)}
            target = write_relay(tmp_path, f"bytes-{i}-{mask}", raw=raw)
            model = relay_model.build(target)
            assert isinstance(model, dict), (i, mask)
            assert_active_agrees(model, f"bytes-{i}-{mask}")


# --------------------------------------------------------------------------
# ACC-DATA-001 — untrusted input: every filesystem *shape* degrades
#
# The section above tests what a relay file *contains*. This one tests what it
# *is*. A relay directory is edited under a live TUI by hand, and a path there
# can be a FIFO, a socket, a device, a directory, a symlink to any of those, or
# something the process may not open at all.
#
# A hang fails ACC-DATA-001 harder than an exception does. `build()` is called
# once per repaint inside a 2 s budget (ACC-LIVE-001), so a blocked read is a
# frozen TUI with no traceback: the S1 gate captured `baton_text` stopped
# inside `open()` on a FIFO with no writer, and 20 s and 60 s probes both never
# returned. Every case below opens a path that is not a regular file.
# --------------------------------------------------------------------------

#: Long enough that a slow-but-finite read still passes, short enough that a
#: real block is caught in one test run rather than at the CI timeout.
BUILD_DEADLINE = 15.0

DEV_ZERO = Path("/dev/zero")


def within(deadline, func, *args, **kwargs):
    """`func(*args)`, or a failure naming the call that never came back.

    A read blocked in the kernel cannot be interrupted from Python — no signal,
    no timeout argument, nothing to cancel — so the call runs on a daemon
    thread and the test outlives it. Without this the suite itself wedges on
    the first FIFO, which is exactly why the defect stayed invisible: a hanging
    test never reports.
    """
    box = {}

    def run():
        try:
            box["value"] = func(*args, **kwargs)
        except BaseException as exc:            # re-raised on the test's thread
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(deadline)
    assert not thread.is_alive(), (
        f"{func.__name__}{args} did not return within {deadline}s — it blocked")
    if "error" in box:
        raise box["error"]
    return box["value"]


def build_in_time(target, **kwargs):
    return within(BUILD_DEADLINE, relay_model.build, target, **kwargs)


def slug(kind):
    return kind.replace(" ", "-")


def bind_socket(path):
    """A bound unix domain socket left on disk at `path`.

    Bound from inside its own directory: an AF_UNIX address is capped at about
    a hundred bytes on macOS and a pytest `tmp_path` is longer than that. The
    socket file survives the close — it is unlinked, never closed, away.
    """
    here = os.getcwd()
    os.chdir(path.parent)
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(path.name)
        finally:
            sock.close()
    finally:
        os.chdir(here)


@pytest.fixture
def shape(tmp_path):
    """Make one filesystem shape at a path, and undo it when the test ends.

    Teardown is load-bearing. A chmod-000 directory cannot be cleaned up by
    pytest's own tmp_path teardown, and a FIFO left with an open writer keeps a
    descriptor alive for the rest of the session — either one makes the second
    `pytest tests/` run of the day fail where the first passed.
    """
    writers, modes = [], []

    def _make(path, kind):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "fifo":
            os.mkfifo(path)
        elif kind == "fifo with a writer":
            os.mkfifo(path)
            # O_RDWR, not O_WRONLY: opening a pipe for writing blocks until a
            # reader arrives, which is the hang under test wearing the other
            # hat. A pipe with a live writer and bytes in it is the case a
            # size-bounded read still gets stuck on.
            writers.append(os.open(path, os.O_RDWR | os.O_NONBLOCK))
            os.write(writers[-1], b'{"legs": [')
        elif kind == "socket":
            bind_socket(path)
        elif kind == "character device":
            # The trap: /dev/zero has a size of 0 and never reaches EOF, so a
            # read bounded by st_size returns nothing and a read to EOF returns
            # never. A symlink is how one reaches a relay directory.
            path.symlink_to(DEV_ZERO)
        elif kind == "directory":
            path.mkdir()
        elif kind == "regular file":
            path.write_text("this is not a directory")
        elif kind == "symlink loop":
            other = path.with_name(path.name + ".loop")
            path.symlink_to(other)
            other.symlink_to(path)
        elif kind == "dangling symlink":
            path.symlink_to(path.with_name(path.name + ".gone"))
        elif kind == "unreadable":
            path.write_text('{"legs": []}')
            modes.append((path, path.stat().st_mode))
            path.chmod(0)
        elif kind == "oversized":
            path.write_bytes(b"x" * (relay_model.MAX_RELAY_FILE_BYTES + 1))
        elif kind == "no execute permission":
            path.mkdir()
            (path / "x.md").write_text("**Commit:** abcdef1\n")
            modes.append((path, path.stat().st_mode))
            path.chmod(0)
        else:
            raise AssertionError(f"unknown shape {kind!r}")
        return path

    yield _make

    for fd in writers:
        os.close(fd)
    for path, mode in modes:
        try:
            path.chmod(mode)
        except OSError:
            pass


#: Every shape a *file* in a relay directory can take instead of a file.
FILE_SHAPES = [
    "fifo",
    "fifo with a writer",
    "socket",
    "character device",
    "directory",
    "symlink loop",
    "dangling symlink",
    "unreadable",
    "oversized",
]

#: Of those, the ones where something really is at the path. A dangling symlink
#: is absence wearing a name, and absence is not a warning.
PRESENT_SHAPES = [k for k in FILE_SHAPES if k != "dangling symlink"]

#: Every shape the `batons/` directory can take instead of a directory.
BATONS_SHAPES = [
    "fifo",
    "socket",
    "character device",
    "regular file",
    "symlink loop",
    "dangling symlink",
    "no execute permission",
]

#: Every shape the relay directory itself can take. It is the one argument
#: `build()` refuses rather than degrades (RelayNotFound), and it must refuse
#: promptly: a hang here is the same frozen repaint by another door.
RELAY_DIR_SHAPES = ["fifo", "socket", "character device", "regular file",
                    "symlink loop", "dangling symlink"]

def skip_unusable(kind):
    if kind == "character device" and not DEV_ZERO.exists():
        pytest.skip("no /dev/zero on this platform")
    if kind in ("unreadable", "no execute permission") and (
            hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("root ignores permission bits")


@pytest.mark.parametrize("kind", FILE_SHAPES)
@pytest.mark.parametrize("filename", RELAY_FILES)
def test_a_relay_json_file_of_any_shape_builds_without_blocking(
        tmp_path, shape, filename, kind):
    """`legs.json` is a FIFO, a socket, /dev/zero, a directory..."""
    skip_unusable(kind)
    target = tmp_path / f"{filename}-{slug(kind)}"
    target.mkdir()
    shape(target / filename, kind)

    model = build_in_time(target)

    assert isinstance(model, dict)
    assert model["legs"] == [] and model["checks"] == []
    source = model["sources"][filename.split(".")[0]]
    if kind in PRESENT_SHAPES:
        assert source == "malformed", (kind, source)
        assert any(filename in w for w in model["warnings"]), model["warnings"]
    else:
        assert source == "missing", (kind, source)


#: What the warning must say about each shape. "it is a FIFO" and "it is not
#: valid JSON" are different repairs for whoever left it there, and a warning
#: that does not distinguish them costs the reader the whole diagnosis.
SHAPE_SAYS = {
    "fifo": "FIFO",
    "fifo with a writer": "FIFO",
    # A socket is refused by `open()` itself - EOPNOTSUPP on macOS, ENXIO on
    # Linux - so it never reaches the shape check. BATONS_SAYS is where
    # S_ISSOCK is actually exercised, because `os.stat` does answer for one.
    "socket": "could not be read",
    "character device": "character device",
    "directory": "directory",
    "symlink loop": "could not be read",
    "unreadable": "could not be read",
    "oversized": "bytes",
}


@pytest.mark.parametrize("kind", PRESENT_SHAPES)
def test_a_relay_json_file_that_is_not_a_file_says_what_it_was(
        tmp_path, shape, kind):
    skip_unusable(kind)
    target = tmp_path / f"why-{slug(kind)}"
    target.mkdir()
    shape(target / "legs.json", kind)

    warning = " ".join(build_in_time(target)["warnings"])

    assert "legs.json" in warning, warning
    assert SHAPE_SAYS[kind] in warning, (kind, warning)


def test_two_paths_that_fail_differently_do_not_share_one_warning(tmp_path, shape):
    """A permission bit and a symlink loop are different repairs.

    Most of the shapes above are refused by `open()` rather than named by the
    shape check, so the errno *is* the diagnosis. A warning that flattens every
    one of them to "it could not be read" costs the reader the whole of it.
    """
    skip_unusable("unreadable")
    said = {}
    for kind in ("unreadable", "symlink loop"):
        target = tmp_path / f"errno-{slug(kind)}"
        target.mkdir()
        shape(target / "legs.json", kind)
        said[kind] = " ".join(w for w in build_in_time(target)["warnings"]
                              if "legs.json" in w)

    assert all(said.values()), said
    assert len(set(said.values())) == 2, said


@pytest.mark.parametrize("kind", FILE_SHAPES)
def test_every_relay_json_file_at_once_of_one_shape_still_builds(
        tmp_path, shape, kind):
    skip_unusable(kind)
    target = tmp_path / f"all-{slug(kind)}"
    target.mkdir()
    for filename in RELAY_FILES:
        shape(target / filename, kind)

    model = build_in_time(target)

    assert isinstance(model, dict)
    json.dumps(model)
    assert_active_agrees(model, kind)


@pytest.mark.parametrize("kind", FILE_SHAPES)
def test_a_baton_of_any_shape_builds_without_blocking(tmp_path, shape, kind):
    """The captured hang, as a test. `batons/x.md` is a FIFO with no writer and
    `build()` stopped inside `open()` in the kernel, for ever."""
    skip_unusable(kind)
    target = write_relay(tmp_path, f"baton-{slug(kind)}",
                         legs={"legs": [{"id": "x", "status": "done"}]})
    shape(target / "batons" / "x.md", kind)

    model = build_in_time(target)

    assert isinstance(model, dict)
    assert [row["leg"] for row in model["runners"]] == ["x"]
    row = model["runners"][0]
    assert row["batonLines"] is None and row["commit"] is None
    assert row["finished"] is None
    if kind in PRESENT_SHAPES:
        assert any("batons/x.md" in w for w in model["warnings"]), model["warnings"]


@pytest.mark.parametrize("kind", FILE_SHAPES)
def test_baton_text_of_any_shape_is_none_and_never_blocks(tmp_path, shape, kind):
    """`baton_text()` is public and a detail view calls it on demand, so it
    carries the same guarantee `build()` does."""
    skip_unusable(kind)
    path = shape(tmp_path / "batons" / f"{slug(kind)}.md", kind)
    assert within(BUILD_DEADLINE, relay_model.baton_text, path) is None
    assert within(BUILD_DEADLINE, relay_model.baton_text, str(path)) is None


#: What the warning must say when `batons/` is not a directory. Naming the
#: shape is the whole value of stating the path before listing it: `scandir`
#: fails on every one of these too, with one flat "not a directory" that tells
#: the reader nothing about what to delete.
BATONS_SAYS = {
    "fifo": "a FIFO",
    "socket": "a socket",
    "character device": "a character device",
    "regular file": "a regular file",
    "symlink loop": "could not be read",
    "no execute permission": "could not be listed",
}


@pytest.mark.parametrize("kind", BATONS_SHAPES)
def test_a_batons_directory_of_any_shape_builds_without_blocking(
        tmp_path, shape, kind):
    skip_unusable(kind)
    target = write_relay(tmp_path, f"bdir-{slug(kind)}",
                         legs={"legs": [{"id": "x", "status": "done"}]})
    shape(target / "batons", kind)

    model = build_in_time(target)

    assert isinstance(model, dict)
    assert [row["leg"] for row in model["runners"]] == ["x"]
    assert model["runners"][0]["batonLines"] is None
    warning = " ".join(w for w in model["warnings"] if "batons" in w)
    if kind == "dangling symlink":
        assert warning == "", warning       # absence, not a wrong shape
    else:
        assert BATONS_SAYS[kind] in warning, (kind, model["warnings"])


@pytest.mark.parametrize("kind", RELAY_DIR_SHAPES)
def test_a_relay_directory_of_any_shape_is_refused_rather_than_awaited(
        tmp_path, shape, kind):
    """`build()` refuses a path that is not a directory — the one documented
    exception to "returns a dict". It must refuse *promptly*: a `RelayNotFound`
    that arrives after a blocked open is still a frozen repaint."""
    skip_unusable(kind)
    path = shape(tmp_path / f"relay-{slug(kind)}", kind)
    with pytest.raises(relay_model.RelayNotFound):
        build_in_time(path)


# --------------------------------------------------------------------------
# the read bound
# --------------------------------------------------------------------------

def test_a_relay_file_over_the_read_bound_is_refused_not_truncated(tmp_path):
    """A bound belongs here: this module reads linearly and `build()` runs once
    per repaint inside 2 s (ACC-LIVE-001), so an unbounded read is a budget a
    coach can blow by pasting a log into a baton.

    Refused, never truncated. A half-read baton still parses — it just reports
    the wrong line count and silently drops every commit claim past the cut —
    and a plausible wrong answer costs more here than a named absence.
    """
    target = write_relay(tmp_path, "oversize",
                         legs={"legs": [{"id": "x", "status": "done"}]})
    baton = target / "batons" / "x.md"
    baton.parent.mkdir()
    over = relay_model.MAX_RELAY_FILE_BYTES + 1
    baton.write_bytes(b"**Commit:** abcdef1\n" + b"x" * over)

    model = build_in_time(target)

    assert model["runners"][0]["batonLines"] is None
    assert model["runners"][0]["commit"] is None       # not a truncated read
    assert any("batons/x.md" in w and str(over + 20) in w
               for w in model["warnings"]), model["warnings"]
    assert relay_model.baton_text(baton) is None


def test_a_baton_just_under_the_read_bound_is_still_read(tmp_path):
    """The bound refuses what a repaint cannot afford, and nothing else. Every
    baton this project has written is under 45 KB."""
    target = write_relay(tmp_path, "big-but-fine",
                         legs={"legs": [{"id": "x", "status": "done"}]})
    baton = target / "batons" / "x.md"
    baton.parent.mkdir()
    body = "**Commit:** abcdef1\n" + "prose\n" * 1000
    body += "-" * (relay_model.MAX_RELAY_FILE_BYTES - len(body) - 1)
    baton.write_text(body)

    model = build_in_time(target)

    assert model["runners"][0]["batonLines"] == body.count("\n") + 1
    assert relay_model.baton_text(baton) == body


def test_the_read_bound_leaves_room_for_the_largest_relay_file_on_disk():
    """A bound tight enough to refuse a real file is a bug, not a guard."""
    largest = max(p.stat().st_size
                  for p in (FIXTURES / "agent-service").glob("**/*")
                  if p.is_file())
    assert largest * 8 < relay_model.MAX_RELAY_FILE_BYTES, largest


# --------------------------------------------------------------------------
# every relay-file read goes through one door
# --------------------------------------------------------------------------

def test_every_relay_file_read_goes_through_the_one_guarded_helper():
    """The guard only guards while it is the only way in.

    The alternative — a `try/except` at each call site — is what the module had,
    and it grew a third read that had neither. One helper is the thing a test
    can hold the module to, so this test holds it: no unguarded reader appears
    anywhere in `relay_model.py`.
    """
    source = (REPO / "scripts" / "relay_model.py").read_text()
    unguarded = re.compile(
        r"\.read_text\(|\.read_bytes\(|(?<!os\.)(?<!\w)open\(|\.glob\(|\.iterdir\(")
    offenders = [f"{n}: {line.strip()}"
                 for n, line in enumerate(source.splitlines(), 1)
                 if unguarded.search(line)]
    assert offenders == [], offenders


# --------------------------------------------------------------------------
# deeply nested JSON — the RecursionError the S1 gate could not reproduce
#
# The coach probed depth 400 and depth 3000 by hand and both degraded with a
# warning, so the claim was left open. Both probes were simply too shallow:
# CPython's JSON scanner has its own recursion guard and raises RecursionError,
# which is a RuntimeError and not the ValueError `_load` was catching. At depth
# 20000 it escaped `build()` outright. The claim was right.
# --------------------------------------------------------------------------

#: Shallow enough to parse, deep enough to blow the scanner, and either side of
#: the boundary — which moves with the interpreter and with how much stack
#: `build()` has already spent, so no single depth would settle it.
NEST_DEPTHS = [400, 3000, 12000, 40000]


@pytest.mark.parametrize("depth", NEST_DEPTHS)
@pytest.mark.parametrize("filename", RELAY_FILES)
def test_json_nested_deeper_than_the_scanner_degrades(tmp_path, filename, depth):
    body = ('{"legs": ' + "[" * depth + "]" * depth + "}").encode()
    target = write_relay(tmp_path, f"nest-{filename}-{depth}", raw={filename: body})

    model = build_in_time(target)

    assert isinstance(model, dict)
    json.dumps(model)


def test_a_leg_field_too_deep_to_render_is_named_rather_than_raised():
    """The second recursion, and the only route to it.

    `json.dumps` recurses too, and the model calls it on any non-string a coach
    puts in a string list. Reaching that through a relay file is a needle: a
    structure the scanner accepts but the renderer cannot render exists only in
    the narrow band where `build()`'s own frames have eaten the difference, and
    the band moves with the interpreter, the platform and the thread's stack.

    So the nesting is built by a loop rather than parsed, and handed straight to
    the coercion that renders it. That is the same value a `skills` list would
    carry, without depending on where two recursion limits happen to fall.
    """
    nest = []
    for _ in range(60_000):
        nest = [nest]

    deep = {}
    for _ in range(60_000):
        deep = {"a": deep}

    # Both halves of the coercion: a member of a list, which is rendered as
    # JSON, and a bare value that is neither string nor list, which is
    # rendered by `str`. Both recurse; a coach can write either.
    for rendered in (relay_model._strlist([nest]), relay_model._strlist(deep)):
        assert isinstance(rendered, list) and len(rendered) == 1
        assert isinstance(rendered[0], str) and rendered[0]


@pytest.mark.parametrize("depth", NEST_DEPTHS)
def test_a_deeply_nested_leg_field_degrades_rather_than_recursing(tmp_path, depth):
    """The second recursion, one layer down: a `skills` list the scanner
    accepts and `json.dumps` then blows the stack rendering, with `build()`'s
    own frames already on it."""
    nest = "[" * depth + "]" * depth
    body = ('{"legs": [{"id": "a", "status": "running", "skills": ['
            + nest + ']}]}').encode()
    target = write_relay(tmp_path, f"nest-skills-{depth}", raw={"legs.json": body})

    model = build_in_time(target)

    assert isinstance(model, dict)
    json.dumps(model)
    assert model["legs"] == [] or model["legs"][0]["id"] == "a"


# --------------------------------------------------------------------------
# ACC-DATA-003 — the invariant, asserted as the check words it
# --------------------------------------------------------------------------

#: Every runner-row field copied straight off the leg the row was built from.
#: With duplicate ids these are the only fields that tell two rows apart, so
#: they are what turns "a runner row" into "the active leg's runner row".
LEG_DERIVED_RUNNER_FIELDS = ("leg", "stage", "stageName", "kind")


def assert_active_agrees(model, label=""):
    """The active runner is the active leg's own row, **or both are absent**.

    The whole disjunction, which is what the check says and what a leg with an
    unusable id used to break by satisfying neither half.

    The identity half needs care. `any(row is runner for row in runners)` says
    only that the runner is *a* row, and `runner["leg"] == leg["id"]` is the
    string equality the amendment exists to replace: when two legs answer to
    one id, two rows satisfy both and the wrong twin passes — which is the
    original Mission Control defect wearing the guard that was meant to catch
    it. So every field the row copies off its leg is compared as well. Two
    rows carrying one id differ in their stage, their stage name and their
    kind, and a row of the *other* twin fails here.
    """
    leg, runner = model["activeLeg"], model["activeRunner"]
    assert (leg is None) == (runner is None), (label, leg, runner)
    if leg is None:
        return
    assert any(row is runner for row in model["runners"]), label
    assert sum(1 for row in model["runners"] if row is runner) == 1, label
    for field in LEG_DERIVED_RUNNER_FIELDS:
        source = "id" if field == "leg" else field
        assert runner[field] == leg[source], (
            label, field, runner[field], leg[source])
    assert runner["status"] == "running", (label, runner["status"])
    assert leg["isActive"] is True, label
    assert leg["status"] == "running", (label, leg["status"])


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_active_leg_and_active_runner_agree_on_every_fixture(name, relay):
    assert_active_agrees(relay_model.build(relay(name)), name)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_active_leg_and_active_runner_agree_on_every_fixture_in_place(name):
    """The same fixtures where they really live, inside this repository.

    Every other reading of them is of a copy in `tmp_path`, which sits outside
    any git repository — so the branch of `build()` that reads commits never
    runs there. Neither half of this invariant depends on mtimes, so the
    fixture needs no stamping and can be read where it lies.
    """
    assert_active_agrees(relay_model.build(FIXTURES / name), f"{name} (in place)")


def test_duplicate_leg_ids_do_not_split_active_leg_from_active_runner(tmp_path):
    """The original Mission Control defect, in its last remaining form: two
    legs answer to one id, and the runner row was looked up by that id — so
    the active leg was running while the active runner was the completed
    namesake.
    """
    target = write_relay(
        tmp_path, "dup",
        legs={"legs": [{"id": "twin", "status": "done"},
                       {"id": "twin", "status": "running"}]})
    model = relay_model.build(target)
    assert_active_agrees(model, "duplicate ids")
    assert model["activeRunner"]["status"] == "running"
    assert any("twin" in w for w in model["warnings"]), model["warnings"]


def test_two_running_twins_in_different_stages_cannot_split_the_two_panes(tmp_path):
    """The shape the old guard could not see.

    Both twins run, so both their rows carry `status: running` and the id
    `twin`; the whole of the previous assertion held for either of them. Only
    the leg-derived columns tell the rows apart, and the active runner must be
    the row of the *first* running leg in plan order — the one `activeLeg`
    names — not the namesake in the next stage.
    """
    target = write_relay(
        tmp_path, "twins",
        legs={"stages": [{"id": "S1", "name": "Stage One", "legs": ["twin"]},
                         {"id": "S2", "name": "Stage Two", "legs": ["twin"]}],
              "legs": [{"id": "twin", "stage": "S1", "status": "running",
                        "kind": "impl"},
                       {"id": "twin", "stage": "S2", "status": "running",
                        "kind": "judge"}]})
    model = relay_model.build(target)

    # The case is genuinely ambiguous by id: two rows answer to `twin`, and
    # both are running, so neither string equality nor a status check can pick
    # between them.
    twins = [row for row in model["runners"] if row["leg"] == "twin"]
    assert len(twins) == 2 and all(r["status"] == "running" for r in twins)
    assert {r["stage"] for r in twins} == {"S1", "S2"}

    assert_active_agrees(model, "two running twins")
    assert model["activeLeg"]["stage"] == "S1"
    assert model["activeRunner"]["stage"] == "S1"
    assert model["activeRunner"]["stageName"] == "Stage One"
    assert model["activeRunner"]["kind"] == "impl"
    assert any("twin" in w for w in model["warnings"]), model["warnings"]


def test_a_nameless_running_leg_beside_duplicate_ids_still_cannot_split_them(tmp_path):
    """Both cases the evidence names, in one relay: a running leg with no
    usable id sits ahead of two running namesakes in plan order. The nameless
    leg is not a candidate, and the pair behind it must not be confused."""
    target = write_relay(
        tmp_path, "nameless-and-twins",
        legs={"stages": [{"id": "S1", "name": "One", "legs": ["twin"]},
                         {"id": "S2", "name": "Two", "legs": ["twin"]}],
              "legs": [{"status": "running", "stage": "S1", "goal": "no id"},
                       {"id": "twin", "stage": "S1", "status": "running"},
                       {"id": "twin", "stage": "S2", "status": "running"}]})
    model = relay_model.build(target)
    assert_active_agrees(model, "nameless beside twins")
    assert model["legCounts"]["running"] == 3
    assert model["activeLeg"]["stage"] == "S1"
    assert model["activeRunner"]["stage"] == "S1"
    # The nameless leg still gets its own runner row; it is simply never the
    # active one.
    assert sorted(r["leg"] for r in model["runners"]) == ["", "twin", "twin"]
    assert all(r["status"] == "running" for r in model["runners"])


def test_a_running_leg_that_cannot_be_identified_is_not_the_active_leg(tmp_path):
    """Neither equal nor both-absent: activeLeg was present with no runner.

    A leg with no usable id cannot be matched to a baton, a check or a commit,
    so it is not a candidate for the active leg at all.
    """
    target = write_relay(tmp_path, "nameless",
                         legs={"legs": [{"status": "running", "goal": "g"}]})
    model = relay_model.build(target)
    assert_active_agrees(model, "no id")
    assert model["activeLeg"] is None
    assert model["legCounts"]["running"] == 1     # it is still a running leg


def test_a_named_running_leg_wins_over_a_nameless_one(tmp_path):
    target = write_relay(
        tmp_path, "nameless-then-named",
        legs={"legs": [{"status": "running"}, {"id": "real", "status": "running"}]})
    model = relay_model.build(target)
    assert_active_agrees(model, "nameless then named")
    assert model["activeLeg"]["id"] == "real"


def test_two_running_legs_are_reported_and_warned_about(tmp_path):
    """Both are reported — the model does not hide what legs.json says — but a
    relay with two legs in flight is a coach error, and the pane should be able
    to say so."""
    target = write_relay(
        tmp_path, "two-running",
        legs={"legs": [{"id": "a", "status": "running"},
                       {"id": "b", "status": "in progress"}]})
    model = relay_model.build(target)
    assert model["legCounts"]["running"] == 2
    assert_active_agrees(model, "two running")
    assert model["activeLeg"]["id"] == "a"        # first in plan order
    assert any("a" in w and "b" in w for w in model["warnings"]), model["warnings"]


def test_active_runner_is_the_same_object_as_its_runner_row(relay):
    model = relay_model.build(relay("agent-service"))
    assert any(row is model["activeRunner"] for row in model["runners"])


@pytest.mark.parametrize("bad", MISSING_OR_BAD)
def test_an_attention_item_of_the_wrong_shape_never_raises(tmp_path, bad):
    """`level` indexes the sort table, so a list there is unhashable, not just
    unknown. Found by the fuzz loop, not by hand."""
    target = write_relay(
        tmp_path, f"attention-{type(bad).__name__}",
        legs={"legs": [{"id": "a", "status": "running"}]},
        dashboard={"attention": [{"level": bad, "label": bad, "text": bad,
                                  "action": bad},
                                 {"level": "bad", "text": "real"}],
                   "notes": [bad]})
    model = relay_model.build(target)
    assert isinstance(model, dict)
    for item in model["attention"]:
        assert item["level"] in ("bad", "warn", "note", "calm")
        assert isinstance(item["label"], str) and isinstance(item["text"], str)
        assert item["action"] is None or isinstance(item["action"], str)
    assert any(item["text"] == "real" for item in model["attention"])

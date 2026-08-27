"""Tests for the shared relay view-model (scripts/relay_model.py).

One test (or one class) per DATA check in `.relay/contract.md`, plus the
adversarial fixtures. Every fixture under `tests/fixtures/` is a frozen relay
directory: the files a coach would have written, and nothing generated.

`tests/fixtures/agent-service/` is a frozen copy of a real in-flight relay,
taken 2026-08-24. Baton mtimes carry the landing order of its runners, and git
does not preserve mtimes, so every test that cares about ordering works against
a copy in `tmp_path` with the recorded mtimes stamped back on (see `relay()`).
"""

import ast
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
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

# Every relay directory under `tests/fixtures/`, DERIVED from disk rather than
# listed by hand. ACC-DATA-001's sweep is only a sweep while it reads all of
# them, and a hardcoded list stops being one the moment a leg adds a fixture:
# `running-impl` landed from a leg whose boundaries excluded this module, and
# the sweep silently covered nine directories of ten. Deriving it closes the
# class rather than that instance - a fixture is swept the moment it exists.
# `test_the_fixture_sweep_reads_every_fixture_on_disk` holds this to that.
ALL_FIXTURES = sorted(p.name for p in FIXTURES.iterdir()
                      if p.is_dir() and not p.name.startswith("."))

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


def test_the_fixture_sweep_reads_every_fixture_on_disk():
    """The sweep above, and the dozen parametrised over the same list, are
    evidence only while the list is the whole corpus. It used to be typed out
    by hand, so a fixture added by a leg that could not edit this file was
    swept by nothing and the suite stayed green at nine directories of ten."""
    on_disk = sorted(p.name for p in FIXTURES.iterdir()
                     if p.is_dir() and not p.name.startswith("."))
    assert on_disk, "there are fixtures on disk to sweep"
    assert ALL_FIXTURES == on_disk
    assert "running-impl" in ALL_FIXTURES        # the one the list had missed
    assert len(ALL_FIXTURES) >= 10


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
        named = [line.strip() for line in lines if RELAY_FILE_NAMES.search(line)]
        reads = [line.strip() for line in lines if RELAY_READS.search(line)]
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
    by_id = {leg["id"]: leg for leg in model["legs"]}

    # state.json still names it; legs.json says done. legs.json wins.
    assert model["relay"]["currentLegDeclared"] == "open-and-green-mr"
    assert by_id["open-and-green-mr"]["status"] == "completed"

    running = [leg for leg in model["legs"] if leg["status"] == "running"]
    assert len(running) == 1, [leg["id"] for leg in running]
    assert running[0]["id"] == "cutover-flip"
    assert model["activeLeg"]["id"] == "cutover-flip"
    assert model["legCounts"]["running"] == 1


def test_agent_service_reports_one_running_leg_not_two(relay):
    """The defect as observed: currentLeg named a done leg and the renderer
    forced it to display running, giving two In Progress legs."""
    model = relay_model.build(relay("agent-service"))
    by_id = {leg["id"]: leg for leg in model["legs"]}

    assert model["relay"]["currentLegDeclared"] == "open-and-green-mr"
    assert by_id["open-and-green-mr"]["status"] == "completed"

    running = [leg for leg in model["legs"] if leg["status"] == "running"]
    assert len(running) == 1, [leg["id"] for leg in running]
    assert running[0]["id"] == "code-judge-S3-r2"
    assert model["activeLeg"]["id"] == "code-judge-S3-r2"


def test_active_leg_is_the_first_running_leg_in_plan_order(relay):
    model = relay_model.build(relay("agent-service"))
    running = [leg for leg in model["legs"] if leg["status"] == "running"]
    assert model["activeLeg"]["id"] == running[0]["id"]
    assert model["activeLeg"]["order"] == min(leg["order"] for leg in running)


def test_currentleg_naming_a_missing_leg_is_not_invented(relay):
    model = relay_model.build(relay("ghost-currentleg"))
    assert model["relay"]["currentLegDeclared"] == "a-leg-that-does-not-exist"
    assert [leg["id"] for leg in model["legs"]] == ["one", "two"]
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
    by_id = {leg["id"]: leg for leg in model["legs"]}
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
        # ACC-DATA-003 is identity, not string equality — and this test is the
        # one the amendment was written about: `runner["leg"] == leg["id"]`
        # HELD throughout the duplicate-id defect, because both twins answer to
        # the one id. `activeRunner = dict(row)` passes it too. The equality is
        # kept because it names the failure readably; the identity is what
        # decides.
        assert runner["leg"] == leg["id"], name
        assert any(row is runner for row in model["runners"]), name
        assert any(row is leg for row in model["legs"]), name


def test_active_runner_is_present_when_a_leg_runs(relay):
    model = relay_model.build(relay("agent-service"))
    assert model["activeRunner"]["leg"] == "code-judge-S3-r2"
    assert model["activeRunner"]["status"] == "running"
    # `in` on a list of dicts is `==`, which a detached copy satisfies. The
    # runner must BE one of the rows (ACC-DATA-003).
    assert any(row is model["activeRunner"] for row in model["runners"])


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
        running = sum(1 for leg in model["legs"] if leg["status"] == "running")
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


#: The spellings ACC-DATA-004 names by name. The check does not say "some
#: reasonable set of aliases"; it lists these nine, so these nine are what the
#: vocabulary owes.
CONTRACT_SPELLINGS = {
    "done": "completed", "complete": "completed", "finished": "completed",
    "DONE": "completed",
    "in progress": "running", "in_progress": "running", "wip": "running",
    "TODO": "pending", "queued": "pending",
}


@pytest.mark.parametrize("raw,expected", sorted(CONTRACT_SPELLINGS.items()))
def test_a_named_spelling_is_recognised_not_merely_defaulted(raw, expected):
    """Membership in the alias set, not only the mapped result.

    `normalise_status` falls back to `pending`, so a pending alias maps
    correctly whether or not the table contains it: deleting `queued` left the
    whole suite green, and `def normalise_status(v): return "pending"` would
    satisfy two of the nine spellings this check names. Asserting the result
    alone cannot tell a recognised word from an unrecognised one. Asserting
    membership can — and it is what makes the `TODO` and `queued` rows of the
    table above evidence rather than arithmetic.
    """
    normalised = raw.strip().lower().replace(" ", "_")
    assert normalised in relay_model.STATUS_ALIASES[expected], (
        f"{raw!r} is not in the {expected} alias set; it only reaches "
        f"{relay_model.normalise_status(raw)!r} through the fallback")
    assert relay_model.normalise_status(raw) == expected


def test_an_unrecognised_word_is_pending_and_is_in_no_alias_set():
    """The other side of the membership rule: `pending` is still the answer for
    a word the vocabulary does not know, and the test above must not be
    satisfiable by adding every string to the table."""
    assert relay_model.normalise_status("blorp") == "pending"
    for aliases in relay_model.STATUS_ALIASES.values():
        assert "blorp" not in aliases


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
    kinds = {leg["id"]: leg["kind"] for leg in model["legs"]}
    assert kinds["cutover-flip"] == "impl"
    assert kinds["code-judge-S3-r2"] == "judge"      # no `kind` field; id says judge
    assert kinds["fix-agenttype-write-paths"] == "fix"
    assert set(kinds.values()) <= {"impl", "fix", "judge"}


def test_fix_kind_from_repairs(relay):
    model = relay_model.build(relay("all-done"))
    kinds = {leg["id"]: leg["kind"] for leg in model["legs"]}
    assert kinds["b"] == "fix"


def test_cancelled_leg_normalises(relay):
    model = relay_model.build(relay("all-done"))
    by_id = {leg["id"]: leg for leg in model["legs"]}
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


def test_runner_duration_for_the_active_leg_needs_a_clock(relay):
    """`now=None` is the explicit refusal of a clock, and it is what keeps a
    frame capture deterministic. It is no longer the DEFAULT: ACC-DATA-005
    requires a relative age under the documented one-argument call, so the
    default reads the wall clock and `now=None` is how a test says otherwise.
    """
    target = relay("agent-service")
    refused = relay_model.build(target, now=None)
    assert refused["activeRunner"]["duration"] is None
    assert refused["activeRunner"]["start"] == pytest.approx(1787582967.6)

    pinned = relay_model.build(target, now=1787583000.0)
    assert pinned["activeRunner"]["start"] == pytest.approx(1787582967.6)
    assert pinned["activeRunner"]["duration"] == pytest.approx(32.4, abs=0.5)


def test_the_documented_one_argument_call_measures_the_running_runner(relay):
    """The other half of the same rule. `build(relay_dir)` is the call the
    module documents and every caller makes, and under it the running runner's
    elapsed time is a real measurement rather than an absence."""
    before = time.time()
    model = relay_model.build(relay("agent-service"))
    after = time.time()
    row = model["activeRunner"]
    assert row["duration"] is not None
    # Its start is the previous baton's landing, so the elapsed time is
    # "now minus that", measured against this test's own clock.
    assert (before - row["start"]) <= row["duration"] <= (after - row["start"])


# RETIRED: `test_no_em_dash_anywhere_in_the_model` asserted the pre-amendment
# rule — "no em-dash appears anywhere in the model" — which ACC-DATA-007's
# 2026-08-25 amendment explicitly withdrew: a commit subject, a check's
# evidence text and a coach's attention prose are quoted verbatim and may
# legitimately contain one. It was green only because its corpus had zero
# commit entries in it; against the git corpus below, whose commit subject
# carries an em-dash the way real ones do, it goes red on a CORRECT model.
# The amended rule is asserted by `test_model_carries_no_display_placeholders`
# (a whole string that IS a placeholder), by the runner-column sweeps, and by
# `test_a_quoted_commit_subject_may_carry_an_em_dash`, which pins the half the
# amendment added.


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
    legs = {leg["id"] for leg in model["legs"]}
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
#: Recent enough that git will accept a commit dated relative to them: git's
#: raw date parser refuses a timestamp small enough to look like a mistake, and
#: the same relay is built inside a repository below.
UNSOURCED_MTIMES = {"batoned": 1_787_000_000.0, "handoff": 1_787_000_060.0,
                    "quiet": 1_787_000_120.0}


UNSOURCED_LEGS = {
    "relay": "unsourced",
    "stages": [{"id": "S1", "name": "Stage One",
                "legs": ["batoned", "handoff", "batonless", "quiet"]}],
    "legs": [{"id": "batoned", "stage": "S1", "status": "done"},
             {"id": "handoff", "stage": "S1", "status": "done"},
             {"id": "batonless", "stage": "S1", "status": "done"},
             {"id": "quiet", "stage": "S1", "status": "done"},
             {"id": "stageless", "status": "done"},
             {"status": "done", "goal": "a leg the coach left unnamed"},
             {"id": "live", "stage": "S1", "status": "running"}],
}


def write_unsourced(root, name="unsourced"):
    """Write the unsourced relay under `root/name` and return its path.

    A function rather than only a fixture because the same relay has to exist
    in two places: on its own in `tmp_path`, and as `<project>/.relay` inside a
    real repository, where `_settle_commits` runs and a claimed commit can be
    denied. A sweep that only ever reads the first of those cannot see a
    placeholder written by the second (ACC-DATA-007).
    """
    target = write_relay(root, name, legs=UNSOURCED_LEGS)
    batons = target / "batons"
    batons.mkdir(exist_ok=True)
    (batons / "batoned.md").write_text(
        "# Baton: batoned\n\n**Status:** success\n**Commit:** 1a2b3c4\n")
    (batons / "handoff.md").write_text(
        "# Baton: handoff\n\n**Status:** success\n**Commit:** 2b3c4d5\n")
    # No `Status:` line and no sentence that claims a commit: a runner who
    # wrote prose and filled in none of the template's fields.
    (batons / "quiet.md").write_text(
        "# Baton: quiet\n\nI ran the leg and wrote this and nothing else.\n")
    for stem, mtime in UNSOURCED_MTIMES.items():
        os.utime(batons / f"{stem}.md", (mtime, mtime))
    return target


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
    * `live`     — the running leg: no baton yet, and the rows below are built
                   with the clock refused (`now=None`), so its elapsed time is
                   unknown too.
    """
    return write_unsourced(tmp_path)


@pytest.fixture
def unsourced_rows(unsourced):
    """`{leg id: row}` for the unsourced relay. The unnamed leg keys on `None`.

    ACC-DATA-007: a runner row names the leg it belongs to or says it cannot.
    The row used to key on `""`, which is an INVENTED value and is in this
    module's own `PLACEHOLDERS` - the leg row keeps `""` so a view renders an
    empty cell, and the runner row carries absence as absence.

    Built with `now=None`: this fixture is about columns with NO source, and a
    wall clock is a source for the running runner's elapsed time.
    """
    model = relay_model.build(unsourced, now=None)
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
    assert None in unsourced_rows, sorted(unsourced_rows, key=str)
    assert unsourced_rows[None]["status"] == "completed"
    assert unsourced_rows["batoned"]["leg"] == "batoned"     # a real id survives


def test_a_leg_with_no_stage_has_a_stage_of_none(unsourced_rows):
    for leg in ("stageless", None):
        assert unsourced_rows[leg]["stage"] is None, leg
        assert unsourced_rows[leg]["stageName"] is None, leg
    assert unsourced_rows["batoned"]["stage"] == "S1"        # a real stage survives
    assert unsourced_rows["batoned"]["stageName"] == "Stage One"


def test_start_is_none_until_a_previous_baton_has_landed(unsourced_rows):
    # The first runner to land: nothing handed off to it.
    assert unsourced_rows["batoned"]["start"] is None
    # No baton at all: nothing on disk says when it ran.
    for leg in ("batonless", "stageless", None):
        assert unsourced_rows[leg]["start"] is None, leg
    # A real handoff survives: the previous baton's landing time.
    assert unsourced_rows["handoff"]["start"] == pytest.approx(
        UNSOURCED_MTIMES["batoned"])
    assert unsourced_rows["live"]["start"] == pytest.approx(
        UNSOURCED_MTIMES["quiet"])


def test_duration_is_none_unless_both_ends_are_known(unsourced_rows):
    assert unsourced_rows["batoned"]["duration"] is None     # start unknown
    assert unsourced_rows["live"]["duration"] is None        # no clock passed
    for leg in ("batonless", "stageless", None):
        assert unsourced_rows[leg]["duration"] is None, leg
        assert unsourced_rows[leg]["finished"] is None, leg
    # Both ends known: 60 seconds, and not a zero standing in for absence.
    assert unsourced_rows["handoff"]["duration"] == pytest.approx(60.0)


def test_commit_is_none_when_no_baton_claims_one(unsourced_rows):
    for leg in ("quiet", "batonless", "stageless", None, "live"):
        assert unsourced_rows[leg]["commit"] is None, leg
    assert unsourced_rows["batoned"]["commit"] == "1a2b3c4"


def test_batonlines_is_none_without_a_baton_and_a_real_count_with_one(
        unsourced_rows):
    for leg in ("batonless", "stageless", None, "live"):
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
    """And with the fixture read where it lies, on the real wall clock and with
    no mtime stamping — the two things the `tmp_path` copy above cannot have.

    It does NOT reach the commit-reading branch, and it used to say it did. No
    fixture under `tests/fixtures/` holds a `.git` of its own, and a relay
    directory that is its own project owns only the repository ROOTED AT IT, so
    reading one in place asks git nothing at all
    (`test_the_fixture_in_place_is_asked_no_git_question_at_all`
    in `test_progress_log.py` is that fact, spied at the seam). The corpus
    sweeps below are what reach that branch; this one is the clock-and-mtime
    half. A docstring that overclaims is how a reader concludes a property is
    covered when it is not, so the claim is asserted here rather than narrated.

    A commit *subject* quoted into a log entry may legitimately contain an
    em-dash — that is a recorded decision, and it is why this sweep is over the
    runner columns rather than over every string in the model."""
    target = FIXTURES / name
    assert relay_model._repo_reading(target).dir is None, target
    _assert_no_placeholder_columns(
        relay_model.build(target)["runners"], f"{name} (in place)")


def test_no_runner_column_carries_a_display_placeholder_when_unsourced(unsourced):
    """The same sweep over the relay that actually has unsourced columns —
    the fixtures fill most of them in, which is how an em-dash substitution
    could hide from the sweep above."""
    _assert_no_placeholder_columns(relay_model.build(unsourced)["runners"],
                                   "unsourced")


# --------------------------------------------------------------------------
# THE CORPUS — relays that can reach the branch a sweep claims to guard
#
# Every sweep above this line reads a `relay(name)` copy in `tmp_path`. A
# `tmp_path` copy is not inside a repository, so `_settle_commits` never runs,
# no runner row's `commit` ever came from git, and the log holds no commit
# entry at all. Measured at the S1 gate: 9 models, 39 runner rows, 312 column
# values, ZERO commit entries. Two placeholders could therefore be written into
# the model with the whole suite green — `_render`'s fallback returning an
# em-dash (568 passed) and `_settle_commits` writing one for a claim the
# repository denies (caught only by ACC-DATA-009, by none of ACC-DATA-007's
# five sweeps).
#
# So the corpus below is the deliverable and the sweeps are its consumers. It
# is the SAME corpus ACC-DATA-009 reads — the frozen agent-service batons on a
# repository that really holds the commits they claim — plus the unsourced
# relay placed inside a repository of its own, which is where a settled commit
# can come back absent. It lives in this file rather than beside the log tests
# because two checks read it and this is the module the other imports.
#
# A corpus is only evidence while it can still produce the shapes it exists to
# produce; `test_the_corpus_can_reach_what_the_sweeps_guard` holds it to that.
# --------------------------------------------------------------------------

HAS_GIT = shutil.which("git") is not None


def test_git_is_installed_or_this_suite_is_not_evidence():
    """A missing git is a RED BUILD, not eighty-four quiet skips.

    Every property this module and `test_progress_log` hold ACC-DATA-009 to -
    the commit window, the branch point, a claim settled against the
    repository, the corpus the ACC-DATA-007 sweeps read - is git-backed and
    carries `skipif(not HAS_GIT)`. The round-4 code judge ran the suite with
    git hidden AND round 3's defect mutated back in (`_settle_commits` writing
    an em-dash for a claim the repository denies) and got `657 passed, 84
    skipped, exit 0`: a green build over a live defect, with the test that
    proves the corpus can reach what the sweeps guard the first thing skipped.

    This module shells out to git as a core function, so git is a requirement
    of the suite rather than an optional extra, and the honest way to say so is
    to fail. The `skipif` marks stay - once this test has gone red the skips
    are an explanation of WHICH properties went unproven rather than a
    substitute for proving them - and the exit code is no longer green.

    The same disease in the other direction: `ALL_FIXTURES` is derived from
    disk and asserted non-empty above, because emptying it removed ninety tests
    and left the suite green too.
    """
    assert HAS_GIT, ("git is not installed: every ACC-DATA-009 property in this "
                     "suite is git-backed and would be skipped, which is a "
                     "green build over unproven behaviour")


def git_run(cwd, *args, when=None):
    """`git -C cwd <args>` with a fixed identity and, optionally, a fixed date.

    A test repository whose commits are dated by the wall clock cannot be
    reasoned about: the whole point of the corpus is that a leg's commit is
    older than its own baton by a known number of seconds.
    """
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Relay Test", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "Relay Test", "GIT_COMMITTER_EMAIL": "t@example.com",
    })
    if when is not None:
        stamp = f"{int(when)} +0000"
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    out = subprocess.run(["git", "-C", str(cwd), *args],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return out


# --------------------------------------------------------------------------
# the real baton corpus (ACC-DATA-007, ACC-DATA-009)
#
# Batons this file writes are in the one form the template prescribes, at event
# counts an order of magnitude below the bounds that bite. The real corpus is
# nothing like that: of the ten batons in `tests/fixtures/agent-service`,
# exactly ONE writes the sha the way `**Commit:** <sha>` prescribes, three
# write it as a bare `Commit `<sha>`` heading, two as `Merge commit: `<sha>``,
# one as `Committed as `<sha>`` two hundred lines down, and three claim no
# commit at all. The same batons quote OTHER shas in prose - a branch point, a
# parent, a parallel runner's work - and a sha appearing in a baton is not a
# claim that the leg produced it.
#
# These tests graft the frozen corpus onto a repository whose commits are the
# shas those batons name. A 7-character sha prefix cannot be forged into a
# real repository, so the graft goes the other way: the repository's own shas
# are substituted into copies of the batons, one for one, leaving every baton's
# prose, structure and line numbers exactly as its runner wrote them.
# --------------------------------------------------------------------------

# What each corpus baton claims as its OWN work, read by hand from the batons
# and cross-checked against `behaviour-judge-S1`'s independent reading of them.
CORPUS_OWN = {
    "reconcile-develop": "c3319e2",
    "reconcile-security": "b9183c3",
    "create-path-credential-guard": "8036f9f",
    "process-entitlement": "42a735f",
    "pg-repository-correctness": "4f0b17c",
    "thread-id-ownership": "7d031a3",
    "s2-test-quality": "55732a4",
}

# Shas the same batons only MENTION. Each of these is a real commit in the
# grafted repository and each sits in a baton next to words that are not a
# claim: the branch `reconcile-develop` forked FROM, the PARENT of the commit
# `create-path-credential-guard` made, the sha `pg-repository-correctness`
# recorded as its own STARTING point. A log that credits one of these to the
# leg whose baton mentions it is reporting the repository, not the run.
CORPUS_QUOTED = ("7f8690c", "2d6c125", "378d178", "ac8b835")

# Of those four, `7f8690c` is the commit the run's branch forked FROM -
# `reconcile-develop`'s own baton says so in as many words - so it sits on
# `main`, BEFORE the branch point, and not on the run's branch. Where the run
# owns a branch the branch point is the floor for every commit on it,
# attributed or not (ACC-DATA-009, amended 2026-08-25); a fork point grafted
# onto the branch would therefore be inside the run's window, which the real
# one is not. The topology is what excludes it, and the graft has to model the
# topology to say so.
CORPUS_FORK_POINT = "7f8690c"

# Batons that name no commit anywhere. Three of ten: honest absence is the
# common case in the real corpus, and it must stay absence.
CORPUS_SILENT = ("chat-session-ownership", "credential-parity", "mask-shape-coverage")

# A commit subject carrying an em-dash, because real ones do. ACC-DATA-007's
# amendment retired "no em-dash appears anywhere in the model" for exactly this
# reason: a subject is quoted verbatim and truncating it would be worse than
# admitting the character. The rule is about values the model INVENTS, and a
# corpus with no em-dash in it anywhere cannot tell the two apart.
CORPUS_EM_DASH_SUBJECT = ("merge: land develop credential-preservation fix — "
                          "onto wave-2 cutover")

_EARLIEST_BATON = min(AGENT_SERVICE_BATON_MTIMES.values())
_MT = AGENT_SERVICE_BATON_MTIMES

# (token, when, subject). Every leg's own commit is OLDER than its own baton,
# because a runner commits and then writes its baton. `7f8690c` is older than
# the relay's earliest event by hours: it is the branch's starting point and
# no part of this run.
CORPUS_COMMITS = [
    ("7f8690c", _EARLIEST_BATON - 12000,
     "Merge branch 'feature/sub-1b-agent-write-methods'"),
    ("c3319e2", _MT["reconcile-develop"] - 221, CORPUS_EM_DASH_SUBJECT),
    ("b9183c3", _MT["reconcile-security"] - 54,
     "merge: land agents-router authentication onto wave-2 cutover"),
    ("2d6c125", _MT["create-path-credential-guard"] - 900,
     "chore(deps): project traffic that is nobody's leg"),
    ("8036f9f", _MT["create-path-credential-guard"] - 43,
     "fix(credentials): refuse to create an agent with a masked PAT"),
    ("42a735f", _MT["process-entitlement"] - 63,
     "fix(process): require entitlement to the agent being addressed"),
    ("ac8b835", _MT["chat-session-ownership"] - 1500,
     "feat(chat): stamp the caller onto the session"),
    ("378d178", _MT["pg-repository-correctness"] - 1200,
     "test(pg): a starting point, not a landing"),
    ("4f0b17c", _MT["pg-repository-correctness"] - 198,
     "fix(db): return the whole agent row, and store the slug"),
    ("7d031a3", _MT["thread-id-ownership"] - 100,
     "fix(threads): refuse a thread the caller does not own"),
    ("55732a4", _MT["s2-test-quality"] - 70,
     "test: make six certifying tests capable of failing"),
]


#: The two claims the ACC-DATA-009 defect actually credited, and the whole
#: reason `corpus_relay_denied` exists.
#:
#: `create-path-credential-guard` claims `8036f9f` as its own work and
#: `pg-repository-correctness` claims `4f0b17c`. Both are agent-service shas,
#: and both are quoted verbatim by THIS repository's judge batons while they
#: report on that relay (`code-judge-S1.md:162`,
#: `behaviour-judge-S1.md:186,190`). This repository has never held either
#: object, and the log credited two of its own legs with them anyway.
#:
#: That reproduction was read off `REPO / ".relay"` in place. `.relay/` is
#: git-ignored, so the reading skipped on every clone, every CI checkout and
#: every container: the repro of the defect the check names ran on exactly one
#: laptop. Withholding these two commits from the graft reproduces the same
#: corpus with none of the dependence — the real batons, the real shas, a
#: repository that does not have them — anywhere the suite runs.
CORPUS_DENIED_CLAIMS = ("4f0b17c", "8036f9f")

#: Legs whose baton claims its own commit in UPPER CASE once the graft has
#: substituted the repository's real shas in (ACC-DATA-009, 2026-08-27).
#:
#: An object name is hex and git resolves either spelling, so `cat-file`
#: confirms an upper-case claim and the runner row carries it — while
#: attribution is keyed on `%h`, which git prints lower case. Storing a claim
#: verbatim therefore credited a leg's own commit to NOBODY, with no warning,
#: and the round-7 code judge measured `rows {'37C9718'} vs log-attributed
#: set()`. Six rounds of judging could not see it because every sha in every
#: corpus was lower case: one kind of thing in the population, and a guard that
#: cannot fail — the same defect as an unclaimed population that is always
#: empty.
#:
#: TWO legs, in two of the claim forms the real batons use, so the dimension is
#: not one instance wearing a corpus's clothes. Neither is a denied claim: a
#: withheld token is never substituted, so upper-casing one would be a no-op in
#: `corpus_relay_denied` and the dimension would quietly halve.
CORPUS_UPPER_CASE_CLAIMS = ("reconcile-develop", "thread-id-ownership")


def _graft_agent_service(root, withheld=()):
    """The frozen agent-service batons, on a repository holding their commits.

    Returns `(relay_dir, sha_of)`, where `sha_of[token]` is the real short sha
    that stands in for the corpus sha `token`.

    A token in `withheld` is neither committed nor substituted: its baton keeps
    the corpus sha its runner wrote, and no object of that name exists in the
    grafted repository. That is a CLAIM THE REPOSITORY DENIES, which is the one
    shape the full graft cannot produce and the shape ACC-DATA-009 was written
    for.
    """
    project = root / "grafted"
    relay_dir = project / ".relay"
    shutil.copytree(FIXTURES / "agent-service", relay_dir)

    git_run(project, "init", "-q", "-b", "main")
    (project / "README").write_text("the project existed before the relay\n")
    git_run(project, "add", "README", when=_EARLIEST_BATON - 20000)
    git_run(project, "commit", "-q", "-m", "chore: the project existed first",
            when=_EARLIEST_BATON - 20000)
    sha_of = {}

    def land(token, when, subject):
        git_run(project, "commit", "-q", "--allow-empty", "-m", subject, when=when)
        sha_of[token] = git_run(project, "rev-parse", "--short=7",
                                "HEAD").stdout.strip()

    ordered = [c for c in sorted(CORPUS_COMMITS, key=lambda c: c[1])
               if c[0] not in withheld]
    for token, when, subject in ordered:
        if token == CORPUS_FORK_POINT:
            land(token, when, subject)
    git_run(project, "checkout", "-q", "-b", "feat/wave2-cutover-reconciled")
    for token, when, subject in ordered:
        if token != CORPUS_FORK_POINT:
            land(token, when, subject)

    for path in sorted((relay_dir / "batons").glob("*.md")):
        text = original = path.read_text()
        for token, real in sha_of.items():
            text = text.replace(token, real)
        # ...and then this baton's OWN claim is re-spelled in upper case, if it
        # is one of the two the corpus carries that way. Only its own claim:
        # the shas these batons merely quote stay as they were, so the corpus
        # holds both spellings rather than trading one uniform population for
        # another.
        if path.stem in CORPUS_UPPER_CASE_CLAIMS:
            own = sha_of.get(CORPUS_OWN[path.stem])
            assert own, (path.stem, "a claim the graft did not make")
            text = text.replace(own, own.upper())
        if text != original:
            path.write_text(text)
        when = AGENT_SERVICE_BATON_MTIMES[path.stem]
        os.utime(path, (when, when))
    return relay_dir, sha_of


@pytest.fixture(scope="session")
def corpus_relay(tmp_path_factory):
    """The graft with every claimed commit present.

    Session-scoped: fifteen git processes is too much to pay once per test, and
    `build()` never writes to a relay directory (there is a test for that), so
    every consumer sees the same bytes.
    """
    if not HAS_GIT:
        pytest.skip("git is not installed")
    return _graft_agent_service(tmp_path_factory.mktemp("corpus"))


@pytest.fixture(scope="session")
def corpus_relay_denied(tmp_path_factory):
    """The same graft, on a repository that never made two of the commits its
    batons claim — `CORPUS_DENIED_CLAIMS`, the two shas the real defect
    credited. Everything else about the corpus is unchanged, so a test reading
    it measures the denied claims and not a broken graft."""
    if not HAS_GIT:
        pytest.skip("git is not installed")
    return _graft_agent_service(tmp_path_factory.mktemp("corpus-denied"),
                                withheld=CORPUS_DENIED_CLAIMS)


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_denied_corpus_claims_two_shas_its_repository_does_not_have(
        corpus_relay_denied):
    """The premise of the ACC-DATA-009 reproduction, asserted rather than
    assumed. If the graft ever starts making these commits, the tests that read
    this corpus stop measuring a denied claim and go green for the wrong
    reason — so the denial is checked against git, here, once."""
    relay_dir, sha_of = corpus_relay_denied
    project = relay_dir.parent
    # Which two, derived from the corpus rather than restated. Emptying
    # `CORPUS_DENIED_CLAIMS` withholds nothing and turns every loop below into
    # zero iterations — a corpus that reproduces nothing, silently.
    assert set(CORPUS_DENIED_CLAIMS) == {
        CORPUS_OWN["pg-repository-correctness"],
        CORPUS_OWN["create-path-credential-guard"],
    }, CORPUS_DENIED_CLAIMS
    # A withheld token is never substituted, so a leg whose claim the graft
    # upper-cases must not also be one the graft denies - the dimension would
    # be silently absent from this corpus.
    assert not set(CORPUS_UPPER_CASE_CLAIMS) & {
        leg for leg, sha in CORPUS_OWN.items() if sha in CORPUS_DENIED_CLAIMS}
    for sha in CORPUS_DENIED_CLAIMS:
        assert sha not in sha_of, sha
        leg = next(k for k, v in CORPUS_OWN.items() if v == sha)
        assert sha in (relay_dir / "batons" / f"{leg}.md").read_text(), (leg, sha)
        found = subprocess.run(["git", "-C", str(project), "cat-file", "-t", sha],
                               capture_output=True, text=True)
        assert found.returncode != 0, (sha, found.stdout)
    # Non-vacuity: the rest of the graft is intact and every other claim is a
    # real object, so what the log does with these two is the only difference.
    assert set(sha_of) == {t for t, _, _ in CORPUS_COMMITS} - set(CORPUS_DENIED_CLAIMS)
    for sha in sha_of.values():
        assert subprocess.run(["git", "-C", str(project), "cat-file", "-t", sha],
                              capture_output=True, text=True).returncode == 0, sha


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_corpus_claims_two_of_its_shas_in_upper_case(corpus_relay):
    """Non-vacuity for `CORPUS_UPPER_CASE_CLAIMS` (ACC-DATA-009, 2026-08-27).

    A corpus dimension that quietly reverts takes every test that reads this
    corpus back to a population with one spelling in it, which is exactly how
    the defect survived six rounds. So the dimension is asserted where it is
    built: the sha is on disk in upper case, and the model credits the leg with
    the repository's own lower-case spelling all the same.
    """
    relay_dir, sha_of = corpus_relay
    assert CORPUS_UPPER_CASE_CLAIMS, "a corpus dimension that is empty is none"
    model = relay_model.build(relay_dir, now=None)
    rows = {r["leg"]: r["commit"] for r in model["runners"]}
    entries = {e["leg"]: e["commit"] for e in model["log"]
               if e["kind"] == "commit" and e["leg"]}
    for leg in CORPUS_UPPER_CASE_CLAIMS:
        real = sha_of[CORPUS_OWN[leg]]
        written = (relay_dir / "batons" / f"{leg}.md").read_text()
        assert real.upper() in written, (leg, real)
        assert real not in written, (leg, real)   # the ONLY spelling on disk
        assert rows[leg] == real, (leg, rows[leg])
        assert entries[leg] == real, (leg, entries.get(leg))
    # And the corpus still holds lower-case claims beside them.
    assert set(CORPUS_OWN) - set(CORPUS_UPPER_CASE_CLAIMS)


def test_the_corpus_fixture_still_names_the_shas_these_tests_read():
    """The premise of every test below. The fixture has been refreshed once
    already; if it is refreshed again and a baton's wording changes, this fails
    here rather than as a silent false pass downstream."""
    batons = FIXTURES / "agent-service" / "batons"
    for leg, sha in CORPUS_OWN.items():
        assert sha in (batons / f"{leg}.md").read_text(), (leg, sha)
    for leg in CORPUS_SILENT:
        text = (batons / f"{leg}.md").read_text()
        loose = set(re.findall(r"`([0-9a-f]{7,40})`", text))
        assert loose <= set(CORPUS_QUOTED), (leg, loose)
    quoted = " ".join(p.read_text() for p in sorted(batons.glob("*.md")))
    for sha in CORPUS_QUOTED:
        assert sha in quoted, sha


#: The sha `unsourced-in-a-repo`'s `handoff` baton claims. Seven hex characters
#: that name no object in any repository this test builds, so the claim is
#: denied and the column must come back absent.
DENIED_SHA = "2b3c4d5"


@pytest.fixture(scope="session")
def unsourced_in_a_repo(tmp_path_factory):
    """The unsourced relay as `<project>/.relay` inside a real repository.

    The relay with a row missing a source for every one of the eight columns,
    where `_settle_commits` actually runs. Two of its batons make the contrast
    the sweep needs:

    * `batoned` claims the repository's own HEAD, so its `commit` column is a
      real sha that came from git — erasing the column fails here.
    * `handoff` claims `DENIED_SHA`, which the repository does not have, so the
      model must report absence — inventing a placeholder fails here.

    Every earlier em-dash sweep read a `tmp_path` copy, where neither branch
    runs at all.
    """
    if not HAS_GIT:
        pytest.skip("git is not installed")
    project = tmp_path_factory.mktemp("unsourced-repo") / "project"
    relay_dir = write_unsourced(project, ".relay")

    git_run(project, "init", "-q", "-b", "main")
    (project / "README").write_text("a project the relay supervises\n")
    git_run(project, "add", "README", when=UNSOURCED_MTIMES["batoned"] - 300)
    git_run(project, "commit", "-q", "-m", "chore: the project existed first",
            when=UNSOURCED_MTIMES["batoned"] - 300)
    git_run(project, "checkout", "-q", "-b", "feat/the-run")
    git_run(project, "commit", "-q", "--allow-empty",
            "-m", "batoned: the leg's own work — reported verbatim",
            when=UNSOURCED_MTIMES["batoned"] - 60)
    real = git_run(project, "rev-parse", "--short=7", "HEAD").stdout.strip()

    baton = relay_dir / "batons" / "batoned.md"
    baton.write_text(baton.read_text().replace("1a2b3c4", real))
    when = UNSOURCED_MTIMES["batoned"]
    os.utime(baton, (when, when))
    return relay_dir, real


#: The corpus, by the label a failure report should name it with.
GIT_CORPUS_LABELS = ("agent-service grafted onto its own repository",
                     "the unsourced relay inside a repository")


@pytest.fixture(scope="session")
def git_corpus(corpus_relay, unsourced_in_a_repo):
    """`{label: relay_dir}` — every relay in the corpus, keyed for reporting."""
    return dict(zip(GIT_CORPUS_LABELS, (corpus_relay[0], unsourced_in_a_repo[0])))


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_corpus_can_reach_what_the_sweeps_guard(git_corpus, unsourced_in_a_repo):
    """The corpus assertion, and the reason this leg exists.

    The old sweeps were decoration because their corpus could not produce the
    values they forbade. This holds the new one to the four shapes the sweeps
    below need — a git-sourced commit column, a denied claim, an unsourced row
    for every column, and a quoted em-dash — so a corpus that quietly stops
    producing one of them fails here rather than passing everything downstream.
    """
    models = {label: relay_model.build(target, now=None)
              for label, target in git_corpus.items()}

    commits = [e for m in models.values() for e in m["log"] if e["kind"] == "commit"]
    assert len(commits) >= 4, [e["m"] for e in commits]

    rows = [row for m in models.values() for row in m["runners"]]
    _, real = unsourced_in_a_repo
    sourced = [r for r in rows if r["commit"] is not None]
    assert len(sourced) >= 4, rows
    assert real in {r["commit"] for r in sourced}

    # A claim the repository denies: the branch `_settle_commits` answers None
    # on, which no `tmp_path` copy has ever reached.
    denied = [r for r in rows if r["leg"] == "handoff"]
    assert denied and denied[0]["commit"] is None, denied

    # And a row with nothing behind each of the eight columns, in a relay where
    # the git branches ran.
    for column in sorted(RUNNER_KEYS - {"n", "leg", "status"}):
        assert any(r[column] is None for r in rows), column

    # The retired rule, made falsifiable: an em-dash the model quotes rather
    # than invents. Against this corpus the pre-amendment
    # "no em-dash anywhere in the model" test goes red, which is why it is gone.
    #
    # The constant is asserted first, because it is the thing that carries the
    # property: a corpus whose only em-dash was edited out of it still passes
    # `subjects` below, on the other relay's subject, while the test that names
    # this constant loses its point silently.
    assert EM_DASH in CORPUS_EM_DASH_SUBJECT, CORPUS_EM_DASH_SUBJECT
    subjects = [e["m"] for e in commits if EM_DASH in e["m"]]
    assert len(subjects) >= 2, [e["m"] for e in commits]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("label", GIT_CORPUS_LABELS)
def test_no_runner_column_carries_a_display_placeholder_in_a_repository(
        label, git_corpus):
    """ACC-DATA-007's sweep, over relays where the commit column has a source.

    This is the sweep the five before it should have been. `_render`'s fallback
    and `_settle_commits`' absent claim both write into a runner column, and
    both were mutable to an em-dash with the whole suite green because no sweep
    read a relay that reached them.
    """
    _assert_no_placeholder_columns(
        relay_model.build(git_corpus[label], now=None)["runners"], label)


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("label", GIT_CORPUS_LABELS)
def test_the_model_invents_no_placeholder_in_a_repository(label, git_corpus):
    """And the whole-model placeholder sweep on the same corpus. A string the
    model invented is one of these exactly; a string it quoted from a commit
    subject or a coach's prose merely contains one."""
    model = relay_model.build(git_corpus[label], now=None)
    banned = {"—", "-", "N/A", "n/a", "undefined", "None", "null", "?"}
    hits = [(where, s) for where, s in walk_strings(model) if s in banned]
    assert hits == [], hits


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_quoted_commit_subject_may_carry_an_em_dash(corpus_relay):
    """ACC-DATA-007 as amended: the rule is about invented values, and a
    subject the model quotes verbatim is not one. The pre-amendment test
    asserted the opposite and was green only because its corpus had no commits
    in it; this asserts the amended rule in the same place."""
    relay_dir, _ = corpus_relay
    model = relay_model.build(relay_dir, now=None)
    # The constant is what carries the property this test is named for, so it
    # is asserted rather than assumed. Without this line the em-dash could be
    # edited out of `CORPUS_EM_DASH_SUBJECT` and every assertion below would
    # still hold - a test that cannot tell a corpus that exercises the property
    # from one that does not.
    assert EM_DASH in CORPUS_EM_DASH_SUBJECT, CORPUS_EM_DASH_SUBJECT
    quoted = [e for e in model["log"]
              if e["kind"] == "commit" and CORPUS_EM_DASH_SUBJECT in e["m"]]
    assert quoted, [e["m"] for e in model["log"] if e["kind"] == "commit"]
    # Quoted whole: the em-dash survives and the subject is not truncated at it.
    assert quoted[0]["m"].endswith(CORPUS_EM_DASH_SUBJECT)
    assert EM_DASH in quoted[0]["m"], quoted[0]["m"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_claimed_commit_the_repository_denies_is_absent_not_invented(
        unsourced_in_a_repo):
    """ACC-DATA-007 over `_settle_commits`' own fallback (NB-3). Mutating that
    `None` to an em-dash was caught by two ACC-DATA-009 tests and by none of
    ACC-DATA-007's five sweeps, because the path only exists in a repository.
    Both halves are asserted together, so erasing the sourced column fails as
    loudly as inventing the absent one."""
    relay_dir, real = unsourced_in_a_repo
    rows = {r["leg"]: r for r in relay_model.build(relay_dir, now=None)["runners"]}
    assert rows["handoff"]["commit"] is None
    assert rows["batoned"]["commit"] == real
    assert DENIED_SHA not in str(rows["handoff"])


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


def test_a_measured_zero_elapsed_is_kept_and_is_not_absence(relay):
    """The elapsed half of the measured-zero rule, which the tokens half had
    covered for both. `if elapsed is not None:` mutated to `if elapsed:` left
    the suite green, and it collapses "nothing spent" into "not measured" —
    the one distinction ACC-DATA-008 exists to preserve."""
    target = relay("tokens")
    dash = json.loads((target / "dashboard.json").read_text())
    dash["elapsed"] = 0
    (target / "dashboard.json").write_text(json.dumps(dash))

    model = relay_model.build(target)
    assert "elapsed" in model["metrics"]
    assert model["metrics"]["elapsed"] == 0

    # And the other side, so the assertion cannot be satisfied by keeping the
    # key unconditionally: a coach's placeholder is still absence.
    dash["elapsed"] = "n/a"
    (target / "dashboard.json").write_text(json.dumps(dash))
    assert "elapsed" not in relay_model.build(target)["metrics"]


def test_a_bool_is_not_a_measurement(relay):
    """`True` is not a token count and `False` is not zero tokens.

    `isinstance(True, int)` is True in Python, so deleting `_scalar`'s two-line
    bool guard makes `"input": true` a metric with the value `true` - a view
    then renders `Input true` - and `"elapsed": false` a measured zero, which
    is the exact "nothing spent" / "not measured" collapse ACC-DATA-008 exists
    to prevent. Deleting that guard left the whole suite green: no fixture had
    a bool anywhere a metric is read.
    """
    target = relay("tokens")
    dash = json.loads((target / "dashboard.json").read_text())
    dash["tokens"] = {"input": True, "cached": False, "output": 12}
    dash["elapsed"] = False
    (target / "dashboard.json").write_text(json.dumps(dash))

    metrics = relay_model.build(target)["metrics"]
    assert metrics["tokens"] == {"output": 12}, metrics
    assert "elapsed" not in metrics, metrics
    # ...at the seam too, where the guard actually lives.
    assert relay_model._scalar(True) is None
    assert relay_model._scalar(False) is None
    assert relay_model._scalar(0) == 0
    # `_whole` carries the same guard for the same reason: a check's `round` is
    # a count, and `True` is not a round number.
    assert relay_model._whole(True) is None
    assert relay_model._whole(0) == 0


def test_a_tokens_object_with_nothing_measurable_in_it_carries_no_key(relay):
    """A `tokens` object every one of whose values is a display placeholder has
    measured nothing, and an empty `tokens` dict in the model reads as "zero
    tokens" to a view that only asks whether the key is there. Deleting the
    `if measured:` guard left the suite green, because no fixture had a
    `tokens` key with nothing measurable behind it."""
    target = relay("tokens")
    dash = json.loads((target / "dashboard.json").read_text())
    dash["tokens"] = {"input": "—", "cached": "", "output": "n/a"}
    (target / "dashboard.json").write_text(json.dumps(dash))

    metrics = relay_model.build(target)["metrics"]
    assert "tokens" not in metrics, metrics

    # One real value among them and the key comes back, carrying only it.
    dash["tokens"]["input"] = "12.0K"
    (target / "dashboard.json").write_text(json.dumps(dash))
    assert relay_model.build(target)["metrics"]["tokens"] == {"input": "12.0K"}


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
    # ACC-DATA-006: "the same object", not merely an equal one. `==` is blind
    # to `entries.append(dict(entry))`, so the model-level guard for this check
    # asserts identity as well - the full account lives beside the other
    # ACC-DATA-006 tests in `test_progress_log.py`.
    assert all(a is b for a, b in zip(model["log"], model["extras"]["log"]))


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
    assert [leg["id"] for leg in model["legs"]] == ["solo"]
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
    """The same directory and the same clock yield the same model.

    Both spellings of "the same clock": a pinned one, and none at all. The
    default reads the wall clock — ACC-DATA-005 requires an age under the
    one-argument call — so determinism is a property of the clock the caller
    passes, and `now=None` is how a frame capture asks for no clock at all.
    """
    target = relay(name)
    assert relay_model.build(target, now=None) == relay_model.build(target, now=None)
    assert (relay_model.build(target, now=1787600000.0)
            == relay_model.build(target, now=1787600000.0))


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
    orders = [leg["order"] for leg in model["legs"]]
    assert orders == sorted(orders)
    stages = [leg["stage"] for leg in model["legs"]]
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


#: The test modules this sweep owns. `tests/frame*.py` is deliberately absent:
#: it belongs to another check and a sweep that fails on somebody else's file
#: is a sweep that gets deleted.
SWEPT_TEST_MODULES = ("test_relay_model.py", "test_progress_log.py")


def _duplicate_top_level_names(source):
    """Top-level `def`/`class` names defined more than once, with their lines."""
    tree = ast.parse(source)
    seen, duplicates = {}, {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        if node.name in seen:
            duplicates.setdefault(node.name, [seen[node.name]]).append(node.lineno)
        else:
            seen[node.name] = node.lineno
    return seen, duplicates


def test_no_test_module_defines_one_name_twice():
    """A second `def` of a name silently deletes the first (ACC-DATA-*, all).

    Python's last definition wins, so a test re-using an existing test's name
    removes that test from the suite with no diagnostic anywhere: the count
    goes down by one, every remaining test passes, and the property the deleted
    test guarded is unguarded. It is the purest form of a guard that does not
    guard, and it leaves no trace in a passing run.

    `ruff`'s F811 would report it, but F811 is turned off for
    `test_progress_log.py` in `ruff.toml` — it fires 39 times there on pytest's
    fixture-argument protocol, which is not a redefinition — so the guard is
    made directly here rather than lost with the noise. This form is also
    stricter than the rule it replaces: F811 says nothing about a name
    redefined after a use, and this does.

    Non-vacuous by construction: it fails if it swept no file, if a module
    defined nothing, or if the detector cannot see a duplicate that is there.
    """
    swept = {}
    for name in SWEPT_TEST_MODULES:
        path = Path(__file__).resolve().parent / name
        assert path.is_file(), path
        names, duplicates = _duplicate_top_level_names(path.read_text())
        assert duplicates == {}, (name, duplicates)
        assert len(names) > 50, (name, len(names))
        swept[name] = len(names)

    assert len(swept) == len(SWEPT_TEST_MODULES), swept
    # And the detector can see one: without this the sweep above passes on an
    # `_duplicate_top_level_names` that returns `{}` unconditionally.
    _, planted = _duplicate_top_level_names(
        "def test_a():\n    pass\n\n\ndef test_a():\n    pass\n")
    assert planted == {"test_a": [1, 5]}, planted


#: Every reason a swept test module is allowed to skip for, and why each one is
#: not a guard that evaporates.
#:
#: A skip is how three guards in this suite stopped guarding. `git is not
#: installed` hid 84 tests over a live defect and reported exit 0. Two
#: ACC-DATA-009 readings sat behind `.relay/`, which `.gitignore` swallows, so
#: they skipped on every clone, every CI checkout and every container — one of
#: them the reproduction of the defect that check names in full. And one of
#: those two was parametrised over a hardcoded `~/Documents/...` path that
#: resolved on one person's disk and nowhere else.
#:
#: None of those three reasons is below, because none of them survived. What is
#: left may only be a PLATFORM fact — something no checkout of this repository
#: can change — and every entry says which. "This machine happens to have a
#: directory" is the reason this sweep exists to refuse, and a skip that wants
#: one has to be written down here, in front of a reader, first.
ALLOWED_SKIP_REASONS = {
    "git is not installed":
        "the suite is RED without git — test_git_is_installed_or_this_suite_"
        "is_not_evidence — so these marks name which properties went unproven "
        "rather than substituting for proving them",
    "no /dev/zero on this platform":
        "a character-device fixture cannot be made where there is no character "
        "device; the other six shapes of the same parametrisation still run",
    "root ignores permission bits":
        "a chmod test passes for the wrong reason as root, which is a property "
        "of the process and not of the checkout",
    "root ignores permission bits, so a chmod test would pass for the wrong "
    "reason":
        "the same platform fact, worded as the NOT_ROOT marker's reason",
    "no descriptor table to read on this platform":
        "neither /dev/fd nor /proc/self/fd exists, so there is nothing to count",
}


def _called_name(node):
    """`f(...)` -> "f", `a.b(...)` -> "b", anything else -> None."""
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _skip_reasons(source):
    """`(reasons, opaque)` for one module's source.

    `reasons` is every plain string handed to `pytest.skip(...)` or to a
    `skipif(..., reason=...)`. `opaque` is the line of every such call whose
    reason this sweep cannot read.

    An f-string reason counts as opaque on purpose: `f"{live} is not on this
    machine"` is precisely the skip this sweep was written to refuse, and a
    reason assembled at run time cannot be registered in front of a reader.
    """
    reasons, opaque = set(), []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name not in ("skip", "skipif", "importorskip"):
            continue
        if name == "skip":
            given = node.args[0] if node.args else None
        else:
            given = next((kw.value for kw in node.keywords if kw.arg == "reason"),
                         None)
        if isinstance(given, ast.Constant) and isinstance(given.value, str):
            reasons.add(given.value)
        else:
            opaque.append(node.lineno)
    return reasons, opaque


#: What pytest itself collects, straight out of its own `python_files`
#: default. This project sets no `python_files`, so BOTH globs are live and a
#: sweep that reads only the first one has a hole exactly the width of the
#: second: a module named `something_test.py` runs in every suite run and is
#: read by no check here. That is not hypothetical — a module planted in that
#: shape, carrying a skip reason this machine decides AND a home-directory
#: read, went green. A sweep is only as wide as what it globs, so this glob is
#: pytest's, not a convention someone remembered.
PYTEST_PYTHON_FILES = ("test_*.py", "*_test.py")


def _collected_test_modules(directory):
    """Sorted names of the files pytest would collect from `directory`.

    Its own two globs, deduplicated: `test_a_test.py` matches both and is one
    module. Split out from `_test_modules_on_disk` so the sweeps' reach can be
    asserted against a directory planted with the evading shape rather than
    only against `tests/`, which today happens to contain none of them —
    the reason the hole survived.
    """
    names = set()
    for pattern in PYTEST_PYTHON_FILES:
        names |= {p.name for p in Path(directory).glob(pattern)}
    return sorted(names)


def _test_modules_on_disk():
    """`[(name, source)]` for every test module in `tests/`, DERIVED from disk.

    The two sweeps below read all of them rather than `SWEPT_TEST_MODULES`.
    A hardcoded list is the exact defect this stage keeps finding — shortening
    it deletes tests silently, which is how `ALL_FIXTURES` lost a fixture and
    how a sweep loses a module — and these two sweeps can afford the whole
    directory because they forbid something no module currently does: every
    module but these two has no skip at all, and none of them names a home
    directory. So neither can fail on another check's file for a reason that
    file's author would dispute.

    "Every test module" means every module PYTEST runs, not every module named
    the way this project happens to name them — see `PYTEST_PYTHON_FILES`.
    """
    here = Path(__file__).resolve().parent
    names = _collected_test_modules(here)
    assert set(SWEPT_TEST_MODULES) <= set(names), names
    assert len(names) >= 5, names
    return [(name, (here / name).read_text()) for name in names]


def test_no_test_module_skips_for_a_reason_this_machine_decides():
    """A skip is a green test that ran nothing, and a suite full of them
    reports success over unproven behaviour (ACC-DATA-*, all).

    This closes the class rather than the three instances that have now been
    closed one at a time: every skip either names a reason registered in
    `ALLOWED_SKIP_REASONS` — where a reader can see it is a platform fact — or
    this fails. The registry is held to being exactly the reasons in use, both
    ways, so a stale entry cannot sit there as a licence for the next skip that
    wants it.
    """
    found, by_module = set(), {}
    for name, source in _test_modules_on_disk():
        reasons, opaque = _skip_reasons(source)
        assert opaque == [], (
            name, opaque, "a skip reason must be a plain string, registered in "
            "ALLOWED_SKIP_REASONS")
        found |= reasons
        by_module[name] = reasons

    assert sorted(found) == sorted(ALLOWED_SKIP_REASONS), {
        "unregistered": sorted(found - set(ALLOWED_SKIP_REASONS)),
        "registered but unused": sorted(set(ALLOWED_SKIP_REASONS) - found),
    }
    assert len(found) >= 4, found
    # A module that can skip must also be a module the rest of this file sweeps
    # — otherwise dropping it from `SWEPT_TEST_MODULES` quietly takes its skips
    # out of range of every check here, which is the same hole one level up.
    assert {n for n, r in by_module.items() if r} <= set(SWEPT_TEST_MODULES), \
        by_module

    # And the detector sees both shapes it exists to see: without this, a
    # `_skip_reasons` returning `(set(), [])` passes everything above.
    planted, planted_opaque = _skip_reasons(
        'import pytest\n'
        'def test_a():\n'
        '    if not LIVE.is_dir():\n'
        '        pytest.skip("absent from every clone")\n'
        '@pytest.mark.skipif(not LIVE.is_dir(), reason="one laptop has it")\n'
        'def test_b(): pass\n'
        '@pytest.mark.skipif(True, reason=f"{LIVE} is not on this machine")\n'
        'def test_c(): pass\n')
    assert planted == {"absent from every clone", "one laptop has it"}, planted
    assert len(planted_opaque) == 1, planted_opaque


def _module_level(tree):
    """Every node evaluated at IMPORT time — module-level statements and the
    decorators of top-level definitions — and nothing inside a function body.

    The distinction is the whole precision of the sweep below. A `~` inside a
    test body is that test's SUBJECT: `relay_model` expands what a coach writes
    in `dashboard.json.path`, and the tests for it set `HOME` to a `tmp_path`
    first. A `~` expanded at import time cannot be pointed anywhere: whatever
    it resolves to is a fact about the machine collecting the suite.
    """
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in stmt.decorator_list:
                yield from ast.walk(decorator)
        else:
            yield from ast.walk(stmt)


#: Where a named user's home directory lives, on the two platforms this suite
#: runs on. ASSEMBLED rather than written out: these are the needles of a
#: detector that reads the module it is written in, and a literal here would be
#: a true positive of its own.
HOME_ROOTS = tuple(f"/{name}/" for name in ("Users", "home"))


def _machine_paths(source):
    """`(lineno, what)` for every place a module reaches for a path only one
    machine has: a literal naming a named user's home anywhere in the file, or
    a `~` resolved at import time."""
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(root in node.value for root in HOME_ROOTS):
                hits.append((node.lineno, node.value))
    for node in _module_level(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value.startswith("~"):
            hits.append((node.lineno, node.value))
        elif isinstance(node, ast.Call) and _called_name(node) in (
                "expanduser", "expandvars", "home"):
            hits.append((node.lineno, _called_name(node)))
    return sorted(set(hits))


def test_no_test_module_names_a_path_only_one_machine_has():
    """`LIVE_RELAYS`' second entry was a `~/Documents/Work/...` path expanded
    at import time.

    It cannot resolve anywhere but the laptop it was typed on, so every
    assertion parametrised over it was green-by-absence on every other machine
    — and it was, silently, behind a skip that read like a platform fact. A
    relay that matters is frozen under `tests/fixtures/`, which travels; a live
    one is DISCOVERED relative to this repository, never spelled out.
    """
    for name, source in _test_modules_on_disk():
        assert _machine_paths(source) == [], name

    # Non-vacuity: the detector finds the entry this test is named after and
    # both other spellings of the same mistake. Assembled rather than written
    # out, because this module is one of the modules swept above — a literal
    # `/Users/...` here would be a true positive of its own.
    planted = _machine_paths(
        'A = os.path.expanduser("~/Documents/Work/x/.relay")\n'
        'B = "{root}someone/relay"\n'
        'C = Path.home() / ".relay"\n'
        'def test_x(monkeypatch):\n'
        '    monkeypatch.setenv("HOME", str(tmp_path))\n'
        '    assert build(d)["relay"]["path"] == "~/proj"\n'.format(
            root=HOME_ROOTS[0]))
    assert [what for _, what in planted] == [
        "expanduser", "~/Documents/Work/x/.relay",
        HOME_ROOTS[0] + "someone/relay", "home"], planted


def test_the_sweeps_read_every_module_pytest_runs(tmp_path):
    """The sweeps below are worth exactly what their glob is worth.

    Both of them used to glob `test_*.py` alone, and pytest collects
    `*_test.py` as well. A module in that shape is a real module — it runs,
    its assertions count, its skips are green tests that ran nothing — and it
    sat outside every check in this file. The reproduction is the planted
    directory here: the evading name is found, the ordinary one still is, and
    a helper module that pytest does NOT run is still left alone, because a
    sweep that read `frame.py` would fail on the harness's own docstring.
    """
    for name in ("test_ordinary.py", "sneaky_test.py", "test_both_test.py",
                 "frame.py", "conftest.py", "notes.txt"):
        (tmp_path / name).write_text("")
    assert _collected_test_modules(tmp_path) == [
        "sneaky_test.py", "test_both_test.py", "test_ordinary.py"]

    # And the sweeps themselves see a module planted in the evading shape,
    # carrying both things they exist to refuse. Reading them through the same
    # helper is the point: a fix that widened only one sweep is a fix that
    # left the other one open.
    (tmp_path / "evader_test.py").write_text(
        'import pytest\n'
        f'LIVE = Path("{HOME_ROOTS[0]}someone/relay")\n'
        '@pytest.mark.skipif(not LIVE.is_dir(), reason="one laptop has it")\n'
        'def test_x(): pass\n')
    source = (tmp_path / "evader_test.py").read_text()
    assert "evader_test.py" in _collected_test_modules(tmp_path)
    assert _skip_reasons(source)[0] == {"one laptop has it"}
    assert [what for _, what in _machine_paths(source)] == [
        HOME_ROOTS[0] + "someone/relay"]


def test_module_does_not_import_curses():
    """The model is the data layer: a renderer imports it, never the reverse.

    The source check is the readable half. The second line here used to be
    `assert "curses" not in sys.modules or True`, which cannot fail — and which
    could not have been written as it was meant even so, because by the time
    this file runs some other test in the suite may legitimately have imported
    curses. The honest form of "the module never pulls it in" is a fresh
    interpreter that imports nothing else, so that is what it asks: `import
    curses` spelled any way at all — a dynamic `__import__`, an
    `importlib.import_module`, a transitive import through another module —
    leaves `curses` in that interpreter's `sys.modules` and fails here, while
    the source check alone sees only the literal spelling.
    """
    source = (REPO / "scripts" / "relay_model.py").read_text()
    assert "import curses" not in source

    probe = ("import sys, json;"
             "sys.path.insert(0, sys.argv[1]);"
             "import relay_model;"
             "print(json.dumps(sorted(m for m in sys.modules"
             " if m == 'curses' or m.startswith('curses.'))))")
    out = subprocess.run([sys.executable, "-c", probe, str(REPO / "scripts")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    pulled = json.loads(out.stdout.strip().splitlines()[-1])
    assert pulled == [], pulled


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
    # Not `in (None, str(bad))`: that accepts both possible answers, so no
    # coercion of this field could ever fail it. A pointer that is not a string
    # is absent, and — when it is present and merely the wrong type — named.
    assert model["relay"]["currentLegDeclared"] is None, model["relay"]
    if bad is not None:
        assert any("`currentLeg`" in w for w in model["warnings"]), model["warnings"]
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
    # The second disjunct used to subsume the first, so this accepted any
    # string at all: a title of `["a"]` rendered as `'["a"]'` passed a test
    # named for coercion. A list is not a title; it is absent and named.
    assert model["relay"]["title"] is None, model["relay"]["title"]
    assert any("`title`" in w and "list" in w for w in model["warnings"]), \
        model["warnings"]


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
            assert [r["leg"] for r in model["runners"]] == [None, "real", "real"], (
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

#: The marker a read carries when it is guarded at its own site rather than by
#: `_read_relay_file`. There is exactly one, and the test below says so: an
#: exemption nobody counts is how an allow-list becomes an exemption for
#: everything.
GUARDED_READ_MARKER = "# guarded read:"

#: Every way this module could read a path outside the one guarded helper.
#: `scandir(`, `listdir(` and `os.walk(` were absent for this sweep's whole
#: life, and `os.scandir` is the module's ONE directory listing — so the single
#: read the sweep most needed to see was the one idiom it could not.
UNGUARDED_READ = re.compile(
    r"\.read_text\(|\.read_bytes\(|(?<!os\.)(?<!\w)open\(|\.glob\(|"
    r"\.iterdir\(|scandir\(|listdir\(|os\.walk\(")


def unguarded_reads(source):
    """Lines of `source` that read a path outside `_read_relay_file`."""
    return [f"{n}: {line.strip()}"
            for n, line in enumerate(source.splitlines(), 1)
            if UNGUARDED_READ.search(line) and GUARDED_READ_MARKER not in line]


def test_every_relay_file_read_goes_through_the_one_guarded_helper():
    """The guard only guards while it is the only way in.

    The alternative — a `try/except` at each call site — is what the module had,
    and it grew a third read that had neither. One helper is the thing a test
    can hold the module to, so this test holds it: no unguarded reader appears
    anywhere in `relay_model.py`.

    The detector is asserted too. A sweep is worth what its detector can see,
    and this one could not see a directory listing at all — which is the shape
    of the module's one read outside the helper, `os.scandir` on the batons
    directory. That read is guarded where it stands and says so with
    `GUARDED_READ_MARKER`; the marker is counted, so an exemption cannot spread
    quietly, and the detector is shown a planted reader of every idiom it
    claims to know.
    """
    source = (REPO / "scripts" / "relay_model.py").read_text()
    assert unguarded_reads(source) == [], unguarded_reads(source)

    # The exemption, counted. One read is guarded at its site; a second one
    # appearing is a decision somebody has to make, not a line to slip in.
    assert source.count(GUARDED_READ_MARKER) == 1, source.count(GUARDED_READ_MARKER)
    marked = [line for line in source.splitlines() if GUARDED_READ_MARKER in line]
    assert "os.scandir(" in marked[0], marked

    # And the detector sees each idiom, so `offenders == []` above means "there
    # are none", not "the regex no longer matches anything".
    planted = ["    data = path.read_text()",
               "    data = path.read_bytes()",
               "    fh = open(path)",
               "    for p in path.glob('*.md'): pass",
               "    for p in path.iterdir(): pass",
               "    for e in os.scandir(path): pass",
               "    for name in os.listdir(path): pass",
               "    for root, dirs, files in os.walk(path): pass"]
    for line in planted:
        assert unguarded_reads(line) == [f"1: {line.strip()}"], line
        assert unguarded_reads(line + "   " + GUARDED_READ_MARKER + " why") == []


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
    for value, rendered in ((nest, relay_model._strlist([nest])),
                            (deep, relay_model._strlist(deep))):
        assert isinstance(rendered, list) and len(rendered) == 1
        assert isinstance(rendered[0], str) and rendered[0]
        # "Named rather than raised" is the whole claim, and `assert rendered[0]`
        # does not hold it: an em-dash is a non-empty string too, and that is
        # the mutation of `_render` the suite walked past for two gate rounds.
        # The value has to be NAMED (ACC-DATA-007: the model never invents).
        assert type(value).__name__ in rendered[0], rendered[0]
        assert EM_DASH not in rendered[0] and rendered[0] not in PLACEHOLDER_STRINGS


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
# ACC-DATA-007 — the model's stringifier, on every branch it guards
#
# `_render` is where a coach's non-string value becomes a string, and its
# `except` catches three exceptions. Only ONE of the three is reachable through
# a relay file: `json.dumps` raises RecursionError on a structure the JSON
# scanner accepted, and TypeError and ValueError need a value `json.loads` can
# never produce — an arbitrary object, and a container that refers to itself.
#
# That is why `return "—"` there left 568 tests passing. A sweep cannot see a
# placeholder written by a branch its corpus cannot reach, so the corpus for
# those two branches is built here, at the seam, rather than pretended at
# through a file that cannot carry them.
# --------------------------------------------------------------------------

def _self_referential():
    """A list that contains itself: `json.dumps` answers ValueError."""
    loop = []
    loop.append(loop)
    return loop


def _too_deep_to_dump():
    """Nesting past the interpreter's recursion limit: RecursionError."""
    nest = []
    for _ in range(60_000):
        nest = [nest]
    return nest


RENDER_UNRENDERABLE = [
    ("a value json cannot serialise (TypeError)", object()),
    ("a container that refers to itself (ValueError)", _self_referential()),
    ("nesting past the recursion limit (RecursionError)", _too_deep_to_dump()),
]


@pytest.mark.parametrize("label,value", RENDER_UNRENDERABLE,
                         ids=[label for label, _ in RENDER_UNRENDERABLE])
def test_render_names_what_it_could_not_render(label, value):
    """Each of the three branches, with an input that actually reaches it.

    The model never invents a value (ACC-DATA-007), and "I could not read this"
    is a statement about the value; an em-dash is a view's way of printing
    nothing at all, and it would be indistinguishable from a field the coach
    left empty.
    """
    rendered = relay_model._render(value)
    assert rendered == f"an unreadable {type(value).__name__}", label
    assert EM_DASH not in rendered
    assert rendered not in PLACEHOLDER_STRINGS


def test_render_still_renders_everything_a_relay_file_can_hold():
    """The other side, so the test above cannot be satisfied by naming the type
    for every value: what a relay file CAN carry is rendered, not named."""
    assert relay_model._render([1, "a"]) == '[1, "a"]'
    assert relay_model._render({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
    assert relay_model._render(None) == "null"


@pytest.mark.parametrize("label,value", RENDER_UNRENDERABLE,
                         ids=[label for label, _ in RENDER_UNRENDERABLE])
def test_an_unrenderable_leg_field_is_named_in_the_model(tmp_path, label, value):
    """And through `build()`, where the string lands in a leg row. `_strlist`
    is the only caller of `_render`, and it feeds seven of the leg columns the
    Active Leg pane draws."""
    target = write_relay(tmp_path, f"unrenderable-{abs(hash(label))}",
                         legs={"legs": [{"id": "a", "status": "running"}]})
    # The value cannot travel through JSON — that is the point of it — so it is
    # handed to the coercion the way a parsed file would hand it over.
    rendered = relay_model._strlist([value])
    assert rendered == [f"an unreadable {type(value).__name__}"], label
    assert isinstance(relay_model.build(target, now=None), dict)


# --------------------------------------------------------------------------
# ACC-DATA-002 — plan order, when the stage list is not what it should be
#
# `_plan_order` ranked a leg of an undeclared stage with `len(stage_rank)`,
# assuming that sorts after every declared stage. `stage_rank` is built by
# SKIPPING unusable ids and by collapsing duplicates, so its length collides
# with a real stage's rank; where the stage entries also carry no `legs` array
# the within-stage key ties too and the file index decides, putting a leg that
# belongs to no declared stage AHEAD of S1 — which moves `activeLeg`, and
# ACC-DATA-002's second rule is about which leg that is.
#
# No test exercised plan order with an unusable or a duplicated stage id, which
# is why a regression in code no recent commit touched went unseen.
# --------------------------------------------------------------------------

#: A `stages` list whose ids do not number as many as its entries. Each shape
#: is one a coach can write into `legs.json` today and one the model already
#: warns about elsewhere — the collision is that it warns and then mis-sorts.
SHORT_RANKED_STAGES = {
    "a stage whose id is null": [{"id": None}, {"id": "S1"}],
    "a stage whose id is a list": [{"id": ["S0"]}, {"id": "S1"}],
    "a stage whose id is a number": [{"id": 7}, {"id": "S1"}],
    "a duplicated stage id": [{"id": "S1"}, {"id": "S1"}],
    "an empty stage id": [{"id": "   "}, {"id": "S1"}],
}


@pytest.mark.parametrize("label,stages", sorted(SHORT_RANKED_STAGES.items()))
def test_a_leg_of_no_declared_stage_sorts_after_the_declared_ones(
        tmp_path, label, stages):
    target = write_relay(
        tmp_path, f"plan-order-{abs(hash(label))}",
        legs={"relay": "plan-order", "stages": stages,
              "legs": [{"id": "zz", "stage": "NOPE", "status": "running"},
                       {"id": "a", "stage": "S1", "status": "running"}]})

    model = relay_model.build(target, now=None)

    assert [leg["id"] for leg in model["legs"]] == ["a", "zz"], label
    # ACC-DATA-002 rule 2: the active leg is the FIRST running leg in plan
    # order, so a broken order silently moves the pane's subject.
    assert model["activeLeg"]["id"] == "a", label
    assert model["activeRunner"]["leg"] == "a", label


def test_the_declared_stages_still_sort_among_themselves(tmp_path):
    """The other side of the same key: fixing the tail rank must not flatten
    the order the stage list declares."""
    target = write_relay(
        tmp_path, "plan-order-declared",
        legs={"relay": "plan-order",
              "stages": [{"id": None}, {"id": "S1"}, {"id": "S2"}],
              "legs": [{"id": "later", "stage": "S2", "status": "done"},
                       {"id": "orphan", "stage": "NOPE", "status": "done"},
                       {"id": "first", "stage": "S1", "status": "done"}]})

    model = relay_model.build(target, now=None)
    assert [leg["id"] for leg in model["legs"]] == ["first", "later", "orphan"]


def test_a_stage_that_declares_its_legs_still_orders_them(tmp_path):
    """And the within-stage key, which is what decides when the stage rank
    ties. A stage's own `legs` array is the order the coach wrote."""
    target = write_relay(
        tmp_path, "plan-order-within",
        legs={"relay": "plan-order",
              "stages": [{"id": None}, {"id": "S1", "legs": ["second", "first"]}],
              "legs": [{"id": "first", "stage": "S1", "status": "done"},
                       {"id": "second", "stage": "S1", "status": "done"},
                       {"id": "orphan", "stage": "NOPE", "status": "done"}]})

    model = relay_model.build(target, now=None)
    assert [leg["id"] for leg in model["legs"]] == ["second", "first", "orphan"]


# --------------------------------------------------------------------------
# ACC-DATA-002 — plan order is what the contract now DEFINES it to be
#
# Rewritten 2026-08-27 against the definition the contract gained that day:
# "Legs are ordered by their stage's position in `legs.json`'s `stages` array,
# then by their position within that stage's `legs` array, then by their
# position in the `legs` array itself... A duplicate stage id, or a stage list
# naming legs that no longer exist, must not silently reorder anything. Where
# the model cannot order legs as declared, it warns."
#
# Both halves were live defects, and both moved `activeLeg`:
#
# * the within-stage rank of a leg its stage does not list was `len(legs) + 1`
#   — a bound taken from the WRONG LIST. The positions it has to sit above are
#   indices into a STAGE's `legs` array, and a coach who renames or merges legs
#   leaves ids in that array with no leg entry left. Four of those in one stage
#   and a DECLARED leg sorts behind a forgotten one. This relay's own
#   `legs.json` has had legs renamed and merged at least five times.
# * `stage_rank` and the two `stageName` maps were last-wins dict
#   comprehensions, so a repeated stage id sorted its first entry's legs behind
#   every later stage AND nulled their `stageName` from a later entry carrying
#   no name — in total silence, while `model["stages"]` went on listing both.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("forgotten", [0, 1, 3, 4, 5, 9])
def test_a_leg_its_stage_lists_sorts_ahead_of_one_its_stage_forgot(
        tmp_path, forgotten):
    """The class, not the instance: however many renamed-away ids a stage's
    `legs` array still carries, a leg that array NAMES comes before a leg of
    the same stage it does not. `len(legs) + 1` held only while the stage list
    was shorter than the leg list, and a coach who renames legs makes it
    longer."""
    gone = [f"renamed-away-{n}" for n in range(forgotten)]
    target = write_relay(
        tmp_path, f"plan-order-forgotten-{forgotten}",
        legs={"relay": "plan-order",
              "stages": [{"id": "S1", "name": "Stage one", "legs": gone + ["a"]}],
              "legs": [{"id": "a", "stage": "S1", "status": "running"},
                       {"id": "b", "stage": "S1", "status": "running"}]})

    model = relay_model.build(target, now=None)

    assert [leg["id"] for leg in model["legs"]] == ["a", "b"], forgotten
    # ACC-DATA-002 rule 2, which is what the mis-rank actually broke: the
    # active leg is the first RUNNING leg in plan order.
    assert model["activeLeg"]["id"] == "a", forgotten
    assert model["activeRunner"]["leg"] == "a", forgotten
    # ...and every id the stage list names but the leg list has lost is said
    # out loud, one warning apiece. "Must not silently reorder anything."
    said = " | ".join(model["warnings"])
    for lost in gone:
        assert f"stage S1 lists leg '{lost}', which has no leg entry" in said


def test_a_stage_list_of_nothing_but_ghosts_still_orders_by_the_leg_array():
    """The degenerate end of the same rule, at the seam rather than through
    `build()`: when NO leg of a stage is named by that stage's list, the legs
    keep the order `legs.json`'s own `legs` array put them in."""
    stages = [{"id": "S1", "name": "One", "legs": ["gone-1", "gone-2", "gone-3"]}]
    legs = [{"id": "a", "stage": "S1"}, {"id": "b", "stage": "S1"}]
    warnings = []
    assert [leg["id"] for leg in relay_model._plan_order(legs, stages, warnings)] \
        == ["a", "b"]
    assert len(warnings) == 3, warnings


def test_a_duplicate_stage_id_reorders_nothing_and_does_not_do_it_silently(
        tmp_path):
    """The first declaration of a stage id is the one that counts — its
    position AND its name — and the model says the file declared it twice.

    Last-wins put S1's legs behind S2's and blanked their `stageName` from a
    second entry that carried no name, while `model["stages"]` still said
    otherwise: a model contradicting itself inside one build."""
    target = write_relay(
        tmp_path, "plan-order-duplicate-stage",
        legs={"relay": "plan-order",
              "stages": [{"id": "S1", "name": "Stage one", "legs": ["a"]},
                         {"id": "S2", "name": "Stage two", "legs": ["b"]},
                         {"id": "S1", "name": None, "legs": []}],
              "legs": [{"id": "a", "stage": "S1", "status": "running"},
                       {"id": "b", "stage": "S2", "status": "running"}]},
        state={"currentStage": "S1"})

    model = relay_model.build(target, now=None)

    assert [leg["id"] for leg in model["legs"]] == ["a", "b"]
    assert model["activeLeg"]["id"] == "a"
    assert model["activeRunner"]["leg"] == "a"
    # The name a supervisor reads on the leg row, on the stage row, and on the
    # current-stage header are one answer, from one map.
    assert {leg["id"]: leg["stageName"] for leg in model["legs"]} == \
        {"a": "Stage one", "b": "Stage two"}
    assert model["relay"]["currentStage"] == {"id": "S1", "name": "Stage one"}
    # `stages` is the file as written — both entries — so the model would
    # contradict itself if the resolution above were silent.
    assert [(s["id"], s["name"]) for s in model["stages"]] == \
        [("S1", "Stage one"), ("S2", "Stage two"), ("S1", None)]
    said = " | ".join(model["warnings"])
    assert "stage id 'S1' is declared by 2 stages" in said


def test_a_duplicate_stage_id_keeps_its_position_wherever_the_copy_sits(
        tmp_path):
    """And the copy cannot pull the stage FORWARD either. S2 is declared
    second; a repeat of it in front of S1 must not make its legs sort first."""
    target = write_relay(
        tmp_path, "plan-order-duplicate-forward",
        legs={"relay": "plan-order",
              "stages": [{"id": "S2", "name": None, "legs": []},
                         {"id": "S1", "name": "Stage one", "legs": ["a"]},
                         {"id": "S2", "name": "Stage two", "legs": ["b"]}],
              "legs": [{"id": "b", "stage": "S2", "status": "done"},
                       {"id": "a", "stage": "S1", "status": "done"}]})

    model = relay_model.build(target, now=None)
    # First-wins, applied consistently: S2's first declaration is at index 0,
    # so S2 leads — and its name is the one that declaration carries, which is
    # none. What is forbidden is a SILENT choice, not this choice.
    assert [leg["id"] for leg in model["legs"]] == ["b", "a"]
    assert {leg["id"]: leg["stageName"] for leg in model["legs"]} == \
        {"a": "Stage one", "b": None}
    assert "stage id 'S2' is declared by 2 stages" in " | ".join(model["warnings"])


def test_three_declarations_of_one_stage_id_warn_once_and_count_them(tmp_path):
    target = write_relay(
        tmp_path, "plan-order-triplicate",
        legs={"relay": "plan-order",
              "stages": [{"id": "S1", "name": "One"}, {"id": "S1"},
                         {"id": "S1"}],
              "legs": [{"id": "a", "stage": "S1", "status": "done"}]})

    model = relay_model.build(target, now=None)
    duplicates = [w for w in model["warnings"] if "is declared by" in w]
    assert len(duplicates) == 1, duplicates
    assert "stage id 'S1' is declared by 3 stages" in duplicates[0]


def test_a_leg_a_stage_lists_twice_keeps_its_first_position(tmp_path):
    """The same rule one level down, and the mutation battery is what found it:
    a `legs` array naming one leg twice declares two positions for it, the
    model can use one, and taking the LAST silently reorders the stage."""
    target = write_relay(
        tmp_path, "plan-order-repeated-leg",
        legs={"relay": "plan-order",
              "stages": [{"id": "S1", "name": "One",
                          "legs": ["a", "b", "a"]}],
              "legs": [{"id": "b", "stage": "S1", "status": "running"},
                       {"id": "a", "stage": "S1", "status": "running"}]})

    model = relay_model.build(target, now=None)

    assert [leg["id"] for leg in model["legs"]] == ["a", "b"]
    assert model["activeLeg"]["id"] == "a"
    assert "stage S1 lists leg 'a' more than once" in \
        " | ".join(model["warnings"])


def test_two_legs_of_one_stage_the_stage_never_named_keep_file_order(tmp_path):
    """ACC-DATA-002's third rank, asserted on its own: "then by their position
    in the `legs` array itself". It is `sort`'s stability rather than a term in
    the key, so it has to be asserted from the outside — in BOTH file orders,
    or a test cannot tell a stable sort from an alphabetical one."""
    for first, second in (("zeta", "alpha"), ("alpha", "zeta")):
        target = write_relay(
            tmp_path, f"plan-order-file-order-{first}",
            legs={"relay": "plan-order",
                  "stages": [{"id": "S1", "name": "One", "legs": []}],
                  "legs": [{"id": first, "stage": "S1", "status": "running"},
                           {"id": second, "stage": "S1", "status": "running"}]})
        model = relay_model.build(target, now=None)
        assert [leg["id"] for leg in model["legs"]] == [first, second]
        assert model["activeLeg"]["id"] == first


def test_a_leg_of_no_stage_at_all_is_not_adopted_by_an_unnameable_one(tmp_path):
    """A stage whose `id` could not be read is not a stage any leg can be in.
    Keying it as `None` makes it the stage of every leg whose `stage` field is
    missing — which moves those legs to that stage's position AND puts its name
    on their rows, an invented value in the one field ACC-DATA-007 forbids it
    in."""
    target = write_relay(
        tmp_path, "plan-order-null-stage",
        legs={"relay": "plan-order",
              "stages": [{"id": None, "name": "Ghost", "legs": ["nowhere"]},
                         {"id": "S1", "name": "One", "legs": ["a"]}],
              "legs": [{"id": "nowhere", "status": "running"},
                       {"id": "a", "stage": "S1", "status": "running"}]})

    model = relay_model.build(target, now=None)

    # The declared stage leads; the stageless leg follows it.
    assert [leg["id"] for leg in model["legs"]] == ["a", "nowhere"]
    assert model["activeLeg"]["id"] == "a"
    assert {leg["id"]: leg["stageName"] for leg in model["legs"]} == \
        {"a": "One", "nowhere": None}
    assert {leg["id"]: leg["stage"] for leg in model["legs"]} == \
        {"a": "S1", "nowhere": None}


def test_two_stages_with_no_usable_id_are_not_one_duplicated_stage(tmp_path):
    """They are two stages the model could not read, each already warned about
    by position. Counting them together says "stage id 'None' is declared by 2
    stages" — a placeholder printed as a value, in a diagnosis."""
    target = write_relay(
        tmp_path, "plan-order-two-null-stages",
        legs={"relay": "plan-order",
              "stages": [{"id": 7, "name": "A"}, {"id": ["x"], "name": "B"},
                         {"id": "S1", "name": "One", "legs": ["a"]}],
              "legs": [{"id": "a", "stage": "S1", "status": "running"}]})

    model = relay_model.build(target, now=None)
    assert not [w for w in model["warnings"] if "is declared by" in w], \
        model["warnings"]
    # ...and each one is still named, by the position it has.
    said = " | ".join(model["warnings"])
    assert "stage #0 `id`" in said and "stage #1 `id`" in said


def test_a_stage_declared_once_is_never_warned_about(relay):
    """The other side: the warning is a diagnosis, not noise. No fixture on
    disk — the nine of them and the live relay — declares a stage twice."""
    for name in ALL_FIXTURES:
        model = relay_model.build(relay(name), now=None)
        assert not [w for w in model["warnings"] if "is declared by" in w], name


# --------------------------------------------------------------------------
# ACC-DATA-001 — a relay directory the process may not search
#
# A relay directory with no search bit is STILL A DIRECTORY, so `build()` gets
# past the RelayNotFound guard, `_load` degrades correctly to permission-denied
# warnings, and then the log derivation walks it looking for `.git`.
# `pathlib`'s `exists()` swallows ENOENT, ENOTDIR, EBADF and ELOOP and lets
# EACCES through, and the `try/except OSError` nearby wrapped only the
# `.resolve()` calls — so `build()` raised PermissionError. At S4 that is an
# uncaught exception inside a 2-second repaint loop.
#
# The shape corpus above covers only NON-directories; nothing chmodded the
# relay directory itself. Root ignores permission bits, so these skip there and
# say so rather than reporting green for the wrong reason.
# --------------------------------------------------------------------------

NOT_ROOT = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root ignores permission bits, so a chmod test would pass for the "
           "wrong reason")


@pytest.fixture
def chmodded():
    """`chmod` paths and put every mode back, so a failure cannot leave a
    directory the next run is unable to delete."""
    restore = []

    def _chmod(path, mode):
        restore.append((path, os.stat(path).st_mode))
        os.chmod(path, mode)
        return path

    try:
        yield _chmod
    finally:
        for path, mode in reversed(restore):
            try:
                os.chmod(path, mode)
            except OSError:
                pass


@NOT_ROOT
@pytest.mark.parametrize("mode", [0o600, 0o000], ids=["no search bit", "no bits"])
def test_a_relay_directory_with_no_search_bit_degrades_rather_than_raising(
        tmp_path, chmodded, mode):
    project = tmp_path / "project"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "locked", "legs": [{"id": "a", "status": "done"}]}))
    chmodded(relay_dir, mode)

    model = build_in_time(relay_dir)

    assert isinstance(model, dict)
    json.dumps(model)
    assert model["warnings"], "an unreadable relay says so"
    assert any("permission" in w.lower() for w in model["warnings"]), \
        model["warnings"]


@NOT_ROOT
def test_a_relay_whose_repository_is_readable_still_finds_it(tmp_path, chmodded):
    """The other side of the guard, and the reason an unreadable candidate
    CONTINUES the walk rather than ending it: the live relay shape is
    `<project>/.relay` with the `.git` at `<project>`. A `.relay` that cannot
    answer is a refusal, not an answer, and the repository is one level up.

    `_has_git` over `_repo_roots` is the cheap precondition that decides whether
    git is asked at all — a necessary condition for git to report a work tree,
    never a sufficient one — so a refusal that ended the search would cost a
    chmod'd live relay its whole history without git ever being consulted."""
    project = tmp_path / "project"
    relay_dir = project / ".relay"
    relay_dir.mkdir(parents=True)
    (project / ".git").mkdir()
    chmodded(relay_dir, 0o600)

    # A REFUSAL IS None, NOT False - a third answer, because a directory that
    # would not be read has not said there is no repository there, and telling
    # a supervisor it did is a false statement about their relay.
    assert relay_model._has_git(relay_dir) is None        # a refusal, not "no"
    assert relay_model._has_git(relay_dir.parent) is True
    assert any(relay_model._has_git(root)
               for root in relay_model._repo_roots(relay_dir)) is True
    # ...and the shape is what carries it there: a relay that is its own
    # project has one candidate, refusal or no refusal.
    assert relay_model._repo_roots(relay_dir / "not-dot-relay") == \
        [relay_dir / "not-dot-relay"]


@NOT_ROOT
def test_a_batons_directory_that_cannot_be_listed_is_named_not_raised(
        tmp_path, chmodded):
    """The `pathlib` glob trap from the probing skill, at the one place this
    module lists a directory it does not own."""
    relay_dir = tmp_path / "relay"
    batons = relay_dir / "batons"
    batons.mkdir(parents=True)
    (batons / "a.md").write_text("# Baton\nSTATUS: success\n")
    (relay_dir / "legs.json").write_text(json.dumps(
        {"legs": [{"id": "a", "status": "done"}]}))
    chmodded(batons, 0o000)

    model = build_in_time(relay_dir)

    assert isinstance(model, dict)
    assert any("batons" in w for w in model["warnings"]), model["warnings"]
    assert {r["leg"]: r["batonPath"] for r in model["runners"]} == {"a": None}


@NOT_ROOT
def test_an_unreadable_baton_is_a_warning_and_an_absent_column(
        tmp_path, chmodded):
    relay_dir = tmp_path / "relay"
    batons = relay_dir / "batons"
    batons.mkdir(parents=True)
    baton = batons / "a.md"
    baton.write_text("# Baton\nSTATUS: success\n**Commit:** 1a2b3c4\n")
    (relay_dir / "legs.json").write_text(json.dumps(
        {"legs": [{"id": "a", "status": "done"}]}))
    chmodded(baton, 0o000)

    model = build_in_time(relay_dir)

    rows = {r["leg"]: r for r in model["runners"]}
    assert rows["a"]["commit"] is None and rows["a"]["batonLines"] is None
    assert any("a.md" in w and "permission" in w.lower()
               for w in model["warnings"]), model["warnings"]


# --------------------------------------------------------------------------
# ACC-DATA-001 — the read guard's own error paths
#
# Five branches of `_read_relay_file` and `_read_baton` that no test reached.
# The sharpest was the size PRE-CHECK: `MAX_RELAY_FILE_BYTES` is an exact
# multiple of `_READ_CHUNK`, so the in-loop bound reported the identical byte
# count and the test asserting on the message could not tell which of the two
# had fired. The two bounds answer two different questions — "this file is too
# big" and "this file grew while I was reading it" — and they now say so.
# --------------------------------------------------------------------------

def test_the_size_pre_check_and_the_growth_bound_are_told_apart(tmp_path):
    """The pre-check fires on a file that was already too big when it was
    opened, and it says so in words the growth bound does not use."""
    target = tmp_path / "relay"
    target.mkdir()
    oversized = target / "legs.json"
    oversized.write_bytes(b"x" * (relay_model.MAX_RELAY_FILE_BYTES + 1))

    model = relay_model.build(target, now=None)

    why = [w for w in model["warnings"] if "legs.json" in w]
    assert len(why) == 1, model["warnings"]
    assert "it is over" in why[0], why[0]
    assert "grew" not in why[0], why[0]


def test_a_file_that_grows_past_the_bound_under_the_read_is_refused(tmp_path):
    """The in-loop bound, which the pre-check cannot stand in for: `st_size` is
    a snapshot and a live relay is being written to. A file whose reported size
    is smaller than what the descriptor yields is exactly that race, held
    still — `fstat` is made to under-report so the loop is the only bound left
    to catch it.
    """
    target = tmp_path / "relay"
    target.mkdir()
    growing = target / "legs.json"
    growing.write_bytes(b"y" * (relay_model.MAX_RELAY_FILE_BYTES + 1))
    real_fstat = os.fstat

    class Undersized:
        """A stat answer that lies about `st_size`, and about nothing else."""

        def __init__(self, st):
            self.st_mode, self.st_mtime, self.st_size = st.st_mode, st.st_mtime, 1

    def lying_fstat(fd):
        st = real_fstat(fd)
        return Undersized(st) if st.st_size > relay_model.MAX_RELAY_FILE_BYTES else st

    raw, mtime, why = None, None, None
    try:
        os.fstat = lying_fstat
        raw, mtime, why = relay_model._read_relay_file(growing)
    finally:
        os.fstat = real_fstat

    assert raw is None and mtime is None
    assert why is not None and "grew" in why, why
    assert str(relay_model.MAX_RELAY_FILE_BYTES) in why


def _open_descriptors():
    """How many file descriptors this process holds, on macOS and on Linux."""
    for probe in ("/dev/fd", "/proc/self/fd"):
        if os.path.isdir(probe):
            return len(os.listdir(probe))
    pytest.skip("no descriptor table to read on this platform")


def test_repeated_builds_do_not_leak_a_descriptor(relay):
    """`_read_relay_file` closes in a `finally`, and nothing held it to that.
    `build()` runs once every two seconds for as long as a supervisor watches,
    reading three JSON files and a baton per leg: a descriptor leaked per read
    exhausts the table in minutes."""
    target = relay("agent-service")
    relay_model.build(target, now=None)          # warm anything cached
    before = _open_descriptors()
    for _ in range(40):
        relay_model.build(target, now=None)
    after = _open_descriptors()
    assert after - before <= 2, (before, after)


def test_a_path_with_an_embedded_nul_is_refused_rather_than_raising():
    """`baton_text` is public and a detail view calls it on whatever path a row
    carries. `os.open` answers a NUL in a path with ValueError, which is not an
    OSError and was not caught anywhere below it."""
    assert relay_model.baton_text("/tmp/a\x00b") is None
    assert relay_model.baton_text(b"\x00") is None
    assert relay_model.baton_text(object()) is None


def test_a_baton_whose_path_cannot_be_resolved_still_yields_its_row(tmp_path):
    """`_read_baton` resolves the path it read, for the `batonPath` a detail
    view opens. `resolve()` is a second walk of the filesystem and can fail
    where the read did not; the row is still worth having, with the path the
    caller gave."""
    relay_dir = tmp_path / "relay"
    batons = relay_dir / "batons"
    batons.mkdir(parents=True)
    (batons / "a.md").write_text("# Baton\nSTATUS: success\n**Commit:** 1a2b3c4\n")
    (relay_dir / "legs.json").write_text(json.dumps(
        {"legs": [{"id": "a", "status": "done"}]}))

    real_resolve = Path.resolve

    def refusing_resolve(self, *args, **kwargs):
        if self.name == "a.md":
            raise OSError(62, "Too many levels of symbolic links")
        return real_resolve(self, *args, **kwargs)

    try:
        Path.resolve = refusing_resolve
        model = relay_model.build(relay_dir, now=None)
    finally:
        Path.resolve = real_resolve

    row = {r["leg"]: r for r in model["runners"]}["a"]
    assert row["commit"] == "1a2b3c4"
    assert row["batonPath"] is not None and row["batonPath"].endswith("a.md")


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
    """The same fixtures where they really live, on the real wall clock.

    Neither half of this invariant depends on mtimes, so the fixture needs no
    stamping and can be read where it lies. What that adds over the `tmp_path`
    copy above is the real clock and the real path — NOT the commit-reading
    branch of `build()`, which this reading does not reach either: a fixture
    holds no `.git` of its own, and a relay that is its own project owns only
    the repository rooted at it. The wording here used to imply otherwise, so
    the bound is
    asserted rather than described. `ACC-DATA-003` over a relay that does reach
    git is `test_active_leg_and_active_runner_agree_in_a_repository` below.
    """
    target = FIXTURES / name
    assert relay_model._repo_reading(target).dir is None, target
    assert_active_agrees(relay_model.build(target), f"{name} (in place)")


#: Which of `ALL_FIXTURES` carry no running leg. `assert_active_agrees`
#: returns early — silently, and correctly — when a relay has no active leg,
#: so a fixture set that drifted to all-done would turn both sweeps above into
#: sweeps of early returns with nothing said about it. This says how much of
#: the sweep is real, and it is asserted, not assumed.
#:
#: NAMED, NOT COUNTED. This was `FIXTURES_WITH_AN_ACTIVE_LEG = 6`, and a leg
#: running beside this one added a fixture WITH a running leg — which made the
#: count wrong while making the sweep bigger. A count cannot tell those two
#: apart; the fixtures that make the sweep vacuous can be named, and a new
#: fixture then has to be added here to be excused.
FIXTURES_WITHOUT_AN_ACTIVE_LEG = {"all-done", "empty", "ghost-currentleg",
                                  "malformed"}


def test_the_active_leg_sweeps_are_not_sweeps_of_early_returns():
    """The non-vacuity guard for the two sweeps above (ACC-DATA-003).

    `assert_active_agrees` asserts the whole disjunction, and one arm of it —
    "both are absent" — is satisfied by returning. That is the right shape for
    the helper and the wrong shape to leave uncounted: a relay with nothing
    running exercises none of the identity assertions, so a sweep over ten
    fixtures says as little as a sweep over zero if none of them is running.
    """
    active = [name for name in ALL_FIXTURES
              if relay_model.build(FIXTURES / name, now=None)["activeLeg"]]
    assert set(active) == set(ALL_FIXTURES) - FIXTURES_WITHOUT_AN_ACTIVE_LEG, \
        active
    assert FIXTURES_WITHOUT_AN_ACTIVE_LEG <= set(ALL_FIXTURES), \
        "no fixture is excused that is not there"
    # And the identity arm really runs on each of them: a leg is active and a
    # runner row is the one it derives.
    for name in active:
        model = relay_model.build(FIXTURES / name, now=None)
        assert model["activeRunner"] is not None, name
        assert any(row is model["activeRunner"] for row in model["runners"]), name


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("label", GIT_CORPUS_LABELS)
def test_active_leg_and_active_runner_agree_in_a_repository(label, git_corpus):
    """ACC-DATA-003 over a relay where the commit branch of `build()` runs.

    Every other reading of this invariant is of a relay outside any repository
    of its own, so `_settle_commits` never runs and no runner row's `commit`
    ever came from git. That is the same hole ACC-DATA-007's five sweeps had,
    and it is closed here with the same corpus rather than left to be found
    again on the next check.
    """
    model = relay_model.build(git_corpus[label], now=None)
    assert_active_agrees(model, label)
    # Non-vacuity, in the shape this corpus exists to provide: the relay really
    # reached git, so the row the invariant is about carries a settled column.
    assert any(row["commit"] for row in model["runners"]), model["runners"]


def test_the_git_corpus_has_an_active_leg_to_agree_about(git_corpus):
    """And at least one of the two corpus relays runs a leg, so the sweep above
    is not two early returns."""
    active = [label for label, target in git_corpus.items()
              if relay_model.build(target, now=None)["activeLeg"]]
    assert active, list(git_corpus)


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
    assert sorted((r["leg"] or "") for r in model["runners"]) == \
        ["", "twin", "twin"]
    assert [r["leg"] for r in model["runners"] if r["leg"] is None], \
        "the nameless leg's row names no leg rather than an empty string"
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
    # Quoted, not merely present: `"a" in w and "b" in w` is satisfied by any
    # sentence with the letters in it — "two legs are both marked running"
    # passes it while naming neither leg, which is the whole point of the
    # warning. The model quotes an id it is naming, so the test asks for that.
    named = [w for w in model["warnings"] if "'a'" in w and "'b'" in w]
    assert len(named) == 1, model["warnings"]
    assert "running" in named[0], named[0]


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


# --------------------------------------------------------------------------
# THE VOCABULARIES AND BOUNDS ARE STATED HERE, NOT READ FROM THE MODULE
#
# THE CLASS THE ROUND-6 CODE JUDGE FOUND, and the reason 18 of its 21 hand
# mutations left the suite green. Every one of these was a constant whose
# expected value the tests fetched from the module itself:
#
#     STATUS_ALIASES["pending"] shrunk 8 -> 2        green
#     BATON_STATUS and PLACEHOLDERS shrunk           green
#     LOG_MAX_COMMITS -> 40                          green
#     LOG_MAX_ENTRIES -> 250                         green
#     GIT_TIMEOUT -> 300.0                           green
#     MAX_RELAY_FILE_BYTES changed                   green
#
# A test that reads its expectation from the module cannot fail when the module
# changes, and reading it is often the RIGHT thing elsewhere in this file - a
# fixture that must be larger than a bound has to know the bound. So the class
# is closed once, here, where the values are written out in full and owe
# nothing to the module: an edit to any of them is red until a human writes the
# new value down beside the old one's reasons.
#
# `test_the_module_declares_no_constant_this_file_has_not_pinned` is what keeps
# it closed: a constant added later is red until it is pinned too.
# --------------------------------------------------------------------------

#: The four leg states and the four check states, in the order the module
#: declares them - `LEG_STATES` is iterated to build `legCounts`, so its
#: MEMBERS are the vocabulary and its ORDER is a view's column order.
PINNED_STATE_TUPLES = {
    "LEG_STATES": ("completed", "running", "pending", "cancelled"),
    "CHECK_STATES": ("passed", "failed", "blocked", "pending"),
}

#: Sort ranks. A check pane leads with what is wrong; an attention band leads
#: with what needs a human. Shrinking either to `{}` is a silent reordering.
PINNED_ORDERS = {
    "CHECK_ORDER": {"failed": 0, "blocked": 1, "pending": 2, "passed": 3},
    "ATTENTION_ORDER": {"bad": 0, "warn": 1, "note": 2, "calm": 3},
    "LOG_KIND_ORDER": {"check": 0, "baton": 1, "commit": 2, "start": 3},
}

#: What a coach types where there is no value. Every one of these reads as
#: absence, and `""` among them is why a runner row may not carry `""` as a
#: leg id (ACC-DATA-007).
PINNED_PLACEHOLDERS = {"", "-", "--", "—", "–", "n/a", "na", "none", "null",
                       "tbd", "?"}

#: ACC-DATA-004. The nine spellings the check names by name are in here, and
#: so are the ones it does not: a `pending` alias maps to `pending` through the
#: FALLBACK whether or not the table knows it, so the whole `pending` set was
#: deletable with every behavioural assertion still green.
PINNED_STATUS_ALIASES = {
    "completed": {"completed", "complete", "done", "finished", "shipped",
                  "landed", "passed", "merged"},
    "running": {"running", "in_progress", "in-progress", "inprogress",
                "active", "wip", "started", "underway"},
    "cancelled": {"cancelled", "canceled", "skipped", "dropped", "abandoned",
                  "superseded"},
    "pending": {"pending", "todo", "queued", "planned", "not_started", "new",
                "blocked", "waiting"},
}

#: The same fallback hole, twice over: an unknown check word is `pending` and
#: an unknown phase word is None, so neither table's members are visible in a
#: result alone.
PINNED_CHECK_ALIASES = {
    "passed": {"passed", "pass", "ok", "green", "satisfied", "evidenced"},
    "failed": {"failed", "fail", "red", "broken"},
    "blocked": {"blocked", "block", "unevidenced", "cannot_verify",
                "unverifiable"},
}

PINNED_PHASE_ALIASES = {
    "running": {"running", "active", "in_progress", "in-progress", "executing"},
    "judging": {"judging", "judge", "gating", "gate", "reviewing", "review"},
    "blocked": {"blocked", "stalled", "paused", "halted", "waiting", "stuck"},
    "complete": {"complete", "completed", "done", "finished", "shipped"},
    "pending": {"pending", "planning", "proposed", "draft", "not_started",
                "awaiting_approval", "approved"},
}

#: ACC-DATA-007. What a runner wrote on its baton's `STATUS:` line, mapped onto
#: a row status. A word missing here falls back to the leg's own state, so a
#: shrunk table is invisible in a row that was completed anyway.
PINNED_BATON_STATUS = {"success": "completed", "ok": "completed",
                       "partial": "partial", "failed": "failed",
                       "failure": "failed"}

#: The labels an attention item may carry that mean a human is being asked for
#: something (ACC-ATTN-001's vocabulary).
PINNED_BAD_LABELS = {"NEEDS YOUR CALL", "STALLED", "BLOCKED", "STOP",
                     "DECISION"}

#: The level and wording each baton status lands in the Progress Log with.
PINNED_BATON_LOG = {
    "completed": ("calm", "{leg} landed"),
    "partial": ("warn", "{leg} landed partial, with work left undone"),
    "failed": ("bad", "{leg} failed"),
}

#: Every numeric bound in the module, with what each one is for. All four were
#: mutable with the suite green: the tests that scale a fixture against a bound
#: read the bound, so shrinking it shrinks the fixture with it.
PINNED_BOUNDS = {
    # How far back the git walk looks. Below LOG_MAX_ENTRIES on purpose, so an
    # entirely attributed log still fits the entry bound.
    "LOG_MAX_COMMITS": 200,
    # How many entries the Progress Log keeps - and it yields to attribution
    # (ACC-DATA-009): every confirmed claim appears however many there are.
    "LOG_MAX_ENTRIES": 300,
    # Seconds ALL of one build()'s git work may take, shared by every read
    # rather than renewed per read. build() runs inside a 2 s repaint loop, so
    # this is the difference between a slow pane and a frozen one - and it is
    # below 2.0 because the file work and the draw come out of the same budget.
    # It was `GIT_TIMEOUT = 3.0`, a ceiling on ONE process that four sequential
    # reads each spent in full: a judge measured build() at 10.7 s.
    "GIT_BUDGET": 1.5,
    # The most a relay file may weigh before the model refuses to read it: 1
    # MiB, twenty times the largest relay file this project has written.
    "MAX_RELAY_FILE_BYTES": 1024 * 1024,
    # The read loop's chunk. MAX_RELAY_FILE_BYTES is an exact multiple of it,
    # which is what makes the in-loop bound and the size pre-check agree.
    "_READ_CHUNK": 1 << 16,
}

#: The shapes a relay path may turn out to be, and the words a warning carries
#: for each. Pinned as the WORDS: `_SHAPES` holds `stat` predicates, and the
#: labels are what a supervisor reads.
PINNED_SHAPE_WORDS = ["a directory", "a FIFO", "a socket",
                      "a character device", "a block device",
                      "a symbolic link", "a regular file"]

#: The three sentence forms that read as a leg claiming a commit as its own
#: work (ACC-DATA-009), and the two other patterns the module compiles.
PINNED_PATTERNS = {
    "_SHA": r"(?<![0-9a-f])([0-9a-f]{7,40})(?![0-9a-f])",
    "COMMIT_CLAIM_RES": [
        r"(?:^|(?<=[.;])\s|(?<=,)\s)\W*(?:\*\*)?(?:merge\s+|final\s+)?"
        r"commit(?:\*\*)?\s*:?\s*(?:\*\*)?\s*`?"
        r"(?<![0-9a-f])([0-9a-f]{7,40})(?![0-9a-f])",
        r"committed(?:\*\*)?\s+as\s*(?:\*\*)?\s*`?"
        r"(?<![0-9a-f])([0-9a-f]{7,40})(?![0-9a-f])",
        r"git\s+commit\b[^\n]{0,60}?`?"
        r"(?<![0-9a-f])([0-9a-f]{7,40})(?![0-9a-f])",
    ],
    "STATUS_RE": r"^\W*(?:\*\*)?status(?:\*\*)?\s*:\s*(\w+)",
    "LABEL_RE": r"^([A-Z][A-Z0-9 /_-]{1,40}):\s*(.+)$",
}

#: The module's public surface. A name leaving it is an import a view loses.
PINNED_ALL = ["build", "baton_text", "normalise_status", "normalise_check",
              "normalise_phase", "kind_of", "RelayNotFound", "LEG_STATES",
              "CHECK_STATES"]


def test_the_state_tuples_are_what_this_file_says_they_are():
    for name, expected in PINNED_STATE_TUPLES.items():
        assert getattr(relay_model, name) == expected, name


def test_the_sort_orders_are_what_this_file_says_they_are():
    for name, expected in PINNED_ORDERS.items():
        assert getattr(relay_model, name) == expected, name


def test_the_placeholder_vocabulary_is_what_this_file_says_it_is():
    assert relay_model.PLACEHOLDERS == PINNED_PLACEHOLDERS
    # ...and it is the vocabulary `_text` actually reads, spelling by spelling.
    for word in PINNED_PLACEHOLDERS:
        assert relay_model._text(f"  {word.upper()}  ") is None, word


def test_the_status_vocabulary_is_what_this_file_says_it_is():
    assert relay_model.STATUS_ALIASES == PINNED_STATUS_ALIASES
    # Every spelling, not only the nine ACC-DATA-004 names: a `pending` alias
    # is indistinguishable from an unknown word in the RESULT, so membership is
    # asserted through the mapping of every word in every set.
    for canon, aliases in PINNED_STATUS_ALIASES.items():
        for alias in aliases:
            assert relay_model.normalise_status(alias) == canon, alias
            assert relay_model.normalise_status(alias.upper()) == canon, alias


def test_the_check_and_phase_vocabularies_are_what_this_file_says_they_are():
    assert relay_model.CHECK_ALIASES == PINNED_CHECK_ALIASES
    assert relay_model.PHASE_ALIASES == PINNED_PHASE_ALIASES
    for canon, aliases in PINNED_CHECK_ALIASES.items():
        for alias in aliases:
            assert relay_model.normalise_check(alias) == canon, alias
    for canon, aliases in PINNED_PHASE_ALIASES.items():
        for alias in aliases:
            assert relay_model.normalise_phase(alias) == canon, alias


def test_the_baton_vocabularies_are_what_this_file_says_they_are():
    assert relay_model.BATON_STATUS == PINNED_BATON_STATUS
    assert relay_model.BATON_LOG == PINNED_BATON_LOG
    assert relay_model.BAD_LABELS == PINNED_BAD_LABELS


def test_the_bounds_are_what_this_file_says_they_are():
    for name, expected in PINNED_BOUNDS.items():
        assert getattr(relay_model, name) == expected, name
    # The three relationships the values exist in, stated rather than implied.
    assert relay_model.LOG_MAX_COMMITS < relay_model.LOG_MAX_ENTRIES
    assert relay_model.MAX_RELAY_FILE_BYTES % relay_model._READ_CHUNK == 0
    # ACC-DATA-001: the whole call has to fit a 2 s repaint, and git is not the
    # only thing in it. A budget equal to the repaint budget is already over it.
    assert 0 < relay_model.GIT_BUDGET < 2.0


#: ACC-DATA-009. Why the model could not read a repository for a relay, in the
#: words a supervisor reads. Written out here rather than fetched, because the
#: defect these replaced was a SENTENCE that was false - one clause covering
#: four different facts - and a test that reads the clause from the module
#: cannot tell a true one from a false one.
PINNED_REPO_REASONS = {
    # Nothing that could hold a repository holds one. The only reading the old
    # single clause was ever right about.
    "REPO_NONE": "this relay is not inside a repository of its own",
    # Every candidate refused to be read, so nobody found out.
    "REPO_UNREADABLE": "this relay's own directory could not be read",
    # Git was asked and did not answer: absent, broken, or slower than
    # GIT_BUDGET.
    "REPO_SILENT": "git could not answer for this relay",
    # Git answered about somewhere else - `core.worktree` aimed elsewhere, or
    # a `.git` git rejected so it reported the surrounding project instead.
    "REPO_ELSEWHERE": "git reports {top} as this relay's work tree, "
                      "which is not it",
}


def test_the_repository_diagnoses_are_what_this_file_says_they_are():
    for name, expected in PINNED_REPO_REASONS.items():
        assert getattr(relay_model, name) == expected, name
    # Four DIFFERENT sentences: collapsing any two of them back together is
    # how the model came to state a falsehood about a relay that has a
    # repository sitting right there.
    assert len(set(PINNED_REPO_REASONS.values())) == 4


def test_the_path_shapes_and_the_patterns_are_what_this_file_says_they_are():
    assert [word for _, word in relay_model._SHAPES] == PINNED_SHAPE_WORDS
    assert relay_model._SHA == PINNED_PATTERNS["_SHA"]
    assert [p.pattern for p in relay_model.COMMIT_CLAIM_RES] == \
        PINNED_PATTERNS["COMMIT_CLAIM_RES"]
    assert relay_model.STATUS_RE.pattern == PINNED_PATTERNS["STATUS_RE"]
    assert relay_model.LABEL_RE.pattern == PINNED_PATTERNS["LABEL_RE"]
    assert relay_model.__all__ == PINNED_ALL


def test_the_open_flags_are_the_three_this_module_needs():
    """Pinned as a composition rather than as an integer: the numbers differ
    between platforms and the guarantee does not. O_NONBLOCK is the whole guard
    against a FIFO with no writer, and O_NOCTTY keeps a terminal device from
    becoming this process's controlling terminal."""
    assert relay_model._OPEN_FLAGS == (
        os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0))
    assert relay_model._OPEN_FLAGS & getattr(os, "O_NONBLOCK", 0)


#: Module-level names this file deliberately does not pin, with why. Anything
#: else new is red until it is written down above.
UNPINNED_MODULE_NAMES = {
    "_NO_CLOCK": "a sentinel object; its identity is its whole content",
    "_OPEN_FLAGS": "pinned as a composition in its own test, not as an integer",
    "_SHAPES": "pinned as its words in `PINNED_SHAPE_WORDS`",
    "COMMIT_CLAIM_RES": "pinned as its patterns in `PINNED_PATTERNS`",
    "STATUS_RE": "pinned as its pattern in `PINNED_PATTERNS`",
    "LABEL_RE": "pinned as its pattern in `PINNED_PATTERNS`",
    "_SHA": "pinned as its pattern in `PINNED_PATTERNS`",
    "PLACEHOLDERS": "pinned in `PINNED_PLACEHOLDERS`",
    "STATUS_ALIASES": "pinned in `PINNED_STATUS_ALIASES`",
    "CHECK_ALIASES": "pinned in `PINNED_CHECK_ALIASES`",
    "PHASE_ALIASES": "pinned in `PINNED_PHASE_ALIASES`",
    "BATON_STATUS": "pinned in `PINNED_BATON_STATUS`",
    "BATON_LOG": "pinned in `PINNED_BATON_LOG`",
    "BAD_LABELS": "pinned in `PINNED_BAD_LABELS`",
    "__all__": "pinned in `PINNED_ALL`",
}


def test_the_module_declares_no_constant_this_file_has_not_pinned():
    """The guard that keeps the class closed rather than the seven instances.

    Every module-level assignment in `relay_model.py` that is not a function or
    a class is either pinned above or named in `UNPINNED_MODULE_NAMES` with a
    reason. A constant added later - a new bound, a new alias table - is red
    until somebody writes its value down here, which is the only thing that
    makes any of these tests capable of failing when the module changes.
    """
    tree = ast.parse((REPO / "scripts" / "relay_model.py").read_text())
    declared = []
    for node in tree.body:
        targets = ([node.target] if isinstance(node, ast.AnnAssign)
                   else getattr(node, "targets", []))
        for target in targets:
            if isinstance(target, ast.Name):
                declared.append(target.id)
    assert declared, "the module declares module-level names"

    pinned = (set(PINNED_STATE_TUPLES) | set(PINNED_ORDERS)
              | set(PINNED_BOUNDS) | set(PINNED_REPO_REASONS)
              | set(UNPINNED_MODULE_NAMES))
    assert set(declared) - pinned == set(), sorted(set(declared) - pinned)
    # ...and nothing here pins a name the module no longer has.
    assert pinned - set(declared) == set(), sorted(pinned - set(declared))

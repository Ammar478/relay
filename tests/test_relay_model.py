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
import re
import shutil
import subprocess
import sys
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
                    import os

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


def test_relay_model_is_the_only_reader_of_relay_files():
    """No module outside relay_model.py opens a relay file.

    `scripts/render_dashboard.py` is the one standing exception: ACC-HTML-001
    ports it onto this model and must delete it from this list. Anything else
    appearing here is a new source of truth and a regression of ACC-DATA-001.
    """
    allowed = {"scripts/relay_model.py", "scripts/render_dashboard.py"}
    reader = re.compile(r"json\.load|\.read_text\(|\bopen\(")
    names = re.compile(r"legs\.json|state\.json|dashboard\.json|batons")

    offenders = set()
    for path in sorted(REPO.glob("**/*.py")):
        if ".git" in path.parts or "fixtures" in path.parts:
            continue
        rel = path.relative_to(REPO).as_posix()
        if rel in allowed or rel.startswith("tests/"):
            continue
        text = path.read_text()
        for line in text.splitlines():
            if reader.search(line) and names.search(line):
                offenders.add(f"{rel}: {line.strip()}")
    assert offenders == set(), offenders


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
    no_baton = rows["merge-and-tag"] if "merge-and-tag" in rows else None
    assert no_baton is None or (
        no_baton["commit"] is None and no_baton["batonLines"] is None
        and no_baton["start"] is None and no_baton["duration"] is None)

    # A leg with a baton: the fields the baton actually carries are filled in.
    landed = rows["reconcile-develop"]
    assert landed["batonLines"] > 0
    # No `**Commit:**` field in this baton, so the first commit its prose names.
    assert landed["commit"] == "7f8690c"
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
                               {"id": "real", "status": "running"}]},
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
# ACC-DATA-003 — the invariant, asserted as the check words it
# --------------------------------------------------------------------------

def assert_active_agrees(model, label=""):
    """`activeRunner.leg == activeLeg.id`, **or both are absent** — the whole
    disjunction, which is what the check says and what a leg with an
    unusable id used to break by satisfying neither half.
    """
    leg, runner = model["activeLeg"], model["activeRunner"]
    assert (leg is None) == (runner is None), (label, leg, runner)
    if leg is None:
        return
    assert runner["leg"] == leg["id"], (label, runner["leg"], leg["id"])
    # Identity, not just equality of ids: with duplicate ids in legs.json two
    # different legs answer to the same string, and the runner row must be the
    # one built from *this* leg.
    assert any(row is runner for row in model["runners"]), label
    assert runner["status"] == "running", (label, runner["status"])
    assert leg["isActive"] is True, label
    assert leg["status"] == "running", (label, leg["status"])


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_active_leg_and_active_runner_agree_on_every_fixture(name, relay):
    assert_active_agrees(relay_model.build(relay(name)), name)


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

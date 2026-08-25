"""Tests for the Progress Log (ACC-DATA-005, ACC-DATA-006).

The model derives a chronological log of what the relay has done from the three
records that carry a real order: baton mtimes, `git log` on the relay's branch,
and the check transitions recorded in `state.json`. A coach who writes
`dashboard.json.log` overrides all of it.

Baton mtimes are the only record of runner order on disk and git does not
preserve them, so every ordering assertion here works against a copy in
`tmp_path` with the recorded mtimes stamped back on. That is what the `relay`
fixture imported from `test_relay_model` does; do not write a second one.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import relay_model  # noqa: E402

# The git corpus lives in `test_relay_model` because ACC-DATA-007's sweeps read
# it too, and that is the module this one already imports from. One corpus, two
# checks: a second copy of it here is how the last hole survived its own repair.
from test_relay_model import (  # noqa: E402,F401  (fixtures are used by name)
    AGENT_SERVICE_BATON_MTIMES,
    CORPUS_FORK_POINT,
    CORPUS_OWN,
    CORPUS_QUOTED,
    CORPUS_SILENT,
    HAS_GIT,
    corpus_relay,
    git_run as _git,
    relay,
)

# Later than every recorded baton mtime in the agent-service fixture, so ages
# come out positive. Pinned, so the whole file is deterministic.
NOW = 1787600000.0


def entries_of(model, kind):
    return [e for e in model["log"] if e["kind"] == kind]


# --------------------------------------------------------------------------
# ACC-DATA-005 — a log is derived when the coach writes none
# --------------------------------------------------------------------------

def test_agent_service_has_no_explicit_log(relay):
    """The premise of ACC-DATA-005's evidence line: the fixture's coach wrote
    no `log`, so anything in the model was derived."""
    target = relay("agent-service")
    dashboard = json.loads((target / "dashboard.json").read_text())
    assert "log" not in dashboard


def test_derived_log_has_at_least_ten_entries(relay):
    """ACC-DATA-005 evidence: >= 10 entries against agent-service."""
    model = relay_model.build(relay("agent-service"), now=NOW)
    assert model["logSource"] == "derived"
    assert len(model["log"]) >= 10


def test_derived_log_is_in_descending_time_order(relay):
    model = relay_model.build(relay("agent-service"), now=NOW)
    times = [e["t"] for e in model["log"]]
    assert times == sorted(times, reverse=True)
    assert len(times) >= 10


def test_every_entry_has_a_timestamp_an_age_and_a_message(relay):
    model = relay_model.build(relay("agent-service"), now=NOW)
    for e in model["log"]:
        assert isinstance(e["t"], float), e
        assert isinstance(e["age"], float), e
        assert isinstance(e["m"], str) and e["m"], e


def test_every_entry_carries_a_kind_and_what_it_concerns(relay):
    """A view needs a column to align and a target to jump to."""
    model = relay_model.build(relay("agent-service"), now=NOW)
    for e in model["log"]:
        assert e["kind"] in ("baton", "commit", "start", "check"), e
        assert e["leg"] is not None or e["check"] is not None or e["commit"], e
        assert e["level"] in ("bad", "warn", "note", "calm"), e


def test_age_is_derived_through_the_injectable_now(relay):
    target = relay("agent-service")
    model = relay_model.build(target, now=NOW)
    for e in model["log"]:
        assert e["age"] == NOW - e["t"]

    later = relay_model.build(target, now=NOW + 3600)
    assert [e["age"] for e in later["log"]] == [e["age"] + 3600 for e in model["log"]]


def test_every_entry_carries_an_age_under_the_documented_one_argument_call(relay):
    """ACC-DATA-005's evidence line, as amended at the S1 gate round 3.

    `build(relay_dir)` is the signature the module documents and the one a view
    makes. Under it every entry must carry a non-None `at` and a non-None
    relative `age` — not only when a clock is injected. The check passed for
    two rounds with `age` None on every entry of every relay, because its
    evidence asked only for a count and an order.

    The measurement is real, not a constant: an entry's age is the distance
    from its own timestamp to a clock this test can bracket.
    """
    before = time.time()
    model = relay_model.build(relay("agent-service"))
    after = time.time()

    assert model["log"]
    for e in model["log"]:
        assert isinstance(e["t"], float), e
        assert e["age"] is not None, e
        assert isinstance(e["age"], float), e
        assert (before - e["t"]) <= e["age"] <= (after - e["t"]), e


def test_the_ages_of_two_entries_differ_by_the_distance_between_them(relay):
    """And the age is derived from each entry's own time rather than stamped
    once: two entries an hour apart are an hour apart in age too."""
    model = relay_model.build(relay("agent-service"))
    ordered = sorted(model["log"], key=lambda e: e["t"])
    oldest, newest = ordered[0], ordered[-1]
    assert newest["t"] > oldest["t"]
    assert (oldest["age"] - newest["age"]) == pytest.approx(
        newest["t"] - oldest["t"], abs=0.5)


def test_age_is_none_when_the_clock_is_explicitly_refused(relay):
    """The determinism the default used to provide, kept and made explicit.

    `now=None` is a caller saying "no clock": every now-derived field stays
    None and the model is byte-identical across builds, which is what a frame
    capture needs. It used to be the DEFAULT, and that is what left ACC-DATA-005
    unsatisfiable under its own documented call — the two intents are
    reconciled by giving each its own spelling rather than by dropping one.
    """
    model = relay_model.build(relay("agent-service"), now=None)
    assert model["log"]
    assert all(e["age"] is None for e in model["log"])
    assert all(isinstance(e["t"], float) for e in model["log"])


def test_every_baton_becomes_a_landing_entry(relay):
    """Including `s2-test-quality`, whose leg entry legs.json forgot: a runner
    really ran it, and the log is a record of what happened, not of the plan."""
    target = relay("agent-service")
    model = relay_model.build(target, now=NOW)
    landed = {e["leg"] for e in entries_of(model, "baton")}
    on_disk = {p.stem for p in (target / "batons").glob("*.md")}
    assert landed == on_disk
    assert "s2-test-quality" in landed


def test_landing_entries_are_timed_by_their_baton_mtime(relay):
    model = relay_model.build(relay("agent-service"), now=NOW)
    timed = {e["leg"]: e["t"] for e in entries_of(model, "baton")}
    for stem, mtime in AGENT_SERVICE_BATON_MTIMES.items():
        assert timed[stem] == mtime
    assert all(e["exact"] for e in entries_of(model, "baton"))


def test_landing_entries_carry_the_commit_their_baton_names(relay):
    model = relay_model.build(relay("agent-service"), now=NOW)
    named = [e for e in entries_of(model, "baton") if e["commit"]]
    assert named, "the agent-service batons name commits"
    assert all(len(e["commit"]) <= 7 for e in named)


def test_the_running_leg_gets_a_start_entry(relay):
    """A running leg has no baton, so without this the log would fall silent at
    the moment the supervisor most wants to read it."""
    target = relay("agent-service")
    model = relay_model.build(target, now=NOW)
    starts = entries_of(model, "start")
    assert [e["leg"] for e in starts] == [model["activeLeg"]["id"]]
    # The handoff is inferred from the previous baton, not recorded for this leg.
    assert starts[0]["exact"] is False
    assert starts[0]["t"] == max(AGENT_SERVICE_BATON_MTIMES.values())


def test_check_transitions_are_logged_against_the_leg_that_claimed_them(relay):
    """state.json has no timestamps, so a re-judged check is pinned to the leg
    that claimed it and is honest about not being dated on its own."""
    model = relay_model.build(relay("agent-service"), now=NOW)
    checks = entries_of(model, "check")
    assert [e["check"] for e in checks] == ["ACC-CRED-004"]
    entry = checks[0]
    assert entry["exact"] is False
    assert entry["leg"] == "credential-parity"
    assert entry["t"] == AGENT_SERVICE_BATON_MTIMES["credential-parity"]
    assert "2" in entry["m"]


def test_a_repaired_check_is_logged_with_its_fix_leg(relay):
    model = relay_model.build(relay("stale-currentleg"), now=NOW)
    repaired = [e for e in entries_of(model, "check") if e["check"] == "ACC-X-002"]
    assert repaired, model["log"]
    assert any("fix-pipeline" in e["m"] for e in repaired)
    assert all(e["level"] == "bad" for e in repaired)


def test_a_check_with_no_datable_leg_is_left_out(relay):
    """ACC-Y-001 is claimed by `cutover-flip`, which has no baton. There is no
    honest time to give it, so it gets no entry rather than a guessed one."""
    model = relay_model.build(relay("stale-currentleg"), now=NOW)
    assert all(e["check"] != "ACC-Y-001" for e in model["log"])


# --------------------------------------------------------------------------
# ACC-DATA-006 — an explicit log overrides the derived one
# --------------------------------------------------------------------------

def test_explicit_log_is_returned_verbatim(relay):
    target = relay("tokens")
    model = relay_model.build(target, now=NOW)
    written = json.loads((target / "dashboard.json").read_text())["log"]
    assert len(written) == 3
    assert model["log"] == written
    assert model["logSource"] == "dashboard"


def test_explicit_log_wins_even_though_a_baton_could_be_derived(relay):
    """The tokens fixture has a baton on disk. The coach's log still wins."""
    target = relay("tokens")
    assert list((target / "batons").glob("*.md"))
    model = relay_model.build(target, now=NOW)
    assert [e["m"] for e in model["log"]] == [
        "Stage judging: ACC-M-002 still failing",
        "measured-leg landed",
        "plan approved",
    ]


def test_an_empty_explicit_log_falls_back_to_the_derived_one(relay, tmp_path):
    target = relay("agent-service")
    (target / "dashboard.json").write_text(json.dumps({"log": []}))
    model = relay_model.build(target, now=NOW)
    assert model["logSource"] == "derived"
    assert len(model["log"]) >= 10
    # An EMPTY array is deliberately not "the coach wrote a log": a coach who
    # writes `[]` has narrated nothing, and the derived log is better than an
    # empty pane. That is a reading of the contract's wording, not a bug, and
    # it is pinned here so the warning added below does not swallow it.
    assert not any("`log`" in w for w in model["warnings"]), model["warnings"]


# --------------------------------------------------------------------------
# ACC-DATA-006 — the edges of "verbatim"
#
# `log` was the one dashboard field that degraded SILENTLY. A `log` that is not
# an array at all was ignored with no warning, unlike `tokens`, `title` and
# `path`, which all name what they held; and a non-dict entry inside the array
# was coerced into an entry shape with no word said, which is not "verbatim"
# either. Whichever way each is decided, a supervisor is owed the same account
# of it as every other malformed field in the file gets.
# --------------------------------------------------------------------------

#: Every shape a coach can write where `log` should be an array.
NOT_AN_ARRAY = [
    ({"22m ago": "landed"}, "dict"),
    ("plan approved", "str"),
    (7, "int"),
    (True, "bool"),
]


@pytest.mark.parametrize("value,typename", NOT_AN_ARRAY,
                         ids=[t for _, t in NOT_AN_ARRAY])
def test_a_log_that_is_not_an_array_is_named_not_silently_ignored(
        relay, value, typename):
    target = relay("agent-service")
    (target / "dashboard.json").write_text(json.dumps({"log": value}))

    model = relay_model.build(target, now=NOW)

    assert model["logSource"] == "derived"
    assert len(model["log"]) >= 10
    named = [w for w in model["warnings"] if "`log`" in w]
    assert len(named) == 1, model["warnings"]
    assert typename in named[0], named[0]


def test_a_log_entry_that_is_not_an_object_is_named_and_still_readable(relay):
    """The other half. A view reads `t` and `m` off every entry, so a bare
    string cannot be passed through as it stands — it is quoted into the entry
    shape with no time of its own, and the supervisor is told that happened
    rather than left to wonder why an entry has no timestamp."""
    target = relay("agent-service")
    (target / "dashboard.json").write_text(json.dumps({"log": [
        {"t": "1h ago", "m": "a proper entry", "cls": "note"},
        "a bare string the coach wrote",
        ["not an entry either"],
    ]}))

    model = relay_model.build(target, now=NOW)

    assert model["logSource"] == "dashboard"
    assert len(model["log"]) == 3
    # The dict entry is untouched: verbatim means verbatim where it can be.
    assert model["log"][0] == {"t": "1h ago", "m": "a proper entry", "cls": "note"}
    assert model["log"][1] == {"t": None, "m": "a bare string the coach wrote",
                               "cls": None}
    # A non-string entry is RENDERED, never replaced by a placeholder: what the
    # coach wrote is still legible on the pane (ACC-DATA-007).
    assert model["log"][2]["t"] is None
    assert model["log"][2]["m"] == '["not an entry either"]', model["log"][2]
    assert "not an entry either" in model["log"][2]["m"]
    assert "—" not in model["log"][2]["m"]
    named = [w for w in model["warnings"] if "log entry" in w]
    assert len(named) == 2, model["warnings"]
    assert "#1" in named[0] and "str" in named[0], named[0]
    assert "#2" in named[1] and "list" in named[1], named[1]


def test_a_coach_written_entry_is_never_dressed_with_a_placeholder(relay):
    """ACC-DATA-007's rule, where an explicit log meets it: the model quotes
    what the coach wrote and does not fill an absent `t` with anything."""
    target = relay("agent-service")
    (target / "dashboard.json").write_text(json.dumps(
        {"log": ["only prose"]}))

    entry = relay_model.build(target, now=NOW)["log"][0]
    assert entry["t"] is None
    assert entry["m"] == "only prose"
    assert "—" not in json.dumps(entry)


# --------------------------------------------------------------------------
# git — a source that is often not there
# --------------------------------------------------------------------------

def test_no_git_repository_still_yields_a_log(relay):
    """The fixture copy in tmp_path sits outside any repository."""
    target = relay("agent-service")
    model = relay_model.build(target, now=NOW)
    assert entries_of(model, "commit") == []
    assert len(model["log"]) >= 10


# --------------------------------------------------------------------------
# the commit source is bounded at the relay's own project
#
# These build the fixture WHERE IT LIVES, not copied into tmp_path. Every other
# test here copies first, in order to stamp baton mtimes, and that copy lands
# outside any repository - which is exactly why no test could see the log
# walking up out of the fixture and reporting THIS repository's commits as the
# fixture relay's own.
#
# WHAT BITES AND WHAT DOES NOT. The three log assertions below - no commit
# entries, no host subject, ten entries of the relay's own kinds - are true
# statements, but they are NOT the guard on the repository bound. They pass on
# a fresh checkout whether or not the bound exists: git does not preserve
# mtimes, so a clone stamps every baton to checkout time, the window opens
# later than every host commit, and the host's history is excluded by the CLOCK
# rather than by the bound. A judge deleted the bound in a `cp -R` clone and
# the suite stayed green at 568.
#
# The two tests immediately below carry the guard instead, and neither reads a
# clock: the walk stops at the relay's own project, and a relay that is not in
# a repository of its own is never asked a git question at all. Add assertions
# here in that shape, not in the shape of the three that follow them.
# --------------------------------------------------------------------------

FIXTURES = REPO / "tests" / "fixtures"


def test_a_relay_inside_a_repository_it_does_not_own_finds_no_repository(tmp_path):
    """The bound itself, with no clock anywhere near it. `tests/fixtures/*` is
    this shape: a relay directory that is its own project, sitting inside a
    repository that belongs to somebody else. The parent walk must stop at the
    project and report no repository, however many `.git` directories sit above
    it."""
    host = tmp_path / "host"
    (host / ".git").mkdir(parents=True)
    relay_dir = host / "nested" / "standalone"
    relay_dir.mkdir(parents=True)

    assert relay_model._in_a_repo(relay_dir, relay_dir) is False
    # One directory of slack - the live `<project>/.relay` shape - and still
    # not the host's `.git` two levels up.
    assert relay_model._in_a_repo(relay_dir, relay_dir.parent) is False
    # The other side of the bound: a relay whose project really does hold the
    # repository still finds it, or bounding the walk would cost every live
    # relay its own history.
    live = host / ".relay"
    live.mkdir()
    assert relay_model._in_a_repo(live, host) is True


def test_the_fixture_in_place_is_asked_no_git_question_at_all():
    """`_in_a_repo` is checked BEFORE git is spawned, so a relay that is not in
    a repository of its own runs no git process. That is observable without a
    clock, which is what makes it the guard: it holds on a fresh checkout,
    where every baton mtime is checkout time and the window would have hidden a
    broken bound behind it."""
    asked = []
    real = relay_model._git

    def spy(relay_dir, *args, **kwargs):
        asked.append((str(relay_dir), args))
        return real(relay_dir, *args, **kwargs)

    relay_model._git = spy
    try:
        model = relay_model.build(FIXTURES / "agent-service", now=NOW)
    finally:
        relay_model._git = real
    assert asked == [], asked
    assert entries_of(model, "commit") == []


def _host_repo_subjects():
    """Commit subjects of the repository the fixtures happen to live in."""
    if not HAS_GIT or not (REPO / ".git").exists():
        return []
    out = subprocess.run(
        ["git", "-C", str(REPO), "log", "--no-color", "--format=%s"],
        capture_output=True, text=True)
    return [s for s in out.stdout.splitlines() if s.strip()]


def test_the_fixture_in_place_derives_no_commits_from_the_host_repo():
    """`tests/fixtures/agent-service` has no `.git` of its own, so the log has
    no commits to report - even though the fixture sits inside a repository
    that has plenty. The search for a repo stops at the relay's own project."""
    model = relay_model.build(FIXTURES / "agent-service", now=NOW)
    assert entries_of(model, "commit") == []


def test_no_host_repo_commit_subject_reaches_the_fixture_log():
    """Named separately from the count above: a borrowed commit is a lie about
    what happened in the fixture's relay, whatever `kind` it arrives under."""
    model = relay_model.build(FIXTURES / "agent-service", now=NOW)
    messages = " | ".join(e["m"] for e in model["log"])
    for subject in _host_repo_subjects():
        assert subject not in messages, subject


def test_the_fixture_in_place_still_clears_the_ten_entry_bar():
    """ACC-DATA-005's evidence line, asserted where the fixture actually lives.
    The batons and check transitions carry it on their own; the commits it used
    to borrow were never its own."""
    model = relay_model.build(FIXTURES / "agent-service", now=NOW)
    assert model["logSource"] == "derived"
    assert len(model["log"]) >= 10
    assert {e["kind"] for e in model["log"]} <= {"baton", "check", "start"}


@pytest.fixture
def repo_relay(tmp_path):
    """A relay dir inside a real repository with two dated commits."""
    project = tmp_path / "project"
    (project / ".relay" / "batons").mkdir(parents=True)
    relay_dir = project / ".relay"
    (relay_dir / "legs.json").write_text(json.dumps({
        "relay": "gitty",
        "stages": [{"id": "S1", "name": "One", "legs": ["alpha", "beta"]}],
        "legs": [{"id": "alpha", "stage": "S1", "status": "done"},
                 {"id": "beta", "stage": "S1", "status": "running"}],
    }))
    (relay_dir / "state.json").write_text(json.dumps({"checks": {}}))
    baton = relay_dir / "batons" / "alpha.md"
    baton.write_text("# Baton - alpha\nSTATUS: success\n**Commit:** abc1234\n")
    os.utime(baton, (NOW - 7200, NOW - 7200))

    _git(project, "init", "-q")
    _git(project, "add", "-A", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "seed the relay", when=NOW - 9000)
    _git(project, "commit", "-q", "--allow-empty", "-m", "alpha: land the thing",
         when=NOW - 7200)
    return relay_dir


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_commits_on_the_branch_become_log_entries(repo_relay):
    """`seed the relay` is dated NOW - 9000, before alpha's baton landed at
    NOW - 7200, so ACC-DATA-009 puts it outside the relay's window: it is the
    commit that created the relay, not work the relay did. The commit inside
    the window is still an entry, still exact, still carrying its sha."""
    model = relay_model.build(repo_relay, now=NOW)
    commits = entries_of(model, "commit")
    subjects = [e["m"] for e in commits]
    assert any("alpha: land the thing" in s for s in subjects)
    assert not any("seed the relay" in s for s in subjects)
    assert all(e["exact"] for e in commits)
    assert all(isinstance(e["commit"], str) and e["commit"] for e in commits)
    assert [e["t"] for e in commits] == [NOW - 7200]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_project_that_is_itself_a_repo_still_gets_its_commits(repo_relay):
    """The other side of the bound. A live relay is `<project>/.relay` and the
    `.git` sits at `<project>` - which is exactly `relay.path`, the last
    directory the search is allowed to look in. Bounding the walk must not cost
    a real relay its own history."""
    model = relay_model.build(repo_relay, now=NOW)
    project = repo_relay.parent
    assert model["relay"]["path"] == str(project.resolve())
    assert (project / ".git").is_dir()
    assert not (project.parent / ".git").exists()  # nothing above to borrow
    # Its own history, inside its own window: the seed commit predates the
    # relay's first landing and is bounded out by ACC-DATA-009, not by the
    # repository bound this test is about.
    commits = entries_of(model, "commit")
    assert [e["m"] for e in commits] == [f"commit {commits[0]['commit']}: "
                                         "alpha: land the thing"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_git_and_batons_are_merged_into_one_descending_order(repo_relay):
    model = relay_model.build(repo_relay, now=NOW)
    times = [e["t"] for e in model["log"]]
    assert times == sorted(times, reverse=True)
    assert {e["kind"] for e in model["log"]} >= {"commit", "baton"}


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_log_is_the_same_on_two_builds_of_a_repository(repo_relay):
    assert (relay_model.build(repo_relay, now=NOW)["log"]
            == relay_model.build(repo_relay, now=NOW)["log"])


def test_a_relay_dir_that_is_not_a_repo_never_raises(tmp_path):
    """`git -C` on a plain directory exits non-zero; the model must degrade."""
    (tmp_path / "legs.json").write_text(json.dumps({"legs": []}))
    model = relay_model.build(tmp_path, now=NOW)
    assert model["log"] == []
    assert model["logSource"] is None


# --------------------------------------------------------------------------
# absence and determinism
# --------------------------------------------------------------------------

def test_a_relay_with_no_batons_derives_no_landings(relay):
    """all-done has three legs and no batons: nothing on disk says when they
    landed, so the model says nothing rather than inventing a time."""
    target = relay("all-done")
    assert not (target / "batons").exists()
    model = relay_model.build(target, now=NOW)
    assert model["log"] == []
    assert model["logSource"] is None


def test_an_empty_relay_has_no_log(relay):
    model = relay_model.build(relay("empty"), now=NOW)
    assert model["log"] == []
    assert model["logSource"] is None


def test_a_malformed_relay_has_no_log(relay):
    model = relay_model.build(relay("malformed"), now=NOW)
    assert isinstance(model["log"], list)
    assert model["logSource"] in (None, "derived")


def test_the_same_directory_and_the_same_now_yield_the_same_log(relay):
    target = relay("agent-service")
    first = relay_model.build(target, now=NOW)["log"]
    second = relay_model.build(target, now=NOW)["log"]
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_log_is_data_not_display(relay):
    """Epoch seconds, never a formatted string; no placeholders, no em-dash."""
    model = relay_model.build(relay("agent-service"), now=NOW)
    for e in model["log"]:
        assert not isinstance(e["t"], str)
        assert not isinstance(e["age"], str)
        assert "—" not in e["m"]
        assert " ago" not in e["m"]


def test_the_log_is_json_serialisable(relay):
    model = relay_model.build(relay("agent-service"), now=NOW)
    assert json.loads(json.dumps(model["log"])) == model["log"]


# --------------------------------------------------------------------------
# bounds — build() is called once per repaint
# --------------------------------------------------------------------------

def test_a_relay_with_hundreds_of_batons_is_bounded(tmp_path):
    relay_dir = tmp_path / "big"
    (relay_dir / "batons").mkdir(parents=True)
    legs = []
    for i in range(500):
        lid = f"leg-{i:03d}"
        legs.append({"id": lid, "stage": "S1", "status": "done"})
        path = relay_dir / "batons" / f"{lid}.md"
        path.write_text("STATUS: success\n")
        os.utime(path, (NOW - 500 + i, NOW - 500 + i))
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "big", "stages": [{"id": "S1", "legs": [l["id"] for l in legs]}],
         "legs": legs}))

    started = time.perf_counter()
    model = relay_model.build(relay_dir, now=NOW)
    elapsed = time.perf_counter() - started

    assert len(model["log"]) == relay_model.LOG_MAX_ENTRIES
    assert model["log"][0]["t"] > model["log"][-1]["t"]
    # The newest events survive the bound; the oldest are the ones dropped.
    assert model["log"][0]["leg"] == "leg-499"
    assert elapsed < 2.0, elapsed


# --------------------------------------------------------------------------
# the commit source is bounded to the relay's own time window (ACC-DATA-009)
#
# `fix-log-repo-scope` bounded WHERE commits come from. These bound WHEN. A
# project's history from before the relay started is not part of this run, and
# a busy project commits faster than a relay lands legs, so a second bound
# keeps commits from outnumbering - and burying - the relay's own events.
# --------------------------------------------------------------------------

def relay_events(model):
    """The relay's own events: everything the relay's records produced."""
    return [e for e in model["log"] if e["kind"] != "commit"]


@pytest.fixture
def windowed_relay(tmp_path):
    """A relay in a repository whose history starts long before the relay does.

    Three commits land before the first baton and two after it, so the window
    has a wrong answer available in both directions. Three relay events (two
    landings and the handoff into the running leg), so the count bound is not
    what excludes the pre-relay commits here - the window is.
    """
    project = tmp_path / "project"
    (project / ".relay" / "batons").mkdir(parents=True)
    relay_dir = project / ".relay"
    (relay_dir / "legs.json").write_text(json.dumps({
        "relay": "windowed",
        "stages": [{"id": "S1", "name": "One", "legs": ["alpha", "beta", "gamma"]}],
        "legs": [{"id": "alpha", "stage": "S1", "status": "done"},
                 {"id": "beta", "stage": "S1", "status": "done"},
                 {"id": "gamma", "stage": "S1", "status": "running"}],
    }))
    (relay_dir / "state.json").write_text(json.dumps({"checks": {}}))

    _git(project, "init", "-q")
    _git(project, "add", "-A", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "before: the project existed first",
         when=NOW - 9000)
    for n, when in enumerate((NOW - 7000, NOW - 6000), start=2):
        _git(project, "commit", "-q", "--allow-empty",
             "-m", f"before: unrelated history {n}", when=when)

    # The relay's first landing. Everything above it predates the run.
    for leg, when in (("alpha", NOW - 5000), ("beta", NOW - 3000)):
        baton = relay_dir / "batons" / f"{leg}.md"
        baton.write_text(f"# Baton - {leg}\nSTATUS: success\n")
        os.utime(baton, (when, when))

    for n, when in enumerate((NOW - 4500, NOW - 2000), start=1):
        _git(project, "commit", "-q", "--allow-empty",
             "-m", f"after: the relay did this {n}", when=when)
    return relay_dir


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_commits_from_before_the_relay_began_are_not_in_the_log(windowed_relay):
    """ACC-DATA-009: the project's history from before the relay started is not
    part of this run."""
    model = relay_model.build(windowed_relay, now=NOW)
    subjects = [e["m"] for e in entries_of(model, "commit")]
    assert subjects, "the in-window commits must still be derived"
    assert not [s for s in subjects if "before:" in s], subjects


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_commits_from_inside_the_window_are_kept(windowed_relay):
    model = relay_model.build(windowed_relay, now=NOW)
    subjects = [e["m"] for e in entries_of(model, "commit")]
    assert any("after: the relay did this 1" in s for s in subjects), subjects
    assert any("after: the relay did this 2" in s for s in subjects), subjects


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_window_opens_at_the_earliest_event_the_relay_recorded(windowed_relay):
    """The bound is the relay's own earliest recorded event, not a count."""
    model = relay_model.build(windowed_relay, now=NOW)
    earliest = min(e["t"] for e in relay_events(model))
    assert earliest == NOW - 5000  # alpha's baton, the first landing
    assert all(e["t"] >= earliest for e in entries_of(model, "commit"))


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_commits_never_outnumber_the_relay_s_own_events(windowed_relay):
    """ACC-DATA-009's second requirement, on a relay whose repo is busy."""
    model = relay_model.build(windowed_relay, now=NOW)
    assert len(entries_of(model, "commit")) <= len(relay_events(model))


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_burst_of_commits_inside_the_window_cannot_bury_the_run(tmp_path):
    """The window alone is not enough: a project can commit forty times between
    two landings. The newest commits survive; no relay event is dropped."""
    project = tmp_path / "busy"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps({
        "relay": "busy",
        "stages": [{"id": "S1", "legs": ["alpha"]}],
        "legs": [{"id": "alpha", "stage": "S1", "status": "done"}],
    }))
    baton = relay_dir / "batons" / "alpha.md"
    baton.write_text("# Baton - alpha\nSTATUS: success\n")
    os.utime(baton, (NOW - 4000, NOW - 4000))

    _git(project, "init", "-q")
    _git(project, "add", "-A", when=NOW - 4000)
    _git(project, "commit", "-q", "-m", "in window 00", when=NOW - 4000)
    for n in range(1, 40):
        _git(project, "commit", "-q", "--allow-empty",
             "-m", f"in window {n:02d}", when=NOW - 4000 + n)

    model = relay_model.build(relay_dir, now=NOW)
    events = relay_events(model)
    commits = entries_of(model, "commit")
    assert len(events) == 1, [e["m"] for e in events]  # alpha landed
    assert len(commits) <= len(events)
    # A cap on commits, never on the relay's own events.
    assert any(e["kind"] == "baton" and e["leg"] == "alpha" for e in model["log"])
    # The newest commits survive, not the oldest.
    assert commits[0]["m"].endswith("in window 39"), commits[0]["m"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_relay_with_no_events_falls_back_to_recent_commits(tmp_path):
    """A brand-new relay has no window: no baton, no handoff, no check
    transition, nothing to bound by. Showing the repository's recent commits is
    better than showing nothing at all."""
    project = tmp_path / "fresh"
    relay_dir = project / ".relay"
    relay_dir.mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps({
        "relay": "fresh",
        "stages": [{"id": "S1", "legs": ["alpha"]}],
        "legs": [{"id": "alpha", "stage": "S1", "status": "pending"}],
    }))
    _git(project, "init", "-q")
    _git(project, "add", "-A", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "the project before any relay",
         when=NOW - 9000)

    model = relay_model.build(relay_dir, now=NOW)
    commits = entries_of(model, "commit")
    assert relay_events(model) == []
    assert len(commits) == 1
    assert "the project before any relay" in commits[0]["m"]
    assert model["logSource"] == "derived"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_window_is_deterministic(windowed_relay):
    assert (relay_model.build(windowed_relay, now=NOW)["log"]
            == relay_model.build(windowed_relay, now=NOW)["log"])


# --------------------------------------------------------------------------
# attribution, not counting, is the property (ACC-DATA-009)
#
# The window says which commits could be the run's; these say which of them
# survive a budget. A relay's own commits are the OLDEST inside its own window,
# because it lands legs more slowly than its project commits - so a budget
# spent newest-first removes exactly them. Every commit a baton attributes to a
# leg is kept first, and only the remainder goes to the newest unattributed
# ones.
#
# The window itself opens at the branch point where the relay runs on a branch
# of its own: a runner commits BEFORE it writes its baton, so the first leg's
# commit is older than the earliest event the relay ever recorded, and a window
# opening there loses it by construction.
# --------------------------------------------------------------------------

def commit_named(model, needle):
    """The one commit entry whose subject contains `needle`, or None."""
    found = [e for e in entries_of(model, "commit") if needle in e["m"]]
    assert len(found) <= 1, [e["m"] for e in found]
    return found[0] if found else None


def _short_sha(cwd, rev="HEAD"):
    return _git(cwd, "rev-parse", "--short", rev).stdout.strip()


def _land(relay_dir, leg, when, sha=None):
    """Write a baton for `leg`, landed at `when`, naming `sha` if it has one."""
    baton = relay_dir / "batons" / f"{leg}.md"
    baton.write_text(f"# Baton - {leg}\nSTATUS: success\n"
                     + (f"**Commit:** {sha}\n" if sha else ""))
    os.utime(baton, (when, when))


@pytest.fixture
def branched_relay(tmp_path):
    """A relay running on a branch of its own, the shape a live relay has.

    The project has history on `main`; the run branches off it and lands two
    legs. Each leg's commit is 60 seconds OLDER than its own baton, because a
    runner commits and then writes its baton - and thirty unrelated commits
    land after them, so newest-first and attribution-first disagree about every
    commit in the window.
    """
    project = tmp_path / "branched"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps({
        "relay": "branched",
        "stages": [{"id": "S1", "legs": ["alpha", "beta", "gamma"]}],
        "legs": [{"id": "alpha", "stage": "S1", "status": "done"},
                 {"id": "beta", "stage": "S1", "status": "done"},
                 {"id": "gamma", "stage": "S1", "status": "running"}],
    }))
    (relay_dir / "state.json").write_text(json.dumps({"checks": {}}))

    _git(project, "init", "-q", "-b", "main")
    _git(project, "add", "-A", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "before: the project existed first",
         when=NOW - 9000)
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "before: unrelated history", when=NOW - 8000)

    _git(project, "checkout", "-q", "-b", "feat/the-run")
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "alpha: the first leg's own work", when=NOW - 5060)
    _land(relay_dir, "alpha", NOW - 5000, _short_sha(project))
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "beta: the second leg's own work", when=NOW - 4060)
    _land(relay_dir, "beta", NOW - 4000, _short_sha(project))

    for n in range(30):
        _git(project, "commit", "-q", "--allow-empty",
             "-m", f"noise {n:02d}: the project carried on", when=NOW - 3000 + n)
    return relay_dir


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_leg_commits_before_it_writes_its_baton(branched_relay):
    """The premise of everything below, asserted so the fixture cannot drift
    out from under it: the first leg's commit is older than its own baton."""
    model = relay_model.build(branched_relay, now=NOW)
    landing = [e for e in model["log"] if e["kind"] == "baton" and e["leg"] == "alpha"]
    assert landing, "alpha landed"
    when = float(_git(branched_relay, "log", "-1", "--format=%ct",
                      "--grep=alpha: the first").stdout.strip())
    assert when < landing[0]["t"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_first_leg_s_commit_is_in_the_log_though_it_predates_its_baton(
        branched_relay):
    """ACC-DATA-009: every commit attributable to a leg appears, including the
    first leg's, which predates its own baton by construction."""
    model = relay_model.build(branched_relay, now=NOW)
    entry = commit_named(model, "alpha: the first leg's own work")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "alpha"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_every_leg_attributed_commit_is_kept(branched_relay):
    """Both legs' commits survive a budget that a newest-first rule would have
    spent entirely on the thirty commits that landed after them."""
    model = relay_model.build(branched_relay, now=NOW)
    attributed = {e["leg"] for e in entries_of(model, "commit") if e["leg"]}
    assert attributed == {"alpha", "beta"}
    assert commit_named(model, "beta: the second leg's own work") is not None


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_remaining_budget_goes_to_the_newest_unattributed_commits(
        branched_relay):
    """What is left after attribution is spent newest-first, and no relay event
    is dropped to pay for any of it."""
    model = relay_model.build(branched_relay, now=NOW)
    commits = entries_of(model, "commit")
    events = relay_events(model)
    assert len(commits) <= len(events)
    unattributed = [e["m"] for e in commits if not e["leg"]]
    # Two of the three events' worth of budget went to the two legs' own
    # commits; what is left buys one commit, and it is the newest in the
    # window, not the newest three.
    assert len(unattributed) == 1, unattributed
    assert "noise 29" in unattributed[0]
    assert not [m for m in unattributed if "noise 00" in m]
    assert {e["leg"] for e in events if e["kind"] == "baton"} == {"alpha", "beta"}


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_window_opens_at_the_branch_point_not_at_the_first_event(
        branched_relay):
    """The window reaches back past the relay's earliest recorded event - it
    has to, or the first leg's commit is lost - and stops at the branch point.
    The project's history from before the branch is still not part of the run.
    """
    model = relay_model.build(branched_relay, now=NOW)
    commits = entries_of(model, "commit")
    earliest_event = min(e["t"] for e in relay_events(model))
    assert min(e["t"] for e in commits) < earliest_event
    assert not [e["m"] for e in commits if "before:" in e["m"]]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_branch_point_window_is_deterministic(branched_relay):
    assert (relay_model.build(branched_relay, now=NOW)["log"]
            == relay_model.build(branched_relay, now=NOW)["log"])


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_repository_with_no_default_branch_falls_back_to_the_event_window(
        tmp_path):
    """The branch point needs a default branch to be a branch point FROM. A
    repository that has none - no `main`, no `master`, no `origin/*` - has no
    branch to bound by, and the relay's earliest event is the window again."""
    project = tmp_path / "trunk"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps({
        "relay": "trunk",
        "stages": [{"id": "S1", "legs": ["alpha"]}],
        "legs": [{"id": "alpha", "stage": "S1", "status": "done"}],
    }))
    _git(project, "init", "-q", "-b", "trunk")
    _git(project, "add", "-A", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "before: the project existed first",
         when=NOW - 9000)
    _git(project, "commit", "-q", "--allow-empty", "-m", "after: in the window",
         when=NOW - 3000)
    _land(relay_dir, "alpha", NOW - 4000)

    model = relay_model.build(relay_dir, now=NOW)
    assert _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "trunk"
    assert commit_named(model, "after: in the window") is not None
    assert commit_named(model, "before: the project existed first") is None


# --------------------------------------------------------------------------
# the environment cannot redirect the read (ACC-DATA-009)
# --------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_an_ambient_git_dir_cannot_make_a_foreign_repo_the_relay_s(
        repo_relay, tmp_path, monkeypatch):
    """`_in_a_repo` bounds WHERE commits are read from on the filesystem, and
    `GIT_DIR` walks straight back past it: git obeys the environment over `-C`,
    so a dashboard opened from a shell that exports one would report a foreign
    repository's commits as this relay's."""
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _git(foreign, "init", "-q", "-b", "main")
    _git(foreign, "commit", "-q", "--allow-empty",
         "-m", "foreign: not this relay's work", when=NOW - 1000)

    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    model = relay_model.build(repo_relay, now=NOW)

    messages = " | ".join(e["m"] for e in model["log"])
    assert "foreign:" not in messages, messages
    assert "alpha: land the thing" in messages, messages


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_an_ambient_git_index_file_does_not_reach_the_read(repo_relay,
                                                           monkeypatch):
    """The same class, one variable along: a stale `GIT_INDEX_FILE` pointing at
    a path git cannot use fails the whole invocation, and the relay's own
    commits vanish from the log for a reason that has nothing to do with it."""
    monkeypatch.setenv("GIT_INDEX_FILE", "/nonexistent/index")
    monkeypatch.setenv("GIT_COMMON_DIR", "/nonexistent/common")
    model = relay_model.build(repo_relay, now=NOW)
    assert "alpha: land the thing" in " | ".join(e["m"] for e in model["log"])


# --------------------------------------------------------------------------
# the outer entry bound must not re-invert the commit bound (ACC-DATA-009)
# --------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_entry_bound_cannot_re_invert_the_commit_bound(tmp_path):
    """`LOG_MAX_ENTRIES` is the loosest bound in the module and must not undo
    the tightest one. With more relay events than half the entry bound, a
    budget of "as many commits as events" merges to more entries than the log
    keeps, and truncating the merge newest-first hands the log back to the
    commits: 250 events and 210 commits used to yield 200 commits above 100
    events. Latent today - it needs more than 150 relay events - and the same
    inversion wearing a different hat.
    """
    project = tmp_path / "huge"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    legs = [{"id": f"leg-{i:03d}", "stage": "S1", "status": "done"}
            for i in range(250)]
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "huge", "stages": [{"id": "S1", "legs": [l["id"] for l in legs]}],
         "legs": legs}))

    _git(project, "init", "-q", "-b", "main")
    _git(project, "add", "-A", when=NOW - 5000)
    _git(project, "commit", "-q", "-m", "noise 000", when=NOW - 5000)
    # One `git` process per commit is the cost here, not Python: 210 of them in
    # a single shell keeps the test near three seconds instead of ten.
    script = ('set -e; i=1; while [ $i -le 209 ]; do '
              'GIT_AUTHOR_DATE="$((BASE+i)) +0000" '
              'GIT_COMMITTER_DATE="$((BASE+i)) +0000" '
              'git commit -q --allow-empty -m "noise $i"; i=$((i+1)); done')
    env = dict(os.environ)
    env.update({"BASE": str(int(NOW - 5000)),
                "GIT_AUTHOR_NAME": "Relay Test", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "Relay Test",
                "GIT_COMMITTER_EMAIL": "t@example.com"})
    out = subprocess.run(["sh", "-c", script], cwd=project, env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr

    # The oldest commit git will still hand back under `--max-count`, so a leg
    # attributed to it is the one a newest-first budget drops first.
    oldest = _short_sha(project, f"HEAD~{relay_model.LOG_MAX_COMMITS - 1}")
    for i, leg in enumerate(legs):
        _land(relay_dir, leg["id"], NOW - 6000 + i,
              oldest if leg["id"] == "leg-000" else None)

    model = relay_model.build(relay_dir, now=NOW)
    commits = entries_of(model, "commit")
    events = relay_events(model)
    assert len(model["log"]) <= relay_model.LOG_MAX_ENTRIES
    assert len(commits) <= len(events), (len(commits), len(events))
    assert [e for e in commits if e["leg"] == "leg-000"], \
        "the attributed commit is kept before any unattributed one"


# --------------------------------------------------------------------------
# ACC-DATA-005's git clause, on the fixture its evidence names
#
# `tests/fixtures/agent-service` has no `.git` of its own - by design, since
# `.gitignore` would swallow it - so in place it yields no commit entries and
# the evidence line's `git log` clause is never demonstrated on the fixture it
# names. Giving the copy a repository of its own demonstrates it there.
# --------------------------------------------------------------------------

@pytest.fixture
def agent_service_repo(relay):
    """The agent-service fixture, copied out with its baton mtimes stamped and
    made a repository in its own right."""
    target = relay("agent-service")
    landed = max(AGENT_SERVICE_BATON_MTIMES.values())
    _git(target, "init", "-q", "-b", "main")
    _git(target, "add", "-A", when=landed + 10)
    _git(target, "commit", "-q", "-m", "fix(credentials): land the guard",
         when=landed + 10)
    _git(target, "commit", "-q", "--allow-empty",
         "-m", "test(pg): cover the repository seam", when=landed + 20)
    return target


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_agent_service_log_is_derived_from_git_as_well(agent_service_repo):
    """ACC-DATA-005's evidence line names three sources against agent-service.
    Batons and check transitions were always demonstrated on it; this is the
    `git log` clause, on the fixture the line names."""
    model = relay_model.build(agent_service_repo, now=NOW)
    assert model["logSource"] == "derived"
    assert len(model["log"]) >= 10
    commits = entries_of(model, "commit")
    assert {e["kind"] for e in model["log"]} >= {"baton", "commit", "check"}
    assert commit_named(model, "fix(credentials): land the guard") is not None
    for e in commits:
        assert isinstance(e["t"], float) and isinstance(e["age"], float)
        assert isinstance(e["commit"], str) and e["commit"]
        assert e["exact"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_agent_service_log_stays_in_order_with_git_in_it(agent_service_repo):
    model = relay_model.build(agent_service_repo, now=NOW)
    times = [e["t"] for e in model["log"]]
    assert times == sorted(times, reverse=True)
    assert len(entries_of(model, "commit")) <= len(relay_events(model))


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_baton_whose_leg_has_no_runner_row_still_attributes_its_commit(tmp_path):
    """A baton is what happened; `legs.json` is what was planned. A leg the
    plan has not caught up with - forgotten, or still marked pending while its
    baton sits on disk - has no runner row, and its landing is in the log all
    the same. Its commit has to be, too, or the log drops exactly the commit of
    the leg that just landed (ACC-DATA-009)."""
    project = tmp_path / "unplanned"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps({
        "relay": "unplanned",
        "stages": [{"id": "S1", "legs": ["alpha"]}],
        "legs": [{"id": "alpha", "stage": "S1", "status": "pending"}],
    }))

    _git(project, "init", "-q", "-b", "main")
    _git(project, "add", "-A", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "before: the project existed first",
         when=NOW - 9000)
    # On a branch of its own, which is where a relay runs and what opens the
    # window early enough to hold a commit made before its own baton.
    _git(project, "checkout", "-q", "-b", "feat/the-run")
    _git(project, "commit", "-q", "--allow-empty", "-m", "alpha: the leg's work",
         when=NOW - 4100)
    _land(relay_dir, "alpha", NOW - 4000, _short_sha(project))
    for n in range(5):
        _git(project, "commit", "-q", "--allow-empty",
             "-m", f"noise {n:02d}: the project carried on", when=NOW - 3000 + n)

    model = relay_model.build(relay_dir, now=NOW)
    assert not [r for r in model["runners"] if r["leg"] == "alpha"]
    entry = commit_named(model, "alpha: the leg's work")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "alpha"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_every_corpus_baton_that_claims_a_commit_is_credited_with_it(corpus_relay):
    """ACC-DATA-009, against the corpus the check's evidence names: for each
    baton that claims a commit, the log entry carries the leg whose baton
    claims it. Fails today for three legs."""
    relay_dir, sha_of = corpus_relay
    model = relay_model.build(relay_dir, now=NOW)
    commits = {e["commit"]: e for e in entries_of(model, "commit")}
    missing, miscredited = [], []
    for leg, token in CORPUS_OWN.items():
        sha = sha_of[token]
        if sha not in commits:
            missing.append((leg, token))
        elif commits[sha]["leg"] != leg:
            miscredited.append((leg, token, commits[sha]["leg"]))
    assert not missing, missing
    assert not miscredited, miscredited


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_every_corpus_landing_carries_its_own_commit(corpus_relay):
    """The same claim on the other entry: a leg's landing names the sha that
    leg produced, not the first sha-shaped token its prose happens to hold."""
    relay_dir, sha_of = corpus_relay
    model = relay_model.build(relay_dir, now=NOW)
    landings = {e["leg"]: e for e in entries_of(model, "baton")}
    named = {leg: landings[leg]["commit"] for leg in CORPUS_OWN if leg in landings}
    assert named == {leg: sha_of[token] for leg, token in CORPUS_OWN.items()
                     if leg in landings}
    rows = {r["leg"]: r for r in model["runners"]}
    for leg, token in CORPUS_OWN.items():
        if leg in rows:
            assert rows[leg]["commit"] == sha_of[token], leg


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_sha_the_corpus_only_mentions_is_credited_to_no_leg(corpus_relay):
    """A branch point, a parent and another runner's starting point. All three
    resolve in this repository and all three sit in a baton; none is a claim."""
    relay_dir, sha_of = corpus_relay
    model = relay_model.build(relay_dir, now=NOW)
    quoted = {sha_of[token] for token in CORPUS_QUOTED}
    credited = [(e["commit"], e["leg"]) for e in model["log"]
                if e["commit"] in quoted and e["leg"]]
    assert credited == [], credited


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_corpus_baton_that_claims_nothing_names_nothing(corpus_relay):
    """Honest absence beats a wrong credit: three of the ten batons say
    nothing about a commit, and the model does not guess one for them."""
    relay_dir, _ = corpus_relay
    model = relay_model.build(relay_dir, now=NOW)
    landings = {e["leg"]: e for e in entries_of(model, "baton")}
    for leg in CORPUS_SILENT:
        assert landings[leg]["commit"] is None, (leg, landings[leg]["commit"])


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_no_commit_from_before_the_relay_began_survives(corpus_relay):
    """ACC-DATA-009's first sentence, on a run that owns a branch: the window
    opens at the BRANCH POINT, so what excludes `7f8690c` is that it sits
    before that point, not that it is dated hours before the relay's earliest
    event. It is the commit the branch forked from and no baton claims it; it
    is not this run's work however loudly `reconcile-develop.md` mentions it."""
    relay_dir, sha_of = corpus_relay
    model = relay_model.build(relay_dir, now=NOW)
    assert sha_of[CORPUS_FORK_POINT] not in {e["commit"] for e in model["log"]}
    # And it is the topology that says so, not a count and not a clock: the
    # commit is a real object in this repository, older than every commit that
    # did survive, and it is off the branch.
    on_branch = _git(relay_dir, "log", "--format=%h",
                     "HEAD", "--not", "refs/heads/main").stdout.split()
    assert sha_of[CORPUS_FORK_POINT] not in on_branch
    assert _git(relay_dir, "cat-file", "-t",
                sha_of[CORPUS_FORK_POINT]).stdout.strip() == "commit"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_first_legs_commit_survives_though_it_predates_the_first_event(
        corpus_relay):
    """The case the exemption was introduced for, kept without the exemption:
    `reconcile-develop` committed before it wrote the baton that is the relay's
    earliest recorded event, and its commit is on the run's own branch."""
    relay_dir, sha_of = corpus_relay
    model = relay_model.build(relay_dir, now=NOW)
    entry = [e for e in entries_of(model, "commit")
             if e["commit"] == sha_of["c3319e2"]]
    assert entry, [e["m"] for e in entries_of(model, "commit")]
    assert entry[0]["leg"] == "reconcile-develop"
    assert entry[0]["t"] < min(e["t"] for e in relay_events(model))


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_corpus_log_does_not_bury_the_run(corpus_relay):
    """The counting clause still holds on the real corpus."""
    relay_dir, _ = corpus_relay
    model = relay_model.build(relay_dir, now=NOW)
    assert len(entries_of(model, "commit")) <= len(relay_events(model))
    assert len(model["log"]) <= relay_model.LOG_MAX_ENTRIES


# --------------------------------------------------------------------------
# a sha is credited only when the relay's OWN repository has it (ACC-DATA-009)
# --------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_claimed_sha_the_repository_does_not_have_is_not_credited(tmp_path):
    """A judge's baton quotes another relay's shas while reporting on it; a
    runner mistypes one. Neither names a commit of this repository, and a
    commit this repository does not have cannot be this leg's work."""
    project = tmp_path / "unresolvable"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps({
        "relay": "unresolvable",
        "stages": [{"id": "S1", "legs": ["alpha", "beta"]}],
        "legs": [{"id": "alpha", "stage": "S1", "status": "done"},
                 {"id": "beta", "stage": "S1", "status": "done"}],
    }))
    _git(project, "init", "-q", "-b", "main")
    _git(project, "add", "-A", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "chore: the project existed first",
         when=NOW - 9000)
    _git(project, "checkout", "-q", "-b", "feat/the-run")
    _land(relay_dir, "alpha", NOW - 5000)
    _git(project, "commit", "-q", "--allow-empty", "-m", "beta: the leg's work",
         when=NOW - 4100)
    _land(relay_dir, "beta", NOW - 4000, "deadbee")

    model = relay_model.build(relay_dir, now=NOW)
    assert "deadbee" not in {e["commit"] for e in model["log"]}
    rows = {r["leg"]: r for r in model["runners"]}
    assert rows["beta"]["commit"] is None
    # The leg's real commit is still in the log; it is simply unattributed,
    # which is all the evidence on disk supports.
    entry = commit_named(model, "beta: the leg's work")
    assert entry is not None and entry["leg"] is None


def test_this_repos_own_relay_credits_only_shas_this_repo_has():
    """The defect as it was reproduced: this repository's own log credited
    `code-judge-S1` with `4f0b17c` and `behaviour-judge-S1` with `8036f9f`,
    both agent-service shas those judges quoted while reporting on another
    relay, neither a valid object here. Run in place, because a copy of a
    relay is outside the repository whose commits it names."""
    own = REPO / ".relay"
    if not HAS_GIT or not (own / "batons").is_dir():
        pytest.skip(".relay is git-ignored and absent from a fresh clone")
    model = relay_model.build(own, now=NOW)
    named = sorted({e["commit"] for e in model["log"] if e["commit"]})
    assert named, "this relay's batons name commits"
    unresolvable = [sha for sha in named
                    if subprocess.run(["git", "-C", str(REPO), "cat-file", "-t", sha],
                                      capture_output=True, text=True).returncode != 0]
    assert unresolvable == [], unresolvable
    # Derived at assert time, never hardcoded. This branch's history was
    # rewritten wholesale on 2026-08-25 - an author rewrite, same trees, every
    # sha new - and the sha this line used to name stopped existing. A test
    # naming a sha survives that as a green test that no longer tests anything;
    # HEAD is the one commit the log must always carry, and git is asked for it.
    head = _git(REPO, "rev-parse", "--short=7", "HEAD").stdout.strip()
    assert head in {e["commit"] for e in entries_of(model, "commit")}


# --------------------------------------------------------------------------
# TWO floors, and neither of them is the budget
# (ACC-DATA-009, corrected 2026-08-25)
#
#   * a commit some leg's baton CLAIMS is floored at the BRANCH POINT. A runner
#     commits before it writes its baton, so a first leg's commit predates
#     every record the relay has, and the branch point is the only bound that
#     admits it.
#   * a commit NO leg claims is floored at the relay's EARLIEST RECORD. A run
#     supervised on a branch that already existed does not own what that branch
#     carried beforehand: that is the project's history, which is the thing
#     this check's title forbids.
#
# THE EVIDENCE RULE for this whole section. The budget is `len(events)`, so a
# small relay hides a pre-relay commit whether or not the window works: it
# simply runs out of budget before reaching it, and the test goes green for the
# wrong reason. Every exclusion below is therefore asserted with SPARE LEGS -
# batons that are events and claim nothing, so the budget can afford every
# commit the walk returned and only the window can still refuse one. A test
# that goes green when the budget is raised is not evidence.
# --------------------------------------------------------------------------

def _long_lived_branch(project, spare_legs=0):
    """A relay supervised on a branch that ALREADY EXISTED.

    `main` carries the project's first commit. `feat/long-lived` carries three
    more from long before the relay, then alpha's own work; alpha's baton lands
    after that commit and claims it, then one commit dated at the baton to the
    second, then five nobody claims. `spare_legs` further batons land later
    still and claim nothing: each is one more relay event, and the budget is
    the event count.

    So the branch holds a commit of each population the two floors separate, on
    both sides of both floors.
    """
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    legs = [{"id": "alpha", "stage": "S1", "status": "done"}]
    legs += [{"id": f"spare-{i:03d}", "stage": "S1", "status": "done"}
             for i in range(spare_legs)]
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "long-lived",
         "stages": [{"id": "S1", "legs": [leg["id"] for leg in legs]}],
         "legs": legs}))

    _git(project, "init", "-q", "-b", "main")
    _git(project, "add", "-A", when=NOW - 90000)
    _git(project, "commit", "-q", "-m", "chore: on main", when=NOW - 90000)
    _git(project, "checkout", "-q", "-b", "feat/long-lived")
    for n in range(3):
        _git(project, "commit", "-q", "--allow-empty",
             "-m", f"before: branch work {n} that predates the relay",
             when=NOW - 80000 + n)
    _git(project, "commit", "-q", "--allow-empty", "-m", "alpha: the leg's work",
         when=NOW - 4100)
    _land(relay_dir, "alpha", NOW - 4000, _short_sha(project))
    # Dated to the second the earliest record carries. A floor is a floor and
    # not a fence: a runner that commits and writes its baton inside the same
    # second is the ordinary case, not an edge one.
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "on the floor: dated exactly at the earliest record",
         when=NOW - 4000)
    for n in range(5):
        _git(project, "commit", "-q", "--allow-empty",
             "-m", f"after: the project carried on {n}", when=NOW - 3000 + n)
    for i in range(spare_legs):
        _land(relay_dir, f"spare-{i:03d}", NOW - 3900 + i)
    return relay_dir


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("spare", [0, 8, 40])
def test_a_long_lived_branchs_pre_relay_commits_are_out_of_window(tmp_path, spare):
    """The check's own title, asserted so the budget cannot supply the answer.

    At `spare=0` this is the test this section replaces, and it passed whether
    or not the window worked: one event bought one commit and the attributed
    one took it. At `spare=8` and `spare=40` the budget can afford every commit
    the walk returned, and only the window can still exclude these three.
    """
    relay_dir = _long_lived_branch(tmp_path / "older", spare)
    model = relay_model.build(relay_dir, now=NOW)
    commits = entries_of(model, "commit")
    events = relay_events(model)
    assert len(events) == 1 + spare, [e["m"] for e in events]
    assert not [e["m"] for e in commits if "before:" in e["m"]]
    if spare:
        # The budget could have bought every commit on the branch, and bought
        # every one the window admits: what is missing is missing because it is
        # out of WINDOW. Asserted as an equality against the branch itself
        # rather than as a count, so a budget that quietly tightens cannot make
        # this pass.
        on_branch = _git(relay_dir, "log", "--format=%s", "HEAD",
                         "--not", "refs/heads/main").stdout.splitlines()
        assert len(on_branch) == 10
        assert {s for s in on_branch if not s.startswith("before:")} == \
            {e["m"].split(": ", 1)[1] for e in commits}
        assert len(commits) < len(events)      # and budget still left over


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_claimed_commit_that_predates_every_record_is_kept(tmp_path):
    """The other floor, on the same branch. Alpha committed before it wrote its
    baton, so its commit is older than every record the relay has; the branch
    point is the only bound that admits it, and the claim is what reaches."""
    relay_dir = _long_lived_branch(tmp_path / "older", 8)
    model = relay_model.build(relay_dir, now=NOW)
    entry = commit_named(model, "alpha: the leg's work")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "alpha"
    assert entry["t"] < min(e["t"] for e in relay_events(model))


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_an_unclaimed_commit_before_the_earliest_record_is_absent(tmp_path):
    """The rule stated as an invariant rather than by name: inside a branch,
    every commit older than the relay's earliest record carries a leg."""
    relay_dir = _long_lived_branch(tmp_path / "older", 40)
    model = relay_model.build(relay_dir, now=NOW)
    floor = min(e["t"] for e in relay_events(model))
    for entry in entries_of(model, "commit"):
        assert entry["leg"] or entry["t"] >= floor, entry["m"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_an_unclaimed_commit_after_the_earliest_record_is_present(tmp_path):
    """The floor is a floor and not a ban: the project's traffic from while the
    relay was running is part of the run's story, and the budget buys it."""
    relay_dir = _long_lived_branch(tmp_path / "older", 8)
    model = relay_model.build(relay_dir, now=NOW)
    after = [e for e in entries_of(model, "commit") if "after:" in e["m"]]
    assert len(after) == 5, [e["m"] for e in entries_of(model, "commit")]
    assert all(e["leg"] is None for e in after)


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_commit_dated_exactly_at_the_earliest_record_is_in_the_window(tmp_path):
    """The floor is inclusive. A runner that commits and writes its baton
    inside the same second is the ordinary case, and an exclusive floor loses
    that commit for a reason nobody could read off the pane."""
    relay_dir = _long_lived_branch(tmp_path / "older", 8)
    model = relay_model.build(relay_dir, now=NOW)
    entry = commit_named(model, "on the floor:")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] is None                        # nothing claims it
    assert entry["t"] == min(e["t"] for e in relay_events(model))


def test_the_two_floors_are_derived_from_the_records_and_the_topology():
    """`_commit_floors` at its own seam, with no repository in sight: the two
    floors differ only where the run owns a branch, and where it does not, a
    claim is NOT exempt from the record floor. That exemption is what let a
    merge dated a day before the relay began into the live relay's log, on the
    strength of a baton that only mentioned it."""
    assert relay_model._commit_floors(1000.0, True) == (None, 1000.0)
    assert relay_model._commit_floors(1000.0, False) == (1000.0, 1000.0)
    # A relay whose records cannot be timed has a window and no floor.
    assert relay_model._commit_floors(None, True) == (None, None)
    assert relay_model._commit_floors(None, False) == (None, None)


# --------------------------------------------------------------------------
# the budget never discards an attributed commit (ACC-DATA-009)
# --------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_budget_never_discards_an_attributed_commit(tmp_path):
    """`min(len(events), LOG_MAX_ENTRIES - len(events))` inverts above 150
    relay events: at 250 events it buys 50 commits, and the sixty legs that
    landed one lose the ten that landed first. Attribution is not a budget
    line - a commit a baton claims is the run's own work, and only the
    unattributed remainder is bought with what is left."""
    project = tmp_path / "long-run"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    legs = [{"id": f"leg-{i:03d}", "stage": "S1", "status": "done"}
            for i in range(250)]
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "long-run",
         "stages": [{"id": "S1", "legs": [leg["id"] for leg in legs]}],
         "legs": legs}))

    _git(project, "init", "-q", "-b", "main")
    _git(project, "add", "-A", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "chore: the project existed first",
         when=NOW - 9000)
    _git(project, "checkout", "-q", "-b", "feat/the-long-run")
    # One `git` process per commit is the cost here, not Python.
    script = ('set -e; i=0; while [ $i -lt 60 ]; do '
              'GIT_AUTHOR_DATE="$((BASE+i*10)) +0000" '
              'GIT_COMMITTER_DATE="$((BASE+i*10)) +0000" '
              'git commit -q --allow-empty -m "leg work $i"; i=$((i+1)); done')
    env = dict(os.environ)
    env.update({"BASE": str(int(NOW - 2600)),
                "GIT_AUTHOR_NAME": "Relay Test", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "Relay Test",
                "GIT_COMMITTER_EMAIL": "t@example.com"})
    out = subprocess.run(["sh", "-c", script], cwd=project, env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr

    # 250 batons; the newest 60 of them claim the 60 commits above, each five
    # seconds after the commit it claims.
    claimed = {}
    for i, leg in enumerate(legs):
        n = i - (len(legs) - 60)
        sha = _short_sha(project, f"HEAD~{59 - n}") if n >= 0 else None
        if sha:
            claimed[leg["id"]] = sha
        _land(relay_dir, leg["id"], NOW - 5000 + i * 10, sha)

    model = relay_model.build(relay_dir, now=NOW)
    commits = entries_of(model, "commit")
    events = relay_events(model)
    credited = {e["commit"]: e["leg"] for e in commits if e["leg"]}
    assert len(events) > 150, len(events)
    assert credited == {sha: leg for leg, sha in claimed.items()}, \
        sorted(set(claimed.values()) - set(credited))
    assert len(commits) <= len(events), (len(commits), len(events))
    assert len(model["log"]) <= relay_model.LOG_MAX_ENTRIES


# --------------------------------------------------------------------------
# the window is a property of the RECORDS, never of the derived log
# (ACC-DATA-009, amended 2026-08-25)
#
# THE INVARIANT: the window is a property of what the relay has RECORDED -
# baton mtimes, recorded events, state transitions - and never of what the log
# derivation has so far produced.
#
# Four fixes to this check landed against the shape in front of them and the
# class stayed open, because the deciding value was the derived entry list.
# Two rules empty it for a relay one leg in: a baton whose leg is still marked
# `running` is skipped as a landing, and the running leg's start entry needs a
# previous baton to hand off from. So a relay ONE LEG IN derived nothing, was
# read as a relay that had recorded nothing, and every commit in the repository
# it happened to sit in became one of its own.
#
# These tests enumerate the ways the derived list can be empty or wrong while
# records exist. Each of them is a door into the same room. A fix that closes
# one shape and not the predicate closes none of them.
# --------------------------------------------------------------------------

def _one_leg_in(project, status="running", baton=True, branch="main",
                commits=40, subject="old project commit"):
    """A relay ONE LEG IN inside a project with unrelated history.

    `git init` in `project`, `commits` commits that are nobody's leg, then a
    relay whose only leg carries `status` - and a baton, unless `baton` is
    False. The relay's records are written LAST, so every commit predates
    every record and no commit is this run's by any reading.
    """
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    _git(project, "init", "-q", "-b", branch)
    _git(project, "commit", "-q", "--allow-empty", "--allow-empty-message",
         "-m", f"{subject} 0", when=NOW - 9000)
    for n in range(1, commits):
        _git(project, "commit", "-q", "--allow-empty",
             "-m", f"{subject} {n}", when=NOW - 9000 + n)
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "one-leg-in",
         "stages": [{"id": "S1", "legs": ["alpha"]}],
         "legs": [{"id": "alpha", "stage": "S1", "status": status}]}))
    (relay_dir / "state.json").write_text(json.dumps({}))
    if baton:
        _land(relay_dir, "alpha", NOW - 10)
    return relay_dir


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_relay_one_leg_in_reports_none_of_its_projects_history(tmp_path):
    """The reproduction, exactly. A single `running` leg holding the only
    baton, inside a repository with forty unrelated commits. Before the fix
    the log was those forty commits and nothing else: `own` was empty, so
    `since` was None, so the window and the budget were both skipped and every
    commit in the walk became an entry."""
    relay_dir = _one_leg_in(tmp_path / "proj")
    model = relay_model.build(relay_dir, now=NOW)
    assert entries_of(model, "commit") == [], \
        [e["m"] for e in entries_of(model, "commit")]
    assert not [e for e in model["log"] if "old project commit" in e["m"]]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_relay_one_leg_in_has_a_window_though_it_has_derived_no_entry(tmp_path):
    """The invariant itself, asserted on the predicate rather than through it.
    This relay derives NO entry - its baton has not landed and its start is
    unknown - and it still has records, and therefore a window. The contract's
    carve-out is 'no batons, no running leg, no judged checks', and this relay
    fails it twice over."""
    relay_dir = _one_leg_in(tmp_path / "proj")
    model = relay_model.build(relay_dir, now=NOW)
    assert relay_events(model) == []          # nothing derived...
    batons = relay_model._read_batons(relay_dir, [])
    has_records, floor = relay_model._relay_records(
        relay_dir, model["runners"], batons, model["checks"], [])
    assert has_records is True                # ...and records all the same
    assert floor == batons["alpha"]["mtime"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_running_first_leg_with_no_baton_still_bounds_the_commits(tmp_path):
    """The second door a judge found. No baton at all, so not even a baton
    mtime is available: the record is `legs.json` saying a leg is running, and
    the file that holds it is what dates it."""
    relay_dir = _one_leg_in(tmp_path / "proj", baton=False, commits=15)
    model = relay_model.build(relay_dir, now=NOW)
    assert list((relay_dir / "batons").glob("*.md")) == []
    assert relay_events(model) == []
    assert entries_of(model, "commit") == [], \
        [e["m"] for e in entries_of(model, "commit")]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_branch_with_no_default_branch_and_a_running_first_leg(tmp_path):
    """The third door. `trunk`, with no `main`, no `master` and no `origin/*`,
    so there is no branch point to floor at either - the records are the only
    bound left, and they have to be enough on their own."""
    relay_dir = _one_leg_in(tmp_path / "proj", branch="trunk")
    project = relay_dir.parent
    assert _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "trunk"
    assert relay_model._default_branch_refs(relay_dir) == []
    model = relay_model.build(relay_dir, now=NOW)
    assert entries_of(model, "commit") == [], \
        [e["m"] for e in entries_of(model, "commit")]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_leg_with_an_unparseable_status_is_still_a_record(tmp_path):
    """A status no coach word maps to normalises to `pending`, so the leg is
    neither running nor completed and derives no entry of its own. Its BATON
    is still a record and still opens a window."""
    relay_dir = _one_leg_in(tmp_path / "proj", status={"not": "a status"})
    model = relay_model.build(relay_dir, now=NOW)
    assert [r["status"] for r in model["legs"]] == ["pending"]
    assert entries_of(model, "commit") == [], \
        [e["m"] for e in entries_of(model, "commit")]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_relay_with_no_legs_at_all_is_still_bounded_by_its_batons(tmp_path):
    """`legs.json` lists nothing, so there are no runner rows, no running leg
    and no start entry. The baton on disk is a record regardless - the log
    reports what happened, not what was planned - and it carries the window."""
    project = tmp_path / "proj"
    relay_dir = _one_leg_in(project, commits=20)
    (relay_dir / "legs.json").write_text(json.dumps({"relay": "empty", "legs": []}))
    model = relay_model.build(relay_dir, now=NOW)
    assert model["runners"] == []
    assert entries_of(model, "commit") == [], \
        [e["m"] for e in entries_of(model, "commit")]
    # The baton still lands as an entry - it has a leg nobody planned.
    assert [e["leg"] for e in entries_of(model, "baton")] == ["alpha"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_relay_whose_only_record_is_a_judged_check_has_a_window(tmp_path):
    """No baton and no running leg: the only thing the relay has recorded is
    that a check was judged, in `state.json`. That transition produces no log
    entry either - it has no baton to be pinned to - and it is still a record,
    so the carve-out does not apply."""
    project = tmp_path / "proj"
    relay_dir = _one_leg_in(project, status="done", baton=False, commits=12)
    (relay_dir / "state.json").write_text(json.dumps(
        {"checks": {"ACC-X-001": {"status": "failed", "round": 2,
                                  "claimedBy": "alpha", "fixLeg": "fix-it"}}}))
    model = relay_model.build(relay_dir, now=NOW)
    assert relay_events(model) == []          # no baton to pin the check to
    assert [c["status"] for c in model["checks"]] == ["failed"]
    assert entries_of(model, "commit") == [], \
        [e["m"] for e in entries_of(model, "commit")]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_an_unjudged_check_is_a_plan_and_not_a_record(tmp_path):
    """The other side of the same predicate, so that 'has a check' does not
    quietly become 'has a record'. A check nobody has judged - pending, no
    round, no fix leg, no judge - is a plan, and a relay carrying only plans is
    the degenerate case the contract carves out: recent commits are the only
    story it has."""
    project = tmp_path / "proj"
    relay_dir = _one_leg_in(project, status="pending", baton=False, commits=3)
    (relay_dir / "state.json").write_text(json.dumps(
        {"checks": {"ACC-X-001": {"status": "pending", "claimedBy": "alpha"}}}))
    model = relay_model.build(relay_dir, now=NOW)
    assert relay_events(model) == []
    assert len(entries_of(model, "commit")) == 3
    assert model["logSource"] == "derived"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_baton_mtimes_in_the_future_close_the_window_rather_than_opening_it(
        tmp_path):
    """A clock skew, a touched file, an archive restored with tomorrow's
    stamps. The floor is the earliest record whatever that record says, so a
    relay whose records are all in the future has a window that no commit in
    the past is inside - which is the safe direction to be wrong in."""
    project = tmp_path / "proj"
    relay_dir = _one_leg_in(project, status="done", commits=10)
    _land(relay_dir, "alpha", NOW + 100000)
    model = relay_model.build(relay_dir, now=NOW)
    assert entries_of(model, "commit") == [], \
        [e["m"] for e in entries_of(model, "commit")]
    assert [e["leg"] for e in entries_of(model, "baton")] == ["alpha"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_detached_head_is_bounded_by_the_records_like_any_other(tmp_path):
    """HEAD on no branch at all. `for-each-ref` still finds `main`, and the
    walk excluding it comes back empty because HEAD is an ancestor of it - so
    there is no branch of the run's own and the records are the whole window.
    """
    project = tmp_path / "proj"
    relay_dir = _one_leg_in(project, status="done", commits=8)
    _git(project, "checkout", "-q", "--detach", "HEAD~2")
    assert _git(project, "rev-parse", "--abbrev-ref",
                "HEAD").stdout.strip() == "HEAD"
    model = relay_model.build(relay_dir, now=NOW)
    assert entries_of(model, "commit") == [], \
        [e["m"] for e in entries_of(model, "commit")]


# --------------------------------------------------------------------------
# a first leg's CLAIMED commit reaches back past the records; its unclaimed
# ones do not (ACC-DATA-009, corrected 2026-08-25)
#
# The 2026-08-25 amendment made the branch point the floor for EVERY commit,
# attributed or not, so that all three of a first leg's commits were admitted.
# The coach wrote that clause and it was wrong: the same rule admits everything
# a long-lived branch carried before the run started, which is the project's
# history and the thing this check's title forbids. The contract was corrected
# the same day - two floors, one per population - and this fixture is where the
# correction is paid for.
#
# Two of alpha's three commits are now OUT, and that is a real loss: they are
# the run's work. Nothing on disk tells them from a long-lived branch's
# pre-relay work - both sit after the branch point and before every record - so
# the only evidence that separates the two populations is a CLAIM, and what
# carries no claim is floored at the records.
# --------------------------------------------------------------------------

@pytest.fixture
def first_leg_burst(tmp_path):
    """A first leg that commits THREE times before it writes its baton.

    Every one of the three is after the branch point and every one is older
    than the relay's earliest recorded event, because a runner commits and
    then writes its baton. The baton claims only the last of them.
    """
    project = tmp_path / "burst"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps({
        "relay": "burst",
        "stages": [{"id": "S1", "legs": ["alpha", "beta", "delta", "gamma"]}],
        "legs": [{"id": "alpha", "stage": "S1", "status": "done"},
                 {"id": "beta", "stage": "S1", "status": "done"},
                 {"id": "delta", "stage": "S1", "status": "done"},
                 {"id": "gamma", "stage": "S1", "status": "running"}],
    }))
    _git(project, "init", "-q", "-b", "main")
    _git(project, "add", "-A", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "before: the project existed first",
         when=NOW - 9000)
    _git(project, "checkout", "-q", "-b", "feat/the-run")
    for n in range(3):
        _git(project, "commit", "-q", "--allow-empty",
             "-m", f"alpha: step {n} of the first leg's work", when=NOW - 5300 + n)
    _land(relay_dir, "alpha", NOW - 5000, _short_sha(project))
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "beta: the second leg's work", when=NOW - 4100)
    _land(relay_dir, "beta", NOW - 4000, _short_sha(project))
    _land(relay_dir, "delta", NOW - 3000)     # landed, committed nothing
    return relay_dir


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_first_legs_claimed_commit_reaches_back_past_the_records(first_leg_burst):
    """The case the branch-point floor exists for. Alpha's third commit is
    older than the relay's earliest recorded event - a runner commits before it
    writes its baton - and the branch point is the only bound that admits it."""
    model = relay_model.build(first_leg_burst, now=NOW)
    steps = [e for e in entries_of(model, "commit") if "alpha: step" in e["m"]]
    assert len(steps) == 1, [e["m"] for e in entries_of(model, "commit")]
    assert steps[0]["leg"] == "alpha"
    assert steps[0]["t"] < min(e["t"] for e in relay_events(model))


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_first_legs_unclaimed_commits_do_not(first_leg_burst):
    """The price of the correction, named rather than left implicit. Alpha's
    first two commits are the run's own work and they are not in the log: no
    baton claims them and they predate every record, which is what a long-lived
    branch's pre-relay work also looks like.

    Asserted with the budget WIDE OPEN, so what excludes them is the window: at
    four events and two attributed commits the budget could buy two more, and
    there are exactly two it is not buying."""
    model = relay_model.build(first_leg_burst, now=NOW)
    commits = entries_of(model, "commit")
    events = relay_events(model)
    assert len(events) == 4, [e["m"] for e in events]
    assert len([e for e in commits if e["leg"]]) == 2
    assert [e for e in commits if not e["leg"]] == []
    assert len(events) - len(commits) == 2      # budget left deliberately over
    on_branch = _git(first_leg_burst, "log", "--format=%s", "HEAD",
                     "--not", "refs/heads/main").stdout.splitlines()
    assert len([s for s in on_branch if "alpha: step" in s]) == 3


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_branch_point_is_still_the_floor(first_leg_burst):
    """Widening the window to the branch point does not widen it past the
    branch point: the project's own history is still not this run's."""
    model = relay_model.build(first_leg_burst, now=NOW)
    assert not [e["m"] for e in entries_of(model, "commit") if "before:" in e["m"]]


# --------------------------------------------------------------------------
# attribution has no cap (ACC-DATA-009)
#
# `attributed[:LOG_MAX_ENTRIES // 2]` was newest-first, so it dropped the
# OLDEST attributed commits - a run's first legs, which is the exact property
# the check exists to protect. 160 legs each landing a commit gave 136 entries
# and lost 24 of them, silently.
# --------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_no_attributed_commit_is_dropped_above_the_entry_half_bound(tmp_path):
    """Past `LOG_MAX_ENTRIES // 2` attributed commits. The contract says every
    commit attributable to a leg is kept, without a cap; the first legs' are
    the ones a newest-first cap removes, and they are the ones a supervisor
    scrolled back for."""
    count = 160
    assert count > relay_model.LOG_MAX_ENTRIES // 2
    project = tmp_path / "many"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    legs = [{"id": f"leg-{i:03d}", "stage": "S1", "status": "done"}
            for i in range(count)]
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "many", "stages": [{"id": "S1", "legs": [l["id"] for l in legs]}],
         "legs": legs}))

    _git(project, "init", "-q", "-b", "main")
    _git(project, "add", "-A", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "chore: the project existed first",
         when=NOW - 9000)
    _git(project, "checkout", "-q", "-b", "feat/the-long-run")
    # One `git` process per commit is the cost here, not Python.
    script = ('set -e; i=0; while [ $i -lt %d ]; do '
              'GIT_AUTHOR_DATE="$((BASE+i*10)) +0000" '
              'GIT_COMMITTER_DATE="$((BASE+i*10)) +0000" '
              'git commit -q --allow-empty -m "leg work $i"; i=$((i+1)); done'
              % count)
    env = dict(os.environ)
    env.update({"BASE": str(int(NOW - 5000)),
                "GIT_AUTHOR_NAME": "Relay Test", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "Relay Test",
                "GIT_COMMITTER_EMAIL": "t@example.com"})
    out = subprocess.run(["sh", "-c", script], cwd=project, env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr

    claimed = {}
    for i, leg in enumerate(legs):
        sha = _short_sha(project, f"HEAD~{count - 1 - i}")
        claimed[leg["id"]] = sha
        _land(relay_dir, leg["id"], NOW - 4000 + i * 10, sha)

    model = relay_model.build(relay_dir, now=NOW)
    credited = {e["commit"]: e["leg"] for e in entries_of(model, "commit")
                if e["leg"]}
    assert credited == {sha: leg for leg, sha in claimed.items()}, \
        f"{len(claimed) - len(credited)} attributed commits were dropped"
    assert len(model["log"]) <= relay_model.LOG_MAX_ENTRIES
    # The oldest legs are the ones a newest-first cap removes first, so name
    # them rather than trusting the count.
    assert credited[claimed["leg-000"]] == "leg-000"
    assert credited[claimed["leg-023"]] == "leg-023"


# --------------------------------------------------------------------------
# `dashboard.json.path` may narrow the repository bound, never widen it
# (ACC-DATA-009)
#
# `path` is a string a coach wrote into a JSON file and is untrusted like every
# other field the model reads. The filesystem bound that keeps a host
# repository's history out of a relay's log is defeated by writing an ancestor
# there - the same boundary, walked in through a different input.
# --------------------------------------------------------------------------

@pytest.fixture
def nested_relay(tmp_path):
    """A relay that is its own project, inside a repository that is not its.

    The host's commits are NEWER than the relay's only record, so nothing but
    the repository bound can keep them out: a broken bound shows them all.
    That is deliberate - the mtimes here are stamped rather than inherited, so
    the test says the same thing on a fresh checkout as on a developer's tree.
    """
    host = tmp_path / "host"
    (host / "nested").mkdir(parents=True)
    _git(host, "init", "-q", "-b", "main")
    (host / "README").write_text("the host repository\n")
    _git(host, "add", "README", when=NOW - 200)
    _git(host, "commit", "-q", "-m", "host: not the relay's work 0",
         when=NOW - 200)
    for n in range(1, 5):
        _git(host, "commit", "-q", "--allow-empty",
             "-m", f"host: not the relay's work {n}", when=NOW - 200 + n)

    relay_dir = host / "nested" / "standalone"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "standalone",
         "stages": [{"id": "S1", "legs": ["alpha"]}],
         "legs": [{"id": "alpha", "stage": "S1", "status": "done"}]}))
    _land(relay_dir, "alpha", NOW - 9000)      # older than every host commit
    return relay_dir


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_relay_that_owns_no_repository_reports_no_commits(nested_relay):
    """The fixture shape, end to end and independent of any clock: the host's
    commits are all INSIDE this relay's window, so only the repository bound
    keeps them out of its log."""
    model = relay_model.build(nested_relay, now=NOW)
    assert model["relay"]["path"] == str(nested_relay.resolve())
    assert entries_of(model, "commit") == [], \
        [e["m"] for e in entries_of(model, "commit")]
    assert not [e for e in model["log"] if "host: not the relay's work" in e["m"]]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("where", ["host", "root", "grandparent"])
def test_a_written_path_cannot_widen_the_repository_bound(nested_relay, where):
    """A coach writes an ancestor into `dashboard.json.path` and the walk is
    supposed to follow it. It must not: the walk reaches the relay directory
    and its immediate parent, and anything else clamps back to the relay
    directory. Three ancestors, one rule."""
    host = nested_relay.parent.parent
    path = {"host": str(host), "root": "/",
            "grandparent": str(host.parent)}[where]
    (nested_relay / "dashboard.json").write_text(json.dumps({"path": path}))
    model = relay_model.build(nested_relay, now=NOW)
    assert model["relay"]["path"] == path         # reported, and not obeyed
    assert entries_of(model, "commit") == [], \
        [e["m"] for e in entries_of(model, "commit")]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_written_path_cannot_widen_the_claim_check_either(nested_relay):
    """The same bound on the other read, and on the cheapest observation of
    it. `_in_a_repo` is checked before git is spawned at all - by the log AND
    by `_resolve_shas`, which asks the relay's own repository whether a claimed
    sha is a commit in it. A relay that owns no repository asks nothing; a
    widened walk starts asking the host's, which is how a baton comes to be
    credited with a foreign object. No clock is involved in observing it."""
    host = nested_relay.parent.parent
    sha = _git(host, "rev-parse", "--short=7", "HEAD").stdout.strip()
    _land(nested_relay, "alpha", NOW - 9000, sha)
    (nested_relay / "dashboard.json").write_text(json.dumps({"path": str(host)}))

    asked = []
    real = relay_model._git
    relay_model._git = lambda d, *a, **k: (asked.append(a), real(d, *a, **k))[1]
    try:
        model = relay_model.build(nested_relay, now=NOW)
    finally:
        relay_model._git = real
    assert asked == [], asked
    assert entries_of(model, "commit") == []


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_live_relay_shape_still_finds_its_own_repository(repo_relay):
    """The clamp narrows the walk, so the shape it must not break is asserted
    beside it: a live relay is `<project>/.relay`, its `.git` is at the parent,
    and `path` naming that parent is the one ancestor the walk may follow."""
    (repo_relay / "dashboard.json").write_text(json.dumps(
        {"path": str(repo_relay.parent)}))
    model = relay_model.build(repo_relay, now=NOW)
    assert [e["m"] for e in entries_of(model, "commit")], "its own history"


# --------------------------------------------------------------------------
# the window is applied where it is derived, not only where it shows
# (ACC-DATA-009)
#
# The window is enforced in two places - `_commit_entries` decides which
# commits are the run's, and `_derived_log` decides how many of them fit. The
# ORIGINAL defect needed both of them to be gated on the derived entry list,
# and reverting either one alone leaves the other masking it. So the first test
# here reads `_commit_entries` directly: a gate that has stopped working is
# worth seeing at the boundary it guards rather than only where it shows.
# --------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_window_is_applied_though_the_derivation_produced_no_entry(tmp_path):
    """`_commit_entries` at its own boundary, on the relay one leg in. There
    are forty commits in the walk and no derived entry to bound them with, and
    the answer is still none of them."""
    relay_dir = _one_leg_in(tmp_path / "proj")
    project = str(relay_dir.parent)
    batons = relay_model._read_batons(relay_dir, [])
    runners = [{"leg": "alpha", "status": "running"}]
    records = relay_model._relay_records(relay_dir, runners, batons, [], [])

    # The walk really does have forty commits to bound - otherwise the
    # assertion below would pass for the wrong reason.
    commits, branched = relay_model._relay_commits(relay_dir, project)
    assert len(commits) == 40 and branched is False

    assert records[0] is True                       # records...
    assert relay_model._commit_entries(
        relay_dir, project, batons, records, 0, NOW) == []


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_window_opens_when_legs_json_recorded_the_running_leg(tmp_path):
    """`legs.json` marks a leg running and carries no timestamp of its own, so
    the file that holds that record is what dates it - and a relay is planned
    BEFORE its first runner lands, so that mtime is earlier than every baton.
    A first leg's own commits sit in exactly that gap. Without the record there
    is no floor below the first baton, and an unbranched run loses them."""
    project = tmp_path / "planned"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    _git(project, "init", "-q", "-b", "main")
    (project / "README").write_text("the project\n")
    _git(project, "add", "README", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "before: long before the relay",
         when=NOW - 9000)
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "alpha: the first leg's own work", when=NOW - 5500)

    legs = relay_dir / "legs.json"
    legs.write_text(json.dumps({
        "relay": "planned",
        "stages": [{"id": "S1", "legs": ["alpha", "beta"]}],
        "legs": [{"id": "alpha", "stage": "S1", "status": "done"},
                 {"id": "beta", "stage": "S1", "status": "running"}]}))
    os.utime(legs, (NOW - 6000, NOW - 6000))       # when the relay was planned
    _land(relay_dir, "alpha", NOW - 5000)          # claims no commit

    model = relay_model.build(relay_dir, now=NOW)
    assert relay_model._default_branch_refs(relay_dir), "main exists"
    assert len(relay_events(model)) == 2            # alpha landed, beta started
    assert commit_named(model, "alpha: the first leg's own work") is not None, \
        [e["m"] for e in entries_of(model, "commit")]
    assert commit_named(model, "before: long before the relay") is None


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_window_opens_when_state_json_recorded_a_check_transition(tmp_path):
    """The same reasoning on the other file. A judged check is a state
    transition the contract names as a record, `state.json` carries no
    timestamp for it, and the file's mtime is the only honest date it has."""
    project = tmp_path / "judged"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    _git(project, "init", "-q", "-b", "main")
    (project / "README").write_text("the project\n")
    _git(project, "add", "README", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "before: long before the relay",
         when=NOW - 9000)
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "work: done while the relay was running", when=NOW - 5500)

    (relay_dir / "legs.json").write_text(json.dumps({
        "relay": "judged",
        "stages": [{"id": "S1", "legs": ["alpha"]}],
        "legs": [{"id": "alpha", "stage": "S1", "status": "done"}]}))
    state = relay_dir / "state.json"
    state.write_text(json.dumps(
        {"checks": {"ACC-X-001": {"status": "failed", "round": 2,
                                  "claimedBy": "alpha", "fixLeg": "fix-it"}}}))
    os.utime(state, (NOW - 6000, NOW - 6000))
    _land(relay_dir, "alpha", NOW - 5000)

    model = relay_model.build(relay_dir, now=NOW)
    assert len(relay_events(model)) == 3    # a landing and two check transitions
    assert commit_named(model, "work: done while the relay was running") \
        is not None, [e["m"] for e in entries_of(model, "commit")]
    assert commit_named(model, "before: long before the relay") is None


# --------------------------------------------------------------------------
# LOG_MAX_COMMITS bounds the WALK, and it is the last bound left over
# attributed commits (ACC-DATA-009)
#
# With the two floors settled and the budget forbidden to buy attribution, the
# git walk's own `--max-count` is the only cap still standing over a claimed
# commit. It is spent newest-first, because newest-first from HEAD is the only
# walk git bounds cheaply: asking for the OLDEST two hundred means walking all
# of them, on every repaint. So a run whose branch carries more than
# LOG_MAX_COMMITS commits loses the commit ENTRIES of its oldest, which are its
# first legs'.
#
# That drop is the outer safety net's, and it is affordable for a reason worth
# stating: a leg's landing entry names its own commit sha whatever the walk
# returned, so what a dropped entry costs the log is the commit's SUBJECT LINE
# and not the attribution. Both halves of the boundary are pinned below, and
# the absence is proved to be the CAP's doing by raising the cap and watching
# the commit appear.
#
# This fixture is the most expensive in the file - LOG_MAX_COMMITS + 5 commits,
# one git process each - so it is built once for the module and only read.
# --------------------------------------------------------------------------

BEYOND_THE_WALK = 5


@pytest.fixture(scope="module")
def past_the_walk_bound(tmp_path_factory):
    """A run whose branch carries five commits MORE than the walk returns.

    Every baton lands BEFORE every commit, so the record floor admits the whole
    branch and the walk's own bound is the only thing that can still drop one.
    `first` claims the OLDEST commit on the branch, which the bounded walk
    stops short of; `last` claims the newest, which it returns. Returns
    `(relay_dir, oldest_sha, newest_sha)`.
    """
    project = tmp_path_factory.mktemp("past-the-bound") / "long-run"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "long-run",
         "stages": [{"id": "S1", "legs": ["first", "last"]}],
         "legs": [{"id": "first", "stage": "S1", "status": "done"},
                  {"id": "last", "stage": "S1", "status": "done"}]}))

    _git(project, "init", "-q", "-b", "main")
    _git(project, "add", "-A", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "chore: the project existed first",
         when=NOW - 9000)
    _git(project, "checkout", "-q", "-b", "feat/the-long-run")
    # One `git` process per commit is the cost here, not Python.
    walked = relay_model.LOG_MAX_COMMITS + BEYOND_THE_WALK
    script = ('set -e; i=0; while [ $i -lt %d ]; do '
              'GIT_AUTHOR_DATE="$((BASE+i)) +0000" '
              'GIT_COMMITTER_DATE="$((BASE+i)) +0000" '
              'git commit -q --allow-empty -m "run work $i"; i=$((i+1)); done'
              % walked)
    env = dict(os.environ)
    env.update({"BASE": str(int(NOW - 8000)),
                "GIT_AUTHOR_NAME": "Relay Test", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "Relay Test",
                "GIT_COMMITTER_EMAIL": "t@example.com"})
    out = subprocess.run(["sh", "-c", script], cwd=project, env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr

    oldest = _short_sha(project, f"HEAD~{walked - 1}")
    newest = _short_sha(project)
    _land(relay_dir, "first", NOW - 8900, oldest)
    _land(relay_dir, "last", NOW - 8800, newest)
    return relay_dir, oldest, newest


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_fixture_really_does_reach_past_the_walk_bound(past_the_walk_bound):
    """The premise of everything below, so none of it can pass for the wrong
    reason: the claimed commit really is outside what the bounded walk
    returns."""
    relay_dir, oldest, _newest = past_the_walk_bound
    on_branch = _git(relay_dir, "log", "--format=%h", "HEAD",
                     "--not", "refs/heads/main").stdout.split()
    assert len(on_branch) == relay_model.LOG_MAX_COMMITS + BEYOND_THE_WALK
    assert oldest not in on_branch[:relay_model.LOG_MAX_COMMITS]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_walk_stops_at_its_own_bound_and_keeps_the_newest(past_the_walk_bound):
    """`_git_log` at its own seam: the cap is what it says it is, and the end
    of the branch it keeps is the newest end."""
    relay_dir, oldest, newest = past_the_walk_bound
    walked = relay_model._git_log(relay_dir, exclude=["refs/heads/main"])
    assert len(walked) == relay_model.LOG_MAX_COMMITS
    shas = [sha for _when, sha, _subject in walked]
    assert shas[0] == newest
    assert oldest not in shas


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_claimed_commit_past_the_walk_bound_still_keeps_its_leg(
        past_the_walk_bound):
    """What the cap costs the log, stated exactly. `first`'s commit is past the
    bound and has no commit entry - but its landing entry and its runner row
    both still name the sha, so what was lost is the commit's subject line and
    not the attribution."""
    relay_dir, oldest, newest = past_the_walk_bound
    model = relay_model.build(relay_dir, now=NOW)
    assert oldest not in {e["commit"] for e in entries_of(model, "commit")}
    assert newest in {e["commit"] for e in entries_of(model, "commit")}
    landings = [e for e in model["log"]
                if e["kind"] == "baton" and e["leg"] == "first"]
    assert [e["commit"] for e in landings] == [oldest]
    assert {r["leg"]: r["commit"] for r in model["runners"]}["first"] == oldest
    assert len(model["log"]) <= relay_model.LOG_MAX_ENTRIES


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("cap,admitted", [
    (relay_model.LOG_MAX_COMMITS + BEYOND_THE_WALK - 1, False),
    (relay_model.LOG_MAX_COMMITS + BEYOND_THE_WALK, True)])
def test_the_walk_bound_is_what_drops_it_and_nothing_else(
        past_the_walk_bound, monkeypatch, cap, admitted):
    """The boundary itself, one commit either side of it, and the evidence that
    neither floor nor budget is involved: the same relay, the same batons, the
    same window - only the cap moves, and the commit appears with it."""
    relay_dir, oldest, _newest = past_the_walk_bound
    monkeypatch.setattr(relay_model, "LOG_MAX_COMMITS", cap)
    model = relay_model.build(relay_dir, now=NOW)
    named = {e["commit"] for e in entries_of(model, "commit")}
    assert (oldest in named) is admitted, sorted(named)[:5]


# --------------------------------------------------------------------------
# `dashboard.json.path` is untrusted input like every other field the model
# reads (ACC-DATA-009)
#
# The coach wrote `"path": "~/Documents/..."` into this repository's own
# dashboard by hand. Nothing here expanded the `~`, so the value named a
# directory called `~` under the process's working directory; `_in_a_repo`
# clamped back to the relay directory, found no `.git`, and the relay was read
# as one that owns no repository - no commit in the log, no baton's claim
# settled, and not one word about any of it. The log looked merely quiet.
# --------------------------------------------------------------------------

@pytest.fixture
def home_relay(tmp_path, monkeypatch):
    """A live-shaped relay under a HOME of its own, so that `~` means something.

    `<home>/proj` is the project and `<home>/proj/.relay` is the relay. Alpha
    landed claiming the project's one commit, and that commit is what these
    tests look for: it is in the log when the repository was read and absent
    when it was not. Beta landed claiming `deadbee`, which this repository does
    not have - the other read the path bounds, and the one that fails silently
    in the opposite direction.
    """
    home = tmp_path / "home"
    project = home / "proj"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "proj",
         "stages": [{"id": "S1", "legs": ["alpha", "beta"]}],
         "legs": [{"id": "alpha", "stage": "S1", "status": "done"},
                  {"id": "beta", "stage": "S1", "status": "done"}]}))
    _git(project, "init", "-q", "-b", "main")
    _git(project, "add", "-A", when=NOW - 4000)
    _git(project, "commit", "-q", "-m", "alpha: the leg's work", when=NOW - 4000)
    _land(relay_dir, "alpha", NOW - 5000, _short_sha(project))
    _land(relay_dir, "beta", NOW - 3000, "deadbee")
    monkeypatch.setenv("HOME", str(home))
    return relay_dir


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_written_path_with_a_tilde_still_reads_the_repository(home_relay):
    """The defect, end to end. A `~` is what a coach types, and both reads the
    path bounds - the commit walk and the claim settlement - happen."""
    (home_relay / "dashboard.json").write_text(json.dumps({"path": "~/proj"}))
    model = relay_model.build(home_relay, now=NOW)
    entry = commit_named(model, "alpha: the leg's work")
    assert entry is not None, model["warnings"]
    assert entry["leg"] == "alpha"
    assert {r["leg"]: r["commit"] for r in model["runners"]}["alpha"] == \
        entry["commit"]
    assert [w for w in model["warnings"] if "`path`" in w] == []


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_written_path_with_a_tilde_settles_the_claims_too(home_relay):
    """`path` bounds TWO reads, and the tilde silenced both. Beta claims a sha
    this repository does not have; only a repository that can be ASKED can
    refuse it, and an unexpanded `~` is a repository that cannot be asked -
    which `_resolve_shas` reads, correctly, as "nothing here can check", so the
    baton is believed and a pane credits a leg with an object that does not
    exist. The commit walk going quiet is the visible half of that defect; this
    is the half that speaks up and is wrong."""
    (home_relay / "dashboard.json").write_text(json.dumps({"path": "~/proj"}))
    model = relay_model.build(home_relay, now=NOW)
    rows = {r["leg"]: r for r in model["runners"]}
    assert rows["beta"]["commit"] is None, "deadbee is not an object here"
    assert rows["alpha"]["commit"] == commit_named(
        model, "alpha: the leg's work")["commit"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_written_path_is_still_quoted_as_the_coach_wrote_it(home_relay):
    """Expanding it is a reading for the READ, and not a correction of the
    coach: `relay.path` is the label a supervisor put on their own project and
    it is reported back unchanged."""
    (home_relay / "dashboard.json").write_text(json.dumps({"path": "~/proj"}))
    model = relay_model.build(home_relay, now=NOW)
    assert model["relay"]["path"] == "~/proj"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("kind", ["missing", "a file", "a tilde that expands to nothing"])
def test_a_written_path_that_names_no_directory_is_warned_about(home_relay, kind):
    """The other half of the same defect: a value that cannot be read from is
    a coach's typo, and it is treated like every other malformed field here -
    warned about and ignored, rather than silently disabling the read."""
    written = {"missing": "/no/such/project/anywhere",
               "a file": str(home_relay / "legs.json"),
               "a tilde that expands to nothing": "~/not-here"}[kind]
    (home_relay / "dashboard.json").write_text(json.dumps({"path": written}))
    model = relay_model.build(home_relay, now=NOW)
    said = [w for w in model["warnings"] if "`path`" in w]
    assert len(said) == 1 and "not a directory" in said[0], model["warnings"]
    assert model["relay"]["path"] == written        # still quoted as written
    # Ignored, not obeyed: the read falls back to the relay's own project,
    # which is the widest bound `_in_a_repo` would have allowed it anyway.
    assert commit_named(model, "alpha: the leg's work") is not None


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_path_that_reads_fine_is_not_warned_about(home_relay):
    """And nothing a coach can write correctly is warned about, or the warning
    is noise a supervisor learns to skip."""
    (home_relay / "dashboard.json").write_text(json.dumps(
        {"path": str(home_relay.parent)}))
    model = relay_model.build(home_relay, now=NOW)
    assert [w for w in model["warnings"] if "`path`" in w] == []
    assert commit_named(model, "alpha: the leg's work") is not None


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_tilde_path_cannot_widen_the_repository_bound(nested_relay, monkeypatch):
    """Expanding it is a reading of what the coach wrote, never a licence.
    `~` now names the HOST repository this relay merely sits inside, and the
    walk still reaches the relay directory and its immediate parent and no
    further: `path` may only NARROW the bound."""
    host = nested_relay.parent.parent
    monkeypatch.setenv("HOME", str(host))
    (nested_relay / "dashboard.json").write_text(json.dumps({"path": "~"}))
    model = relay_model.build(nested_relay, now=NOW)
    assert os.path.expanduser("~") == str(host)     # the tilde really resolves
    assert entries_of(model, "commit") == [], \
        [e["m"] for e in entries_of(model, "commit")]

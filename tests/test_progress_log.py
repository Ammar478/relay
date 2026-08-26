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
import shutil
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
    ALL_FIXTURES,
    FIXTURES,
    CORPUS_DENIED_CLAIMS,
    CORPUS_FORK_POINT,
    CORPUS_OWN,
    CORPUS_QUOTED,
    CORPUS_SILENT,
    HAS_GIT,
    corpus_relay,
    corpus_relay_denied,
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


def test_an_explicit_dict_entry_is_the_coachs_own_object_not_a_copy(relay):
    """ACC-DATA-006's "unchanged, as the same objects" - the half a `==`
    assertion cannot see.

    `entries.append(dict(entry))` is `==` to the entry it copies and is not it,
    and both guards for this check compared with `==`, so the model could have
    been handing views a copy of the coach's log at every gate and every one of
    them would have stayed green. The contract's word is *unchanged*: the model
    does not edit what the coach wrote, and the strongest statement of "did not
    edit" is "did not touch".

    `model["extras"]` is the parsed `dashboard.json` itself, so the objects the
    coach wrote are reachable from the model and identity is assertable from
    outside. Held together with the `==` guard above: identity alone would miss
    an entry edited in place, and `==` alone misses a copy.
    """
    target = relay("tokens")
    model = relay_model.build(target, now=NOW)
    written = model["extras"]["log"]
    assert model["logSource"] == "dashboard"
    assert len(written) == 3 and all(isinstance(e, dict) for e in written)
    assert len(model["log"]) == len(written)
    for i, (returned, original) in enumerate(zip(model["log"], written)):
        assert returned is original, (i, id(returned), id(original))


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
    # The dict entry is untouched: verbatim means verbatim where it can be,
    # and "untouched" is identity - a copy is `==` to it and is not it.
    assert model["log"][0] == {"t": "1h ago", "m": "a proper entry", "cls": "note"}
    assert model["log"][0] is model["extras"]["log"][0]
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
    """Commit subjects of the repository the fixtures happen to live in.

    Every failure mode of this read returns the empty list, and the caller
    asserts once per subject — so an empty list is a test that asserts nothing
    and reports success. `--format=%s` mutated to `--format=%H` is the same
    hole with the list still full: the needles stop being subjects and
    `subject not in messages` is true forever. Both are closed at the call
    site, which asserts what came back before using it.
    """
    if not HAS_GIT or not (REPO / ".git").exists():
        return []
    out = subprocess.run(
        ["git", "-C", str(REPO), "log", "--no-color", "--format=%s"],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
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
    subjects = _host_repo_subjects()
    # The needles, asserted before they are used: an empty list makes the loop
    # below assert nothing at all, and a list of shas makes it assert nothing
    # that matters. This repository's commits are `leg-id: prose` — real
    # subjects, with spaces in them, and there are many.
    assert len(subjects) >= 10, subjects
    assert sum(" " in s for s in subjects) >= 10, subjects
    assert messages, model["log"]
    for subject in subjects:
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
    """Both admissible answers used to be accepted here, so a model that
    derived a Progress Log out of unparseable input passed a test named for
    having none. There is nothing datable in this fixture: no log, and
    `logSource` None so a view says "none" rather than `1-0 of 0`."""
    model = relay_model.build(relay("malformed"), now=NOW)
    assert model["log"] == [], model["log"]
    assert model["logSource"] is None


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
        {"relay": "big", "stages": [{"id": "S1", "legs": [leg["id"] for leg in legs]}],
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
        {"relay": "huge", "stages": [{"id": "S1", "legs": [leg["id"] for leg in legs]}],
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


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_real_corpus_credits_only_shas_its_repository_has(corpus_relay_denied):
    """THE DEFECT'S OWN REPRODUCTION, on a corpus that travels.

    It was reproduced by reading `REPO / ".relay"` in place: this repository's
    log credited `code-judge-S1` with `4f0b17c` and `behaviour-judge-S1` with
    `8036f9f`, both agent-service shas those judges quoted while reporting on
    another relay, neither a valid object here.

    That reading is behind `.relay/`, which is git-ignored, so it skipped on
    every clone, every CI checkout and every container - the reproduction of
    the defect ACC-DATA-009 names in full ran on one laptop and nowhere else,
    and a `skipif` nobody reads is how a guard stops guarding.

    `corpus_relay_denied` is the same corpus without the machine: the real
    agent-service batons, claiming their real shas in the real prose their
    runners wrote, on a repository whose history withholds exactly those two.
    A sha this repository does not have is not this leg's work, wherever the
    suite runs. The live relay is still read, at the bottom of this test, as an
    extra reading of the assertions this one has already made - so the test
    body is never empty and never skipped.
    """
    relay_dir, sha_of = corpus_relay_denied
    model = relay_model.build(relay_dir, now=None)
    denied = set(CORPUS_DENIED_CLAIMS)
    real = set(sha_of.values())

    rows = {r["leg"]: r["commit"] for r in model["runners"]}
    settled = {sha for sha in rows.values() if sha}
    assert settled, model["runners"]          # the graft still settles claims
    assert settled <= real, sorted(settled - real)
    for sha in CORPUS_DENIED_CLAIMS:
        leg = next(k for k, v in CORPUS_OWN.items() if v == sha)
        assert rows[leg] is None, (leg, rows[leg])

    named = {e["commit"] for e in entries_of(model, "commit") if e["commit"]}
    assert named, model["log"]
    assert named <= real, sorted(named - real)
    # Not "not credited to that leg" - not anywhere in the model. A denied sha
    # reaching a column, a subject or a message is the same lie about what this
    # run did, whichever pane it arrives in.
    blob = json.dumps(model)
    for sha in denied:
        assert sha not in blob, sha

    # THE LIVE READING, as an extra. Everything above has already run; what a
    # live relay adds is drift - batons nobody froze, written since. It is read
    # here rather than in a test of its own precisely so that its absence
    # cannot empty a test body: on a clone this loop runs zero times and the
    # assertions that matter have already been made.
    for own in live_relay_dirs():
        model = relay_model.build(own, now=NOW)
        named = sorted({e["commit"] for e in model["log"] if e["commit"]})
        assert named, ("this relay's batons name commits", own)
        unresolvable = [
            sha for sha in named
            if subprocess.run(["git", "-C", str(own.parent), "cat-file", "-t", sha],
                              capture_output=True, text=True).returncode != 0]
        assert unresolvable == [], unresolvable
        # Derived at assert time, never hardcoded. This branch's history was
        # rewritten wholesale on 2026-08-25 - an author rewrite, same trees,
        # every sha new - and the sha this line used to name stopped existing.
        # A test naming a sha survives that as a green test that no longer
        # tests anything; HEAD is the one commit the log must always carry, and
        # git is asked for it.
        head = _git(own.parent, "rev-parse", "--short=7", "HEAD").stdout.strip()
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
    commits = entries_of(model, "commit")
    # A `for` over an empty list asserts nothing and reports success, and this
    # section's own evidence rule says a test that goes green when the log
    # empties is not evidence. Both populations are required to be here, so
    # the invariant below is a statement about a log that has something in it.
    assert [e for e in commits if e["leg"]], [e["m"] for e in commits]
    assert [e for e in commits if not e["leg"]], [e["m"] for e in commits]
    for entry in commits:
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


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_narrowed_walk_that_stops_above_the_window_is_thrown_away(tmp_path):
    """The branch point's ONLY remaining job, at its own seam (ACC-DATA-009,
    simplified 2026-08-26): it bounds how far back the walk looks and decides
    nothing. Here the run's branch was cut AFTER the relay's earliest record,
    so the narrowed walk stops above the floor and would be deciding that the
    project's traffic from while the relay ran does not belong. It is thrown
    away and the full walk is used - one extra `git log`, no lost commit."""
    project = tmp_path / "late"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    legs = [{"id": "alpha", "stage": "S1", "status": "done"}]
    legs += [{"id": f"spare-{i}", "stage": "S1", "status": "done"} for i in range(3)]
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "late", "stages": [{"id": "S1", "legs": [leg["id"] for leg in legs]}],
         "legs": legs}))
    _git(project, "init", "-q", "-b", "main")
    _git(project, "commit", "-q", "--allow-empty", "--allow-empty-message",
         "-m", "before: long before the relay", when=NOW - 90000)
    _land(relay_dir, "alpha", NOW - 5000)                 # the earliest record
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "while: on main while the relay ran", when=NOW - 4000)
    _git(project, "checkout", "-q", "-b", "feat/cut-late")
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "late: on the branch the relay cut", when=NOW - 3000)
    for i in range(3):
        _land(relay_dir, f"spare-{i}", NOW - 2500 + i)    # budget for both

    refs = relay_model._default_branch_refs(relay_dir)
    assert refs, "the repository has a default branch to narrow the walk with"
    narrowed = [c[2] for c in relay_model._git_log(relay_dir, exclude=refs)]
    assert narrowed == ["late: on the branch the relay cut"]

    floor = NOW - 5000
    walked = [c[2] for c in relay_model._relay_commits(
        relay_dir, str(project), floor, {})]
    assert "while: on main while the relay ran" in walked, walked

    model = relay_model.build(relay_dir, now=NOW)
    assert commit_named(model, "while: on main while the relay ran") is not None, \
        [e["m"] for e in entries_of(model, "commit")]
    assert commit_named(model, "late: on the branch the relay cut") is not None
    assert commit_named(model, "before: long before the relay") is None


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


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_claimed_commit_is_kept_where_the_budget_is_zero(tmp_path):
    """ACC-DATA-009: an attributed commit is not a budget line.

    This is the shape that tells `attributed + rest[:budget - len(attributed)]`
    apart from `(attributed + rest)[:budget]`. A RUNNING leg's baton claiming a
    commit contributes no EVENT - a baton is skipped as a landing while its leg
    is still running, and a first leg has no previous landing to start from -
    so the relay has records, a window, and a budget of ZERO, while the
    repository confirms the commit the baton claims. The claim is the evidence,
    so the entry is there.

    The test above this one cannot see the difference: it has 250 events
    against 60 claims, and every budget it computes is larger than the number
    of claims, so truncating the claims with it removes nothing. Here the
    budget is smaller than the claims, which is the only arrangement in which
    the two expressions disagree.
    """
    relay_dir = _one_leg_in(tmp_path / "proj", commits=3, baton=False)
    project = relay_dir.parent
    claimed = _short_sha(project)
    _land(relay_dir, "alpha", NOW - 10, claimed)

    model = relay_model.build(relay_dir, now=NOW)
    commits = entries_of(model, "commit")
    attributed = [e for e in commits if e["leg"]]
    # The premise, asserted rather than assumed: a relay one leg in derives no
    # event at all, so the budget is zero and cannot buy this commit.
    assert relay_events(model) == [], relay_events(model)
    assert len(attributed) > len(relay_events(model))
    assert [e["commit"] for e in commits] == [claimed], [e["m"] for e in commits]
    assert commits[0]["leg"] == "alpha"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_claimed_commits_outnumbering_the_budget_are_all_kept(tmp_path):
    """The same rule where the budget is real but too small (ACC-DATA-009).

    Three legs in flight at once, each holding a baton that claims a commit.
    Two of them have a previous landing to start from, so the relay derives two
    events and buys two unattributed commits with them - and it confirms three
    claims. Every claim appears; the budget is spent on the remainder, which
    here is nothing.

    A zero budget alone would leave `(attributed + rest)[:budget]` half caught:
    a mutant that special-cased the empty case would pass it. This one needs
    the rule itself.
    """
    project = tmp_path / "three-in-flight"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    legs = ["alpha", "beta", "gamma"]
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "three-in-flight",
         "stages": [{"id": "S1", "legs": legs}],
         "legs": [{"id": leg, "stage": "S1", "status": "running"} for leg in legs]}))
    (relay_dir / "state.json").write_text(json.dumps({}))

    _git(project, "init", "-q", "-b", "main")
    _git(project, "commit", "-q", "--allow-empty", "-m", "chore: the project existed first",
         when=NOW - 9000)
    _git(project, "checkout", "-q", "-b", "feat/the-run")
    claimed = {}
    for n, leg in enumerate(legs):
        _git(project, "commit", "-q", "--allow-empty",
             "-m", f"{leg}: the leg's own work", when=NOW - 5000 + n * 100)
        claimed[leg] = _short_sha(project)
    for n, leg in enumerate(legs):
        _land(relay_dir, leg, NOW - 4000 + n * 100, claimed[leg])

    model = relay_model.build(relay_dir, now=NOW)
    commits = entries_of(model, "commit")
    credited = {e["commit"]: e["leg"] for e in commits if e["leg"]}
    events = relay_events(model)
    # The premise: fewer events than claims, so a budget spent on attribution
    # has to discard one of them.
    assert 0 < len(events) < len(claimed), (len(events), len(claimed))
    assert credited == {sha: leg for leg, sha in claimed.items()}, \
        sorted(set(claimed.values()) - set(credited))


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
    """The third door. `trunk` is the ONLY ref this repository has - no `main`,
    no `master`, no `origin/*` and no second branch - so there is nothing to
    narrow the walk with either. The records are the only bound left and they
    have to be enough on their own, which is what the simplified rule says for
    every commit nobody claims."""
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
        {"relay": "many", "stages": [{"id": "S1", "legs": [leg["id"] for leg in legs]}],
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
    commits = relay_model._relay_commits(relay_dir, project, records[1], {})
    assert len(commits) == 40

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
    # HEAD is `main` itself, so the walk bound is HEAD's own ref and narrows
    # nothing: the records are the whole window for what nobody claims.
    assert relay_model._default_branch_refs(relay_dir) == ["refs/heads/main"]
    assert relay_model._git_log(
        relay_dir, exclude=relay_model._default_branch_refs(relay_dir)) == []
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
def test_a_claimed_commit_past_the_walk_bound_is_fetched_by_name(
        past_the_walk_bound):
    """INVERTED 2026-08-26. `first`'s commit is past the walk's own cap, so no
    walk this module can afford will ever return it - and the cap is a bound on
    a WALK, which ACC-DATA-009 no longer lets decide what belongs. The claim is
    confirmed by the repository, so the commit is asked for BY NAME and the
    entry is there with its subject line, its time and its leg. What the cap
    used to cost the log was that entry, with the runner row beside it still
    naming the sha; the two panes now say the same thing at any cap."""
    relay_dir, oldest, newest = past_the_walk_bound
    model = relay_model.build(relay_dir, now=NOW)
    named = {e["commit"]: e for e in entries_of(model, "commit")}
    assert oldest in named, sorted(named)[:5]
    assert named[oldest]["leg"] == "first"
    assert named[oldest]["m"].endswith("run work 0")     # the subject, not a stub
    assert newest in named
    landings = [e for e in model["log"]
                if e["kind"] == "baton" and e["leg"] == "first"]
    assert [e["commit"] for e in landings] == [oldest]
    assert {r["leg"]: r["commit"] for r in model["runners"]}["first"] == oldest
    assert len(model["log"]) <= relay_model.LOG_MAX_ENTRIES


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("cap", [
    relay_model.LOG_MAX_COMMITS + BEYOND_THE_WALK - 1,
    relay_model.LOG_MAX_COMMITS + BEYOND_THE_WALK])
def test_no_cap_on_the_walk_can_decide_whether_a_claim_is_in_the_log(
        past_the_walk_bound, monkeypatch, cap):
    """The boundary itself, one commit either side of it. This test used to
    assert that the claimed commit appeared with the cap and vanished without
    it; that is precisely a bound on a walk deciding what belongs, and the
    2026-08-26 simplification forbids it. The same relay, the same batons, the
    same window, the cap either side of the commit - and the claim is in the
    log either way, still credited to `first`."""
    relay_dir, oldest, _newest = past_the_walk_bound
    monkeypatch.setattr(relay_model, "LOG_MAX_COMMITS", cap)
    model = relay_model.build(relay_dir, now=NOW)
    named = {e["commit"]: e["leg"] for e in entries_of(model, "commit")}
    assert named.get(oldest) == "first", sorted(named)[:5]


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


# --------------------------------------------------------------------------
# THE CLAIM IS THE EVIDENCE, ON EVERY TOPOLOGY (ACC-DATA-009, simplified
# 2026-08-26 after a fifth failure)
#
#   > A commit a baton claims and the repository confirms is this run's work.
#   > Full stop - no window, no floor, no branch point. A commit no leg claims
#   > is bounded by the relay's earliest record. The branch point survives only
#   > as the outer bound of the `git log` WALK - a performance bound on how far
#   > back to look, never a decision about what belongs.
#
# WHY THIS SECTION EXISTS AT ALL, AND WHY IT KEPT THE MATRIX. Five legs each
# defined the branch point more carefully than the last, and a judge falsified
# each one with a repository topology the previous had not seen: a plain clone,
# `origin/HEAD` naming the run's own branch, a trunk called `develop` or
# `trunk`, and finally the coach merging this relay's branch into `main`
# mid-run, which made the merge-base against the trunk HEAD itself and emptied
# the log of every commit twenty runner rows were still naming. A rule that
# depends on topology is the wrong rule, so the matrix below now asserts the
# INVARIANT rather than the branch point: on every one of these topologies the
# claimed commit is in the log, and the log and the runner rows say the same
# thing.
#
# So these build the ref topologies DELIBERATELY - with `update-ref`,
# `symbolic-ref` and a real merge, and once with a real `git clone` - rather
# than taking whatever `git init` happens to leave behind.
#
# THE FIXTURE'S LOAD-BEARING DETAIL: alpha's commit is 60 s OLDER than alpha's
# baton, because a runner commits before it writes one. It therefore predates
# every record the relay has, so nothing but the claim can admit it - which is
# exactly what the simplified rule says admits it, on any topology at all.
# --------------------------------------------------------------------------

def _branch_point_relay(project, base="main", branch="feat/the-run"):
    """A relay one leg in, on a branch of its own, claiming its own commit."""
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "branch-point",
         "stages": [{"id": "S1", "legs": ["alpha", "beta"]}],
         "legs": [{"id": "alpha", "stage": "S1", "status": "done"},
                  {"id": "beta", "stage": "S1", "status": "running"}]}))
    _git(project, "init", "-q", "-b", base)
    (project / "README").write_text("the project\n")
    _git(project, "add", "README", when=NOW - 9000)
    _git(project, "commit", "-q", "-m", "before: the project existed first",
         when=NOW - 9000)
    _git(project, "checkout", "-q", "-b", branch)
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "alpha: the first leg's own work", when=NOW - 5060)
    _land(relay_dir, "alpha", NOW - 5000, _short_sha(project))
    return relay_dir


def _remote_ref(project, name, rev):
    """A remote-tracking ref, without needing a remote to fetch from."""
    _git(project, "update-ref", f"refs/remotes/origin/{name}", rev)


def _origin_head(project, name):
    _git(project, "symbolic-ref", "refs/remotes/origin/HEAD",
         f"refs/remotes/origin/{name}")


def _topology_none(project, base, branch):
    """`git init` and nothing else: one local base branch, no remote."""


def _topology_clone(project, base, branch):
    """What `git clone -b <branch>` leaves behind: `origin/HEAD` NAMES the
    cloned branch, and the branch has a remote-tracking ref of its own."""
    _remote_ref(project, branch, "HEAD")
    _remote_ref(project, base, base)
    _origin_head(project, branch)


def _topology_origin_head_on_the_base(project, base, branch):
    """The same refs, with `origin/HEAD` naming the base branch instead."""
    _remote_ref(project, branch, "HEAD")
    _remote_ref(project, base, base)
    _origin_head(project, base)


def _topology_only_the_branchs_own_remote(project, base, branch):
    """The branch's own remote-tracking ref and no other remote ref at all."""
    _remote_ref(project, branch, "HEAD")


def _topology_merged_into_the_trunk(project, base, branch):
    """THIS REPOSITORY, on the morning of 2026-08-26. The coach merged the
    run's branch into the trunk mid-run and the trunk moved on, so every commit
    HEAD carries is reachable from the trunk as well: the merge-base against it
    IS HEAD, `HEAD --not <trunk>` walks nothing at all, and the run reads as
    having added nothing. In place the log fell from 18 commit entries to 5
    while twenty runner rows went on naming twenty commits."""
    _git(project, "checkout", "-q", base)
    _git(project, "merge", "-q", "--no-ff", "-m", f"merge: {branch} into {base}",
         branch, when=NOW - 4500)
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "trunk: moved on after the merge", when=NOW - 4400)
    _git(project, "checkout", "-q", branch)
    _remote_ref(project, base, base)
    _origin_head(project, base)


TOPOLOGIES = {
    "no remote at all": _topology_none,
    "origin/HEAD names the run's own branch": _topology_clone,
    "origin/HEAD names the base branch": _topology_origin_head_on_the_base,
    "only the branch's own remote-tracking ref": _topology_only_the_branchs_own_remote,
    "the branch is already merged into its trunk": _topology_merged_into_the_trunk,
}


def _built(tmp_path, topology, base):
    branch = "feat/the-run"
    relay_dir = _branch_point_relay(tmp_path / "proj", base=base, branch=branch)
    TOPOLOGIES[topology](relay_dir.parent, base, branch)
    return relay_model.build(relay_dir, now=NOW)


# Four base-branch names and one that is nobody's convention. A branch point is
# a property of the TOPOLOGY - where the run's branch forked from the rest of
# the repository - and not of what the branch it forked from is called. Two of
# these were reported by the round-4 code judge: on `develop` and on `trunk`
# the leg's own claimed commit left the log entirely.
BASE_NAMES = ["main", "master", "develop", "trunk", "zx9-nobodys-convention"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("base", BASE_NAMES)
@pytest.mark.parametrize("topology", sorted(TOPOLOGIES))
def test_a_leg_claimed_commit_survives_every_ref_topology(tmp_path, topology, base):
    """One relay, one leg, one claimed commit, five ref topologies and five
    base branch names. The commit predates every record, so nothing but the
    claim can admit it - and the claim is evidence on every topology, which is
    the whole of the rule this leg implements. Each of these five broke a
    previous leg's branch point; none of them can reach the claim."""
    model = _built(tmp_path, topology, base)
    entry = commit_named(model, "alpha: the first leg's own work")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "alpha"
    assert commit_named(model, "before: the project existed first") is None


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("base", BASE_NAMES)
@pytest.mark.parametrize("topology", sorted(TOPOLOGIES))
def test_the_log_never_contradicts_the_runner_rows(tmp_path, topology, base):
    """THE INVARIANT, asserted directly rather than through any one topology.

    A runner row and a commit entry are two panes reporting the same fact, and
    a model that contradicts itself is a worse failure than one that omits: the
    clone defect had 18 rows naming a commit the log did not carry. Where the
    run owns a branch, the two sets are the same set."""
    model = _built(tmp_path, topology, base)
    claimed = {r["commit"] for r in model["runners"] if r["commit"]}
    attributed = {e["commit"] for e in entries_of(model, "commit") if e["leg"]}
    assert claimed, "the fixture's leg claims a commit"
    assert claimed == attributed, model["warnings"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_repository_with_no_other_ref_keeps_the_claim_all_the_same(tmp_path):
    """INVERTED 2026-08-26, by the contract's own simplification. With the base
    branch deleted the run's branch is the only ref there is, so there is no
    branch point and nothing to narrow the walk with. Under the rule this
    replaces, that cost alpha its commit - it predates alpha's baton, and the
    record floor governed claims too. Under the rule now in force the claim IS
    the evidence, so the commit stays and the pane stops disagreeing with the
    runner row beside it."""
    relay_dir = _branch_point_relay(tmp_path / "proj")
    _git(relay_dir.parent, "branch", "-q", "-D", "main")
    assert _git(relay_dir.parent, "for-each-ref", "--format=%(refname)"
                ).stdout.split() == ["refs/heads/feat/the-run"]
    assert relay_model._default_branch_refs(relay_dir) == []
    model = relay_model.build(relay_dir, now=NOW)
    entry = commit_named(model, "alpha: the first leg's own work")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "alpha"
    # ...and the project's own history is still nobody's: it is unclaimed, and
    # the record floor is what bounds an unclaimed commit.
    assert commit_named(model, "before: the project existed first") is None


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_detached_head_at_the_branch_tip_still_owns_its_branch(tmp_path):
    """HEAD on no branch, at the tip of the branch the run has been landing on.
    Nothing about the repository changed but the symbolic ref, so nothing about
    the run's window may change either: the branch ref at HEAD names where the
    run is and is not a fork away from it."""
    relay_dir = _branch_point_relay(tmp_path / "proj")
    _git(relay_dir.parent, "checkout", "-q", "--detach", "HEAD")
    assert _git(relay_dir.parent, "rev-parse", "--abbrev-ref",
                "HEAD").stdout.strip() == "HEAD"
    model = relay_model.build(relay_dir, now=NOW)
    entry = commit_named(model, "alpha: the first leg's own work")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "alpha"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_origin_head_naming_the_run_s_own_branch_empties_the_walk_not_the_log(
        tmp_path):
    """The seam the clone defect lived at, asserted at the seam. `origin/HEAD`
    resolves to the run's own branch after `git clone -b <branch>`, so the
    narrowed walk excludes HEAD itself and comes back with NOTHING. That used
    to be the answer; now it is only a walk that failed, and the claim is
    fetched by name regardless."""
    relay_dir = _branch_point_relay(tmp_path / "proj")
    project = relay_dir.parent
    _topology_clone(project, "main", "feat/the-run")
    refs = relay_model._default_branch_refs(relay_dir)
    assert "refs/remotes/origin/HEAD" in refs
    assert relay_model._git_log(relay_dir, exclude=refs) == [], "the walk sees nothing"

    model = relay_model.build(relay_dir, now=NOW)
    entry = commit_named(model, "alpha: the first leg's own work")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "alpha"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_sibling_branch_on_the_run_s_own_line_keeps_the_window_open(tmp_path):
    """The live agent-service relay's shape. Each leg commits on a branch of
    its own and the run's branch collects them, so refs sit ON the run's own
    line - one at HEAD, one a commit back. A previous leg read the contract's
    'nearest ref' literally over every ref in the repository, took the nearest
    of these as the branch point, closed the window to nothing and dropped
    every leg-claimed commit while 15 rows went on claiming them. Neither half
    of that can happen now: the walk is narrowed by the DEFAULT-branch refs
    only, and a claim does not depend on the walk at all."""
    relay_dir = _branch_point_relay(tmp_path / "proj")
    project = relay_dir.parent
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "beta: the second leg's own work", when=NOW - 4060)
    _land(relay_dir, "beta", NOW - 4000, _short_sha(project))
    _git(project, "update-ref", "refs/heads/leg/alpha", "HEAD~1")
    _git(project, "update-ref", "refs/heads/leg/beta", "HEAD")

    model = relay_model.build(relay_dir, now=NOW)
    attributed = {e["leg"] for e in entries_of(model, "commit") if e["leg"]}
    assert attributed == {"alpha", "beta"}, [e["m"] for e in entries_of(model, "commit")]
    claimed = {r["commit"] for r in model["runners"] if r["commit"]}
    assert claimed == {e["commit"] for e in entries_of(model, "commit") if e["leg"]}


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_clone_of_the_repository_shows_the_log_the_source_tree_shows(tmp_path):
    """The defect end to end, on a real clone rather than a constructed ref
    shape - because `git clone -b <branch>` is how anyone obtains a repository,
    and the Progress Log was empty on a clone of the repository the TUI ships
    in. The relay's `dashboard.json` names the SOURCE project, which is what a
    coach's dashboard says and what a clone inherits: unusable there, so the
    clone reads its own project and says so."""
    src = tmp_path / "src"
    relay_dir = _branch_point_relay(src)
    (relay_dir / "dashboard.json").write_text(json.dumps({"path": str(src)}))
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", "-b", "feat/the-run", str(src), str(clone))
    shutil.copytree(relay_dir, clone / ".relay")
    assert _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD"
                ).stdout.strip() == "refs/remotes/origin/feat/the-run"

    here = relay_model.build(relay_dir, now=NOW)
    there = relay_model.build(clone / ".relay", now=NOW)
    assert [e["m"] for e in entries_of(here, "commit")], "the source tree's log"
    assert ([e["m"] for e in entries_of(there, "commit")]
            == [e["m"] for e in entries_of(here, "commit")])
    assert [w for w in there["warnings"] if "`path`" in w], there["warnings"]
    assert [w for w in here["warnings"] if "`path`" in w] == []


# --------------------------------------------------------------------------
# a `path` the model cannot use is warned about, not silently obeyed
# (ACC-DATA-009)
#
# `_in_a_repo` clamps a written `path` that is not the relay's own project back
# to the relay directory, which is the right bound and the wrong silence: the
# read then finds no repository, the log carries no commit, no baton's claim is
# settled - and `relay.path` goes on reporting the value as though it were in
# use. A value that names a directory the model may not read from is as
# unusable as one that names no directory at all, and the model warns about
# every other unusable field it reads.
# --------------------------------------------------------------------------

@pytest.fixture
def other_project(tmp_path):
    """A second real repository holding every commit the relay's batons claim.

    The worst shape of the defect: a path that is a directory, that is a
    repository, and that answers every question asked of it - with another
    project's history.
    """
    return tmp_path / "elsewhere"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("kind", ["an ancestor", "a home directory",
                                  "another repository"])
def test_a_path_that_is_not_the_relays_own_project_is_warned_about(
        home_relay, other_project, kind):
    """Three values a coach can write that the model cannot read from, and one
    warning apiece. Each is a directory, so none of them is caught by the
    `not a directory` warning that already exists."""
    home = home_relay.parent.parent
    if kind == "another repository":
        _git(home, "clone", "-q", str(home_relay.parent), str(other_project))
    written = {"an ancestor": str(home),
               "a home directory": "~",
               "another repository": str(other_project)}[kind]
    (home_relay / "dashboard.json").write_text(json.dumps({"path": written}))

    model = relay_model.build(home_relay, now=NOW)
    said = [w for w in model["warnings"] if "`path`" in w]
    assert len(said) == 1, model["warnings"]
    assert written in said[0], said[0]
    assert model["relay"]["path"] == written        # reported as the coach wrote it


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_path_the_model_cannot_use_falls_back_to_the_relays_own_project(
        home_relay):
    """Warned about AND ignored, exactly as a `path` that names no directory
    is: the read falls back to the relay's own project, which is the widest
    bound `_in_a_repo` would have allowed the written value anyway. Silence
    left the log empty and the claims unsettled instead."""
    (home_relay / "dashboard.json").write_text(json.dumps(
        {"path": str(home_relay.parent.parent)}))
    model = relay_model.build(home_relay, now=NOW)
    entry = commit_named(model, "alpha: the leg's work")
    assert entry is not None, model["warnings"]
    assert entry["leg"] == "alpha"
    rows = {r["leg"]: r for r in model["runners"]}
    assert rows["alpha"]["commit"] == entry["commit"]
    assert rows["beta"]["commit"] is None, "deadbee is not an object here"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_two_shapes_a_relay_actually_has_are_not_warned_about(
        home_relay, repo_relay):
    """The bound narrows, so what it must not break is asserted beside it: a
    live relay is `<project>/.relay` and a fixture relay is its own project,
    and a `path` naming either of those is a coach getting it right."""
    for relay_dir, written in ((home_relay, str(home_relay.parent)),
                               (repo_relay, str(repo_relay))):
        (relay_dir / "dashboard.json").write_text(json.dumps({"path": written}))
        model = relay_model.build(relay_dir, now=NOW)
        assert [w for w in model["warnings"] if "`path`" in w] == [], written


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("base", ["main", "trunk"])
def test_a_remote_tracking_ref_behind_its_own_branch_is_still_the_run_s(
        tmp_path, base):
    """The ordinary state of a branch with unpushed work, and the only shape
    where where a ref POINTS cannot say whose it is: `origin/<branch>` and the
    `origin/HEAD` that names it both sit at a commit HEAD has moved past, so
    only the NAME is left to read. Taken as branch points they open the window
    after the first leg's commit, and that leg's row goes on claiming what the
    log dropped.

    Both halves bite, and on different bases. `origin/HEAD` is one of the
    default-branch refs, so it reaches the walk wherever the repository has
    one; `origin/<branch>` reaches it where the repository has none."""
    relay_dir = _branch_point_relay(tmp_path / "proj", base=base)
    project = relay_dir.parent
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "beta: the second leg's own work", when=NOW - 4060)
    _land(relay_dir, "beta", NOW - 4000, _short_sha(project))
    _remote_ref(project, "feat/the-run", "HEAD~1")     # where it was last pushed
    _remote_ref(project, base, base)
    _origin_head(project, "feat/the-run")

    model = relay_model.build(relay_dir, now=NOW)
    assert {e["leg"] for e in entries_of(model, "commit") if e["leg"]} == \
        {"alpha", "beta"}, [e["m"] for e in entries_of(model, "commit")]
    claimed = {r["commit"] for r in model["runners"] if r["commit"]}
    assert claimed == {e["commit"] for e in entries_of(model, "commit") if e["leg"]}


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_branch_another_branch_has_absorbed_keeps_everything_it_earned(tmp_path):
    """INVERTED 2026-08-26. A ref that reaches every commit HEAD has: `next`
    merged the run's branch and moved on, so the merge-base with it is HEAD
    itself and every narrowed walk comes back empty. Under the rule this
    replaces, alpha's claimed commit was then floored at the relay's records
    and lost, because a runner commits before it writes its baton; that is the
    shape the coach's own merge into `main` put this repository into, and it is
    what emptied its Progress Log. The claim now stands on its own evidence,
    and the unclaimed commits are still bounded by the records."""
    relay_dir = _branch_point_relay(tmp_path / "proj", base="trunk")
    project = relay_dir.parent
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "after: the relay did this while it ran", when=NOW - 4000)
    _git(project, "branch", "-q", "next")
    _git(project, "checkout", "-q", "next")
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "next: carried the run's work onward", when=NOW - 3000)
    _git(project, "checkout", "-q", "feat/the-run")
    assert _git(project, "branch", "--contains", "HEAD", "--format=%(refname)"
                ).stdout.split() == ["refs/heads/feat/the-run", "refs/heads/next"]

    model = relay_model.build(relay_dir, now=NOW)
    entry = commit_named(model, "alpha: the first leg's own work")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "alpha"
    assert commit_named(model, "after: the relay did this while it ran") is not None
    # `next`'s own commit is not on HEAD's line at all, and the project's
    # history predates every record: neither is this run's.
    assert commit_named(model, "next: carried the run's work onward") is None
    assert commit_named(model, "before: the project existed first") is None


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_stash_is_not_a_bound_on_the_walk(tmp_path):
    """`git stash` writes `refs/stash`, and a stash commit's parent is HEAD.
    Used as a bound on the walk it stops it at HEAD, and a supervisor's
    Progress Log loses its unclaimed commits for exactly as long as somebody
    leaves work stashed. Only the DEFAULT-branch refs bound the walk, so git's
    own bookkeeping - `refs/stash`, `refs/notes/*`, `refs/bisect/*` - cannot
    reach it. Asserted on a repository with no default branch, where a scan
    over every ref would have had nothing else to choose from."""
    relay_dir = _branch_point_relay(tmp_path / "proj", base="trunk")
    project = relay_dir.parent
    (project / "README").write_text("uncommitted work\n")
    _git(project, "stash", "push", "-q", "-m", "work in progress", when=NOW - 4000)
    assert _git(project, "rev-parse", "--verify", "refs/stash").stdout.strip()
    assert relay_model._default_branch_refs(relay_dir) == []

    model = relay_model.build(relay_dir, now=NOW)
    entry = commit_named(model, "alpha: the first leg's own work")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "alpha"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_an_annotated_tag_at_head_is_not_a_bound_on_the_walk(tmp_path):
    """A release cut on the run's own branch. A tag at HEAD used as a walk
    bound stops the walk at HEAD, and an ANNOTATED tag points at a tag object
    rather than at the commit, so a scan over every ref had to peel it before
    it could even tell. Only the default-branch refs bound the walk, so there
    is nothing here to peel and nothing to get wrong."""
    relay_dir = _branch_point_relay(tmp_path / "proj", base="trunk")
    project = relay_dir.parent
    _git(project, "tag", "-a", "-m", "the release", "v1.0", when=NOW - 4000)
    assert _git(project, "cat-file", "-t", "refs/tags/v1.0").stdout.strip() == "tag"
    assert relay_model._default_branch_refs(relay_dir) == []

    model = relay_model.build(relay_dir, now=NOW)
    entry = commit_named(model, "alpha: the first leg's own work")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "alpha"


# --------------------------------------------------------------------------
# THE MODEL NEVER CONTRADICTS ITSELF (ACC-DATA-009, simplified 2026-08-26)
#
# A runner row's `commit` and a commit entry's `leg` are two panes reporting
# one settled fact: `baton["commit"]`, the sha the baton claimed and the
# repository confirmed. Under the rule now in force they cannot disagree by
# construction - the same value feeds both - so the disagreement is asserted
# DIRECTLY rather than through whichever topology last exposed it.
#
# It is asserted this way because every previous fix was a topology fix, and
# the measurement that condemned each of them was always this one: 18 rows
# claiming a commit and 0 commit entries on a clone; 20 rows claiming and 5
# entries in place after the trunk absorbed the branch. Neither shape has to be
# reachable by a test for the invariant to be checkable - it is checkable on
# every relay this suite can read.
# --------------------------------------------------------------------------

# A LIVE RELAY IS AN EXTRA, NEVER A GUARD.
#
# This list used to hold two paths: `REPO / ".relay"`, which is git-ignored and
# therefore absent from every clone, CI checkout and container; and a hardcoded
# `~/Documents/Work/.../agent-service/.relay`, which resolved on one person's
# disk and nowhere else. Both were read behind a `pytest.skip`, so every
# assertion over them was green-by-absence off this laptop - the same defect
# `HAS_GIT` had (84 quiet skips over a live defect) and `ALL_FIXTURES` had
# (90 tests deletable to zero, still green).
#
# The home-directory entry is gone. The relay it named is already frozen at
# `tests/fixtures/agent-service`, and what it proved beyond the freeze - a
# baton claiming a sha no branch here reaches - is frozen too, as
# `corpus_relay_denied`. This repository's own relay stays, DISCOVERED rather
# than named, and is read only in addition to a frozen corpus that is read
# every time. The rule the readings below follow: the frozen corpus carries the
# assertion, a live relay adds drift to it, and no test's whole body sits
# behind whether a directory happens to exist.

#: Where a live relay could be, relative to this repository. Discovered, never
#: named: a path a test spells out is a path that resolves on one machine.
LIVE_RELAY_CANDIDATES = (REPO / ".relay",)


def live_relay_dirs(candidates=LIVE_RELAY_CANDIDATES):
    """Which candidates are relay directories on the machine running this."""
    return [p for p in candidates if (p / "batons").is_dir()]


def test_live_relay_dirs_answers_for_a_candidate_that_is_and_one_that_is_not(
        tmp_path):
    """The discovery itself, tested where a relay can be MADE.

    Otherwise `live_relay_dirs` is the one thing in this file whose behaviour
    depends on the machine reading it: returning `[]` unconditionally would be
    invisible on a clone and would silently delete the live reading everywhere
    else. Here it is asked about two directories that exist because this test
    made them, so the answer is the same on every machine.
    """
    present = tmp_path / "present"
    (present / "batons").mkdir(parents=True)
    absent = tmp_path / "absent"
    absent.mkdir()
    assert live_relay_dirs((present, absent)) == [present]
    assert live_relay_dirs(()) == []
    # And the candidate list is derived from this repository, not typed out.
    assert LIVE_RELAY_CANDIDATES == (REPO / ".relay",)


def _reads_a_repository(relay_dir):
    """Whether the model can confirm a claim for the relay at `relay_dir`.

    The same two steps `build()` takes, in the same order: a `path` a coach
    wrote decides which project is read, and `_in_a_repo` decides whether that
    project holds a repository at all. Outside one, nothing on disk can confirm
    a claim and there are no commit entries to compare rows against.
    """
    relay_dir = Path(relay_dir)
    written = None
    try:
        written = json.loads((relay_dir / "dashboard.json").read_text()).get("path")
    except (OSError, ValueError, AttributeError):
        written = None
    project = relay_model._project_dir(
        written if isinstance(written, str) else None, relay_dir, [])
    return relay_model._in_a_repo(relay_dir, project)


def assert_the_model_agrees_with_itself(model, relay_dir):
    """THE INVARIANT: the commits the rows claim and the commits the log
    attributes are the same commits, in both directions.

    Direction one: every sha a runner row carries is a commit entry. This is
    the failure the check was rewritten for - a row naming work the log says is
    not this run's.

    Direction two: every commit entry credited to a leg that HAS a row is a sha
    that row carries. A leg `legs.json` forgot has a baton and no row, so its
    entry is exempt; nothing else is.

    Sha SETS, not `(leg, sha)` pairs, because two batons claiming one sha is a
    contradiction on disk that the model resolves deliberately - the leg that
    landed first is credited, once.

    RETURNS the arm it took: `"no-log"`, `"dashboard-log"`, `"no-repository"`
    or `"compared"`. Three of the four are early returns, and an early return
    is indistinguishable from a passing comparison in a green run. NO fixture
    reading of this invariant reaches `"compared"` - no fixture holds a `.git`
    of its own - so without the label, twenty parametrised cases report success
    having compared nothing, forever.
    `test_the_agreement_invariant_is_compared_and_not_only_returned_from` is
    what reads the label.
    """
    rows = {r["leg"]: r["commit"] for r in model["runners"] if r["commit"]}
    if model["logSource"] != "derived":
        # ACC-DATA-006: a coach who writes `dashboard.json.log` is quoted
        # verbatim, and their prose is not the model's derivation. There is
        # nothing derived here to agree or disagree with.
        assert [e for e in model["log"] if e.get("kind") == "commit"] == []
        return "dashboard-log" if model["logSource"] == "dashboard" else "no-log"
    entries = {e["commit"]: e["leg"] for e in entries_of(model, "commit") if e["leg"]}
    if not _reads_a_repository(relay_dir):
        assert entries == {}, entries      # nothing here can confirm a claim
        # A ROW MAY still carry one: `_parse_baton` seeds `commit` with the
        # baton's first claim and `_settle_commits` only ever narrows it, so
        # outside a repository the baton's own word stands (its docstring says
        # so in as many words). Asserting `rows == {}` here was tried while
        # closing this leg and is WRONG - it contradicts the documented
        # behaviour on nine of ten fixtures.
        return "no-repository"
    assert set(rows.values()) <= set(entries), \
        sorted(set(rows.values()) - set(entries))
    assert {(leg, sha) for sha, leg in entries.items() if leg in rows} <= \
        {(leg, sha) for leg, sha in rows.items()}
    return "compared"


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_the_log_and_the_rows_agree_on_every_fixture(relay, name):
    """Every fixture on disk, copied and read the way a view reads one."""
    target = relay(name)
    assert_the_model_agrees_with_itself(relay_model.build(target, now=NOW), target)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_the_log_and_the_rows_agree_on_every_fixture_read_in_place(name):
    """The same fixtures read WHERE THEY LIVE - inside this repository, which
    is a real git repository with real commits and none of them theirs.

    What this adds over the copy above is the real path, and the answer it
    asserts is that NONE of the host repository's commits reach a fixture's
    log: `_in_a_repo` stops the walk at the relay's own project, and no fixture
    holds a `.git`. It does NOT settle a claim against a repository, and the
    wording here used to say it did - so the arm is asserted rather than
    narrated. The reading that does settle a claim is
    `test_the_log_and_the_rows_agree_in_the_git_corpus` below.
    """
    target = FIXTURES / name
    arm = assert_the_model_agrees_with_itself(
        relay_model.build(target, now=NOW), target)
    assert arm != "compared", (name, arm)


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_log_and_the_rows_agree_in_the_git_corpus(corpus_relay):
    """The invariant with the comparison actually made (ACC-DATA-009).

    The frozen agent-service batons on a repository that really holds the
    commits they claim: twenty rows, real settled shas, and a log derived from
    the same claims. This is the fixture corpus reading that the twenty
    parametrised cases above cannot be.
    """
    relay_dir, _ = corpus_relay
    model = relay_model.build(relay_dir, now=None)
    assert {r["commit"] for r in model["runners"] if r["commit"]}, model["runners"]
    assert assert_the_model_agrees_with_itself(model, relay_dir) == "compared"


def test_the_agreement_invariant_is_compared_and_not_only_returned_from(
        corpus_relay):
    """Non-vacuity for `assert_the_model_agrees_with_itself` (ACC-DATA-009).

    The helper has three arms and two of them return before comparing
    anything. In a green run an early return and a passing comparison look
    exactly alike, so this counts them: every fixture reading takes an early
    arm - that is a property of the fixtures, stated here rather than
    discovered by a judge - and the corpus reading takes the comparing one. If
    the corpus ever stops reaching a repository, this fails here instead of
    quietly turning every reading of the invariant into an early return.
    """
    arms = {name: assert_the_model_agrees_with_itself(
                relay_model.build(FIXTURES / name, now=NOW), FIXTURES / name)
            for name in ALL_FIXTURES}
    assert "compared" not in set(arms.values()), arms
    # The three fixtures that derive a log at all reach the deepest arm a
    # fixture can reach. Six deriving nothing and one quoting a coach is the
    # rest of the parametrisation, and it is worth knowing that is what those
    # nineteen green cases are.
    assert sum(arm == "no-repository" for arm in arms.values()) == 3, arms
    assert sum(arm == "dashboard-log" for arm in arms.values()) == 1, arms
    assert sum(arm == "no-log" for arm in arms.values()) == 6, arms

    relay_dir, _ = corpus_relay
    assert assert_the_model_agrees_with_itself(
        relay_model.build(relay_dir, now=None), relay_dir) == "compared"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
@pytest.mark.parametrize("base", BASE_NAMES)
@pytest.mark.parametrize("topology", sorted(TOPOLOGIES))
def test_the_log_and_the_rows_agree_on_every_ref_topology(tmp_path, topology, base):
    """The matrix again, through the invariant rather than through the
    fixture's own subject line."""
    relay_dir = _branch_point_relay(tmp_path / "proj", base=base,
                                    branch="feat/the-run")
    TOPOLOGIES[topology](relay_dir.parent, base, "feat/the-run")
    model = relay_model.build(relay_dir, now=NOW)
    assert {r["commit"] for r in model["runners"] if r["commit"]}, "a row claims one"
    assert_the_model_agrees_with_itself(model, relay_dir)


def _file_stamps(relay_dir):
    return sorted((p.name, p.stat().st_mtime)
                  for p in relay_dir.rglob("*") if p.is_file())


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_log_and_the_rows_agree_on_a_real_relay(corpus_relay,
                                                    corpus_relay_denied):
    """The two relays this model is actually pointed at were measured
    contradicting themselves on 2026-08-26 - 20 rows against 5 entries here,
    and 0 entries at all from a clone. Both shapes are frozen: the full graft
    is a relay whose repository confirms every claim, and the denied graft is
    one whose repository confirms all but two.

    Every reading is read-only, in place, with no clock injected: `build()` may
    not write to a relay directory, and this suite may not write to a live one.
    The frozen readings happen on every machine; a live relay, where the
    machine running the suite has one, is read in addition to them.
    """
    read = []
    for relay_dir in (corpus_relay[0], corpus_relay_denied[0]):
        before = _file_stamps(relay_dir)
        model = relay_model.build(relay_dir, now=None)
        assert {r["commit"] for r in model["runners"] if r["commit"]}, relay_dir
        assert assert_the_model_agrees_with_itself(model, relay_dir) == "compared"
        assert _file_stamps(relay_dir) == before, f"build() wrote to {relay_dir}"
        read.append(relay_dir)
    # The frozen readings are not optional. An empty loop is a green test that
    # compared nothing, which is exactly what the two skipped live readings
    # this replaced were.
    assert len(read) == 2, read

    for live in live_relay_dirs():
        before = _file_stamps(live)
        model = relay_model.build(live)
        assert {r["commit"] for r in model["runners"] if r["commit"]}, live
        assert_the_model_agrees_with_itself(model, live)
        assert _file_stamps(live) == before, f"build() wrote to {live}"


# --------------------------------------------------------------------------
# a claim is fetched by NAME, so no walk can lose it (ACC-DATA-009)
# --------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_claimed_commits_answers_for_a_sha_and_says_nothing_for_a_stranger(
        tmp_path):
    """`_claimed_commits` at its own seam. It asks git for objects by name, so
    what it returns is decided by the repository having them and by nothing
    else - no walk, no ref, no branch."""
    relay_dir = _branch_point_relay(tmp_path / "proj")
    sha = _short_sha(relay_dir.parent)
    assert [c[1] for c in relay_model._claimed_commits(relay_dir, [sha])] == [sha]
    assert relay_model._claimed_commits(relay_dir, ["deadbee"]) == []


def test_claimed_commits_spawns_no_process_when_the_walk_missed_nothing(
        tmp_path, monkeypatch):
    """The repaint cost of the guarantee, which is zero in the common case: the
    walk usually reaches every claimed commit, and an empty list is answered
    without asking git anything. `build()` runs once per repaint."""
    asked = []
    monkeypatch.setattr(relay_model, "_git",
                        lambda *a, **k: asked.append(a) or None)
    assert relay_model._claimed_commits(tmp_path, []) == []
    assert asked == []


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_claimed_commit_no_branch_here_reaches_is_still_this_run_s_work(
        tmp_path):
    """Reachability is not a condition, and this is a live shape rather than a
    hypothetical one: the agent-service relay's batons claim `096a713` and
    `5c9caf2`, merges that landed on `develop`, which its own HEAD cannot
    reach. ACC-DATA-009 admits a commit on the claim plus the repository, and
    the repository has the object. The alternative is a runner row naming a sha
    the log calls somebody else's work, which is the contradiction this check
    now forbids in either direction."""
    relay_dir = _branch_point_relay(tmp_path / "proj")
    project = relay_dir.parent
    _git(project, "checkout", "-q", "-b", "develop")
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "beta: landed on develop and never came back", when=NOW - 4060)
    sha = _short_sha(project)
    _git(project, "checkout", "-q", "feat/the-run")
    assert sha not in _git(project, "log", "--format=%h", "HEAD").stdout.split()
    _land(relay_dir, "beta", NOW - 4000, sha)

    model = relay_model.build(relay_dir, now=NOW)
    entry = commit_named(model, "beta: landed on develop and never came back")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "beta"
    assert_the_model_agrees_with_itself(model, relay_dir)


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_clone_of_a_branch_its_trunk_absorbed_reads_the_same_log(tmp_path):
    """BOTH failures this leg closes, in one repository, end to end.

    The trunk absorbed the run's branch and moved on, so `HEAD --not <trunk>`
    walks nothing; and the clone is a real `git clone -b <branch>`, which
    points `origin/HEAD` at the cloned branch. Measured against this repository
    on 2026-08-26 under the rule this replaces: 5 commit entries read in place,
    0 read from the clone, 20 runner rows claiming a commit in both.

    The whole log is compared, not just its commits: `copytree` preserves the
    baton mtimes, so every entry in both must be the same entry."""
    src = tmp_path / "src"
    relay_dir = _branch_point_relay(src)
    _topology_merged_into_the_trunk(src, "main", "feat/the-run")
    # What a coach's dashboard says, and what a clone inherits: the source.
    (relay_dir / "dashboard.json").write_text(json.dumps({"path": str(src)}))
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", "-b", "feat/the-run", str(src), str(clone))
    shutil.copytree(relay_dir, clone / ".relay")
    assert _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD"
                ).stdout.strip() == "refs/remotes/origin/feat/the-run"
    assert _git(clone, "log", "--format=%h", "HEAD", "--not",
                "refs/remotes/origin/main").stdout.split() == []

    here = relay_model.build(relay_dir, now=NOW)
    there = relay_model.build(clone / ".relay", now=NOW)

    def rows(model):
        return [(e["t"], e["kind"], e["m"], e["leg"], e["commit"])
                for e in model["log"]]

    assert entries_of(here, "commit"), "the source tree's log has commits"
    assert rows(there) == rows(here)
    for model, where in ((here, relay_dir), (there, clone / ".relay")):
        assert_the_model_agrees_with_itself(model, where)
    assert [w for w in there["warnings"] if "`path`" in w], there["warnings"]


# --------------------------------------------------------------------------
# four properties nothing here could see until a mutation went green
# (ACC-DATA-009)
#
# Each of these was written because reverting the line it guards left the
# suite green. Three of them are decisions the contract states in so many
# words - the budget is not a bound on attribution, a relay with no records
# has no window, `path` may only narrow the repository bound - and the fourth
# is the seven characters a claim and a commit are compared at.
# --------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_budget_cannot_bound_attribution_when_there_are_no_events(tmp_path):
    """The contract names this defect's exact form: `kept = (attributed +
    rest)[:budget]` is wrong "even though it leaves today's suite green". It
    stays green because every other relay here has more events than claims.

    A relay ONE LEG IN has neither. Its only leg is still running, so its baton
    is a RECORD and not an event entry - the disambiguation ACC-DATA-009 spells
    out - and the budget is zero. A budget that bounds attribution then drops
    the one commit the run has to show for itself, while its runner row goes on
    naming the sha."""
    project = tmp_path / "one-leg"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "one-leg", "stages": [{"id": "S1", "legs": ["alpha"]}],
         "legs": [{"id": "alpha", "stage": "S1", "status": "running"}]}))
    _git(project, "init", "-q", "-b", "main")
    _git(project, "commit", "-q", "--allow-empty", "--allow-empty-message",
         "-m", "before: the project existed first", when=NOW - 9000)
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "alpha: the leg's own work", when=NOW - 5060)
    _land(relay_dir, "alpha", NOW - 5000, _short_sha(project))

    model = relay_model.build(relay_dir, now=NOW)
    assert relay_events(model) == []       # a record on disk, no event entry
    entry = commit_named(model, "alpha: the leg's own work")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "alpha"
    # ...and the budget still buys nothing for the population it does bound.
    assert commit_named(model, "before: the project existed first") is None
    assert_the_model_agrees_with_itself(model, relay_dir)


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_relay_with_no_records_sees_the_project_and_not_just_its_branch(
        tmp_path):
    """The contract's degenerate carve-out, read exactly: a relay that has
    recorded nothing has NO window, and "recent commits are the only story
    there is" under the existing `--max-count` bound. Narrowing that walk to
    the run's own branch is a window by another name - this relay would be
    shown the one commit on its branch and nothing of the project it sits in,
    with no record anywhere saying it should be."""
    project = tmp_path / "fresh"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "fresh", "stages": [{"id": "S1", "legs": ["alpha"]}],
         "legs": [{"id": "alpha", "stage": "S1", "status": "pending"}]}))
    _git(project, "init", "-q", "-b", "main")
    _git(project, "commit", "-q", "--allow-empty", "--allow-empty-message",
         "-m", "main work 0", when=NOW - 9000)
    _git(project, "commit", "-q", "--allow-empty", "-m", "main work 1",
         when=NOW - 8000)
    _git(project, "checkout", "-q", "-b", "feat/fresh")
    _git(project, "commit", "-q", "--allow-empty", "-m", "branch work",
         when=NOW - 7000)

    model = relay_model.build(relay_dir, now=NOW)
    assert relay_events(model) == []          # nothing recorded, no window
    subjects = sorted(e["m"].split(": ", 1)[1] for e in entries_of(model, "commit"))
    assert subjects == ["branch work", "main work 0", "main work 1"], subjects


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_repository_that_abbreviates_wider_than_seven_still_attributes(
        tmp_path):
    """`%h` is not seven characters everywhere. git widens the abbreviation as
    a repository grows, and `core.abbrev` sets it outright - so a commit that
    is not cut to the same seven characters `commit_claims` cuts a claim to
    never equals one. Every commit entry goes unattributed while every runner
    row still names its sha, which is this check's own contradiction. Nothing
    else in this suite can see it: every repository here, including this one,
    abbreviates to seven today."""
    relay_dir = _branch_point_relay(tmp_path / "proj")
    project = relay_dir.parent
    _git(project, "config", "core.abbrev", "12")
    assert len(_git(project, "log", "-1", "--format=%h").stdout.strip()) == 12

    model = relay_model.build(relay_dir, now=NOW)
    entry = commit_named(model, "alpha: the first leg's own work")
    assert entry is not None, [e["m"] for e in entries_of(model, "commit")]
    assert entry["leg"] == "alpha"
    assert len(entry["commit"]) == 7
    assert_the_model_agrees_with_itself(model, relay_dir)


def test_the_repository_bound_may_only_be_narrowed_by_the_project(tmp_path):
    """`_in_a_repo` at its own seam. `project` comes from `dashboard.json.path`
    and is untrusted, so it may NARROW the search for a `.git` and never widen
    it: the two shapes a relay has are its own directory and `<project>/.relay`,
    and anything else clamps back to the relay directory.

    Asserted here rather than only through `build()`, because `_project_dir`
    now refuses a foreign path before this bound is ever reached - so the two
    guards mask each other end to end, and reverting either one alone left the
    suite green."""
    host = tmp_path / "host"
    (host / ".git").mkdir(parents=True)
    relay_dir = host / "a" / "b" / ".relay"
    relay_dir.mkdir(parents=True)

    assert relay_model._in_a_repo(relay_dir, str(host)) is False
    assert relay_model._in_a_repo(relay_dir, "/") is False
    assert relay_model._in_a_repo(relay_dir, str(relay_dir.parent)) is False
    (relay_dir.parent / ".git").mkdir()       # the live `<project>/.relay`
    assert relay_model._in_a_repo(relay_dir, str(relay_dir.parent)) is True
    (relay_dir / ".git").mkdir()              # a relay that is its own project
    assert relay_model._in_a_repo(relay_dir, str(relay_dir)) is True


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_a_commit_with_an_odd_subject_is_still_this_run_s_work(tmp_path):
    """Two subjects git allows and the parser used to drop: one containing the
    field separator this module asks git to use, and one that is empty. A
    dropped line is a parser deciding what belongs, and on a claimed commit
    ACC-DATA-009 no longer permits anything to decide but the claim and the
    repository - so both are in the log, credited, with the subject git
    actually recorded."""
    project = tmp_path / "odd"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "odd", "stages": [{"id": "S1", "legs": ["alpha", "beta"]}],
         "legs": [{"id": "alpha", "stage": "S1", "status": "done"},
                  {"id": "beta", "stage": "S1", "status": "done"}]}))
    _git(project, "init", "-q", "-b", "main")
    _git(project, "commit", "-q", "--allow-empty",
         "-m", "alpha: a subject\x1fwith the separator in it", when=NOW - 5060)
    _land(relay_dir, "alpha", NOW - 5000, _short_sha(project))
    _git(project, "commit", "-q", "--allow-empty", "--allow-empty-message",
         "-m", "", when=NOW - 4060)
    _land(relay_dir, "beta", NOW - 4000, _short_sha(project))

    model = relay_model.build(relay_dir, now=NOW)
    by_leg = {e["leg"]: e for e in entries_of(model, "commit") if e["leg"]}
    assert set(by_leg) == {"alpha", "beta"}, [e["m"] for e in entries_of(model, "commit")]
    assert by_leg["alpha"]["m"].endswith("a subject\x1fwith the separator in it")
    assert by_leg["beta"]["m"].endswith(": ")          # no subject, not invented
    assert_the_model_agrees_with_itself(model, relay_dir)


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_two_batons_claiming_one_sha_credit_the_leg_that_landed_first(tmp_path):
    """A contradiction on disk, not a choice for the model: two batons name the
    same commit. The leg that landed FIRST is credited, so the log says the
    same thing on every build, and both runner rows still carry the sha they
    claim - the invariant compares sha sets for exactly this reason."""
    project = tmp_path / "twice"
    relay_dir = project / ".relay"
    (relay_dir / "batons").mkdir(parents=True)
    (relay_dir / "legs.json").write_text(json.dumps(
        {"relay": "twice", "stages": [{"id": "S1", "legs": ["alpha", "beta"]}],
         "legs": [{"id": "alpha", "stage": "S1", "status": "done"},
                  {"id": "beta", "stage": "S1", "status": "done"}]}))
    _git(project, "init", "-q", "-b", "main")
    _git(project, "commit", "-q", "--allow-empty", "-m", "the one commit",
         when=NOW - 5060)
    sha = _short_sha(project)
    _land(relay_dir, "alpha", NOW - 5000, sha)
    _land(relay_dir, "beta", NOW - 4000, sha)

    model = relay_model.build(relay_dir, now=NOW)
    entry = commit_named(model, "the one commit")
    assert entry is not None and entry["leg"] == "alpha"
    assert {r["leg"]: r["commit"] for r in model["runners"]} == \
        {"alpha": sha, "beta": sha}
    assert_the_model_agrees_with_itself(model, relay_dir)


def test_every_git_read_is_bounded_in_time(tmp_path, monkeypatch):
    """`build()` runs once per repaint inside a 2 s budget, so an unbounded
    `git` is a frozen TUI rather than a slow one. The bound is passed on every
    read, and a read that hits it degrades to "git could not answer" - which is
    the same answer as git being absent, and is already handled everywhere.

    A genuinely hanging `git` is ACC-LIVE-001's business in S4; this is the
    line that makes the bound exist at all, and nothing else here could see it
    go missing."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(relay_model.subprocess, "run", fake_run)
    assert relay_model._git(tmp_path, "rev-parse", "HEAD") is None
    assert [c.get("timeout") for c in calls] == [relay_model.GIT_TIMEOUT]

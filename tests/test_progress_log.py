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

from test_relay_model import (  # noqa: E402,F401  (`relay` is a pytest fixture)
    AGENT_SERVICE_BATON_MTIMES,
    relay,
)

# Later than every recorded baton mtime in the agent-service fixture, so ages
# come out positive. Pinned, so the whole file is deterministic.
NOW = 1787600000.0

HAS_GIT = shutil.which("git") is not None


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


def test_age_is_none_without_a_clock(relay):
    """`now` is the only clock; without it no now-derived field is filled in."""
    model = relay_model.build(relay("agent-service"))
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
# fixture relay's own. Assertions below must not depend on mtimes, since a
# fresh clone stamps them all to checkout time.
# --------------------------------------------------------------------------

FIXTURES = REPO / "tests" / "fixtures"


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


def _git(cwd, *args, when=None):
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

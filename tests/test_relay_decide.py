"""Tests for the coach's deterministic decisions (scripts/relay_decide.py).

Each relay here is written into `tmp_path` by the test that needs it, so the
legs a decision is about sit next to the assertion about them.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import relay_decide  # noqa: E402
import relay_model  # noqa: E402


def leg(lid, stage="S1", status="pending", **extra):
    return {"id": lid, "stage": stage, "status": status, **extra}


def write_relay(root, legs, contract=None, stages=None):
    relay = root / ".relay"
    relay.mkdir()
    if stages is None:
        stages = [{"id": s, "legs": [lg["id"] for lg in legs if lg["stage"] == s]}
                  for s in dict.fromkeys(lg["stage"] for lg in legs)]
    (relay / "legs.json").write_text(json.dumps({"relay": "t", "stages": stages,
                                                 "legs": legs}))
    if contract is not None:
        (relay / "contract.md").write_text("".join(
            f"### {cid} — a check\nBody.\n\n" for cid in contract))
    return relay


def model_of(relay):
    return relay_model.build(relay, now=None)


# --------------------------------------------------------------------------
# paths and conflicts
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,expected", [
    ("src/auth/", "src/auth/session.ts", True),
    ("src/auth", "src/auth/", True),
    ("src/auth/", "src/authz/", False),
    ("./src/a.py", "src/a.py", True),
    ("src/a.py", "src/b.py", False),
    ("", "src/a.py", True),
])
def test_paths_overlap_by_whole_components(a, b, expected):
    assert relay_decide.paths_overlap(a, b) is expected
    assert relay_decide.paths_overlap(b, a) is expected


def test_a_writing_leg_without_touches_conflicts_with_everything():
    silent = {"kind": "impl", "touches": []}
    other = {"kind": "impl", "touches": ["docs/"]}
    assert relay_decide.legs_conflict(silent, other)


def test_a_judge_never_conflicts():
    judge = {"kind": "judge", "touches": []}
    writer = {"kind": "impl", "touches": ["src/"]}
    assert not relay_decide.legs_conflict(judge, writer)


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------

def test_disjoint_legs_fan_out_and_overlapping_ones_wait(tmp_path):
    relay = write_relay(tmp_path, [
        leg("a", touches=["src/a/"]),
        leg("b", touches=["src/b/"]),
        leg("c", touches=["src/a/deep.py"]),
    ])
    result = relay_decide.dispatch(model_of(relay))
    assert result["dispatch"] == ["a", "b"]
    assert result["waiting"] == [{"leg": "c", "reason": "touches overlap a"}]


def test_a_running_leg_holds_its_files(tmp_path):
    relay = write_relay(tmp_path, [
        leg("a", status="running", touches=["src/"]),
        leg("b", touches=["src/x.py"]),
        leg("c", touches=["docs/"]),
    ])
    result = relay_decide.dispatch(model_of(relay))
    assert result["inFlight"] == ["a"]
    assert result["dispatch"] == ["c"]


def test_a_leg_waits_for_its_dependencies_to_land(tmp_path):
    relay = write_relay(tmp_path, [
        leg("a", status="running", touches=["src/a/"]),
        leg("b", dependsOn=["a"], touches=["src/b/"]),
        leg("c", dependsOn=["ghost"], touches=["src/c/"]),
    ])
    waiting = {w["leg"]: w["reason"] for w in
               relay_decide.dispatch(model_of(relay))["waiting"]}
    assert waiting == {"b": "waiting on a",
                       "c": "depends on legs with no entry: ghost"}


def test_judges_wait_for_every_stage_leg_then_run_together(tmp_path):
    legs = [
        leg("a", status="completed", touches=["src/a/"]),
        leg("b", touches=["src/b/"]),
        leg("code-judge-S1", kind="judge"),
        leg("behaviour-judge-S1", kind="judge"),
    ]
    relay = write_relay(tmp_path, legs)
    result = relay_decide.dispatch(model_of(relay))
    assert result["dispatch"] == ["b"]
    assert {w["leg"] for w in result["waiting"]} == {"code-judge-S1",
                                                   "behaviour-judge-S1"}

    legs[1]["status"] = "completed"
    (tmp_path / "later").mkdir()
    relay = write_relay(tmp_path / "later", legs)
    result = relay_decide.dispatch(model_of(relay))
    assert result["dispatch"] == ["code-judge-S1", "behaviour-judge-S1"]


def test_only_the_current_stage_dispatches(tmp_path):
    relay = write_relay(tmp_path, [
        leg("a", status="running", touches=["src/a/"]),
        leg("s2", stage="S2", touches=["src/other/"]),
    ])
    result = relay_decide.dispatch(model_of(relay))
    assert result["stage"] == "S1"
    assert result["dispatch"] == []


def test_a_blocked_leg_is_not_dispatched(tmp_path):
    relay = write_relay(tmp_path, [leg("a", status="blocked", touches=["x/"])])
    result = relay_decide.dispatch(model_of(relay))
    assert result["dispatch"] == []
    assert result["waiting"] == [{"leg": "a", "reason": "marked blocked in legs.json"}]


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------

def test_coverage_names_orphans_duplicates_and_unknown_claims(tmp_path):
    relay = write_relay(tmp_path, [
        leg("a", fulfills=["ACC-X-001", "ACC-X-002"]),
        leg("b", fulfills=["ACC-X-002", "ACC-X-009"]),
        leg("code-judge-S1", kind="judge", fulfills=["ACC-X-003"]),
    ], contract=["ACC-X-001", "ACC-X-002", "ACC-X-003"])
    result = relay_decide.coverage(model_of(relay),
                                   relay_model.contract_ids(relay))
    assert result["orphans"] == ["ACC-X-003"]
    assert result["duplicates"] == {"ACC-X-002": ["a", "b"]}
    assert result["unknown"] == ["ACC-X-009"]


def test_a_missing_contract_fails_coverage(tmp_path):
    relay = write_relay(tmp_path, [leg("a", fulfills=["ACC-X-001"])])
    assert relay_model.contract_ids(relay) is None
    result = relay_decide.coverage(model_of(relay), None)
    assert result["missingContract"] is True


def test_contract_ids_reads_the_real_fixture_contract():
    ids = relay_model.contract_ids(REPO / "tests/fixtures/agent-service")
    assert ids and all(i.startswith("ACC-") for i in ids)
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------
# budget
# --------------------------------------------------------------------------

def test_the_claiming_leg_and_two_repairs_reach_the_budget(tmp_path):
    relay = write_relay(tmp_path, [
        leg("build", fulfills=["ACC-X-001", "ACC-X-002"]),
        leg("fix-1", repairs=["ACC-X-001"]),
        leg("fix-2", repairs=["ACC-X-001"]),
        leg("code-judge-S1", kind="judge", fulfills=["ACC-X-001"]),
    ])
    result = relay_decide.budget(model_of(relay))
    assert result["legs"] == {"ACC-X-001": 3, "ACC-X-002": 1}
    assert result["atBudget"] == {"ACC-X-001": ["build", "fix-1", "fix-2"]}
    assert result["unattributed"] == []


def test_a_leg_naming_a_check_twice_counts_once(tmp_path):
    relay = write_relay(tmp_path, [leg("a", fulfills=["ACC-X-001"],
                                       repairs=["ACC-X-001"])])
    assert relay_decide.budget(model_of(relay))["legs"] == {"ACC-X-001": 1}


def test_a_fix_leg_naming_no_check_is_unattributed(tmp_path):
    """The agent-service relay had four fix legs and a repair count of zero:
    none of them named a check in `repairs`, so the budget could never fire."""
    relay = write_relay(tmp_path, [leg("fix-quietly", kind="fix"),
                                   leg("fix-named", repairs=["ACC-X-001"])])
    assert relay_decide.budget(model_of(relay))["unattributed"] == ["fix-quietly"]
    assert run("budget", "--relay-dir", str(relay)).returncode == 1


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------

def run(*args):
    return subprocess.run([sys.executable, str(REPO / "scripts/relay_decide.py"),
                           *args], capture_output=True, text=True)


def test_cli_exit_codes_name_blockers(tmp_path):
    relay = write_relay(tmp_path, [leg("a", fulfills=["ACC-X-001"])],
                        contract=["ACC-X-001", "ACC-X-002"])
    ok = run("dispatch", "--relay-dir", str(relay))
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["dispatch"] == ["a"]

    gap = run("coverage", "--relay-dir", str(relay))
    assert gap.returncode == 1
    assert json.loads(gap.stdout)["orphans"] == ["ACC-X-002"]

    missing = run("budget", "--relay-dir", str(tmp_path / "nowhere"))
    assert missing.returncode == 2


def test_cli_reads_the_real_fixture():
    result = run("dispatch", "--relay-dir",
                 str(REPO / "tests/fixtures/agent-service"))
    assert result.returncode == 0, result.stderr
    assert "dispatch" in json.loads(result.stdout)


# --------------------------------------------------------------------------
# baton items
# --------------------------------------------------------------------------

BATON = """# Baton — a

**Status**: success
**Commit**: `abc1234`

## Implemented

- not a disposal item

## Left undone

- The Safari path,
  deferred to its own leg.

## Commands run

| Command | Exit |
|---|---|

## Issues discovered

1. **Stale container** at the wrong head.
   Rebuilt it.
2. Driver error reads as a missing database.
- A bullet after the numbers.

## Procedure followed

- Yes.
"""


def test_baton_items_are_the_disposal_sections_one_item_each():
    assert relay_decide.baton_items(BATON) == [
        {"section": "Left undone", "text": "The Safari path, deferred to its own leg."},
        {"section": "Issues discovered",
         "text": "**Stale container** at the wrong head. Rebuilt it."},
        {"section": "Issues discovered",
         "text": "Driver error reads as a missing database."},
        {"section": "Issues discovered", "text": "A bullet after the numbers."},
    ]


def test_a_section_reading_nothing_has_no_items():
    text = "## Left undone\n\n- nothing\n\n## Issues discovered\n\n- None.\n"
    assert relay_decide.baton_items(text) == []
    assert relay_decide.baton_items(None) == []


def test_items_reads_the_real_fixture_baton():
    result = run("items", "credential-parity", "--relay-dir",
                 str(REPO / "tests/fixtures/agent-service"))
    assert result.returncode == 0, result.stderr
    found = json.loads(result.stdout)["items"]
    issues = [i for i in found if i["section"] == "Issues discovered"]
    assert len(issues) == 5
    assert issues[0]["text"].startswith("**The `aihub-pg-test` container")


def test_items_for_a_leg_with_no_baton_is_a_blocker(tmp_path):
    relay = write_relay(tmp_path, [leg("a")])
    result = run("items", "a", "--relay-dir", str(relay))
    assert result.returncode == 1
    assert json.loads(result.stdout)["baton"] is None

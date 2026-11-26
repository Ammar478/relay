"""Execution gates: control state, stage evidence, and attempted-work budgets."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_relay_decide import leg, model_of, write_relay
import relay_decide

CID = "ACC-X-001"


def state(root, **changes):
    data = {"run": {"state": "running"}, "checks": {}}
    data.update(changes)
    (root / "state.json").write_text(json.dumps(data))


def staged(root, verdict=None):
    relay = write_relay(root, [
        leg("build", status="completed", fulfills=[CID]),
        leg("judge", status="completed", kind="judge"),
        leg("next", stage="S2", touches=["next/"], fulfills=["ACC-Y-001"]),
        leg("judge-next", stage="S2", kind="judge"),
    ], contract=[CID, "ACC-Y-001"])
    state(relay, checks={CID: verdict} if verdict is not None else {})
    return relay


def passed(**changes):
    data = {"status": "passed", "stage": "S1", "scopeRevision": 1,
            "markedBy": "judge", "evidence": "evidence/check.txt", "verifiedLegs": ["build"]}
    data.update(changes)
    return data


@pytest.mark.parametrize("mode", ["paused", "blocked", "complete"])
def test_control_state_prevents_new_work_and_preserves_running(tmp_path, mode):
    relay = write_relay(tmp_path, [leg("active", status="running"), leg("queued", touches=["other"])])
    state(relay, run={"state": mode})
    result = relay_decide.dispatch(model_of(relay))
    assert result["dispatch"] == []
    assert result["inFlight"] == ["active"]
    assert result["blockers"]


@pytest.mark.parametrize("bad", [None, [], {}, {"run": {"state": "runnning"}}, {"run": "running"},
                                 {"run": {"state": "running"}, "checks": {CID: None}}])
def test_invalid_execution_state_fails_closed(tmp_path, bad):
    relay = write_relay(tmp_path, [leg("queued")])
    (relay / "state.json").write_text(json.dumps(bad))
    assert relay_decide.dispatch(model_of(relay))["blockers"]


@pytest.mark.parametrize("verdict", [None, passed(status="pending"), passed(status="failed"),
                                    passed(status="blocked"), passed(evidence=""),
                                    passed(markedBy="coach"), passed(verifiedLegs=[]),
                                    passed(scopeRevision=0), passed(scopeRevision=True), passed(scopeRevision=1.0), passed(stage="S2")])
def test_completed_stage_does_not_advance_without_current_evidence(tmp_path, verdict):
    result = relay_decide.dispatch(model_of(staged(tmp_path, verdict)), scope_revision=1)
    assert result["stage"] == "S1"
    assert result["dispatch"] == []
    assert result["blockers"]


def test_verified_stage_advances(tmp_path):
    result = relay_decide.dispatch(model_of(staged(tmp_path, passed())), scope_revision=1)
    assert result["dispatch"] == ["next"]


@pytest.mark.parametrize("approval, allowed", [
    ({}, False), ({"approvedBy": "coach", "decisionRef": "note", "reason": "later"}, False),
    ({"approvedBy": "user", "decisionRef": "request/42", "reason": "defer with known impact"}, True),
])
def test_debt_requires_recorded_user_authority(tmp_path, approval, allowed):
    relay = staged(tmp_path, passed(status="debt", approval=approval))
    result = relay_decide.dispatch(model_of(relay), scope_revision=1)
    assert (result["dispatch"] == ["next"]) == allowed


def test_final_stage_not_complete_with_unverified_checks(tmp_path):
    relay = write_relay(tmp_path, [leg("build", status="completed", fulfills=[CID]),
                                  leg("judge", status="completed", kind="judge")])
    assert not relay_decide.dispatch(model_of(relay))["complete"]
    state(relay, checks={CID: passed()})
    assert relay_decide.dispatch(model_of(relay), scope_revision=1)["complete"]


def repairs(root, statuses):
    work = [leg("build", status="completed", fulfills=[CID])]
    work += [leg(f"fix-{i}", status=status, kind="fix", repairs=[CID], touches=[f"fix/{i}"])
             for i, status in enumerate(statuses)]
    work.append(leg("judge", kind="judge"))
    return write_relay(root, work, contract=[CID])


def test_planned_repairs_do_not_spend_budget_and_only_one_runs(tmp_path):
    relay = repairs(tmp_path, ["pending"] * 4)
    model = model_of(relay)
    assert relay_decide.budget(model)["legs"] == {CID: 1}
    assert relay_decide.dispatch(model)["dispatch"] == ["fix-0"]


def test_third_attempt_allowed_fourth_rejected(tmp_path):
    relay = repairs(tmp_path, ["completed", "pending", "pending"])
    assert relay_decide.dispatch(model_of(relay))["dispatch"] == ["fix-1"]
    plan = json.loads((relay / "legs.json").read_text())
    plan["legs"][2]["status"] = "completed"
    (relay / "legs.json").write_text(json.dumps(plan))
    result = relay_decide.dispatch(model_of(relay))
    assert not result["dispatch"]
    assert any("budget" in item["reason"] for item in result["waiting"])


@pytest.mark.parametrize("status", ["failed", "partial", " failed ", " PARTIAL ", "cancelled", "running"])
def test_started_or_uncertain_attempts_cannot_erase_budget(tmp_path, status):
    relay = repairs(tmp_path, ["completed", status, "pending"])
    model = model_of(relay)
    assert relay_decide.budget(model)["legs"][CID] == 3
    assert "fix-2" not in relay_decide.dispatch(model)["dispatch"]


def test_unattributed_repair_is_not_dispatched(tmp_path):
    relay = write_relay(tmp_path, [leg("fix", kind="fix", repairs=[], touches=["x"])])
    assert not relay_decide.dispatch(model_of(relay))["dispatch"]


def test_repair_invalidates_evidence_for_previous_work(tmp_path):
    relay = repairs(tmp_path, ["completed"])
    plan = json.loads((relay / "legs.json").read_text())
    plan["legs"][-1]["status"] = "completed"
    (relay / "legs.json").write_text(json.dumps(plan))
    state(relay, checks={CID: passed()})
    assert not relay_decide.dispatch(model_of(relay))["complete"]
    state(relay, checks={CID: passed(verifiedLegs=["build", "fix-0"])})
    assert relay_decide.dispatch(model_of(relay))["complete"]


def test_gate_failure_sets_cli_blocker_status(tmp_path):
    result = relay_decide.dispatch(model_of(staged(tmp_path)))
    assert relay_decide._blocked("dispatch", result)


def test_read_only_post_judge_review_does_not_deadlock_judges(tmp_path):
    relay = write_relay(tmp_path, [
        leg("build", status="completed", fulfills=[CID]),
        leg("judge", kind="judge"),
        leg("merge-review", kind="review", dependsOn=["judge"]),
    ])
    assert relay_decide.dispatch(model_of(relay))["dispatch"] == ["judge"]


def test_duplicate_execution_keys_are_rejected(tmp_path):
    relay = write_relay(tmp_path, [leg("queued")])
    (relay / "state.json").write_text('{"run":{"state":"paused","state":"running"}}')
    assert relay_decide.dispatch(model_of(relay))["blockers"]


def test_named_pipe_execution_state_never_blocks(tmp_path):
    import os
    relay = write_relay(tmp_path, [leg("queued")])
    (relay / "state.json").unlink()
    os.mkfifo(relay / "state.json")
    script = "import relay_model; relay_model.execution_state(sys.argv[1])"
    result = subprocess.run([sys.executable, "-c",
                             "import sys; sys.path.insert(0, 'scripts'); " + script, str(relay)],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=3)
    assert result.returncode != 0
    assert "not a regular file" in result.stderr


def test_failed_attempt_can_be_repaired_and_judged_without_reset(tmp_path):
    relay = write_relay(tmp_path, [
        leg("build", status="failed", fulfills=[CID]),
        leg("repair", kind="fix", repairs=[CID], dependsOn=["build"]),
        leg("judge", kind="judge", dependsOn=["build", "repair"]),
    ])
    assert relay_decide.dispatch(model_of(relay))["dispatch"] == ["repair"]
    plan = json.loads((relay / "legs.json").read_text())
    plan["legs"][1]["status"] = "completed"
    (relay / "legs.json").write_text(json.dumps(plan))
    assert relay_decide.dispatch(model_of(relay))["dispatch"] == ["judge"]
    plan["legs"][2]["status"] = "completed"
    (relay / "legs.json").write_text(json.dumps(plan))
    state(relay, checks={CID: passed(verifiedLegs=["build", "repair"])})
    assert relay_decide.dispatch(model_of(relay), scope_revision=1)["complete"]


def test_repair_cannot_run_before_implementation(tmp_path):
    relay = write_relay(tmp_path, [
        leg("build", fulfills=[CID], touches=["src"]),
        leg("repair", kind="fix", repairs=[CID], touches=["other"]),
    ])
    assert relay_decide.dispatch(model_of(relay))["dispatch"] == ["build"]


def test_repair_requires_rejudging_other_checks_in_stage(tmp_path):
    other = "ACC-X-002"
    relay = write_relay(tmp_path, [
        leg("build", status="completed", fulfills=[CID]),
        leg("build-other", status="completed", fulfills=[other]),
        leg("fix", status="completed", kind="fix", repairs=[CID]),
        leg("judge", status="completed", kind="judge"),
    ])
    all_work = ["build", "build-other", "fix"]
    state(relay, checks={
        CID: passed(verifiedLegs=all_work),
        other: passed(verifiedLegs=["build", "build-other"]),
    })
    assert not relay_decide.dispatch(model_of(relay), scope_revision=1)["complete"]
    state(relay, checks={cid: passed(verifiedLegs=all_work) for cid in [CID, other]})
    assert relay_decide.dispatch(model_of(relay), scope_revision=1)["complete"]

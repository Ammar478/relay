"""Acceptance and adversarial scenarios for scope-gated dispatch."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import relay_scope  # noqa: E402

CONTRACT = """### ACC-SAVE-001 — Clarified save action
Save changes is visible; save succeeds; errors remain visible on failure.
Tool: browser
Evidence: save success and failure captures.
"""


@pytest.fixture
def bundle():
    scope = json.loads((REPO / "templates/requirements.json").read_text())
    plan = json.loads((REPO / "templates/legs.json").read_text())
    return scope, plan


def approve(scope, plan):
    scope["review"] = {"reviewer": "independent reviewer",
                       "evidence": "validation/scope.md", "verdict": "passed",
                       "fingerprint": relay_scope.fingerprint(scope, plan, CONTRACT)}


def result(bundle):
    scope, plan = bundle
    approve(scope, plan)
    return relay_scope.validate(scope, plan, CONTRACT)


def test_lite_complete_example(bundle):
    checked = result(bundle)
    assert checked["ready"], checked
    assert checked["workflow"]["mode"] == "lite"


def test_required_outcome_without_acceptance_is_rejected(bundle):
    bundle[0]["requirements"][0]["acceptanceIds"] = []
    checked = result(bundle)
    assert not checked["ready"]
    assert any("REQ-001: missing acceptance" in e for e in checked["errors"])


def test_unadopted_proposal_cannot_be_implemented(bundle):
    bundle[0]["requirements"][0].update(origin="proposed", disposition="excluded")
    checked = result(bundle)
    assert any("unapproved or unknown requirement REQ-001" in e for e in checked["errors"])


def test_nonfunctional_constraint_needs_measurable_threshold(bundle):
    req = bundle[0]["requirements"][0]
    req["kind"] = "nonfunctional"
    assert not result(bundle)["ready"]
    req["verification"]["threshold"] = "No settings lost on failed save"
    assert result(bundle)["ready"]


def test_reversible_inference_uses_existing_authorization(bundle):
    bundle[0]["requirements"][0].update(origin="inferred", decisionRef="relay.md: authorized local implementation choice")
    assert result(bundle)["ready"]


def test_material_unknown_prevents_implementation(bundle):
    bundle[0]["workflow"]["criticalUnknowns"] = ["Who owns persisted settings?"]
    checked = result(bundle)
    assert not checked["ready"]
    assert checked["workflow"]["mode"] == "full"


@pytest.mark.parametrize("facts, mode, check", [
    ({"bounded": False}, "standard", "code and behaviour review"),
    ({"authorization": True}, "full", "permission boundaries"),
    ({"migration": True, "externalContract": True, "multipleSystems": True}, "full", "restore"),
    ({"authorization": True, "requestedMode": "lite"}, "full", "denied and permitted journeys"),
])
def test_risk_controls_process_and_preserves_checks(bundle, facts, mode, check):
    bundle[0]["workflow"].update(facts)
    chosen = result(bundle)["workflow"]
    assert chosen["mode"] == mode
    assert check in chosen["mandatoryChecks"]
    if facts.get("requestedMode") == "lite":
        assert chosen["requestConflict"]


def test_changed_requirement_invalidates_existing_review(bundle):
    scope, plan = bundle
    approve(scope, plan)
    scope["requirements"][0]["statement"] += " and autosave"
    assert not relay_scope.validate(scope, plan, CONTRACT)["ready"]


def test_changed_contract_or_plan_invalidates_review(bundle):
    scope, plan = bundle
    approve(scope, plan)
    assert not relay_scope.validate(scope, plan, CONTRACT + "\nExtra constraint")["ready"]
    plan["legs"][0]["touches"].append("src/unrelated")
    assert not relay_scope.validate(scope, plan, CONTRACT)["ready"]


def test_runtime_progress_does_not_invalidate_scope_review(bundle):
    scope, plan = bundle
    approve(scope, plan)
    plan["legs"][0].update(status="running", sessionId="run-1")
    assert relay_scope.validate(scope, plan, CONTRACT)["ready"]


def test_semantic_rejection_blocks_even_structurally_complete_plan(bundle):
    scope, plan = bundle
    approve(scope, plan)
    scope["review"]["verdict"] = "failed"
    assert not relay_scope.validate(scope, plan, CONTRACT)["ready"]


@pytest.mark.parametrize("mutation, diagnostic", [
    (lambda s, p: p["legs"].append(copy.deepcopy(p["legs"][0])), "duplicate id"),
    (lambda s, p: p["legs"][0]["dependsOn"].append("review-save"), "cyclic"),
    (lambda s, p: p["legs"][0]["dependsOn"].append("missing"), "unknown dependency"),
    (lambda s, p: p["legs"][0]["touches"].append("src/../private"), "canonical"),
    (lambda s, p: p["stages"][0]["legs"].pop(), "membership"),
    (lambda s, p: p.pop("concurrency"), "concurrency"),
    (lambda s, p: s["requirements"][0].update(disposition="deferred"), "user decision"),
    (lambda s, p: s["requirements"][0].update(disposition="unresolved"), "unresolved"),
    (lambda s, p: p["legs"][0].update(requirementIds=[]), "requirementIds"),
    (lambda s, p: p["legs"][0].update(fulfills=[]), "implementation owner"),
])
def test_invalid_plan_is_actionable(bundle, mutation, diagnostic):
    mutation(*bundle)
    checked = result(bundle)
    assert not checked["ready"]
    assert any(diagnostic in error for error in checked["errors"])


def write_bundle(root, bundle):
    scope, plan = bundle
    (root / "state.json").write_text(json.dumps({"run": {"state": "running"}, "checks": {}}))
    (root / "requirements.json").write_text(json.dumps(scope))
    (root / "legs.json").write_text(json.dumps(plan))
    (root / "contract.md").write_text(CONTRACT)


def cli(root, script="relay_decide.py", *args):
    return subprocess.run([sys.executable, str(REPO / "scripts" / script),
                           *args, "--relay-dir", str(root)], text=True, capture_output=True)


def test_cli_draft_review_and_dispatch_end_to_end(tmp_path, bundle):
    write_bundle(tmp_path, bundle)
    draft = cli(tmp_path, "relay_scope.py", "--draft")
    assert draft.returncode == 0, draft.stderr
    blocked = cli(tmp_path, "relay_decide.py", "dispatch")
    assert blocked.returncode == 1
    assert json.loads(blocked.stdout)["dispatch"] == []
    approve(*bundle)
    write_bundle(tmp_path, bundle)
    ready = cli(tmp_path, "relay_decide.py", "dispatch")
    assert ready.returncode == 0, ready.stdout + ready.stderr
    assert json.loads(ready.stdout)["dispatch"] == ["save-label"]


def test_legacy_run_needs_migration_but_remains_readable(tmp_path, bundle):
    write_bundle(tmp_path, bundle)
    (tmp_path / "requirements.json").unlink()
    blocked = cli(tmp_path, "relay_decide.py", "dispatch")
    assert blocked.returncode == 1
    assert json.loads(blocked.stdout)["scope"]["migrationRequired"]
    coverage = cli(tmp_path, "relay_decide.py", "coverage")
    assert coverage.returncode == 0


@pytest.mark.parametrize("content", ["null", "[]", "{", '{"a":1,"a":2}'])
def test_malformed_json_fails_closed(tmp_path, content):
    (tmp_path / "requirements.json").write_text(content)
    checked = relay_scope.check(tmp_path)
    assert not checked["ready"]
    assert "invalid scope inputs" in checked["errors"][0]


@pytest.mark.parametrize("key, value", [
    ("workflow", []), ("requirements", {}), ("journeys", None),
    ("scopeRevision", True), ("review", "passed"),
])
def test_wrong_shapes_fail_closed(tmp_path, bundle, key, value):
    bundle[0][key] = value
    write_bundle(tmp_path, bundle)
    assert not relay_scope.check(tmp_path)["ready"]


def test_missing_independent_review_is_not_ready(bundle):
    scope, plan = bundle
    plan["legs"].pop()
    plan["stages"][0]["legs"].pop()
    assert not result(bundle)["ready"]


def test_full_requires_separate_judges(bundle):
    scope, plan = bundle
    scope["workflow"]["authorization"] = True
    scope["workflow"]["riskAcceptanceIds"] = {"authorization": ["ACC-SAVE-001"]}
    assert not result(bundle)["ready"]
    plan["legs"][1]["reviewRole"] = "code"
    second = copy.deepcopy(plan["legs"][1])
    second.update(id="review-behaviour", reviewRole="behaviour")
    plan["legs"].append(second)
    plan["stages"][0]["legs"].append(second["id"])
    assert result(bundle)["ready"]


@pytest.mark.parametrize("bad_legs", [[None], [True], ["leg"], 7, None, {}])
def test_malformed_leg_rows_are_json_blockers(tmp_path, bundle, bad_legs):
    bundle[1]["legs"] = bad_legs
    write_bundle(tmp_path, bundle)
    checked = cli(tmp_path, "relay_scope.py")
    assert checked.returncode == 1
    assert not json.loads(checked.stdout)["ready"]
    assert "Traceback" not in checked.stderr


def test_later_stage_dependency_is_rejected(bundle):
    scope, plan = bundle
    plan["stages"].append({"id": "S2", "outcome": "Next", "legs": ["later"]})
    plan["legs"].append({"id": "later", "stage": "S2", "kind": "read",
                         "dependsOn": [], "fulfills": [], "touches": []})
    plan["legs"][0]["dependsOn"] = ["later"]
    checked = result(bundle)
    assert any("later stage" in error for error in checked["errors"])


def test_dispatch_reports_malformed_rows_without_display_parser_crash(tmp_path, bundle):
    bundle[1]["legs"] = [None]
    write_bundle(tmp_path, bundle)
    checked = cli(tmp_path, "relay_decide.py", "dispatch")
    assert checked.returncode == 1
    assert json.loads(checked.stdout)["dispatch"] == []
    assert not checked.stderr


def test_sensitive_risk_needs_contract_evidence_links(bundle):
    bundle[0]["workflow"]["authorization"] = True
    checked = result(bundle)
    assert any("riskAcceptanceIds required" in error for error in checked["errors"])
    bundle[0]["workflow"]["riskAcceptanceIds"] = {"authorization": ["ACC-UNKNOWN-001"]}
    checked = result(bundle)
    assert any("unknown risk acceptance" in error for error in checked["errors"])


def test_scope_file_fifo_does_not_hang_dispatch(tmp_path, bundle):
    import os
    write_bundle(tmp_path, bundle)
    (tmp_path / "requirements.json").unlink()
    os.mkfifo(tmp_path / "requirements.json")
    checked = subprocess.run(
        [sys.executable, str(REPO / "scripts/relay_decide.py"), "dispatch",
         "--relay-dir", str(tmp_path)], capture_output=True, text=True, timeout=3)
    assert checked.returncode == 1
    assert "not a regular file" in json.loads(checked.stdout)["scope"]["errors"][0]


@pytest.mark.parametrize("status", [None, 1, True, [], {}, "success-ish"])
def test_malformed_status_cannot_be_normalized_into_a_fresh_attempt(bundle, status):
    bundle[1]["legs"][0]["status"] = status
    checked = result(bundle)
    assert not checked["ready"]
    assert any("recognized execution status" in error for error in checked["errors"])

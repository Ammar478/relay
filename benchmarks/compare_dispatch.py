#!/usr/bin/env python3
"""Targeted scheduler regression comparison, NOT an agent/productivity benchmark."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def fixtures(candidate):
    spec = importlib.util.spec_from_file_location("relay_scope", candidate / "scripts/relay_scope.py")
    sys.path.insert(0, str(candidate / "scripts"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, risk, two_stages in [
        ("simple-edit", None, False), ("existing-feature", None, True),
        ("external-integration", "externalContract", False), ("permissions", "authorization", True),
    ]:
        scope = json.loads((candidate / "templates/requirements.json").read_text())
        plan = json.loads((candidate / "templates/legs.json").read_text())
        cid = scope["requirements"][0]["acceptanceIds"][0]
        scope["sourceRequest"] = f"Synthetic scheduler fixture: {name}; no application is built."
        if name == "existing-feature":
            scope["workflow"]["bounded"] = False
        if risk:
            scope["workflow"][risk] = True
            scope["workflow"]["riskAcceptanceIds"] = {risk: [cid]}
            plan["legs"][1]["reviewRole"] = "code"
            reviewer = copy.deepcopy(plan["legs"][1])
            reviewer.update(id="behaviour-review", reviewRole="behaviour")
            plan["legs"].append(reviewer)
            plan["stages"][0]["legs"].append(reviewer["id"])
        if two_stages:
            req = copy.deepcopy(scope["requirements"][0])
            req.update(id="REQ-002", acceptanceIds=["ACC-NEXT-001"])
            scope["requirements"].append(req)
            plan["stages"].append({"id": "S2", "outcome": "Next outcome", "legs": []})
            for original in list(plan["legs"]):
                new = copy.deepcopy(original)
                new.update(id=original["id"] + "-next", stage="S2", status="pending")
                new["dependsOn"] = [dep + "-next" for dep in original["dependsOn"]]
                if new["kind"] == "impl":
                    new.update(requirementIds=["REQ-002"], fulfills=["ACC-NEXT-001"])
                plan["legs"].append(new)
                plan["stages"][-1]["legs"].append(new["id"])
            for leg in plan["legs"]:
                if leg["stage"] == "S1":
                    leg["status"] = "completed"
        state = {"run": {"state": "running"}, "checks": {}}
        if two_stages:
            state["checks"][cid] = {"stage": "S1", "scopeRevision": 1, "status": "passed",
                                   "markedBy": "behaviour-review" if risk else "review-save",
                                   "evidence": "synthetic-fixture-evidence",
                                   "verifiedLegs": ["save-label"]}
        if name == "external-integration":
            plan["legs"][0]["status"] = "completed"
            for index, status in enumerate(["completed", "pending"]):
                repair = copy.deepcopy(plan["legs"][0])
                repair.update(id=f"repair-{index}", kind="fix", fulfills=[], repairs=[cid],
                              status=status, dependsOn=["save-label"])
                plan["legs"].insert(index + 1, repair)
                plan["stages"][0]["legs"].insert(index + 1, repair["id"])
        contract = "\n".join(
            f"### {cid} — Synthetic acceptance\nTool: fixture\nEvidence: fixture\n"
            for req in scope["requirements"] for cid in req["acceptanceIds"])
        for broken in (False, True):
            s, p, st = copy.deepcopy(scope), copy.deepcopy(plan), copy.deepcopy(state)
            if name == "simple-edit":
                expected = [] if broken else ["save-label"]
                if broken:
                    st["run"]["state"] = "paused"
            elif name == "existing-feature":
                expected = [] if broken else ["save-label-next"]
                if broken:
                    st["checks"][cid]["status"] = "failed"
            elif name == "permissions":
                expected = [] if broken else ["save-label-next"]
                if broken:
                    st["checks"][cid]["scopeRevision"] = 0
            else:
                expected = [] if broken else ["repair-1"]
                if broken:
                    p["legs"][2]["status"] = "completed"
                    repair = copy.deepcopy(p["legs"][2])
                    repair.update(id="repair-2", status="pending")
                    p["legs"].insert(3, repair)
                    p["stages"][0]["legs"].insert(3, "repair-2")
            s["review"] = {"reviewer": "synthetic-fixture", "evidence": "not a semantic review",
                           "verdict": "passed", "fingerprint": module.fingerprint(s, p, contract)}
            yield f"{name}/{'blocked' if broken else 'healthy'}", s, p, st, contract, expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    records = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="relay-dispatch-comparison-") as temp:
        for index, (name, scope, plan, state, contract, expected) in enumerate(fixtures(args.candidate)):
            root = Path(temp) / str(index)
            root.mkdir()
            for filename, data in [("requirements", scope), ("legs", plan), ("state", state)]:
                (root / f"{filename}.json").write_text(json.dumps(data))
            (root / "contract.md").write_text(contract)
            reason = ({"simple-edit": "run is paused", "existing-feature": "passed verdict",
                       "external-integration": "attempt budget", "permissions": "scope revision"}
                      [name.split("/")[0]] if name.endswith("/blocked") else None)
            record = {"case": name, "expectedDispatch": expected, "expectedReason": reason}
            for version, repo in [("baseline", args.baseline), ("candidate", args.candidate)]:
                run = subprocess.run([sys.executable, str(repo / "scripts/relay_decide.py"),
                                      "dispatch", "--relay-dir", str(root)],
                                     capture_output=True, text=True, timeout=15)
                try:
                    result = json.loads(run.stdout)
                except ValueError:
                    result = {"error": run.stderr}
                record[version] = {"exitCode": run.returncode, "result": result,
                                   "matchesExpected": (result.get("dispatch") == expected
                                                       and (reason is None or reason in json.dumps(result)))}
            records.append(record)
    report = {"type": "targeted synthetic scheduler regressions",
              "sourceHashes": {v: {name: hashlib.sha256((repo / "scripts" / name).read_bytes()).hexdigest()
                                  for name in ["relay_decide.py", "relay_model.py"]}
                               for v, repo in [("baseline", args.baseline), ("candidate", args.candidate)]},
              "notMeasured": ["feature quality", "agent latency", "tokens", "human interventions", "48-hour reproduction"],
              "harnessWallSeconds": round(time.monotonic() - started, 3), "cases": records,
              "summary": {v: {"matched": sum(r[v]["matchesExpected"] for r in records), "total": len(records)}
                          for v in ("baseline", "candidate")}}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))
    return 0 if all(r["candidate"]["matchesExpected"] for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())

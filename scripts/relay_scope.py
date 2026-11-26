#!/usr/bin/env python3
"""Validate requirement traceability before dispatch; never certify semantic truth."""
import argparse
import hashlib
import json
import re

import relay_model

MODES = ("lite", "standard", "full")
RISK_CHECKS = {
    "authorization": ["permission boundaries", "denied and permitted journeys"],
    "migration": ["data integrity", "migration", "restore"],
    "externalContract": ["consumer compatibility", "integration failure"],
}
CHECK = re.compile(r"^###\s+(ACC-[A-Z0-9-]+)\b", re.M)


def fingerprint(scope, plan, contract):
    """Bind review to scope/design of work, not mutable execution status."""
    scope = {k: v for k, v in scope.items() if k != "review"}
    plan = {**plan, "legs": [
        {k: v for k, v in leg.items() if k not in ("status", "sessionId")}
        for leg in plan.get("legs", [])
    ]}
    content = json.dumps([scope, plan, contract], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode()).hexdigest()


def workflow(scope):
    """Choose process from explicit impact facts; preserve mandatory risk checks."""
    facts = scope["workflow"]
    reasons, checks = [], ["requirement coverage", "code and behaviour review"]
    for key, required in RISK_CHECKS.items():
        if facts[key]:
            reasons.append(key)
            checks.extend(required)
    if facts["criticalUnknowns"]:
        reasons.append("critical unknowns")
    if reasons or facts["multipleSystems"]:
        mode = "full"
        if facts["multipleSystems"]:
            reasons.append("multiple systems")
    elif not facts["bounded"] or not facts["reversible"]:
        mode = "standard"
        reasons.append("change is not both bounded and reversible")
    else:
        mode = "lite"
        reasons.append("bounded, reversible, known impact")
    requested = facts.get("requestedMode")
    if requested in MODES and MODES.index(requested) > MODES.index(mode):
        mode = requested
        reasons.append("requested process")
    return {"mode": mode, "reasons": reasons, "mandatoryChecks": checks,
            "requestedMode": requested, "requestConflict": bool(
                requested in MODES and MODES.index(requested) < MODES.index(mode))}


def validate(scope, plan, contract, require_review=True):
    errors = []

    def need(ok, message):
        if not ok:
            errors.append(message)

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    def texts(value):
        return isinstance(value, list) and all(text(x) for x in value)

    def rows(value, label):
        if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
            errors.append(f"{label}: expected an array of objects")
            return []
        return value

    need(type(scope.get("schemaVersion")) is int and scope["schemaVersion"] == 1,
         "schemaVersion: expected 1")
    need(text(scope.get("sourceRequest")), "sourceRequest: preserve original request or immutable reference")
    need(type(scope.get("scopeRevision")) is int and scope["scopeRevision"] > 0,
         "scopeRevision: expected positive integer")
    facts = scope.get("workflow", {})
    if not isinstance(facts, dict):
        facts = {}
    valid_facts = all(type(facts.get(k)) is bool for k in
                      ("bounded", "reversible", "multipleSystems", *RISK_CHECKS))
    valid_facts = valid_facts and texts(facts.get("criticalUnknowns"))
    need(valid_facts, "workflow: explicit impact booleans and criticalUnknowns array required")
    need(text(facts.get("rationale")), "workflow: rationale required")
    need(facts.get("requestedMode") in (None, *MODES), "workflow: invalid requestedMode")
    selected = workflow(scope) if valid_facts else None
    if valid_facts:
        need(not facts["criticalUnknowns"], "workflow: resolve critical unknowns or run a bounded discovery task before implementation")

    requirements = rows(scope.get("requirements"), "requirements")
    journeys = rows(scope.get("journeys"), "journeys")
    legs = rows(plan.get("legs"), "legs")
    stages = rows(plan.get("stages"), "stages")
    need(bool(requirements), "requirements: at least one requirement required")
    need(bool(legs), "legs: at least one leg required")

    def index(entries, label):
        found = {}
        for row in entries:
            key = row.get("id")
            if not text(key):
                errors.append(f"{label}: nonempty id required")
            elif key in found:
                errors.append(f"{label}: duplicate id {key}")
            else:
                found[key] = row
        return found

    reqs, journey_map = index(requirements, "requirements"), index(journeys, "journeys")
    leg_map, stage_map = index(legs, "legs"), index(stages, "stages")
    check_ids = CHECK.findall(contract)
    checks = set(check_ids)
    need(bool(checks), "contract: no acceptance checks")
    need(len(check_ids) == len(checks), "contract: duplicate acceptance IDs")
    risk_links = facts.get("riskAcceptanceIds", {})
    risk_links = risk_links if isinstance(risk_links, dict) else {}
    for risk in RISK_CHECKS:
        if facts.get(risk) is True:
            ids = risk_links.get(risk)
            need(texts(ids) and bool(ids), f"workflow.{risk}: riskAcceptanceIds required")
            if texts(ids):
                for cid in ids:
                    need(cid in checks, f"workflow.{risk}: unknown risk acceptance {cid}")
    linked = set()
    for jid, journey in journey_map.items():
        for key in ("actor", "start", "outcome", "failureCases"):
            need(text(journey.get(key)), f"{jid}: {key} required")
    for rid, req in reqs.items():
        for key in ("statement", "sourceRef", "risk"):
            need(text(req.get(key)), f"{rid}: {key} required")
        need(req.get("kind") in ("functional", "nonfunctional"), f"{rid}: invalid kind")
        need(req.get("origin") in ("explicit", "inferred", "proposed"), f"{rid}: invalid origin")
        disposition = req.get("disposition")
        need(disposition in ("required", "deferred", "excluded", "unresolved"), f"{rid}: invalid disposition")
        need(disposition != "unresolved", f"{rid}: unresolved requirement")
        decision = req.get("decisionRef")
        if req.get("origin") == "explicit" and disposition != "required":
            need(text(decision), f"{rid}: user decision required to remove explicit scope")
        if req.get("origin") in ("inferred", "proposed") and disposition == "required":
            need(text(decision), f"{rid}: adoption decision or existing authorization required")
        need(texts(req.get("journeyIds")), f"{rid}: journeyIds must be an array")
        for jid in req.get("journeyIds", []) if texts(req.get("journeyIds")) else []:
            need(jid in journey_map, f"{rid}: unknown journey {jid}")
        acceptance = req.get("acceptanceIds")
        need(texts(acceptance), f"{rid}: acceptanceIds must be an array")
        if disposition != "required":
            continue
        if req.get("kind") == "functional":
            need(bool(req.get("journeyIds")), f"{rid}: functional requirement needs a journey")
        need(bool(acceptance), f"{rid}: missing acceptance IDs")
        for cid in acceptance if texts(acceptance) else []:
            need(cid in checks, f"{rid}: unknown acceptance {cid}")
            linked.add(cid)
        verification = req.get("verification")
        verification = verification if isinstance(verification, dict) else {}
        for key in ("tool", "environment", "evidence"):
            need(text(verification.get(key)), f"{rid}: verification.{key} required")
        if req.get("kind") == "nonfunctional":
            need(text(verification.get("threshold")), f"{rid}: measurable verification.threshold required")
    need(not (checks - linked), f"contract: untraced checks {sorted(checks - linked)}")

    claims = {}
    covered = set()
    for lid, leg in leg_map.items():
        stage = leg.get("stage")
        need(isinstance(stage, str) and stage in stage_map, f"{lid}: unknown stage")
        for key in ("dependsOn", "fulfills", "touches"):
            need(texts(leg.get(key)), f"{lid}: {key} must be an array")
        deps = leg.get("dependsOn", [])
        for dep in deps if texts(deps) else []:
            need(dep in leg_map, f"{lid}: unknown dependency {dep}")
        raw_status = leg.get("status")
        statuses = set().union(*relay_model.STATUS_ALIASES.values()) | {"failed", "partial"}
        need(isinstance(raw_status, str) and raw_status.strip().lower().replace(" ", "_") in statuses,
             f"{lid}: explicit recognized execution status required")
        kind = leg.get("kind")
        need(kind in ("impl", "fix", "judge", "review", "read"), f"{lid}: invalid kind")
        if kind in ("impl", "fix"):
            rids = leg.get("requirementIds")
            need(texts(rids) and bool(rids), f"{lid}: requirementIds required, including foundations")
            for rid in rids if texts(rids) else []:
                need(rid in reqs and reqs[rid].get("disposition") == "required",
                     f"{lid}: unapproved or unknown requirement {rid}")
                covered.add(rid)
            need(texts(leg.get("verification")) and bool(leg.get("verification")),
                 f"{lid}: verification required")
            for path in leg.get("touches", []) if texts(leg.get("touches")) else []:
                need(not path.startswith("/") and ".." not in path.split("/") and "\\" not in path,
                     f"{lid}: touches must use canonical repository-relative paths: {path}")
        for cid in leg.get("fulfills", []) if texts(leg.get("fulfills")) else []:
            need(kind == "impl", f"{lid}: only implementation legs claim acceptance")
            need(cid in checks, f"{lid}: unknown check {cid}")
            claims.setdefault(cid, []).append(lid)
            rids = leg.get("requirementIds", [])
            need(any(cid in reqs[r].get("acceptanceIds", []) for r in rids
                     if isinstance(r, str) and r in reqs) if texts(rids) else False,
                 f"{lid}: {cid} does not belong to its requirements")
    for cid in checks:
        need(len(claims.get(cid, [])) == 1, f"{cid}: expected exactly one implementation owner")
    for rid, req in reqs.items():
        if req.get("disposition") == "required":
            need(rid in covered, f"{rid}: absent from implementation plan")
    for sid, stage in stage_map.items():
        need(text(stage.get("outcome")), f"{sid}: user-visible outcome required")
        members = stage.get("legs")
        need(texts(members), f"{sid}: legs must be an array")
        if texts(members):
            expected = {lid for lid, leg in leg_map.items() if leg.get("stage") == sid}
            need(set(members) == expected and len(members) == len(set(members)),
                 f"{sid}: stage membership mismatch")
        judges = [leg for leg in legs if leg.get("stage") == sid and leg.get("kind") == "judge"]
        roles = [leg.get("reviewRole") for leg in judges]
        for judge in judges:
            need(judge.get("reviewRole") in ("code", "behaviour", "combined"),
                 f"{judge.get('id')}: reviewRole must be code, behaviour or combined")
        if selected and selected["mode"] == "full":
            need("code" in roles and "behaviour" in roles,
                 f"{sid}: Full requires separate code and behaviour judge legs")
        else:
            need("combined" in roles or ("code" in roles and "behaviour" in roles),
                 f"{sid}: independent code and behaviour review required")

    # The existing dispatcher walks legs in order and cannot resolve a dependency
    # on a later stage. Reject that deadlock rather than leaving all work waiting.
    stage_order = {sid: i for i, sid in enumerate(stage_map)}
    previous = -1
    for lid, leg in leg_map.items():
        sid = leg.get("stage")
        position = stage_order.get(sid, -1) if isinstance(sid, str) else -1
        need(position >= previous, f"{lid}: legs must follow stage order")
        previous = max(previous, position)
        for dep in leg.get("dependsOn", []) if texts(leg.get("dependsOn")) else []:
            dependency = leg_map.get(dep, {})
            target = dependency.get("stage")
            if isinstance(target, str) and target in stage_order:
                need(stage_order[target] <= position, f"{lid}: dependency on later stage {dep}")

    # Iterative topological traversal avoids recursion limits on malformed graphs.
    pending = {lid: set(leg.get("dependsOn", [])) for lid, leg in leg_map.items()
               if texts(leg.get("dependsOn"))}
    while pending:
        ready = {lid for lid, deps in pending.items() if not deps}
        if not ready:
            errors.append(f"plan: cyclic or unresolved dependencies {sorted(pending)}")
            break
        pending = {lid: deps - ready for lid, deps in pending.items() if lid not in ready}
    limits = plan.get("concurrency", {})
    limits = limits if isinstance(limits, dict) else {}
    for key in ("runners", "uiJudges", "apiJudges"):
        need(type(limits.get(key)) is int and limits[key] >= 0, f"concurrency.{key}: nonnegative integer required")
    need(type(limits.get("runners")) is int and limits["runners"] > 0, "concurrency.runners: must allow work")
    need(all(type(limits.get(k)) is int for k in ("uiJudges", "apiJudges"))
         and limits.get("uiJudges", 0) + limits.get("apiJudges", 0) > 0,
         "concurrency: must allow review")
    # Malformed rows have already produced diagnostics; do not fingerprint them.
    digest = (fingerprint(scope, plan, contract)
              if isinstance(plan.get("legs"), list) and len(legs) == len(plan["legs"])
              else None)
    review = scope.get("review", {})
    review = review if isinstance(review, dict) else {}
    if require_review:
        need(review.get("fingerprint") == digest, "review: missing or stale scope/plan/contract fingerprint")
        for key in ("reviewer", "evidence"):
            need(text(review.get(key)), f"review: {key} required")
        need(review.get("verdict") == "passed", "review: semantic coverage not approved")
    return {"ready": not errors, "errors": errors, "workflow": selected, "fingerprint": digest,
            "scopeRevision": scope.get("scopeRevision"),
            "semanticCoverage": "recorded review; not proven by this validator"}


def check(relay_dir, require_review=True):
    try:
        inputs = relay_model.scope_inputs(relay_dir)
        if inputs is None:
            return {"ready": False, "migrationRequired": True,
                    "errors": ["requirements.json missing: recover original request and review scope before dispatch"]}
        return validate(*inputs, require_review=require_review)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        return {"ready": False, "errors": [f"invalid scope inputs: {exc}"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relay-dir", default=".relay")
    parser.add_argument("--draft", action="store_true", help="check structure and obtain fingerprint before review")
    args = parser.parse_args(argv)
    result = check(args.relay_dir, require_review=not args.draft)
    print(json.dumps(result, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

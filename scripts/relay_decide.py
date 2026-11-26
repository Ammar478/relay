#!/usr/bin/env python3
"""The coach's deterministic decisions, computed instead of reasoned.

    python3 scripts/relay_decide.py dispatch  [--relay-dir .relay]
    python3 scripts/relay_decide.py coverage  [--relay-dir .relay]
    python3 scripts/relay_decide.py budget    [--relay-dir .relay]
    python3 scripts/relay_decide.py items LEG [--relay-dir .relay]

Why it exists
-------------
Three of the coach's recurring decisions have exactly one right answer given
the files on disk: which legs may run now, whether every check is claimed by
exactly one leg, and whether a check has used up its three repair legs. A coach
reasoning them out on a slow model pays for every one and still gets them
wrong - `ACC-DATA-009` reached 10 repair legs because the count was taken from
memory, not from `legs.json`. These are code's job, not the model's.

`dispatch` also enforces the concurrency ceiling the plan declares in
`legs.json` (`concurrency`, references/environment.md). Disjoint `touches`
keeps two runners out of each other's files and says nothing about the port,
the database and the log file two legs that both start the stack share, so a
ceiling the view merely displays is not a ceiling - this is where it binds.

What it does not decide
-----------------------
Nothing here marks a check, writes a file, or disposes of a handoff item. It
reads through `relay_model.build()`, the only reader of relay files, and
prints JSON the coach acts on. Judgement - what a handoff issue means, whether
a check passed - stays with the coach and the judges.

Exit status: 0 when the answer needs no action beyond following it, 1 when it
names a blocker (an orphaned or double-claimed check, a check at its repair
budget or a fix leg outside it, a leg with no baton, a ceiling already full),
2 when there is no relay to read. Dispatch also returns 1 for missing, invalid
or stale-reviewed scope; diagnostic commands remain available to legacy runs.
"""

import argparse
import json
from pathlib import Path
import re
import sys

import relay_handoff
import relay_model
import relay_scope

#: The baton sections whose every item the coach must dispose of.
DISPOSAL_SECTIONS = ("Left undone", "Issues discovered")

_ITEM_START = re.compile(r"^(?:\d+[.)]|[-*])\s+")
_NOTHING = {"nothing", "none", "n/a", "nothing.", "none."}

#: Legs a check may take - its claiming leg and its repairs - before the coach must put a scope decision to
#: the human instead of writing another (SKILL.md, Phase 6).
FIX_BUDGET = 3

LANDED = "completed"
FINISHED = {"completed", "cancelled"}


def _parts(path):
    return [p for p in path.strip().strip("/").split("/") if p not in ("", ".")]


def paths_overlap(a, b):
    """True when one path is the other or lies inside it, by whole components.

    `src/auth/` stands for everything under it, so it overlaps
    `src/auth/session.ts`; it does not overlap `src/authz/`.
    """
    pa, pb = _parts(a), _parts(b)
    if not pa or not pb:
        return True
    shorter, longer = sorted((pa, pb), key=len)
    return longer[:len(shorter)] == shorter


def legs_conflict(leg, other):
    """True unless the two legs' write sets are provably disjoint.

    A leg of a non-writing kind (`relay_model.NON_WRITING_KINDS` - a judge, a
    review, a read-only fan-out) writes nothing to the work tree, so it never
    conflicts. A leg that writes and declares no `touches` cannot be proven
    disjoint from anything, so it conflicts with everything: running it beside
    another is a guess.
    """
    if (leg["kind"] in relay_model.NON_WRITING_KINDS
            or other["kind"] in relay_model.NON_WRITING_KINDS):
        return False
    if not leg["touches"] or not other["touches"]:
        return True
    return any(paths_overlap(a, b) for a in leg["touches"] for b in other["touches"])


def _attempted(leg):
    # Pending plans do not consume attempts. Cancelled work counts conservatively:
    # cancellation must not erase a possibly started attempt.
    return leg["status"] in ("completed", "running", "cancelled") or (
        (leg["rawStatus"] or "").strip().lower() in ("failed", "partial"))


def _settled(leg):
    return leg["status"] in FINISHED or (
        leg["kind"] in ("impl", "fix")
        and (leg["rawStatus"] or "").strip().lower() in ("failed", "partial"))


def _check_work(leg):
    return set(leg["fulfills"] + leg["repairs"]) if leg["kind"] in ("impl", "fix") else set()


def stage_blockers(model, stage, state, scope_revision=None):
    """Gate completed stages on recorded independent evidence, never leg count."""
    legs = [leg for leg in model["legs"] if leg["stage"] == stage]
    judges = {leg["id"]: leg for leg in legs if leg["kind"] == "judge"}
    errors = []
    if not judges or any(j["status"] != LANDED for j in judges.values()):
        errors.append("independent stage judges have not completed")
    checks = set().union(*(_check_work(leg) for leg in legs)) if legs else set()
    if not checks:
        errors.append("stage has no acceptance checks")
    for cid in sorted(checks):
        record = state.get("checks", {}).get(cid, {})
        if record.get("stage") != stage:
            errors.append(f"{cid}: missing or mismatched stage verdict")
            continue
        if scope_revision is not None and (type(record.get("scopeRevision")) is not int
                                           or record["scopeRevision"] != scope_revision):
            errors.append(f"{cid}: verdict belongs to a different scope revision")
            continue
        if record.get("status") == "debt":
            approval = record.get("approval", {})
            if not (isinstance(approval, dict) and approval.get("approvedBy") == "user"
                    and isinstance(approval.get("decisionRef"), str) and approval["decisionRef"].strip()
                    and isinstance(approval.get("reason"), str) and approval["reason"].strip()):
                errors.append(f"{cid}: debt needs a recorded user decision and reason")
            continue
        judge = record.get("markedBy")
        if not (isinstance(judge, str) and judge in judges and judges[judge]["status"] == LANDED
                and record.get("status") == "passed"
                and isinstance(record.get("evidence"), str) and record["evidence"].strip()):
            errors.append(f"{cid}: independent passed verdict and evidence required")
            continue
        work = [leg for leg in legs if leg["kind"] in ("impl", "fix")]
        supporting = [leg for leg in work if cid in _check_work(leg)]
        verified = record.get("verifiedLegs")
        if (not isinstance(verified, list) or not all(isinstance(x, str) for x in verified)
                or any(not _settled(leg) or leg["id"] not in verified for leg in work)
                or not any(leg["status"] == LANDED for leg in supporting)):
            errors.append(f"{cid}: verdict does not cover all settled stage implementation and repair legs")
    return errors


def current_stage(model, state=None, scope_revision=None):
    """First unfinished OR unaccepted stage, including the final stage."""
    state = state if state is not None else relay_model.execution_state(model["relay"]["relayDir"])
    for stage in dict.fromkeys(leg["stage"] for leg in model["legs"]):
        legs = [leg for leg in model["legs"] if leg["stage"] == stage]
        if any(not _settled(leg) for leg in legs):
            return stage
        if stage_blockers(model, stage, state, scope_revision):
            return stage
    return None


def _repair_blocker(leg, legs, occupied):
    if leg["kind"] != "fix":
        return None
    if not leg["repairs"]:
        return "repair must name acceptance IDs"
    for cid in leg["repairs"]:
        owners = [other for other in legs if cid in other["fulfills"] and other["kind"] == "impl"]
        if len(owners) != 1 or owners[0]["stage"] != leg["stage"]:
            return f"{cid}: repair requires one implementation owner in the same stage"
        if not _settled(owners[0]):
            return f"{cid}: implementation attempt must finish before repair"
        attempts = {other["id"] for other in legs if cid in _check_work(other) and _attempted(other)}
        attempts.update(other["id"] for other in occupied if cid in _check_work(other))
        if len(attempts) >= FIX_BUDGET:
            return f"{cid}: attempt budget {FIX_BUDGET} exhausted; user scope decision required"
        if any(cid in _check_work(other) for other in occupied):
            return f"{cid}: another attempt is in flight"
    return None


def dispatch(model, scope_revision=None):
    """Recommend a batch only after execution control, stage and attempt gates."""
    legs = [leg for leg in model["legs"] if leg["id"]]
    in_flight = [leg for leg in legs if leg["status"] == "running"]
    result = {"stage": None, "inFlight": [leg["id"] for leg in in_flight],
              "dispatch": [], "waiting": [], "ceiling": model["relay"]["concurrency"],
              "blockers": [], "complete": False}
    try:
        state = relay_model.execution_state(model["relay"]["relayDir"])
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        result["blockers"] = [str(exc)]
        return result
    if state["run"]["state"] != "running":
        result["blockers"] = [f"run is {state['run']['state']}; no new dispatch"]
        return result
    stage = current_stage(model, state, scope_revision)
    result["stage"] = stage
    if stage is None:
        result["complete"] = True
        return result
    status = {leg["id"]: leg["status"] for leg in legs}
    batch = []
    for leg in legs:
        if leg["stage"] != stage or leg["status"] != "pending":
            continue
        reason = (_repair_blocker(leg, legs, in_flight + batch)
                  or _why_not(leg, legs, status, in_flight + batch, result["ceiling"]))
        if reason:
            result["waiting"].append({"leg": leg["id"], "reason": reason})
        else:
            batch.append(leg)
    result["dispatch"] = [leg["id"] for leg in batch]
    stage_legs = [leg for leg in legs if leg["stage"] == stage]
    if all(_settled(leg) for leg in stage_legs):
        result["blockers"] = stage_blockers(model, stage, state, scope_revision)
    elif not batch and not in_flight:
        result["blockers"] = ["no runnable work; resolve waiting reasons before retrying"]
    return result


def _over_ceiling(leg, occupied, ceiling):
    """A reason string when `leg` would breach the concurrency ceiling.

    Two legs with disjoint `touches` that both start the stack still share a
    port, a database and a log file, which is why this is a second gate after
    `legs_conflict` and not a refinement of it. Writing legs (impl, fix) count
    against `runners`; everything else against the judge seats.

    RELAY CANNOT TELL A UI JUDGE FROM AN API JUDGE - `legs.json` has no field
    for it - so where both ceilings are declared the conservative sum is used:
    under-dispatching a gate by one shard is a slower round, while two browser
    drivers on one machine's RAM is a round that times out and lies about why.
    """
    limits = ceiling or {}
    if leg["kind"] in ("impl", "fix"):
        limit = limits.get("runners")
        if limit is None:
            return None
        busy = [o for o in occupied if o["kind"] in ("impl", "fix")]
        what = "runner"
    else:
        ui, api = limits.get("uiJudges"), limits.get("apiJudges")
        limit = ((ui + api) if ui is not None and api is not None
                 else ui if ui is not None else api)
        if limit is None:
            return None
        busy = [o for o in occupied if o["kind"] not in ("impl", "fix")]
        what = "judge"
    if len(busy) >= limit:
        return (f"the {what} ceiling is {limit}, with {len(busy)} in flight")
    return None


def _why_not(leg, legs, status, occupied, ceiling):
    raw_status = (leg["rawStatus"] or "").strip().lower().replace(" ", "_")
    known = set().union(*relay_model.STATUS_ALIASES.values())
    if raw_status and raw_status not in known:
        return f"status {raw_status}: diagnose and create a fresh repair leg; do not rerun this attempt"
    if raw_status == "blocked":
        return "marked blocked in legs.json"
    unknown = [d for d in leg["dependsOn"] if d not in status]
    if unknown:
        return f"depends on legs with no entry: {', '.join(unknown)}"
    by_id = {other["id"]: other for other in legs}
    def satisfied(dep):
        other = by_id[dep]
        return status[dep] == LANDED or (
            leg["kind"] == "judge" and other["kind"] in ("impl", "fix") and _settled(other)
        ) or (
            leg["kind"] == "fix" and _settled(other)
            and bool(_check_work(other) & set(leg["repairs"])))
    open_deps = [d for d in leg["dependsOn"] if not satisfied(d)]
    if open_deps:
        return f"waiting on {', '.join(open_deps)}"
    if leg["kind"] == "judge":
        unlanded = [other["id"] for other in legs
                    if other["stage"] == leg["stage"] and other["kind"] in ("impl", "fix", "read")
                    and not _settled(other)]
        if unlanded:
            return f"stage legs not landed: {', '.join(unlanded)}"
    clash = [other["id"] for other in occupied if legs_conflict(leg, other)]
    if clash:
        return f"touches overlap {', '.join(clash)}"
    return _over_ceiling(leg, occupied, ceiling)


def coverage(model, contract):
    """Every contract check claimed by exactly one non-judge leg's `fulfills`.

    `contract` is the id list from `contract.md`; None means there is none,
    which is itself the first thing wrong.
    """
    claims = {}
    for leg in model["legs"]:
        if leg["kind"] == "judge":
            continue
        for cid in leg["fulfills"]:
            claims.setdefault(cid, []).append(leg["id"] or "<unidentified leg>")

    contract = contract or []
    declared = set(contract)
    result = {
        "checks": len(contract),
        "orphans": [cid for cid in contract if cid not in claims],
        "duplicates": {cid: owners for cid, owners in sorted(claims.items())
                       if len(owners) > 1},
        "unknown": sorted(cid for cid in claims if cid not in declared),
    }
    if not contract:
        result["missingContract"] = True
    return result


def budget(model, limit=FIX_BUDGET):
    """Legs per check, counted from `legs.json` - never from memory.

    Count started or settled implementation/repair attempts, including failed,
    partial and conservatively cancelled work. Queued plans do not spend the
    budget. Dispatch reserves a batch's slots and prevents overlapping attempts
    against the same check. A fix with no repairs is always unattributed.
    """
    counts = {}
    for leg in model["legs"]:
        claims = [] if leg["kind"] == "judge" else leg["fulfills"]
        if not _attempted(leg):
            continue
        for cid in dict.fromkeys(claims + leg["repairs"]):
            counts.setdefault(cid, []).append(leg["id"] or "<unidentified leg>")
    return {
        "limit": limit,
        "legs": {cid: len(owners) for cid, owners in sorted(counts.items())},
        "atBudget": {cid: owners for cid, owners in sorted(counts.items())
                     if len(owners) >= limit},
        "unattributed": [leg["id"] or "<unidentified leg>" for leg in model["legs"]
                         if leg["kind"] == "fix" and not leg["repairs"]],
    }


def baton_items(text):
    """Every item under the baton's disposal sections, one string each.

    Jev gives one answer per call, and a baton lists several unrelated
    issues: asked about the whole baton it cleared its confidence bar on none
    of two batons, asked per item on three of thirteen. An item is a top-level
    bullet or numbered line with its indented continuation joined on; a
    section reading "nothing" has no items.
    """
    items = []
    for section in DISPOSAL_SECTIONS:
        body = re.search(rf"^##\s+{re.escape(section)}\s*$(.*?)(?=^##\s|\Z)",
                         text or "", re.M | re.S | re.I)
        current = None
        for line in (body.group(1) if body else "").splitlines():
            if _ITEM_START.match(line):
                current = {"section": section, "text": line.strip()}
                items.append(current)
            elif current is not None and line.strip():
                current["text"] += " " + line.strip()
    for item in items:
        item["text"] = _ITEM_START.sub("", item["text"], count=1)
    return [item for item in items if item["text"].strip().lower() not in _NOTHING]


def items(model, leg_id):
    """The disposal items of one leg's handoff, read through `relay_model`.

    A JSON handoff and a prose baton carry the same duty - every item under
    `leftUndone` and `discoveredIssues` gets disposed of - so the caller sees
    one shape either way. The schema module (`relay_handoff.items`) owns what
    counts as an item for the JSON form, for the same reason `baton_items`
    owns it for the prose form: two callers parsing one file is two answers
    to "what must the coach dispose of".
    """
    row = next((r for r in model["runners"] if r["leg"] == leg_id), None)
    path = row["batonPath"] if row else None
    if path is None:
        return {"leg": leg_id, "baton": None, "items": []}
    if path.endswith(".json"):
        return _handoff_items(path, leg_id)
    return {"leg": leg_id, "baton": path,
            "items": baton_items(relay_model.baton_text(path))}


def _handoff_items(path, leg_id):
    data, why = relay_model.handoff_data(path)
    if data is None:
        return {"leg": leg_id, "baton": path, "items": [],
                **({"warning": why} if why else {})}
    return {"leg": leg_id, "baton": path, "items": relay_handoff.items(data)}


def _blocked(command, result):
    if command == "dispatch":
        return bool(result.get("blockers"))
    if command == "coverage":
        return bool(result["orphans"] or result["duplicates"] or result["unknown"]
                    or result.get("missingContract"))
    if command == "budget":
        return bool(result["atBudget"] or result["unattributed"])
    if command == "items":
        return result["baton"] is None
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("dispatch", "coverage", "budget", "items"))
    ap.add_argument("leg", nargs="?", help="the leg whose baton `items` reads")
    ap.add_argument("--relay-dir", default=".relay")
    args = ap.parse_args(argv)
    scope = None
    if args.command == "dispatch" and Path(args.relay_dir).is_dir():
        scope = relay_scope.check(args.relay_dir)
        if not scope["ready"]:
            print(json.dumps({"dispatch": [], "scope": scope}, indent=2))
            return 1
    try:
        model = relay_model.build(args.relay_dir, now=None)
    except relay_model.RelayNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.command == "items" and not args.leg:
        ap.error("items needs a LEG")
    if args.command == "dispatch":
        result = dispatch(model, scope_revision=scope["scopeRevision"])
        result["scope"] = scope
    elif args.command == "coverage":
        result = coverage(model, relay_model.contract_ids(args.relay_dir))
    elif args.command == "budget":
        result = budget(model)
    else:
        result = items(model, args.leg)
    if model["warnings"]:
        result["warnings"] = model["warnings"]
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 1 if _blocked(args.command, result) else 0


if __name__ == "__main__":
    sys.exit(main())

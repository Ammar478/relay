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

What it does not decide
-----------------------
Nothing here marks a check, writes a file, or disposes of a baton item. It
reads through `relay_model.build()`, the only reader of relay files, and
prints JSON the coach acts on. Judgement - what a baton issue means, whether a
check passed - stays with the coach and the judges.

Exit status: 0 when the answer needs no action beyond following it, 1 when it
names a blocker (an orphaned or double-claimed check, a check at its repair
budget or a fix leg outside it, a leg with no baton), 2 when there is no relay
to read.
"""

import argparse
import json
import re
import sys

import relay_model

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

    A judge writes nothing to the work tree, so it never conflicts. A leg that
    writes and declares no `touches` cannot be proven disjoint from anything,
    so it conflicts with everything: running it beside another is a guess.
    """
    if "judge" in (leg["kind"], other["kind"]):
        return False
    if not leg["touches"] or not other["touches"]:
        return True
    return any(paths_overlap(a, b) for a in leg["touches"] for b in other["touches"])


def current_stage(model):
    """The first stage, in plan order, that still has a leg to finish."""
    for leg in model["legs"]:
        if leg["status"] not in FINISHED:
            return leg["stage"]
    return None


def dispatch(model):
    """Every pending leg of the current stage that may start now.

    A leg starts when every leg it `dependsOn` has landed, its `touches` are
    disjoint from every leg in flight and every leg already in this batch, and -
    for a judge - every implementation and fix leg of its stage has landed.
    Plan order breaks ties: the earlier leg takes the files.
    """
    legs = [leg for leg in model["legs"] if leg["id"]]
    status = {leg["id"]: leg["status"] for leg in legs}
    stage = current_stage(model)
    in_flight = [leg for leg in legs if leg["status"] == "running"]
    batch, waiting = [], []

    for leg in legs:
        if leg["stage"] != stage or leg["status"] != "pending":
            continue
        reason = _why_not(leg, legs, status, in_flight + batch)
        if reason:
            waiting.append({"leg": leg["id"], "reason": reason})
        else:
            batch.append(leg)

    return {
        "stage": stage,
        "inFlight": [leg["id"] for leg in in_flight],
        "dispatch": [leg["id"] for leg in batch],
        "waiting": waiting,
    }


def _why_not(leg, legs, status, occupied):
    if (leg["rawStatus"] or "").strip().lower() == "blocked":
        return "marked blocked in legs.json"
    unknown = [d for d in leg["dependsOn"] if d not in status]
    if unknown:
        return f"depends on legs with no entry: {', '.join(unknown)}"
    open_deps = [d for d in leg["dependsOn"] if status[d] != LANDED]
    if open_deps:
        return f"waiting on {', '.join(open_deps)}"
    if leg["kind"] == "judge":
        unlanded = [other["id"] for other in legs
                    if other["stage"] == leg["stage"] and other["kind"] != "judge"
                    and other["status"] not in FINISHED]
        if unlanded:
            return f"stage legs not landed: {', '.join(unlanded)}"
    clash = [other["id"] for other in occupied if legs_conflict(leg, other)]
    if clash:
        return f"touches overlap {', '.join(clash)}"
    return None


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

    Every leg against a check counts: the leg that claims it in `fulfills`
    plus each leg whose `repairs` names it (SKILL.md, Phase 6). At `limit` the
    coach stops and puts a scope decision to the human. A fix leg that names no
    check in `repairs` is counted against nothing, so the budget cannot hold
    while one exists: it is listed as `unattributed`.
    """
    counts = {}
    for leg in model["legs"]:
        claims = [] if leg["kind"] == "judge" else leg["fulfills"]
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
    """The disposal items of one leg's baton, read through `relay_model`."""
    row = next((r for r in model["runners"] if r["leg"] == leg_id), None)
    path = row["batonPath"] if row else None
    if path is None:
        return {"leg": leg_id, "baton": None, "items": []}
    return {"leg": leg_id, "baton": path,
            "items": baton_items(relay_model.baton_text(path))}


def _blocked(command, result):
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
    try:
        model = relay_model.build(args.relay_dir, now=None)
    except relay_model.RelayNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.command == "items" and not args.leg:
        ap.error("items needs a LEG")
    if args.command == "dispatch":
        result = dispatch(model)
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

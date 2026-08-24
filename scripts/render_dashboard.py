#!/usr/bin/env python3
"""Render .relay/ state into a Relay Control dashboard.

Usage:
    python3 scripts/render_dashboard.py [--relay-dir .relay] [--out .relay/control.html]

Reads whatever exists in the relay directory and writes a single
self-contained HTML file. Missing inputs degrade to empty panels rather than
failing — the dashboard is a view, never a gate.

Inputs
    legs.json           stages + legs (judge legs are legs too)
    state.json          per-check status
    dashboard.json      optional; the live fields state.json cannot hold —
                        path, elapsed, tokens, activeLeg, runners, log,
                        attention, checkTitles. See references/dashboard.md.
"""

import argparse
import re
import json
import pathlib
import sys

TEMPLATE = pathlib.Path(__file__).resolve().parent.parent / "assets" / "control.html"
START, END = "/* RELAY-DATA-START */", "/* RELAY-DATA-END */"

STATUS_ORDER = {"failed": 0, "blocked": 1, "pending": 2, "passed": 3}


def load(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def kind_of(f):
    """impl | fix | judge — explicit `kind` wins, then the id, then the default."""
    kind = f.get("kind")
    if kind in ("impl", "fix", "judge"):
        return kind
    if f.get("isFix") or f.get("repairs"):
        return "fix"
    if "judge" in f.get("id", ""):
        return "judge"
    return "impl"



STATUS_ALIASES = {
    "completed": {"completed", "complete", "done", "finished", "shipped", "landed", "passed"},
    "running":   {"running", "in_progress", "in-progress", "inprogress", "active", "wip", "started"},
    "cancelled": {"cancelled", "canceled", "skipped", "dropped", "abandoned"},
    "pending":   {"pending", "todo", "queued", "planned", "not_started"},
}


def normalise_status(value):
    """Coaches write 'done', 'in progress', 'TODO'. Map anything sensible onto the
    four states the dashboard knows, so a vocabulary drift never renders as
    'undefined' or a 0% progress bar."""
    v = str(value or "pending").strip().lower().replace(" ", "_")
    for canon, aliases in STATUS_ALIASES.items():
        if v in aliases:
            return canon
    return "pending"


CHECK_ALIASES = {
    "passed":  {"passed", "pass", "ok", "green", "satisfied"},
    "failed":  {"failed", "fail", "red", "broken"},
    "blocked": {"blocked", "block", "unevidenced", "cannot_verify"},
}


def normalise_check(value):
    v = str(value or "pending").strip().lower().replace(" ", "_")
    for canon, aliases in CHECK_ALIASES.items():
        if v in aliases:
            return canon
    return "pending"


BATON_STATUS = {"success": "Success", "partial": "Partial", "failed": "Failed", "failure": "Failed"}
SHA_RE = re.compile(r"(?:commit|sha)[^`\n]*`([0-9a-f]{7,40})`", re.I)
STATUS_RE = re.compile(r"^\W*(?:\*\*)?status(?:\*\*)?\s*:\s*(\w+)", re.I | re.M)


def read_baton(path):
    """Batons are prose, not a rigid form. Pull out what is reliably there:
    when it landed, how long it is, the commit it names, and a status if given."""
    text = path.read_text(errors="replace")
    m = STATUS_RE.search(text)
    status = BATON_STATUS.get((m.group(1).lower() if m else ""), "Success")
    sha = SHA_RE.search(text)
    return {
        "text": text[:9000] + ("\n\n… truncated, full baton on disk" if len(text) > 9000 else ""),
        "status": status,
        "commit": sha.group(1)[:7] if sha else "—",
        "lines": text.count("\n") + 1,
        "mtime": path.stat().st_mtime,
    }


def hhmm(ts):
    import time
    return time.strftime("%H:%M", time.localtime(ts))


def took(seconds):
    if seconds is None or seconds <= 0:
        return "—"
    m = int(seconds // 60)
    return f"{m}m" if m < 60 else f"{m // 60}h {m % 60:02d}m"


def runners_from_state(mdir, leg_rows):
    """One row per completed leg, in the order they landed. A leg with a baton on
    disk gets its detail; one without is marked rather than silently missing."""
    bdir = mdir / "batons"
    batons = {p.stem: read_baton(p) for p in bdir.glob("*.md")} if bdir.is_dir() else {}

    done = [l for l in leg_rows if l["status"] == "completed"]
    done.sort(key=lambda l: batons.get(l["id"], {}).get("mtime", 0))

    rows, prev = [], None
    for n, leg in enumerate(done, 1):
        b = batons.get(leg["id"])
        if b:
            rows.append({
                "n": n, "leg": leg["id"], "stage": leg["m"], "status": b["status"],
                "finished": hhmm(b["mtime"]), "took": took(b["mtime"] - prev if prev else None),
                "commit": b["commit"], "lines": b["lines"], "baton": b["text"],
                "session": leg["id"], "start": "—", "dur": "—",
                "input": "—", "cached": "—", "output": "—", "model": "—", "stream": [],
            })
            prev = b["mtime"]
        else:
            rows.append({
                "n": n, "leg": leg["id"], "stage": leg["m"], "status": "No baton",
                "finished": "—", "took": "—", "commit": "—", "lines": 0, "baton": "",
                "session": leg["id"], "start": "—", "dur": "—",
                "input": "—", "cached": "—", "output": "—", "model": "—", "stream": [],
            })
    rows.reverse()
    return rows


def build(mdir):
    legsfile = load(mdir / "legs.json")
    state = load(mdir / "state.json")
    extra = load(mdir / "dashboard.json")

    flist = legsfile.get("legs", [])
    stages = legsfile.get("stages", [])
    checks = state.get("checks", {})
    mnames = {m.get("id"): m.get("name", m.get("id", "")) for m in stages}

    leg_rows = [
        {
            "id": f.get("id", ""),
            "m": f.get("stage", ""),
            "stageName": mnames.get(f.get("stage"), ""),
            "status": (
                "running" if f.get("id") == state.get("currentLeg")
                else normalise_status(f.get("status"))
            ),
            "kind": kind_of(f),
            "fulfills": ",".join(f.get("fulfills", [])) or "—",
        }
        for f in flist
    ]

    # --- contract, grouped by the area segment of the check id ----------
    titles = extra.get("checkTitles", {})
    groups = {}
    for aid, a in checks.items():
        parts = aid.split("-")
        area = parts[1] if len(parts) > 2 else "GENERAL"
        groups.setdefault(area, []).append({
            "id": aid,
            "title": titles.get(aid, a.get("title", "")),
            "status": normalise_check(a.get("status")),
            "evidence": a.get("evidence") or a.get("reason") or "—",
            "round": a.get("round", 0),
        })
    contract = [
        {"area": area,
         "checks": sorted(v, key=lambda a: (STATUS_ORDER.get(a["status"], 9), a["id"]))}
        for area, v in sorted(groups.items())
    ]

    # --- attention: derived first, then whatever the coach added -----
    done = sum(1 for f in leg_rows if f["status"] == "completed")
    live = [f for f in leg_rows if f["status"] != "cancelled"]

    attention = []
    for aid, a in checks.items():
        if a.get("status") == "failed" and a.get("round", 0) >= 3:
            attention.append({
                "level": "bad", "label": "STALLED",
                "text": f"{aid} has failed {a['round']} judging rounds — "
                        "the check or the leg spec is wrong, not the code.",
                "action": "pause → re-scope",
            })
        elif a.get("status") == "blocked":
            attention.append({
                "level": "warn", "label": "BLOCKED",
                "text": f"{aid}: {a.get('reason', 'evidence could not be collected')}",
                "action": "needs a decision",
            })
    extra_signals = extra.get("attention") or []
    if isinstance(extra_signals, (str, dict)):
        extra_signals = [extra_signals]
    attention += list(extra_signals)
    notes = extra.get("notes") or []
    if isinstance(notes, (str, dict)):
        notes = [notes]
    attention += list(notes)
    if not attention:
        attention.append({
            "level": "calm", "label": "ON TRACK",
            "text": f"{done} of {len(live)} legs complete, no stalled checks.",
            "action": "",
        })

    tokens = extra.get("tokens", {})
    if isinstance(tokens, str):          # tolerate a single pre-formatted string
        tokens = {"input": tokens, "cached": "—", "output": "—"}

    return {
        "path": extra.get("path", str(mdir.resolve().parent)),
        "status": str(extra.get("status", state.get("phase", "RUNNING"))).upper(),
        "elapsed": extra.get("elapsed", "—"),
        "tokens": {
            "input":  tokens.get("input", "—"),
            "cached": tokens.get("cached", "—"),
            "output": tokens.get("output", "—"),
        },
        "attention": attention,
        "activeLeg": extra.get("activeLeg", {
            "id": state.get("currentLeg", "—"),
            "stage": mnames.get(state.get("currentStage"), "—"),
            "skill": "—", "fulfills": [], "preconditions": [], "expected": [],
            "description": "",
        }),
        "legs": leg_rows,
        "contract": contract,
        "runners": extra.get("runners") or runners_from_state(mdir, leg_rows),
        "log": extra.get("log", []),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--relay-dir", default=".relay")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    mdir = pathlib.Path(args.relay_dir)
    if not mdir.is_dir():
        sys.exit(f"no relay directory at {mdir}")

    out = pathlib.Path(args.out) if args.out else mdir / "control.html"
    tpl = TEMPLATE.read_text()
    if START not in tpl or END not in tpl:
        sys.exit("template is missing its RELAY-DATA sentinels")

    data = "const RELAY = " + json.dumps(build(mdir), indent=2) + ";"
    head, rest = tpl.split(START, 1)
    _, tail = rest.split(END, 1)
    out.write_text(head + START + "\n" + data + "\n" + END + tail)
    print(out)


if __name__ == "__main__":
    main()

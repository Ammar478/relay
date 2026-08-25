#!/usr/bin/env python3
"""One reconciled view-model of a relay, for every renderer.

    from relay_model import build
    model = build(".relay")

This module is the only code in the repository that reads `.relay/*.json` or the
batons directory (ACC-DATA-001). The curses TUI and the HTML renderer both draw
from `build()`, so they cannot disagree about what the relay is doing.

Why it exists
-------------
The HTML dashboard read four sources and reconciled none of them. `state.json`
named a `currentLeg` that `legs.json` had already marked `done`, and the renderer
forced it to display as running: two legs shown In Progress when one was running.
The Active Leg panel and the Active Runner panel derived their leg from different
sources and named different legs. The Runners view counted `Active (0)` while two
legs displayed as running. Every one of those is impossible here by construction:
one leg list, one status per leg, one active leg, and the active runner derived
from it.

The three reconciliation rules
------------------------------
1. A leg's own `status` in `legs.json` wins over `state.json.currentLeg`.
   `currentLeg` is a coach's bookmark and goes stale; the leg's status is
   maintained by whoever finished the leg. The bookmark survives as
   `relay.currentLegDeclared` for diagnostics, and never as a status.
2. The active leg is the first leg in plan order whose own status is running.
   The active runner is the runner row of that leg — derived, never looked up
   from a second source.
3. `dashboard.json` is untrusted and optional. It may add what the other files
   cannot hold (elapsed, tokens, models, log, attention); it may not contradict
   them about leg or check status.

Data, not display
-----------------
Absence is `None` or a missing key, never `"—"`, `"N/A"` or `0`. Times are epoch
seconds, not `"15:08"`. Nothing here is truncated, coloured or padded — a view
decides how absence looks. Baton prose is left on disk and fetched by
`baton_text()`; the model carries its path, its line count and its commit.

Every input is untrusted
------------------------
`build()` returns a dict for every directory it is given. A file that is not
valid UTF-8, not valid JSON, empty, truncated mid-write, or a JSON array where
an object belongs; a leg `id` that is null, a number or a list; a `claimedBy`
that is an object; a `stages` that is a number - each of those is a `warnings`
entry naming the field and what it held, and the model carries on with that
value absent. Nothing on disk, and nothing constructible, makes `build()` raise
(ACC-DATA-001). The one exception is the argument itself: a path that is not a
directory, or is empty, raises `RelayNotFound`, because a view that asked for
nothing must be told so rather than shown the working directory.

Coercion happens once, at the top of this module, so no rule below it asks what
type a field is. A leg whose id cannot be read is inert: it is counted and
listed, but it can never be the active leg, because nothing on disk could be
matched to it (ACC-DATA-003).

The Progress Log is derived, not required
-----------------------------------------
`dashboard.json.log` is a coach's optional narration. When there is none the
model derives one from the records that carry a real order: baton mtimes,
`git log` on the relay's branch, and the check transitions in `state.json`.
Entries with no honest timestamp are pinned to the leg that claimed them and
flagged `exact: False`; entries with no honest time at all are not invented.
Commits are bounded to the relay's own window - the branch it runs on, or the
run's own earliest event where there is no branch - and a commit a baton
attributes to a leg is kept before any other, and is never dropped for being
older than that window, so the log tells the run's story rather than the
repository's (ACC-DATA-009).

Determinism
-----------
The same directory yields the same model. The only wall-clock input is the
optional `now` argument, which is required before any elapsed-time field is
filled in; without it those fields stay `None`. `git` is read through a list
argv with a timeout and a commit cap, and its absence is not an error.
"""

import json
import os
import pathlib
import re
import subprocess

__all__ = [
    "build",
    "baton_text",
    "normalise_status",
    "normalise_check",
    "normalise_phase",
    "kind_of",
    "RelayNotFound",
    "LEG_STATES",
    "CHECK_STATES",
]

LEG_STATES = ("completed", "running", "pending", "cancelled")
CHECK_STATES = ("passed", "failed", "blocked", "pending")

# Failed first: the Contract view leads with what is wrong (ACC-CONT-004).
CHECK_ORDER = {"failed": 0, "blocked": 1, "pending": 2, "passed": 3}
ATTENTION_ORDER = {"bad": 0, "warn": 1, "note": 2, "calm": 3}

# Strings a coach may write meaning "there is no value here". They are display
# placeholders, and the model drops them rather than passing them on as data.
PLACEHOLDERS = {"", "-", "--", "—", "–", "n/a", "na", "none", "null", "tbd", "?"}


class RelayNotFound(Exception):
    """The path given to build() is not a directory."""


# --------------------------------------------------------------------------
# untrusted input — the one place a value is made safe to use
# --------------------------------------------------------------------------
#
# Every relay file is written by hand, by a coach, by a runner, or by a process
# that was interrupted mid-write, and `build()` runs once per repaint against
# whatever is on disk at that instant. So no field is assumed to have the type
# the template gives it: a leg `id` arrives as null, a `claimedBy` as a list, a
# `stages` as a number. Rather than an isinstance check at each use, a value is
# coerced once, here, on the way in - and where the coach wrote something the
# field cannot hold, the coercion leaves a warning naming the field and the type
# it held. The model then goes on with the value absent, which every downstream
# rule already handles. Absence is what a malformed value degrades to; a
# traceback is not (ACC-DATA-001).


def _text(value):
    """The usable string inside an untrusted value, else None.

    None for anything that is not a string, for the empty string, and for the
    placeholders a coach types where there is no value ("-", "n/a", "TBD"):
    those are display, and the model carries absence as None.
    """
    if isinstance(value, str):
        text = value.strip()
        return text if text.lower() not in PLACEHOLDERS else None
    return None


def _text_or_warn(value, what, warnings):
    """`_text`, and a warning when the value was there but was not a string.

    A missing key and an explicit null are absence and pass quietly. A list
    where an id belongs is a file a supervisor needs told about.
    """
    if value is not None and not isinstance(value, str):
        warnings.append(f"{what} is a {type(value).__name__}, not a string; "
                        "it is being ignored")
        return None
    return _text(value)


def _scalar(value):
    """A value a metric may carry: a number, or a string with something in it.

    A list, an object or a bool is not a measurement (`True` is not a token
    count), and 0 is (ACC-DATA-008).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return _text(value)


def _whole(value):
    """An honest integer, or None. `True` is not a round number, it is a bool."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _records(value):
    """The dict members of a list of records, and nothing else.

    A coach may write `legs` as a number, an object, or a list with a stray
    string in it; each of those is no records rather than a TypeError.
    """
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


# --------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------

STATUS_ALIASES = {
    "completed": {"completed", "complete", "done", "finished", "shipped", "landed",
                  "passed", "merged"},
    "running":   {"running", "in_progress", "in-progress", "inprogress", "active",
                  "wip", "started", "underway"},
    "cancelled": {"cancelled", "canceled", "skipped", "dropped", "abandoned",
                  "superseded"},
    "pending":   {"pending", "todo", "queued", "planned", "not_started", "new",
                  "blocked", "waiting"},
}


def normalise_status(value):
    """Map a coach's word for a leg onto one of the four leg states.

    Coaches write `done`, `in progress`, `TODO`, `DONE`. Anything unrecognised
    is `pending` — never `None` and never `undefined` (ACC-DATA-004). `blocked`
    is deliberately pending: it is not one of the four states a view filters on,
    and the coach's own word survives on the leg row as `rawStatus`.
    """
    v = str(value if value is not None else "").strip().lower().replace(" ", "_")
    for canon, aliases in STATUS_ALIASES.items():
        if v in aliases:
            return canon
    return "pending"


CHECK_ALIASES = {
    "passed":  {"passed", "pass", "ok", "green", "satisfied", "evidenced"},
    "failed":  {"failed", "fail", "red", "broken"},
    "blocked": {"blocked", "block", "unevidenced", "cannot_verify", "unverifiable"},
}


def normalise_check(value):
    """Map a check's status onto passed / failed / blocked / pending."""
    v = str(value if value is not None else "").strip().lower().replace(" ", "_")
    for canon, aliases in CHECK_ALIASES.items():
        if v in aliases:
            return canon
    return "pending"


PHASE_ALIASES = {
    "running":  {"running", "active", "in_progress", "in-progress", "executing"},
    "judging":  {"judging", "judge", "gating", "gate", "reviewing", "review"},
    "blocked":  {"blocked", "stalled", "paused", "halted", "waiting", "stuck"},
    "complete": {"complete", "completed", "done", "finished", "shipped"},
    "pending":  {"pending", "planning", "proposed", "draft", "not_started",
                 "awaiting_approval", "approved"},
}


def normalise_phase(value):
    """Relay phase, or None when the word is not one the views know."""
    v = str(value if value is not None else "").strip().lower().replace(" ", "_")
    for canon, aliases in PHASE_ALIASES.items():
        if v in aliases:
            return canon
    return None


def kind_of(leg):
    """impl | fix | judge — explicit `kind` wins, then the id, then the default.

    Every input is untrusted: a leg that is not a record at all, or whose id is
    a number, a list or null, is an `impl` leg rather than an exception.
    """
    if not isinstance(leg, dict):
        return "impl"
    kind = leg.get("kind")
    if kind in ("impl", "fix", "judge"):
        return kind
    if leg.get("isFix") or leg.get("repairs"):
        return "fix"
    lid = _text(leg.get("id")) or ""
    if "judge" in lid:
        return "judge"
    if lid.startswith("fix-"):
        return "fix"
    return "impl"


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def _load(path):
    """(data, state, why) where state is ok | missing | malformed.

    A relay file caught mid-write is malformed, not fatal: the model degrades to
    an empty panel and records a warning (ACC-LIVE-003 depends on this).

    The bytes are decoded here rather than by `read_text`, because a write
    interrupted inside a multi-byte character produces a file that is not valid
    UTF-8 at all, and `read_text` raises that before any JSON parsing is
    attempted. A dashboard watching a live relay meets exactly that file. `why`
    names which repair the file needs, since "not valid UTF-8" and "not valid
    JSON" are different problems for whoever wrote it.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}, "missing", None
    except (OSError, ValueError):
        return {}, "malformed", "it could not be read"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {}, "malformed", "it is not valid UTF-8"
    try:
        data = json.loads(text)
    except ValueError:
        return {}, "malformed", "it is not valid JSON"
    if not isinstance(data, dict):
        return {}, "malformed", (
            f"its top level is a {type(data).__name__}, not an object")
    return data, "ok", None


BATON_STATUS = {"success": "completed", "ok": "completed", "partial": "partial",
                "failed": "failed", "failure": "failed"}
_SHA = r"(?<![0-9a-f])([0-9a-f]{7,40})(?![0-9a-f])"

# A sha appearing in a baton is NOT a claim that the leg produced it
# (ACC-DATA-009). Batons quote other shas constantly and for good reasons: the
# branch point a merge forked from, the parent of the commit being reported, a
# parallel runner's work that landed underneath, another relay's history a
# judge is reporting on. Scraping the first commit-shaped token out of the
# prose credited `reconcile-develop` with the branch point it forked FROM
# instead of the merge it made, `thread-id-ownership` with a parallel runner's
# commit instead of its own, and two judges of this relay with agent-service
# shas that are not objects in this repository at all.
#
# So only these three forms are read as a leg claiming a commit as its own
# work. Each is a statement whose subject is the commit; a sha that merely
# appears next to the word is not one.
#
# 1. THE FIELD the baton template prescribes, `**Commit:** <sha>`, at the head
#    of a line. Markdown bold closes on either side of the colon and runners
#    have written both, the colon itself is often dropped (`Commit `<sha>` on
#    <branch>`), and a merge is reported as `Merge commit: <sha>`. A clause
#    boundary counts as a line head, because a runner writes
#    `Branch `x`, commit `<sha>` (parent `<y>`)` and means the first of them.
# 2. `committed as <sha>` - the runner in the first person, wherever it sits.
#    Bare `committed <sha>` is NOT this form: `the parallel runner committed
#    <sha> while I was working` is the sentence it would misread.
# 3. `git commit` reported in the Commands-run table with the sha it produced,
#    on the same line. That is the runner's own invocation and its result.
COMMIT_CLAIM_RES = (
    re.compile(r"(?:^|(?<=[.;])\s|(?<=,)\s)\W*(?:\*\*)?(?:merge\s+|final\s+)?"
               r"commit(?:\*\*)?\s*:?\s*(?:\*\*)?\s*`?" + _SHA,
               re.I | re.M),
    re.compile(r"committed(?:\*\*)?\s+as\s*(?:\*\*)?\s*`?" + _SHA, re.I),
    re.compile(r"git\s+commit\b[^\n]{0,60}?`?" + _SHA, re.I),
)
STATUS_RE = re.compile(r"^\W*(?:\*\*)?status(?:\*\*)?\s*:\s*(\w+)", re.I | re.M)


def commit_claims(text):
    """Every sha `text` claims as its own work, in the order it claims them.

    Seven characters each, the width `git log --format=%h` gives a repository
    this size, so a claim and a commit compare as equals rather than by prefix.
    """
    seen = {}
    for pattern in COMMIT_CLAIM_RES:
        for match in pattern.finditer(text):
            seen.setdefault(match.group(1)[:7], match.start())
    return sorted(seen, key=seen.get)


def baton_text(path):
    """The full prose of a baton, or None when it is not there.

    Kept out of `build()` on purpose: it is long, it is prose (em-dashes and
    all), and only a detail view wants it. Reading it here rather than in the
    view keeps every relay-file read inside this module (ACC-DATA-001).
    """
    try:
        return pathlib.Path(path).read_text(errors="replace")
    except (FileNotFoundError, IsADirectoryError, OSError):
        return None


def _read_baton(path):
    """What a baton reliably carries: when it landed, how long it is, the
    commits it claims, and a status if the runner wrote one.

    `commit` starts as the first claim and is settled against the relay's own
    repository by `_settle_commits` - a claim the repository cannot confirm is
    not this leg's work (ACC-DATA-009). Outside a repository there is nothing
    to confirm it against and the baton's own word is all the evidence there
    is, so the first claim stands.
    """
    text = baton_text(path)
    if text is None:
        return None
    m = STATUS_RE.search(text)
    claims = commit_claims(text)
    try:
        # A baton can be deleted between the listing and this stat: the relay
        # is live and the model is only a reader.
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return {
        "status": BATON_STATUS.get(m.group(1).lower()) if m else None,
        "claims": claims,
        "commit": claims[0] if claims else None,
        "lines": text.count("\n") + 1,
        "mtime": mtime,
        "path": str(path.resolve()),
    }


def _strlist(value):
    """A list of strings from a field a coach may have written as a bare string."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [v if isinstance(v, str) else json.dumps(v, sort_keys=True)
                for v in value]
    return [str(value)]


# --------------------------------------------------------------------------
# legs
# --------------------------------------------------------------------------

def _stage_records(legsfile, warnings):
    """`legs.json.stages`, coerced: an id and a name that are strings or absent.

    A stage id is used as a dictionary key by plan ordering and by the stage
    name lookup, so a list or an object there is not merely wrong, it is
    unhashable.
    """
    records = []
    for i, stage in enumerate(_records(legsfile.get("stages"))):
        records.append({
            "id": _text_or_warn(stage.get("id"), f"legs.json: stage #{i} `id`",
                                warnings),
            "name": _text_or_warn(stage.get("name"),
                                  f"legs.json: stage #{i} `name`", warnings),
            "legs": _strlist(stage.get("legs")),
        })
    return records


def _leg_records(legsfile, warnings):
    """`legs.json.legs`, coerced: `id` and `stage` are strings or None.

    RULE: a leg whose `id` is missing, empty, or not a string cannot be
    identified. Its id becomes None here, and `build()` refuses to make it the
    active leg (ACC-DATA-003): nothing on disk can be matched to it - not a
    baton, not a check's `claimedBy`, not a commit - so an "active" leg of that
    shape would name a runner that cannot exist. It stays in the leg list and
    in the counts, because it is still a leg the coach planned, and the warning
    says why it is inert.

    Duplicate ids are warned about for the same reason: two legs answering to
    one string is how the active leg and the active runner came to name
    different legs in the first place.
    """
    records, seen = [], {}
    for i, leg in enumerate(_records(legsfile.get("legs"))):
        lid = _text(leg.get("id"))
        if lid is None:
            records.append(dict(leg, id=None, stage=_text(leg.get("stage"))))
            warnings.append(
                f"legs.json: leg #{i} has no usable `id` "
                f"({type(leg.get('id')).__name__}); it cannot be identified, so "
                "it is not a candidate for the active leg")
            continue
        seen[lid] = seen.get(lid, 0) + 1
        records.append(dict(
            leg, id=lid,
            stage=_text_or_warn(leg.get("stage"),
                                f"legs.json: leg '{lid}' `stage`", warnings)))

    for lid, count in seen.items():
        if count > 1:
            warnings.append(
                f"legs.json: leg id '{lid}' is used by {count} legs; ids must be "
                "unique or a view cannot tell the legs apart")
    return records


def _plan_order(legs, stages, warnings):
    """Plan order: stages in the order `legs.json` declares them, and within a
    stage the order its `legs` list declares, then any leg of that stage the
    stage list forgot, then legs whose stage is unknown. Deterministic for a
    given file, and stable when a coach appends a leg mid-relay.
    """
    stage_rank = {s["id"]: i for i, s in enumerate(stages) if s["id"]}
    within = {}
    for stage in stages:
        for pos, lid in enumerate(stage["legs"]):
            within.setdefault((stage["id"], lid), pos)

    tail = len(legs) + 1
    decorated = []
    for i, leg in enumerate(legs):
        sid = leg.get("stage")
        decorated.append((
            (stage_rank.get(sid, len(stage_rank)),
             within.get((sid, leg.get("id")), tail),
             i),
            leg,
        ))
    decorated.sort(key=lambda pair: pair[0])

    known = {leg.get("id") for leg in legs}
    for stage in stages:
        for lid in stage["legs"]:
            if lid not in known:
                warnings.append(
                    f"legs.json: stage {stage['id']} lists leg '{lid}', "
                    "which has no leg entry")
    return [leg for _, leg in decorated]


def _leg_rows(legsfile, warnings):
    stages = _stage_records(legsfile, warnings)
    legs = _leg_records(legsfile, warnings)
    names = {s["id"]: s["name"] for s in stages}

    rows = []
    for order, leg in enumerate(_plan_order(legs, stages, warnings)):
        raw = leg.get("status")
        rows.append({
            # An unidentifiable leg carries the empty string, never "None": a
            # view renders an empty cell, and `build()` reads the falsy id as
            # "this leg cannot be the active one".
            "id": leg.get("id") or "",
            "stage": leg.get("stage"),
            "stageName": names.get(leg.get("stage")),
            # RULE: the leg's own status is the only source of leg status.
            # state.json.currentLeg never promotes a leg to running.
            "status": normalise_status(raw),
            "rawStatus": raw if isinstance(raw, str) and raw.strip() else None,
            "kind": kind_of(leg),
            "goal": _text(leg.get("goal")),
            "fulfills": _strlist(leg.get("fulfills")),
            "repairs": _strlist(leg.get("repairs")),
            "dependsOn": _strlist(leg.get("dependsOn")),
            "touches": _strlist(leg.get("touches")),
            "boundaries": _strlist(leg.get("boundaries")),
            "verification": _strlist(leg.get("verification")),
            "skills": _strlist(leg.get("skills")),
            "order": order,
            "isActive": False,
        })

    stage_rows = [{"id": s["id"], "name": s["name"], "legIds": list(s["legs"])}
                  for s in stages]
    return stage_rows, rows


# --------------------------------------------------------------------------
# runners
# --------------------------------------------------------------------------

def _read_batons(relay_dir):
    """{leg id: baton} for every baton on disk, read once per build.

    Two panels want them - the runner rows and the derived progress log - and
    the batons directory is read exactly once for both.
    """
    bdir = relay_dir / "batons"
    batons = {}
    try:
        paths = sorted(bdir.glob("*.md")) if bdir.is_dir() else []
    except OSError:
        return batons
    for path in paths:
        baton = _read_baton(path)
        if baton is not None:
            batons[path.stem] = baton
    return batons


def _runner_rows(batons, leg_rows, active_leg, now, warnings):
    """One row per leg a runner has worked: every completed leg, plus the
    running one. A baton fills the row in; without one the fields stay None
    rather than becoming an em-dash (ACC-DATA-007).

    RULE: the active runner is the row built from the active leg *object*, not
    the row whose `leg` string matches its id. Two legs in `legs.json` may
    answer to one id - that is exactly how the Active Leg pane and the Active
    Runner pane came to name different legs - and identity is the only match
    that cannot be confused by it (ACC-DATA-003).
    """
    known = {leg["id"] for leg in leg_rows}
    for stem in sorted(batons):
        if stem not in known:
            warnings.append(
                f"batons/{stem}.md has no leg entry in legs.json; "
                "it gets no runner row")

    done = [leg for leg in leg_rows if leg["status"] == "completed"]
    running = [leg for leg in leg_rows if leg["status"] == "running"]

    # ORDER: batoned runners in the order their batons landed — the only record
    # of sequence on disk. Completed legs with no baton follow in plan order,
    # because nothing says when they landed. The running runner is last.
    def key(leg):
        baton = batons.get(leg["id"])
        return (0, baton["mtime"], leg["order"]) if baton else (1, 0.0, leg["order"])

    ordered = sorted(done, key=key) + sorted(running, key=lambda l: l["order"])

    rows, prev_finished, active_index = [], None, None
    for n, leg in enumerate(ordered, 1):
        baton = batons.get(leg["id"])
        finished = baton["mtime"] if baton else None
        is_running = leg["status"] == "running"

        # RULE: a runner starts when the previous runner's baton lands. That
        # handoff is the only start signal on disk; with no previous baton the
        # start is unknown and stays None.
        start = prev_finished
        if is_running:
            end = now
        else:
            end = finished
            if finished is None:
                start = None
        duration = (end - start) if (start is not None and end is not None) else None

        if baton is not None:
            status = baton["status"] or "completed"
        else:
            status = "running" if is_running else "completed"

        rows.append({
            "n": n,
            "leg": leg["id"],
            "stage": leg["stage"],
            "stageName": leg["stageName"],
            "kind": leg["kind"],
            "start": start,
            "finished": finished,
            "duration": duration,
            "commit": baton["commit"] if baton else None,
            "batonLines": baton["lines"] if baton else None,
            "batonPath": baton["path"] if baton else None,
            "status": "running" if is_running else status,
        })
        if leg is active_leg:
            active_index = len(rows) - 1
        if finished is not None:
            prev_finished = finished

    counts = {
        "total": len(rows),
        "active": sum(1 for r in rows if r["status"] == "running"),
        "completed": sum(1 for r in rows if r["status"] == "completed"),
        "partial": sum(1 for r in rows if r["status"] == "partial"),
        "failed": sum(1 for r in rows if r["status"] == "failed"),
    }
    active = rows[active_index] if active_index is not None else None
    return rows, counts, active


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def _check_rows(state, extras, warnings):
    raw = state.get("checks")
    if not isinstance(raw, dict):
        if raw is not None:
            warnings.append("state.json: `checks` is not an object; ignored")
        raw = {}
    titles = extras.get("checkTitles")
    titles = titles if isinstance(titles, dict) else {}

    rows = []
    for cid, check in raw.items():
        check = check if isinstance(check, dict) else {}
        cid = str(cid)
        parts = cid.split("-")
        title = titles.get(cid) or check.get("title")
        rows.append({
            "id": cid,
            "area": parts[1] if len(parts) > 2 else "GENERAL",
            "title": _text(title),
            "status": normalise_check(check.get("status")),
            "rawStatus": check.get("status") if isinstance(check.get("status"), str) else None,
            "stage": _text_or_warn(check.get("stage"),
                                   f"state.json: check {cid} `stage`", warnings),
            # `claimedBy` is a leg id and is looked up as a dictionary key by the
            # progress log, so a list or an object there is unhashable, not just
            # wrong.
            "claimedBy": _text_or_warn(check.get("claimedBy"),
                                       f"state.json: check {cid} `claimedBy`",
                                       warnings),
            # round is absent, not 0, when the check has never been judged.
            "round": _whole(check.get("round")),
            "evidence": _text(check.get("evidence")),
            "reason": _text(check.get("reason")),
            "fixLeg": _text_or_warn(check.get("fixLeg"),
                                    f"state.json: check {cid} `fixLeg`", warnings),
            "judgedBy": _text(check.get("judged")),
        })

    groups = {}
    for row in rows:
        groups.setdefault(row["area"], []).append(row)
    grouped = [
        {
            "area": area,
            "checks": sorted(members, key=lambda c: (CHECK_ORDER[c["status"]], c["id"])),
            "passed": sum(1 for c in members if c["status"] == "passed"),
            "total": len(members),
        }
        for area, members in sorted(groups.items())
    ]
    counts = {"total": len(rows)}
    counts.update({s: sum(1 for c in rows if c["status"] == s) for s in CHECK_STATES})
    return rows, grouped, counts


# --------------------------------------------------------------------------
# attention
# --------------------------------------------------------------------------

BAD_LABELS = {"NEEDS YOUR CALL", "STALLED", "BLOCKED", "STOP", "DECISION"}
LABEL_RE = re.compile(r"^([A-Z][A-Z0-9 /_-]{1,40}):\s*(.+)$", re.S)


def _attention_item(value, default_label="NOTE", default_level="note"):
    """A coach writes attention as a dict, or as a string with an ALL-CAPS
    prefix, or as plain prose. All three become the same shape."""
    if isinstance(value, dict):
        # Coerced before use: `level` indexes a table, so a list or an object
        # there is unhashable, and a label that is not a string would be
        # rendered as its own repr.
        level = _text(value.get("level"))
        return {
            "level": level if level in ATTENTION_ORDER else default_level,
            "label": _text(value.get("label")) or default_label,
            "text": _text(value.get("text")) or _text(value.get("m")) or "",
            "action": _text(value.get("action")),
        }
    text = _text(value) or ""
    m = LABEL_RE.match(text)
    if m and default_label == "NOTE":
        label, body = m.group(1).strip(), m.group(2).strip()
        return {
            "level": "bad" if label in BAD_LABELS else "warn",
            "label": label,
            "text": body,
            "action": None,
        }
    return {"level": default_level, "label": default_label, "text": text, "action": None}


def _attention(checks, extras, leg_counts):
    items = []

    # Derived first: these two are the signals the dashboard exists to raise.
    for check in checks:
        if check["status"] == "failed" and (check["round"] or 0) >= 3:
            items.append({
                "level": "bad", "label": "STALLED",
                "text": f"{check['id']} has failed {check['round']} judging rounds "
                        "- the check or the leg spec is wrong, not the code.",
                "action": "pause, then re-scope",
            })
        elif check["status"] == "blocked":
            items.append({
                "level": "warn", "label": "BLOCKED",
                "text": f"{check['id']}: "
                        f"{check['reason'] or 'evidence could not be collected'}",
                "action": "needs a decision",
            })

    raw = extras.get("attention")
    if isinstance(raw, (str, dict)):
        raw = [raw]
    for value in raw if isinstance(raw, list) else []:
        items.append(_attention_item(value))

    notes = extras.get("notes")
    if isinstance(notes, (str, dict)):
        notes = [notes]
    for value in notes if isinstance(notes, list) else []:
        items.append(_attention_item(value, default_label="NOTE", default_level="note"))

    items = [i for i in items if i["text"]]
    if not items:
        live = leg_counts["total"] - leg_counts["cancelled"]
        items.append({
            "level": "calm", "label": "ON TRACK",
            "text": f"{leg_counts['completed']} of {live} legs complete, "
                    "no stalled checks.",
            "action": None,
        })
    # Stable sort: worst first, insertion order kept within a level.
    return sorted(items, key=lambda i: ATTENTION_ORDER[i["level"]])


# --------------------------------------------------------------------------
# log — the running story of what the relay has done (ACC-DATA-005/006)
# --------------------------------------------------------------------------

# Bounds. `build()` runs once per repaint and must stay in the low
# milliseconds, so the two unbounded sources are capped: git is asked for at
# most this many commits (the walk stops there, it does not read the whole
# history), and the merged log keeps at most this many of its most recent
# entries. Batons are already bounded by the number of legs.
#
# These three are the outer safety net, sized for the repaint budget rather
# than for the run. Whether a commit belongs to the log at all is decided by
# the relay's own window in `_relay_commits` and the attribution budget in
# `_commit_entries` (ACC-DATA-009), which are much tighter bounds in practice
# and are not a replacement for these. The entry bound is applied to the
# relay's own events before the budget is worked out, so that the loosest bound
# in the module cannot re-order what the tightest one decided.
LOG_MAX_COMMITS = 200
LOG_MAX_ENTRIES = 300
GIT_TIMEOUT = 3.0

# The names a repository's default branch goes by, as full refs so nothing has
# to be resolved from a shorthand that could mean two things. A commit
# reachable from one of these was in the project before the relay's branch
# existed; the run's own commits are exactly the ones its branch adds on top,
# which is the branch point ACC-DATA-009 opens the window at.
GIT_DEFAULT_REFS = (
    "refs/heads/main",
    "refs/heads/master",
    "refs/remotes/origin/HEAD",
    "refs/remotes/origin/main",
    "refs/remotes/origin/master",
)

# Deterministic tie-break when two events share a timestamp. A check transition
# is pinned to the landing it was claimed at, so it reads just above it; the
# handoff into the next runner reads just below.
LOG_KIND_ORDER = {"check": 0, "baton": 1, "commit": 2, "start": 3}

BATON_LOG = {
    "completed": ("calm", "{leg} landed"),
    "partial":   ("warn", "{leg} landed partial, with work left undone"),
    "failed":    ("bad", "{leg} failed"),
}


def _log_entry(t, exact, kind, level, message, now,
               leg=None, check=None, commit=None):
    """One row of the Progress Log.

    `t` is epoch seconds and `age` is seconds since `t`, derived through the
    injectable `now` so a view can render "7h ago" and a test can pin it.
    `exact` says whether `t` is the event's own recorded time (a baton mtime, a
    commit date) or a time inherited from the leg the event belongs to, which
    is the best `state.json` can honestly offer.
    """
    return {
        "t": float(t),
        "age": (now - t) if now is not None else None,
        "exact": exact,
        "kind": kind,
        "level": level,
        "leg": leg,
        "check": check,
        "commit": commit,
        "m": message,
    }


def _in_a_repo(path, project):
    """True when `path`, or a parent of it no higher than `project`, holds a `.git`.

    Checked before spawning git at all: most relay directories a view opens are
    inside a repo, but a fixture or a copied relay is not, and a process spawn
    per repaint for a guaranteed failure is not free.

    RULE: the search stops at `project` - the same `relay.path` the model
    reports, so what the dashboard calls the project and where commits are read
    from cannot drift apart. A live relay is `<project>/.relay` with its `.git`
    at `<project>`, so it still finds its own repository; a relay that merely
    happens to sit inside some other repository (every fixture under
    `tests/fixtures/`) finds nothing, instead of reporting that repository's
    commits as its own. When `project` is not on the walk at all - a coach can
    write any `dashboard.json.path` they like - the relay directory itself is
    the only honest bound left.
    """
    try:
        current = pathlib.Path(path).resolve()
    except OSError:
        return False
    try:
        limit = pathlib.Path(project).resolve()
    except (OSError, TypeError, ValueError):
        limit = current
    if limit != current and limit not in current.parents:
        limit = current
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return True
        if candidate == limit:
            break
    return False


def _git(relay_dir, *args, stdin=None):
    """`git -C <relay_dir> <args>` stdout, or None when git could not answer.

    Never raises: git may be absent, the directory may not be a repository, the
    repository may have no commits, or git may be slow enough to be worth
    giving up on. Any of those degrades to no commit entries, and the log is
    still worth having from the batons alone.

    RULE: the caller's git environment is dropped. `GIT_DIR` points git at a
    repository of the environment's choosing whatever `-C` says, and
    `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_OBJECT_DIRECTORY` and the
    `GIT_CONFIG_*` overrides each redirect a read in their own way - so a
    dashboard opened from a shell that exports one would report a foreign
    repository's commits as this relay's. That is the defect `_in_a_repo`
    bounds away on the filesystem, walked back in through the environment.
    Every `GIT_*` name goes, rather than the handful that redirect today.
    """
    # A list argv, never a shell: `relay_dir` is a path from the caller and is
    # passed as one argument, not interpolated into a command string.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        out = subprocess.run(["git", "-C", str(relay_dir), *args],
                             input=stdin, capture_output=True, text=True,
                             timeout=GIT_TIMEOUT, env=env)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _resolve_shas(relay_dir, project, shas):
    """Which of `shas` name a commit in the relay's own repository.

    Returns None when the question cannot be asked at all - the relay is not
    in a repository of its own, or git could not answer - so a caller can tell
    "this repository does not have it" from "nothing here can check". The two
    are different answers and only the first falsifies a baton's claim.

    ONE git process for every sha in the relay, not one per sha: `cat-file
    --batch-check` takes the whole list on stdin and answers in input order,
    inside the same `GIT_TIMEOUT` as every other read here. A short sha that is
    ambiguous in this repository answers `ambiguous` and is treated as
    unresolved, which is the honest reading of it.
    """
    order = sorted(shas)
    if not order or not _in_a_repo(relay_dir, project):
        return None
    out = _git(relay_dir, "cat-file", "--batch-check=%(objectname) %(objecttype)",
               stdin="\n".join(order) + "\n")
    if out is None:
        return None
    lines = out.splitlines()
    if len(lines) != len(order):
        return None            # not the answer that was asked for; do not guess
    return {sha for sha, line in zip(order, lines)
            if line.rsplit(" ", 1)[-1:] == ["commit"]}


def _settle_commits(relay_dir, project, batons):
    """Settle every baton's claimed commit against the relay's own repository.

    A leg is credited with a commit only when its baton claims the sha as its
    own work AND the sha is a commit in the repository the relay supervises
    (ACC-DATA-009). Both halves are load-bearing: this relay's own judges wrote
    reports quoting another relay's shas in claim-shaped sentences, and those
    shas are not objects here.
    """
    resolved = _resolve_shas(
        relay_dir, project, {sha for b in batons.values() for sha in b["claims"]})
    if resolved is None:
        return
    for baton in batons.values():
        baton["commit"] = next((sha for sha in baton["claims"] if sha in resolved),
                               None)


def _git_log(relay_dir, exclude=()):
    """[(epoch, short sha, subject)] for HEAD, newest first.

    `exclude` is refs whose history is not this run's: git walks back from HEAD
    and stops at every commit reachable from one of them, so what comes back is
    the branch's own commits - everything from its branch point onwards.
    """
    out = _git(relay_dir, "log", "--no-color", f"--max-count={LOG_MAX_COMMITS}",
               "--format=%ct%x1f%h%x1f%s", "HEAD",
               *(("--not", *exclude) if exclude else ()))
    commits = []
    for line in (out or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3 or not parts[2].strip():
            continue
        try:
            when = float(parts[0])
        except ValueError:
            continue
        commits.append((when, parts[1].strip()[:7], parts[2].strip()))
    return commits


def _default_branch_refs(relay_dir):
    """The default-branch refs this repository actually has, in full form.

    `for-each-ref` resolves the patterns and prints only what exists, so a
    repository with no `main` yields an empty list rather than an error, and no
    history is walked to find out - it is the cheapest question git answers.
    """
    out = _git(relay_dir, "for-each-ref", "--format=%(refname)", *GIT_DEFAULT_REFS)
    return [line.strip() for line in (out or "").splitlines() if line.strip()]


def _relay_commits(relay_dir, project, own):
    """(commits, since, branched) for this run, newest first (ACC-DATA-009).

    A project's history is far longer and far busier than the relay that
    supervises one slice of it, so a window decides which commits are the run's
    at all. Two bounds make it, and both are returned for the caller to apply:

    * `commits` is already bounded by TOPOLOGY. When the relay runs on a branch
      of its own, the walk excludes everything reachable from the default
      branch, so what comes back is what this branch adds on top of its branch
      point. `branched` says so. A commit from before the branch point is not
      this run's work however loudly a baton talks about it, so no claim can
      reach back past this bound.
    * `since` is the relay's EARLIEST RECORDED EVENT, or None when it has
      recorded none. It is the second bound and the caller applies it: a
      project's history from before the relay started is not part of this run
      (ACC-DATA-009), whether it sits on the branch or before it.

    The one commit that legitimately predates `since` is a leg's own: a runner
    commits *before* it writes its baton, so the first leg's commit is older
    than the earliest event the relay ever recorded - measured in this
    repository, by 46.7 seconds. That is why `branched` is reported rather than
    folded into `since` here: inside a branch of the run's own, a commit a
    baton claims is admitted at the branch point, and nowhere else is.

    A relay that has recorded no event at all has neither bound. It has no
    window and nothing to count against, and the outer `--max-count` walk is
    all that is left; showing a fresh relay its recent commits is better than
    showing it nothing.

    Two git invocations in the common case, both bounded by `GIT_TIMEOUT`: the
    ref probe walks nothing, and a walk that comes back empty means HEAD is the
    default branch, which is the unbranched case.
    """
    if not _in_a_repo(relay_dir, project):
        return [], None, False
    since = min(entry["t"] for entry in own) if own else None
    defaults = _default_branch_refs(relay_dir)
    if defaults:
        branch = _git_log(relay_dir, exclude=defaults)
        if branch:
            return branch, since, True
    return _git_log(relay_dir), since, False


def _commit_entries(relay_dir, project, batons, own, budget, now):
    """Commit entries for the log: the run's own commits first (ACC-DATA-009).

    `own` is every entry the relay's own records produced - landings, handoffs
    and check transitions. It decides the window (see `_relay_commits`), and
    `budget` is how many commits may sit beside it.

    WHICH commits the budget buys is the property, not how many. A relay's own
    commits are the *oldest* inside its own window, so a budget spent newest
    first removes exactly them and keeps the project's unrelated traffic: the
    live agent-service relay carried 12 commit entries under that rule, none of
    them attributable to a leg, which satisfies a count bound while inverting
    what the log is for.

    RULE: an attributed commit is not a budget line. Every commit a baton
    claims is kept, and `budget` buys only the newest of what is left over.
    The budget used to be `min(events, MAX - events)` spent on attribution
    first, which above 150 relay events starts discarding attributed commits
    oldest-first and re-inverts the property at scale.

    RULE: the time bound applies to attributed commits too, with exactly one
    exception - a commit a baton claims, inside a branch the run owns, is
    admitted at the branch point. That is where a first leg's commit sits: a
    runner commits before it writes its baton, so its commit is older than the
    relay's earliest event. Exempting attributed commits from the bound
    *entirely*, which is what the previous fix did, let a merge dated a day
    before the relay began back into the live relay's log on the strength of a
    baton that only mentioned it. Where there is no branch of the run's own
    there is no branch point either, and the earliest event bounds everything.

    Only commits are budgeted. A baton, a handoff or a check transition is
    never dropped to make room: they are the events a supervisor came to the
    pane for. And a relay that has recorded no event at all has neither a
    window nor anything to budget against, so the walk comes through as it
    came: see `_relay_commits`.
    """
    commits, since, branched = _relay_commits(relay_dir, project, own)
    commits = sorted(commits, key=lambda c: -c[0])
    # Attribution comes from the batons rather than from the runner rows, for
    # the same reason the landings above do: a baton is what happened, and a
    # leg that `legs.json` forgot - or has not marked done yet - has no runner
    # row while its baton sits on disk naming the commit it landed.
    #
    # `_read_baton` cuts what the baton says and `_git_log` cuts git's own `%h`
    # to the same seven characters, so this is an equality, not a prefix search.
    #
    # Two batons claiming one sha is a contradiction on disk, not a choice for
    # the model: the leg that landed first is credited, so the log says the
    # same thing on every build.
    by_commit = {}
    for leg, baton in sorted(batons.items(), key=lambda kv: (kv[1]["mtime"], kv[0])):
        if baton["commit"]:
            by_commit.setdefault(baton["commit"], leg)
    if own:
        def in_window(commit, claimed):
            if since is None:
                return True
            return commit[0] >= since or (claimed and branched)

        attributed = [c for c in commits
                      if by_commit.get(c[1]) and in_window(c, True)]
        rest = [c for c in commits
                if not by_commit.get(c[1]) and in_window(c, False)]
        kept = attributed + rest[:max(0, budget - len(attributed))]
        # `git log` already yields newest first; sorting states the intent and
        # is stable, so equal commit times keep git's own order and the merge
        # stays deterministic across builds.
        commits = sorted(kept, key=lambda c: -c[0])

    return [
        _log_entry(when, True, "commit", "note", f"commit {sha}: {subject}", now,
                   leg=by_commit.get(sha), commit=sha)
        for when, sha, subject in commits
    ]


def _derived_log(relay_dir, project, runners, batons, checks, now):
    """The story of the run, from the three records that carry a real order.

    1. A baton's mtime is when that leg landed, and its STATUS says how. Every
       baton on disk counts, including one whose leg `legs.json` forgot: the
       log records what happened, not what was planned.
    2. Commits on the relay's branch are real events with real times. The
       exchange zone is git, so a commit is a leg's work becoming visible.
       They are derived last, because they are bounded by the other two: see
       `_commit_entries` for the window and the count.
    3. `state.json` records that a check was judged more than once, or was sent
       to a fix leg, but carries no timestamps. Those entries are pinned to the
       landing of the leg that claimed the check and marked `exact: False`.
       A check whose claiming leg has no baton has no honest time at all, and
       gets no entry rather than a guessed one.
    """
    status_of = {row["leg"]: row["status"] for row in runners}

    entries = []
    # 1. landings.
    for leg, baton in batons.items():
        if status_of.get(leg) == "running":
            continue  # its start is the event; the baton has not landed yet
        status = status_of.get(leg) or baton["status"] or "completed"
        level, template = BATON_LOG.get(status, BATON_LOG["completed"])
        entries.append(_log_entry(
            baton["mtime"], True, "baton", level, template.format(leg=leg), now,
            leg=leg, commit=baton["commit"]))

    # The handoff into the running leg. Every other runner's start is the
    # previous runner's landing and is already an entry above; this one is the
    # only leg in flight, and without it the log falls silent exactly when a
    # supervisor is watching.
    for row in runners:
        if row["status"] == "running" and row["start"] is not None:
            entries.append(_log_entry(
                row["start"], False, "start", "note",
                f"{row['leg']} started", now, leg=row["leg"]))

    # 3. check transitions.
    anchor = {leg: baton["mtime"] for leg, baton in batons.items()}
    for check in checks:
        at = anchor.get(check["claimedBy"])
        if at is None:
            continue
        rounds = check["round"]
        if isinstance(rounds, int) and rounds > 1:
            entries.append(_log_entry(
                at, False, "check",
                "bad" if check["status"] == "failed" else "warn",
                f"{check['id']} took {rounds} judging rounds and is now "
                f"{check['status']}", now,
                leg=check["claimedBy"], check=check["id"]))
        if check["fixLeg"]:
            entries.append(_log_entry(
                at, False, "check", "bad",
                f"{check['id']} failed judging and was sent to fix leg "
                f"{check['fixLeg']}", now,
                leg=check["claimedBy"], check=check["id"]))

    # 2. commits, last: `entries` is now the relay's own record of itself, and
    # it is what bounds them (ACC-DATA-009).
    #
    # The budget is how many UNATTRIBUTED commits may sit beside the relay's
    # own record of itself, and it is the relay's own event count: a log where
    # the project's traffic outnumbers the run's events buries the run
    # (ACC-DATA-009). Attributed commits are not bought with it - see
    # `_commit_entries` - so what is left after them is what it buys.
    #
    # It is deliberately NOT `min(events, LOG_MAX_ENTRIES - events)` any more.
    # That form made the outer entry bound decide the attribution question,
    # and above 150 events it decided it backwards. The entry bound is applied
    # once, at the end, to the merged log: it is the loosest bound in the
    # module and it stays the last word rather than a second opinion on which
    # commits belong.
    events = sorted(entries, key=lambda e: -e["t"])
    commits = _commit_entries(
        relay_dir, project, batons, events, len(events), now)

    # The outer entry bound, applied once and last. It decides how much of the
    # log fits in a pane, and it must not decide which commits belong: that is
    # settled above. So it is spent in the order the pane is worth reading in.
    #
    # 1. Attributed commits: the run's own work, and never dropped to make room
    #    for anything else. They are bounded by half the entry bound, and only
    #    by it - past 150 of them the log cannot hold both a run's landings and
    #    its commits, and squeezing the landings out to keep commits would
    #    invert the count bound rather than honour it.
    # 2. The relay's own events, newest first, in whatever room is left.
    # 3. Unattributed commits, with what remains, and never more of them than
    #    there are events to bury - unless the relay has recorded no event at
    #    all, which has nothing to bury and nothing to count against
    #    (ACC-DATA-009).
    attributed = [e for e in commits if e["leg"]][:LOG_MAX_ENTRIES // 2]
    room = LOG_MAX_ENTRIES - len(attributed)
    kept = events[:room]
    spare = room - len(kept)
    if events:
        spare = min(spare, max(0, len(kept) - len(attributed)))
    entries = kept + attributed + [e for e in commits if not e["leg"]][:spare]

    entries.sort(key=lambda e: (-e["t"], LOG_KIND_ORDER[e["kind"]], e["m"]))
    return entries[:LOG_MAX_ENTRIES]


def _log(extras, relay_dir, project, runners, batons, checks, now):
    """The Progress Log, and where it came from.

    ACC-DATA-006: a coach who writes `dashboard.json.log` is quoted verbatim,
    down to whatever they put in `t` - it is their pane, and `logSource` tells
    a view it is reading prose rather than the model's own numbers.
    ACC-DATA-005: otherwise the log is derived. `logSource` is None when there
    was nothing to derive, so a view says "none" honestly instead of rendering
    `1-0 of 0`.
    """
    raw = extras.get("log")
    if isinstance(raw, list) and raw:
        entries = [e if isinstance(e, dict) else {"t": None, "m": str(e), "cls": None}
                   for e in raw]
        return entries, "dashboard"

    entries = _derived_log(relay_dir, project, runners, batons, checks, now)
    return (entries, "derived") if entries else ([], None)


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def build(relay_dir, now=None):
    """Build the reconciled view-model of the relay at `relay_dir`.

    `now` is the only clock. Pass `time.time()` for live elapsed values; leave
    it None (the default) and every now-derived field stays None, which keeps
    the model deterministic for tests and for frame capture.

    Raises RelayNotFound when the path is not a directory, is empty, or is not
    a path at all - `build("")` names no relay, and building the process's
    working directory instead of saying so is how a view ends up drawing
    someone else's relay. Everything else degrades: a missing file is an empty
    panel, a malformed file is an empty panel plus a warning.
    """
    if relay_dir is None or (isinstance(relay_dir, str) and not relay_dir.strip()):
        raise RelayNotFound("no relay directory given")
    try:
        relay_dir = pathlib.Path(relay_dir)
        is_dir = relay_dir.is_dir()
    except (TypeError, ValueError, OSError):
        raise RelayNotFound(f"not a relay directory: {relay_dir!r}") from None
    if not is_dir:
        raise RelayNotFound(f"no relay directory at {relay_dir}")

    warnings = []
    legsfile, legs_src, legs_why = _load(relay_dir / "legs.json")
    state, state_src, state_why = _load(relay_dir / "state.json")
    extras, dash_src, dash_why = _load(relay_dir / "dashboard.json")
    for name, src, why in (("legs.json", legs_src, legs_why),
                           ("state.json", state_src, state_why),
                           ("dashboard.json", dash_src, dash_why)):
        if src == "malformed":
            warnings.append(
                f"{name} could not be parsed: {why or 'it is malformed'}; "
                "it is being ignored")

    stages, leg_rows = _leg_rows(legsfile, warnings)
    stage_names = {s["id"]: s["name"] for s in stages}

    leg_counts = {"total": len(leg_rows)}
    leg_counts.update({s: sum(1 for l in leg_rows if l["status"] == s)
                       for s in LEG_STATES})

    # RULE: the active leg is the first leg in plan order whose own status is
    # running. state.json.currentLeg is a bookmark, kept only for diagnostics.
    declared = _text_or_warn(state.get("currentLeg"), "state.json: `currentLeg`",
                             warnings)
    known_ids = {leg["id"] for leg in leg_rows}
    if declared and declared not in known_ids and legs_src == "ok":
        warnings.append(
            f"state.json: currentLeg '{declared}' has no leg entry in legs.json")

    # A leg with no usable id is not a candidate: nothing on disk can be matched
    # to it, so it could only ever be an active leg with no runner - neither
    # half of ACC-DATA-003's "equal, or both absent".
    running = [leg for leg in leg_rows if leg["status"] == "running"]
    if len(running) > 1:
        warnings.append(
            "legs.json: " + ", ".join(f"'{leg['id']}'" if leg["id"] else "an "
                                      "unidentified leg" for leg in running) +
            " are all marked running; a relay runs one leg at a time, and the "
            "first in plan order is being shown as the active one")
    active_leg = next((leg for leg in running if leg["id"]), None)
    if active_leg is not None:
        active_leg["isActive"] = True

    # RULE: `path` is the project the relay supervises. A relay directory called
    # `.relay` sits inside its project, so the project is its parent; a
    # directory called anything else (a fixture, a copy) is its own path.
    #
    # Derived before the batons are read, because it is also the bound on where
    # a commit may be read from, and a baton's claimed commit is settled
    # against that repository before either the runner rows or the log quotes
    # it. Two panes naming different commits for one leg is the class of defect
    # this module exists to remove.
    path = _text_or_warn(extras.get("path"), "dashboard.json: `path`", warnings)
    if path is None:
        resolved = relay_dir.resolve()
        path = str(resolved.parent if resolved.name == ".relay" else resolved)

    batons = _read_batons(relay_dir)
    _settle_commits(relay_dir, path, batons)
    runners, runner_counts, active_runner = _runner_rows(
        batons, leg_rows, active_leg, now, warnings)

    checks, check_groups, check_counts = _check_rows(state, extras, warnings)

    phase = normalise_phase(state.get("phase"))
    phase_source = "state"
    if phase is None:
        # Derived when state.json has no usable phase: complete if nothing is
        # left to run, running if something is, pending otherwise.
        phase_source = "derived"
        if not leg_rows:
            phase = "pending"
        elif leg_counts["running"]:
            phase = "running"
        elif leg_counts["completed"] + leg_counts["cancelled"] == leg_counts["total"]:
            phase = "complete"
        else:
            phase = "pending"

    stage_id = _text_or_warn(state.get("currentStage"),
                             "state.json: `currentStage`", warnings)
    current_stage = None
    if stage_id:
        current_stage = {"id": stage_id, "name": stage_names.get(stage_id)}
        if stage_id not in stage_names and legs_src == "ok":
            warnings.append(
                f"state.json: currentStage '{stage_id}' is not a stage in legs.json")

    name = _text(legsfile.get("relay")) or _text(state.get("relay"))
    title = _text_or_warn(extras.get("title"), "dashboard.json: `title`", warnings)

    # ACC-DATA-008: a metric with no source is a missing key, not a zero.
    metrics = {}
    elapsed = _scalar(extras.get("elapsed"))
    if elapsed is not None:
        metrics["elapsed"] = elapsed
    tokens = extras.get("tokens")
    if isinstance(tokens, dict):
        measured = {str(k): _scalar(v) for k, v in tokens.items()
                    if _scalar(v) is not None}
        if measured:
            metrics["tokens"] = measured
    elif tokens is not None:
        warnings.append("dashboard.json: `tokens` is not an object; ignored")

    log, log_source = _log(extras, relay_dir, path, runners, batons, checks, now)

    return {
        "relay": {
            "name": name,
            "title": title or name,
            "path": path,
            "relayDir": str(relay_dir.resolve()),
            "phase": phase,
            "phaseSource": phase_source,
            "currentStage": current_stage,
            "currentLegDeclared": declared,
        },
        "metrics": metrics,
        "stages": stages,
        "legs": leg_rows,
        "legCounts": leg_counts,
        "activeLeg": active_leg,
        "runners": runners,
        "runnerCounts": runner_counts,
        "activeRunner": active_runner,
        "checks": checks,
        "checkGroups": check_groups,
        "checkCounts": check_counts,
        "attention": _attention(checks, extras, leg_counts),
        "log": log,
        "logSource": log_source,
        # Untrusted passthrough of everything else the coach wrote: `models`
        # and the judge toggles (ACC-MODEL-001..003) live here until a view
        # needs a shape for them. Prose here is the coach's own, not the
        # model's; the model never puts a placeholder in it.
        "extras": extras,
        "sources": {"legs": legs_src, "state": state_src, "dashboard": dash_src},
        "warnings": warnings,
    }


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("relay_dir", nargs="?", default=".relay")
    args = ap.parse_args()
    try:
        json.dump(build(args.relay_dir), sys.stdout, indent=2, sort_keys=False)
        sys.stdout.write("\n")
    except RelayNotFound as exc:
        sys.exit(str(exc))

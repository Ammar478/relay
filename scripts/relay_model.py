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

The Progress Log is derived, not required
-----------------------------------------
`dashboard.json.log` is a coach's optional narration. When there is none the
model derives one from the records that carry a real order: baton mtimes,
`git log` on the relay's branch, and the check transitions in `state.json`.
Entries with no honest timestamp are pinned to the leg that claimed them and
flagged `exact: False`; entries with no honest time at all are not invented.
Commits are bounded to the relay's own window - the project's history from
before the run began is not part of the run's story (ACC-DATA-009).

Determinism
-----------
The same directory yields the same model. The only wall-clock input is the
optional `now` argument, which is required before any elapsed-time field is
filled in; without it those fields stay `None`. `git` is read through a list
argv with a timeout and a commit cap, and its absence is not an error.
"""

import json
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
    """impl | fix | judge — explicit `kind` wins, then the id, then the default."""
    kind = leg.get("kind")
    if kind in ("impl", "fix", "judge"):
        return kind
    if leg.get("isFix") or leg.get("repairs"):
        return "fix"
    lid = leg.get("id", "")
    if "judge" in lid:
        return "judge"
    if lid.startswith("fix-"):
        return "fix"
    return "impl"


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def _load(path):
    """(data, state) where state is ok | missing | malformed.

    A relay file caught mid-write is malformed, not fatal: the model degrades to
    an empty panel and records a warning (ACC-LIVE-003 depends on this).
    """
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return {}, "missing"
    except OSError:
        return {}, "malformed"
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return {}, "malformed"
    return (data, "ok") if isinstance(data, dict) else ({}, "malformed")


BATON_STATUS = {"success": "completed", "ok": "completed", "partial": "partial",
                "failed": "failed", "failure": "failed"}
# A baton written to the template carries `**Commit:** <sha>` as a field; that
# wins. Otherwise take the first commit-shaped reference in the prose, which is
# a guess the view must not dress up as certainty.
COMMIT_FIELD_RE = re.compile(
    r"^\W*(?:\*\*)?commit(?:\*\*)?\s*:\s*`?([0-9a-f]{7,40})`?", re.I | re.M)
SHA_RE = re.compile(r"(?:commit|sha)[^`\n]*`([0-9a-f]{7,40})`", re.I)
STATUS_RE = re.compile(r"^\W*(?:\*\*)?status(?:\*\*)?\s*:\s*(\w+)", re.I | re.M)


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
    """What a baton reliably carries: when it landed, how long it is, the commit
    it names, and a status if the runner wrote one."""
    text = baton_text(path)
    if text is None:
        return None
    m = STATUS_RE.search(text)
    sha = COMMIT_FIELD_RE.search(text) or SHA_RE.search(text)
    return {
        "status": BATON_STATUS.get(m.group(1).lower()) if m else None,
        "commit": sha.group(1)[:7] if sha else None,
        "lines": text.count("\n") + 1,
        "mtime": path.stat().st_mtime,
        "path": str(path.resolve()),
    }


def _meaningful(value):
    """False for a coach's placeholder, True for a real value — including 0,
    which is a measurement and not an absence (ACC-DATA-008)."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in PLACEHOLDERS
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


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

def _plan_order(legs, stages, warnings):
    """Plan order: stages in the order `legs.json` declares them, and within a
    stage the order its `legs` list declares, then any leg of that stage the
    stage list forgot, then legs whose stage is unknown. Deterministic for a
    given file, and stable when a coach appends a leg mid-relay.
    """
    stage_rank = {s.get("id"): i for i, s in enumerate(stages) if s.get("id")}
    within = {}
    for stage in stages:
        for pos, lid in enumerate(_strlist(stage.get("legs"))):
            within.setdefault((stage.get("id"), lid), pos)

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
        for lid in _strlist(stage.get("legs")):
            if lid not in known:
                warnings.append(
                    f"legs.json: stage {stage.get('id')} lists leg '{lid}', "
                    "which has no leg entry")
    return [leg for _, leg in decorated]


def _leg_rows(legsfile, warnings):
    stages = [s for s in legsfile.get("stages", []) if isinstance(s, dict)]
    legs = [l for l in legsfile.get("legs", []) if isinstance(l, dict)]
    names = {s.get("id"): s.get("name") for s in stages}

    rows = []
    for order, leg in enumerate(_plan_order(legs, stages, warnings)):
        raw = leg.get("status")
        rows.append({
            "id": leg.get("id") or "",
            "stage": leg.get("stage"),
            "stageName": names.get(leg.get("stage")),
            # RULE: the leg's own status is the only source of leg status.
            # state.json.currentLeg never promotes a leg to running.
            "status": normalise_status(raw),
            "rawStatus": raw if isinstance(raw, str) and raw.strip() else None,
            "kind": kind_of(leg),
            "goal": leg.get("goal") if _meaningful(leg.get("goal")) else None,
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

    stage_rows = [{"id": s.get("id"), "name": s.get("name"),
                   "legIds": _strlist(s.get("legs"))} for s in stages]
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
    if bdir.is_dir():
        for path in sorted(bdir.glob("*.md")):
            baton = _read_baton(path)
            if baton is not None:
                batons[path.stem] = baton
    return batons


def _runner_rows(batons, leg_rows, active_id, now, warnings):
    """One row per leg a runner has worked: every completed leg, plus the
    running one. A baton fills the row in; without one the fields stay None
    rather than becoming an em-dash (ACC-DATA-007).
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

    rows, prev_finished = [], None
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
        if finished is not None:
            prev_finished = finished

    counts = {
        "total": len(rows),
        "active": sum(1 for r in rows if r["status"] == "running"),
        "completed": sum(1 for r in rows if r["status"] == "completed"),
        "partial": sum(1 for r in rows if r["status"] == "partial"),
        "failed": sum(1 for r in rows if r["status"] == "failed"),
    }
    active = next((r for r in rows if r["leg"] == active_id), None) if active_id else None
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
        parts = str(cid).split("-")
        title = titles.get(cid) or check.get("title")
        rows.append({
            "id": cid,
            "area": parts[1] if len(parts) > 2 else "GENERAL",
            "title": title if _meaningful(title) else None,
            "status": normalise_check(check.get("status")),
            "rawStatus": check.get("status") if isinstance(check.get("status"), str) else None,
            "stage": check.get("stage"),
            "claimedBy": check.get("claimedBy"),
            # round is absent, not 0, when the check has never been judged.
            "round": check.get("round") if isinstance(check.get("round"), int) else None,
            "evidence": check.get("evidence") if _meaningful(check.get("evidence")) else None,
            "reason": check.get("reason") if _meaningful(check.get("reason")) else None,
            "fixLeg": check.get("fixLeg") if _meaningful(check.get("fixLeg")) else None,
            "judgedBy": check.get("judged") if _meaningful(check.get("judged")) else None,
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
        text = value.get("text") or value.get("m") or ""
        label = value.get("label") or default_label
        level = value.get("level") or default_level
        return {
            "level": level if level in ATTENTION_ORDER else default_level,
            "label": str(label),
            "text": str(text),
            "action": value.get("action") if _meaningful(value.get("action")) else None,
        }
    text = str(value).strip()
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
# than for the run. What a commit belongs to the log at all is decided by the
# relay's own window in `_commit_entries` (ACC-DATA-009), which is a much
# tighter bound in practice and is not a replacement for these.
LOG_MAX_COMMITS = 200
LOG_MAX_ENTRIES = 300
GIT_TIMEOUT = 3.0

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


def _git_commits(relay_dir, project):
    """[(epoch, short sha, subject)] for the tip of the relay's branch.

    Never raises: git may be absent, the directory may not be a repository, the
    repository may have no commits, or git may be slow enough to be worth
    giving up on. Any of those degrades to no commit entries, and the log is
    still worth having from the batons alone.
    """
    if not _in_a_repo(relay_dir, project):
        return []
    try:
        # A list argv, never a shell: `relay_dir` is a path from the caller and
        # is passed as one argument, not interpolated into a command string.
        out = subprocess.run(
            ["git", "-C", str(relay_dir), "log", "--no-color",
             f"--max-count={LOG_MAX_COMMITS}", "--format=%ct%x1f%h%x1f%s"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []

    commits = []
    for line in out.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3 or not parts[2].strip():
            continue
        try:
            when = float(parts[0])
        except ValueError:
            continue
        commits.append((when, parts[1].strip()[:7], parts[2].strip()))
    return commits


def _commit_entries(relay_dir, project, runners, own, now):
    """Commit entries for the log, bounded to the relay's own window.

    `own` is every entry the relay's own records produced - landings, handoffs
    and check transitions. Both bounds below are derived from it, and both
    exist because a project's history is far longer and far busier than the
    relay that supervises one slice of it (ACC-DATA-009).

    1. WINDOW. Commits dated before the earliest event the relay recorded are
       the project's history, not this run's, and get no entry. The bound is
       the relay's own oldest timestamp, never a count of commits: a relay
       whose first baton is minutes old sits in a repo with years of history,
       and the window is what tells the two apart.
    2. COUNT. Inside the window a busy project still commits faster than a
       relay lands legs, so at most as many commits are kept as the relay has
       events of its own, newest first. The window alone does not achieve
       ACC-DATA-009's second requirement - measured against the live
       agent-service relay, the window cuts 200 commits to 28 beside 14 relay
       events, which still buries the run. Only commits are capped. A baton, a
       handoff or a check transition is never dropped to make room: they are
       the events a supervisor came to the pane for.

    With no events at all - a brand-new relay, nothing landed yet - there is no
    window and nothing to count against, and the outer `--max-count` walk is
    the only bound left. Showing a fresh relay its recent commits is better
    than showing it nothing.
    """
    commits = _git_commits(relay_dir, project)
    if own:
        since = min(entry["t"] for entry in own)
        commits = [c for c in commits if c[0] >= since]
        # `git log` already yields newest first; sorting states the intent and
        # is stable, so equal commit times keep git's own order and the slice
        # stays deterministic across builds.
        commits = sorted(commits, key=lambda c: -c[0])[:len(own)]

    by_commit = {row["commit"]: row["leg"] for row in runners if row["commit"]}
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
    entries += _commit_entries(relay_dir, project, runners, list(entries), now)

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

    Raises RelayNotFound when the path is not a directory. Everything else
    degrades: a missing file is an empty panel, a malformed file is an empty
    panel plus a warning.
    """
    relay_dir = pathlib.Path(relay_dir)
    if not relay_dir.is_dir():
        raise RelayNotFound(f"no relay directory at {relay_dir}")

    warnings = []
    legsfile, legs_src = _load(relay_dir / "legs.json")
    state, state_src = _load(relay_dir / "state.json")
    extras, dash_src = _load(relay_dir / "dashboard.json")
    for name, src in (("legs.json", legs_src), ("state.json", state_src),
                      ("dashboard.json", dash_src)):
        if src == "malformed":
            warnings.append(f"{name} could not be parsed; it is being ignored")

    stages, leg_rows = _leg_rows(legsfile, warnings)
    stage_names = {s["id"]: s["name"] for s in stages}

    leg_counts = {"total": len(leg_rows)}
    leg_counts.update({s: sum(1 for l in leg_rows if l["status"] == s)
                       for s in LEG_STATES})

    # RULE: the active leg is the first leg in plan order whose own status is
    # running. state.json.currentLeg is a bookmark, kept only for diagnostics.
    declared = state.get("currentLeg") if _meaningful(state.get("currentLeg")) else None
    known_ids = {leg["id"] for leg in leg_rows}
    if declared and declared not in known_ids and legs_src == "ok":
        warnings.append(
            f"state.json: currentLeg '{declared}' has no leg entry in legs.json")

    active_leg = next((leg for leg in leg_rows if leg["status"] == "running"), None)
    if active_leg is not None:
        active_leg["isActive"] = True

    batons = _read_batons(relay_dir)
    runners, runner_counts, active_runner = _runner_rows(
        batons, leg_rows, active_leg["id"] if active_leg else None, now, warnings)

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

    stage_id = state.get("currentStage") if _meaningful(state.get("currentStage")) else None
    current_stage = None
    if stage_id:
        current_stage = {"id": stage_id, "name": stage_names.get(stage_id)}
        if stage_id not in stage_names and legs_src == "ok":
            warnings.append(
                f"state.json: currentStage '{stage_id}' is not a stage in legs.json")

    name = legsfile.get("relay") or state.get("relay")
    name = name if _meaningful(name) else None
    title = extras.get("title") if _meaningful(extras.get("title")) else None

    # RULE: `path` is the project the relay supervises. A relay directory called
    # `.relay` sits inside its project, so the project is its parent; a
    # directory called anything else (a fixture, a copy) is its own path.
    if _meaningful(extras.get("path")):
        path = extras["path"]
    else:
        resolved = relay_dir.resolve()
        path = str(resolved.parent if resolved.name == ".relay" else resolved)

    # ACC-DATA-008: a metric with no source is a missing key, not a zero.
    metrics = {}
    if _meaningful(extras.get("elapsed")):
        metrics["elapsed"] = extras["elapsed"]
    tokens = extras.get("tokens")
    if isinstance(tokens, dict):
        measured = {k: v for k, v in tokens.items() if _meaningful(v)}
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

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

So is the *shape* of every path. A relay file can be a FIFO, a socket, a device,
a directory, a symlink to any of those or to nothing at all, a file this process
may not open, or one too large for a repaint to afford. Each is a warning naming
what was found, and `build()` returns - and none of them blocks, which matters
more than not raising: a read stopped in the kernel freezes the view with no
traceback to read. Every relay-file read goes through `_read_relay_file`, which
opens nothing it has not confirmed is a regular file.

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
A commit a baton claims and the repository confirms is this run's work on any
repository topology at all - no window, no floor, no branch point - and it is
fetched by name so that no walk can lose it. A commit no leg claims is bounded
by the relay's earliest record, so the log tells the run's story rather than the
repository's (ACC-DATA-009, simplified 2026-08-26).

Determinism
-----------
The same directory and the same clock yield the same model. `now` is the only
wall-clock input and the only source of non-determinism in the module: omitted,
it is read once at the top of `build()` so that ages and elapsed times are real
(ACC-DATA-005); passed an epoch value it is pinned; passed `None` it is refused
outright and every now-derived field stays `None`, which is what a frame
capture wants. `git` is read through a list argv with a timeout and a commit
cap, and its absence is not an error.
"""

import json
import os
import pathlib
import re
import stat
import subprocess
import time

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


#: `build()`'s `now` when the caller did not mention a clock at all, which is
#: distinct from a caller who passed `None` to refuse one. Two callers want two
#: different things and they used to share one spelling:
#:
#: * a view, and every documented one-argument call, wants relative ages and
#:   elapsed times - ACC-DATA-005 requires an age on every log entry under that
#:   signature, and defaulting to no clock left it `None` on every entry of
#:   every relay while the check's evidence still passed;
#: * a test and a frame capture want the model to be byte-identical across
#:   builds, which only a fixed clock - or none - can give.
#:
#: So the default reads the wall clock and `now=None` is the explicit refusal.
#: A sentinel rather than a magic number, because every real epoch value is a
#: legitimate thing to pin the model to.
_NO_CLOCK = object()


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
# reading — the one door
# --------------------------------------------------------------------------
#
# A relay directory is not a document, it is a path, and a path is whatever the
# filesystem says it is. `.relay/batons/x.md` can be a FIFO, a unix socket, a
# character device, a directory, a symlink to any of those, a symlink to
# nothing, a symlink to itself, or a file this process may not open. None of
# those is a hypothetical: a relay directory is edited by hand under a live
# view, and every one of them was reachable here.
#
# What that costs is worse than an exception. `build()` runs once per repaint
# inside a 2 s budget (ACC-LIVE-001), so a read that blocks does not produce a
# traceback - it produces a frozen TUI with no output at all. Opening a FIFO
# with no writer blocks in the kernel for ever; reading /dev/zero to EOF never
# reaches one. The S1 gate captured both.
#
# RULE: every relay-file read in this module goes through `_read_relay_file`,
# and it opens nothing it has not first confirmed is a regular file. A
# `try/except` per call site was what this module had, and it grew a third read
# that had neither the guard nor the test - one door is the thing a test can
# hold the module to (`test_every_relay_file_read_goes_through_the_one_guarded_helper`).

# The most a relay file may weigh before the model refuses to read it.
#
# A bound belongs here: this module reads linearly - 8 MB costs about a second,
# measured - and one repaint reads three JSON files plus a baton per leg inside
# 2 s. Unbounded, a coach who pastes a build log into a baton stalls the view.
# The largest relay file this project has ever written is 45 KB, so 1 MiB is
# more than twenty times the worst real case and still a fraction of a repaint.
#
# RULE: an oversized file is refused, never truncated. A half-read baton still
# parses - it just reports the wrong line count and silently drops every commit
# claim past the cut - and a plausible wrong answer costs more here than a
# named absence does.
MAX_RELAY_FILE_BYTES = 1024 * 1024

# O_NONBLOCK is the whole guard against a pipe: with it the open returns at
# once whether or not a writer exists, and `fstat` on the descriptor - not on
# the path, which a rename could change underneath the check - says what was
# really opened. O_NOCTTY keeps a terminal device from becoming this process's
# controlling terminal on the way past.
_OPEN_FLAGS = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
               | getattr(os, "O_NOCTTY", 0))

_READ_CHUNK = 1 << 16

_SHAPES = (
    (stat.S_ISDIR, "a directory"),
    (stat.S_ISFIFO, "a FIFO"),
    (stat.S_ISSOCK, "a socket"),
    (stat.S_ISCHR, "a character device"),
    (stat.S_ISBLK, "a block device"),
    (stat.S_ISLNK, "a symbolic link"),
    (stat.S_ISREG, "a regular file"),
)


def _shape(mode):
    """What a path turned out to be, in words a warning can carry."""
    for is_kind, name in _SHAPES:
        if is_kind(mode):
            return name
    return "of a kind this model cannot read"


def _errno_reason(exc):
    """Why the filesystem refused, in the words it used.

    Carried into the warning rather than flattened to "it could not be read",
    because a symlink loop, a permission bit and a socket are three different
    repairs for whoever left the path there.
    """
    return (exc.strerror or type(exc).__name__).lower()


def _read_relay_file(path):
    """(raw, mtime, why) for a relay file, without ever blocking.

    Three answers, and the caller can tell them apart:

    * `(bytes, mtime, None)` - a regular file, read whole.
    * `(None, None, None)`   - nothing is at that path.
    * `(None, None, why)`    - something is at that path that a relay file may
      not be, and `why` says what.

    The mtime comes from the same `fstat` that cleared the descriptor, so the
    time reported for a baton is the time of the bytes actually read rather
    than of whatever the path pointed at a moment later.
    """
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except FileNotFoundError:
        return None, None, None
    except OSError as exc:
        return None, None, f"it could not be read ({_errno_reason(exc)})"
    except (TypeError, ValueError):
        return None, None, "it is not a usable path"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, None, (
                f"it is {_shape(st.st_mode)}, not a regular file")
        if st.st_size > MAX_RELAY_FILE_BYTES:
            return None, None, _too_big(st.st_size)
        chunks, total = [], 0
        while True:
            try:
                chunk = os.read(fd, _READ_CHUNK)
            except OSError as exc:
                return None, None, (
                    f"it could not be read ({_errno_reason(exc)})")
            if not chunk:
                return b"".join(chunks), st.st_mtime, None
            chunks.append(chunk)
            total += len(chunk)
            # The size above is a snapshot; a live relay is being written to.
            # Bound the loop as well, so a file growing under the read cannot
            # outrun it.
            if total > MAX_RELAY_FILE_BYTES:
                return None, None, _grew_too_big(total)
    finally:
        os.close(fd)


def _too_big(size):
    return (f"it is over {size} bytes, past the {MAX_RELAY_FILE_BYTES}-byte "
            "limit one repaint can afford")


def _grew_too_big(total):
    """The in-loop bound's own words, deliberately not the pre-check's.

    The two bounds answer two different questions - "this file was already too
    big when it was opened" and "this file outgrew the bound while it was being
    read" - and they are two different repairs for whoever left the path there.
    They used to share one message, and because `MAX_RELAY_FILE_BYTES` is an
    exact multiple of `_READ_CHUNK` the byte count was identical too: the test
    for the pre-check passed by arithmetic coincidence and could not tell which
    of the two bounds had fired.
    """
    return (f"it grew past the {MAX_RELAY_FILE_BYTES}-byte limit one repaint "
            f"can afford while it was being read ({total} bytes and counting)")


def _load(path):
    """(data, state, why) where state is ok | missing | malformed.

    A relay file caught mid-write is malformed, not fatal: the model degrades to
    an empty panel and records a warning (ACC-LIVE-003 depends on this). So is a
    relay file that is not a file at all - the shape is named in `why` and the
    panel is empty either way, because there is no third thing a view can draw.

    The bytes are decoded here rather than by pathlib's text reader, because a
    write interrupted inside a multi-byte character produces a file that is not
    valid UTF-8 at all, and a text read raises that before any JSON parsing is
    attempted. A dashboard watching a live relay meets exactly that file. `why`
    names which repair the file needs, since "not valid UTF-8" and "not valid
    JSON" are different problems for whoever wrote it.
    """
    raw, _mtime, why = _read_relay_file(path)
    if raw is None:
        return ({}, "missing", None) if why is None else ({}, "malformed", why)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {}, "malformed", "it is not valid UTF-8"
    try:
        data = json.loads(text)
    except ValueError:
        return {}, "malformed", "it is not valid JSON"
    except RecursionError:
        # The JSON scanner recurses once per level and guards itself with a
        # RecursionError, which is a RuntimeError and not the ValueError above.
        # A relay file nesting twenty thousand arrays deep is 40 KB of text and
        # took `build()` down with it.
        return {}, "malformed", "it is nested too deeply to parse"
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
    """The full prose of a baton, or None when there is no baton to read.

    Kept out of `build()` on purpose: it is long, it is prose (em-dashes and
    all), and only a detail view wants it. Reading it here rather than in the
    view keeps every relay-file read inside this module (ACC-DATA-001), and
    inside the one guard - this is public, a detail view calls it on whatever
    path a row carries, and it owes the same "never blocks" `build()` does.
    """
    raw, _mtime, _why = _read_relay_file(path)
    return None if raw is None else raw.decode("utf-8", "replace")


def _read_baton(path):
    """(baton, why): what a baton reliably carries - when it landed, how long
    it is, the commits it claims, and a status if the runner wrote one - or
    `(None, why)` when the path is not a baton this model can read.

    `why` is None when there is simply nothing there. A path that holds
    something a baton may not be is a warning the caller raises, because a
    runner row silently missing its commit is the kind of absence this model
    exists to name rather than to show.

    `commit` starts as the first claim and is settled against the relay's own
    repository by `_settle_commits` - a claim the repository cannot confirm is
    not this leg's work (ACC-DATA-009). Outside a repository there is nothing
    to confirm it against and the baton's own word is all the evidence there
    is, so the first claim stands.
    """
    raw, mtime, why = _read_relay_file(path)
    if raw is None:
        return None, why
    text = raw.decode("utf-8", "replace")
    m = STATUS_RE.search(text)
    claims = commit_claims(text)
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    return {
        "status": BATON_STATUS.get(m.group(1).lower()) if m else None,
        "claims": claims,
        "commit": claims[0] if claims else None,
        "lines": text.count("\n") + 1,
        "mtime": mtime,
        "path": resolved,
    }, None


def _render(value):
    """A value that is not a string, rendered as one - or named, when it cannot be.

    `json.dumps` and `repr` both recurse once per level of nesting, and a relay
    file is untrusted: a list nested twelve thousand deep parses and then blows
    the stack on the way back out, with `build()`'s own frames already on it.
    That is a RecursionError escaping `build()`, which ACC-DATA-001 forbids as
    plainly as it forbids any other.
    """
    try:
        return json.dumps(value, sort_keys=True)
    except (RecursionError, TypeError, ValueError):
        return f"an unreadable {type(value).__name__}"


def _strlist(value):
    """A list of strings from a field a coach may have written as a bare string."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [v if isinstance(v, str) else _render(v) for v in value]
    try:
        return [str(value)]
    except RecursionError:
        return [f"an unreadable {type(value).__name__}"]


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

    # RULE: a leg of no declared stage ranks after every declared one, and the
    # rank that does that is `len(stages)` - NOT `len(stage_rank)`.
    # `stage_rank` is built by skipping unusable ids and by collapsing
    # duplicates, so it is SHORTER than `stages` for exactly the files this
    # module already warns about: a null id, a list id, a repeated id. Its
    # length then collides with a real stage's rank, and where the stage
    # entries also carry no `legs` array the within-stage key ties too and the
    # file index decides - putting a leg belonging to no declared stage AHEAD
    # of S1, which moves `activeLeg` (ACC-DATA-002's second rule). The ranks
    # themselves are indices into `stages`, so `len(stages)` is above all of
    # them however many ids were unusable.
    tail = len(legs) + 1
    decorated = []
    for i, leg in enumerate(legs):
        sid = leg.get("stage")
        decorated.append((
            (stage_rank.get(sid, len(stages)),
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

def _read_batons(relay_dir, warnings):
    """{leg id: baton} for every baton on disk, read once per build.

    Two panels want them - the runner rows and the derived progress log - and
    the batons directory is read exactly once for both.

    `batons` is a path like any other, so it too can be a FIFO, a socket, a
    regular file, a symlink to nothing, or a directory this process may not
    list. Each of those means the same thing to a view - no runner carries a
    baton - and each is a different repair, so each is named.
    """
    bdir = relay_dir / "batons"
    batons = {}
    try:
        st = os.stat(bdir)
    except FileNotFoundError:
        return batons                  # a relay that has run no legs yet
    except OSError as exc:
        warnings.append(f"the batons directory could not be read "
                        f"({_errno_reason(exc)}); no runner row carries a baton")
        return batons
    if not stat.S_ISDIR(st.st_mode):
        warnings.append(f"batons is {_shape(st.st_mode)}, not a directory; "
                        "no runner row carries a baton")
        return batons
    try:
        # `os.scandir` rather than a glob, so a directory that cannot be listed
        # is an error this function sees rather than an empty result it cannot
        # tell from a relay whose runners have written nothing.
        with os.scandir(bdir) as entries:   # guarded read: OSError below
            names = sorted(e.name for e in entries if e.name.endswith(".md"))
    except OSError as exc:
        warnings.append(f"the batons directory could not be listed "
                        f"({_errno_reason(exc)}); no runner row carries a baton")
        return batons
    for name in names:
        path = bdir / name
        baton, why = _read_baton(path)
        if baton is None:
            if why is not None:
                warnings.append(f"batons/{name} could not be read: {why}; "
                                "its runner row carries no baton")
            continue
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

    ordered = sorted(done, key=key) + sorted(running, key=lambda leg: leg["order"])

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
# than for the run. Whether an UNCLAIMED commit belongs to the log at all is
# decided by the relay's record floor and the attribution budget in
# `_commit_entries` (ACC-DATA-009), which are much tighter bounds in practice
# and are not a replacement for these. A claimed commit is decided by neither:
# it is this run's work on the strength of the claim and the repository. The
# entry bound is applied to the relay's own events before the budget is worked
# out, so that the loosest bound in the module cannot re-order what the
# tightest one decided.
#
# WHAT THE WALK DROPS, and why in that order. `git log` walks back from HEAD,
# so `LOG_MAX_COMMITS` keeps the NEWEST commits of the window and drops the
# oldest: a run whose branch carries more than 200 commits loses the commit
# entries of its first legs'. That is the one direction git bounds cheaply -
# asking for the OLDEST two hundred means walking all of them, on every
# repaint - and it is affordable here because a leg's landing entry names its
# own commit sha whatever the walk returned. It does not even cost the subject
# line any more: a claimed commit the walk did not reach is fetched by name
# (`_claimed_commits`), because ACC-DATA-009 admits it on the strength of the
# claim and the repository, and no bound on a walk may take it away. So this
# bound stands over UNCLAIMED commits only, and is deliberately the loosest.
LOG_MAX_COMMITS = 200
LOG_MAX_ENTRIES = 300
GIT_TIMEOUT = 3.0

# THERE IS NO BRANCH POINT IN THIS MODULE, IN ANY ROLE (ACC-DATA-009, corrected
# 2026-08-26). A tuple of default-branch ref names used to live here, and
# `_relay_commits` narrowed its walk with `git log HEAD --not <those refs>`.
# That was the sixth instance of deciding by topology, and the contract's own
# wording had permitted it: it asked for "a performance bound on how far back
# to look, never a decision about what belongs", and `--not <ref>` cannot be
# that. `--not` excludes commits REACHABLE FROM another ref, so after a trunk
# is merged into the run's branch an unclaimed commit above the record floor
# and reachable from HEAD was dropped from the log solely because `main` also
# reached it.
#
# The walk is bounded by DEPTH and by the record floor, and by nothing else:
# `--max-count` says how far back to look and `_floored` says what is inside
# the window. Both are properties of this run; neither can be changed by where
# some other ref happens to point. The cost is one walk that is sometimes
# longer than it needs to be, which is the trade the contract asks for.

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


def _repo_dir(relay_dir):
    """The work tree the relay's repository is READ from, or None.

    ASK GIT (ACC-DATA-009, amended 2026-08-26). This used to walk parents for
    a `.git` and stop at the relay directory's immediate parent, which encoded
    a guess - that a relay sits beside its repository root. `<repo>/services/
    <svc>/.relay` is an ordinary monorepo shape and the guess is simply wrong
    there: a relay one directory down found no `.git` within its bound and was
    read as owning no repository, so every leg-claimed commit left the log
    while the runner rows went on naming them. Git answers this correctly from
    any subdirectory of a work tree, so the answer is git's and no longer a
    walk's.

    WHAT REMAINS A BOUND is the relay's own SHAPE, which is not repository
    topology and is the same split `_project_dir` derives the project label
    from:

    * a relay directory called `.relay` sits INSIDE its project, so the project
      is whatever work tree contains it, at any depth. This is the live shape.
    * a relay directory called anything else IS its project - every fixture
      under `tests/fixtures/`, and any copied relay. Its repository is the one
      ROOTED AT IT, and a repository it merely happens to sit inside is not
      its: without this, every fixture in this repository would report this
      repository's commits as its own.

    THE SHAPE IS ONE LIST (`_repo_roots`) and it is consulted twice, for two
    different jobs. It skips the process spawn where no candidate holds a
    `.git` at all - most relay directories a view opens are inside a
    repository, but a fixture or a copied relay is not, and a spawn per repaint
    for a guaranteed failure is not free. Then it checks GIT'S ANSWER, which is
    the bound itself: a `.git` that git does not accept - an empty directory, a
    copy that lost its objects - makes git report the HOST work tree instead,
    and a fixture would inherit the host repository's commits on the strength
    of a directory that is not a repository at all. The precondition says
    whether to ask; only the answer decides.

    Never raises: `relay_dir` may be unreadable, git may be absent, and each of
    those means the same thing here.
    """
    try:
        resolved = pathlib.Path(relay_dir).resolve()
    except OSError:
        return None
    roots = _repo_roots(resolved)
    if not any(_has_git(root) for root in roots):
        return None
    out = _git(resolved, "rev-parse", "--show-toplevel")
    # GIT'S ANSWER, compared as it arrives. `--show-toplevel` prints the work
    # tree's REAL path - absolute and symlink-resolved even where
    # `core.worktree` names a symlink - and the question was asked from a
    # resolved directory, so there is nothing left here to resolve.
    #
    # Where git could not answer at all - no work tree, a `.git` it will not
    # accept, no git installed - the text is empty and `pathlib.Path("")` is
    # `pathlib.Path(".")`: relative, and every candidate root is absolute, so
    # no answer matches nothing. That is deliberate and is why it is not
    # resolved: `Path("").resolve()` is the PROCESS'S working directory, and a
    # dashboard is opened from wherever a supervisor's shell happens to be,
    # which is very often inside some other project's repository.
    top = pathlib.Path((out or "").strip())
    return top if top in roots else None


def _repo_roots(resolved):
    """The directories this relay's repository may be rooted at, nearest first.

    THE ONE BOUND LEFT, written once. It is a property of the relay's SHAPE and
    says nothing about any repository:

    * a relay directory called `.relay` sits INSIDE its project, so its
      repository may be rooted at any ancestor - `<repo>/.relay` and
      `<repo>/services/<svc>/.relay` are the same shape at two depths, and the
      guess that it was always the immediate parent is what lost a monorepo
      relay its whole history.
    * a relay directory called anything else IS its project, so its repository
      is the one rooted at it and nothing above it.

    `resolved.parents` runs to the filesystem root deliberately: depth is not
    bounded, because a monorepo's is not.
    """
    if resolved.name == ".relay":
        return [resolved, *resolved.parents]
    return [resolved]


def _has_git(path):
    """Whether `path` holds a `.git`. A refusal is not an answer, and not fatal.

    `pathlib`'s `exists()` swallows the errors that mean "nothing is there" -
    ENOENT, ENOTDIR, ELOOP, EBADF - and lets EACCES through, because "I may not
    look" is not "there is nothing here". A relay directory with no search bit
    is still a directory, so `build()` is well past its RelayNotFound guard by
    the time this runs; an exception escaping here is an uncaught traceback
    inside a 2 s repaint loop, which ACC-DATA-001 forbids as plainly as any
    other. Answering False lets the caller go on to the next candidate: the
    directory that could not answer is the one whose parent may hold the
    repository, and giving up at the first refusal would cost a chmod'd live
    relay its own history.
    """
    try:
        return (path / ".git").exists()
    except OSError:
        return False


def _project_dir(written, relay_dir, warnings, repo):
    """The relay's project as a LABEL, plus a warning for a `path` it is not.

    `written` is `dashboard.json.path` as the coach typed it, or None. That
    string is quoted back verbatim as `relay.path` - a supervisor's label for
    their own project is theirs - and this function answers the label to fall
    back to when there is none. It no longer decides where a commit is READ
    from: `_repo_dir` asks git that, and git is right at every depth
    (ACC-DATA-009, amended 2026-08-26). A correct `path` used to fail to rescue
    a relay below the repository root, and was warned about into the bargain;
    an untrusted string cannot redirect a read it no longer bounds.

    RULE: a value that names no directory is a coach's typo, and it is handled
    the way every other malformed field here is (`_text_or_warn`) - warned
    about and ignored. `~` is expanded first, because a coach writes a shell
    path by hand and nothing else in this module expands one; this repository's
    own `dashboard.json` said `~/Documents/...` and was read as a directory
    called `~` under the process's working directory.

    RULE: a value that names a directory that is not this relay's project is
    warned about on the same terms (ACC-DATA-009). Silence is not an acceptable
    answer to "I could not use what you wrote" - a clone of a relay's own
    repository inherits its coach's `path`, naming the source project, and
    `relay.path` reports it all the while as though it were in use.

    WHAT COUNTS AS THE PROJECT is every directory from the repository root down
    to the relay's own container: `<repo>` and `<repo>/services/<svc>` are both
    honest labels for a relay at `<repo>/services/<svc>/.relay`, and an
    ancestor of the root, a different repository, a `~` that expands to
    neither, and the relay directory itself are none of them. Where git reports
    no repository the container is all there is.

    Never raises: `written` is untrusted, and every shape of it that cannot be
    read is the same answer here.
    """
    resolved = relay_dir.resolve()
    container = resolved.parent if resolved.name == ".relay" else resolved
    if written is None:
        return str(container)
    try:
        expanded = os.path.expanduser(written)
        usable = os.path.isdir(expanded)
    except (OSError, TypeError, ValueError):
        expanded, usable = written, False
    if not usable:
        warnings.append(f"dashboard.json: `path` {written!r} is not a directory; "
                        "commits are being read from the relay's own project "
                        "instead")
        return str(container)
    try:
        target = pathlib.Path(expanded).resolve()
    except (OSError, ValueError):
        target = None
    if target not in _project_labels(container, repo):
        warnings.append(f"dashboard.json: `path` {written!r} does not name this "
                        "relay's own project; commits are being read from "
                        f"{str(container)!r} instead")
    return str(container)


def _project_labels(container, repo):
    """Every directory that honestly names the relay's project.

    The relay's own container and everything between it and the repository root
    git reported, inclusive. At depth 0 that is one directory; in a monorepo it
    is the service directory, the repository root, and each step between.
    """
    labels = [container]
    if repo is not None and repo != container:
        labels.extend(p for p in container.parents
                      if p == repo or repo in p.parents)
    return labels


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
    repository's commits as this relay's. That is the defect `_repo_dir`
    bounds away by asking git, walked back in through the environment - and
    `_repo_dir` asks git through this function, so the scrub bounds its own
    answer too.
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


def _resolve_shas(repo, shas):
    """Which of `shas` name a commit in the relay's own repository.

    `repo` is the work tree git reported for the relay (`_repo_dir`), or None.

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
    if not order or repo is None:
        return None
    out = _git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype)",
               stdin="\n".join(order) + "\n")
    if out is None:
        return None
    lines = out.splitlines()
    if len(lines) != len(order):
        return None            # not the answer that was asked for; do not guess
    return {sha for sha, line in zip(order, lines)
            if line.rsplit(" ", 1)[-1:] == ["commit"]}


def _settle_commits(repo, batons):
    """Settle every baton's claimed commit against the relay's own repository.

    A leg is credited with a commit only when its baton claims the sha as its
    own work AND the sha is a commit in the repository the relay supervises
    (ACC-DATA-009). Both halves are load-bearing: this relay's own judges wrote
    reports quoting another relay's shas in claim-shaped sentences, and those
    shas are not objects here.
    """
    resolved = _resolve_shas(
        repo, {sha for b in batons.values() for sha in b["claims"]})
    if resolved is None:
        return
    for baton in batons.values():
        baton["commit"] = next((sha for sha in baton["claims"] if sha in resolved),
                               None)


def _parse_commits(out):
    """[(epoch, short sha, subject)] from the one commit format asked for here.

    Shared by the two reads below, so a commit the walk returned and a commit
    fetched by name arrive in the same shape - cut to the same seven characters
    a baton's claim is cut to (`commit_claims`), which is what makes a claim
    and a commit compare as equals rather than by prefix. A line that is not
    that shape is skipped rather than guessed at.

    RULE: the subject takes everything after the second separator, and an empty
    one is still a commit. A subject may legally contain `\x1f` and a commit
    may legally have no subject at all; a parser that drops either is a bound
    on a walk deciding what belongs, which on a claimed commit is precisely
    what ACC-DATA-009 no longer permits.
    """
    commits = []
    for line in (out or "").splitlines():
        parts = line.split("\x1f", 2)
        if len(parts) != 3:
            continue
        try:
            when = float(parts[0])
        except ValueError:
            continue
        sha = parts[1].strip()[:7]
        if sha:
            commits.append((when, sha, parts[2].strip()))
    return commits


def _git_log(repo):
    """[(epoch, short sha, subject)] for HEAD, newest first.

    ONE BOUND, AND IT IS A DEPTH (ACC-DATA-009, corrected 2026-08-26):
    `--max-count` says how far back to look. There is deliberately no `--not`,
    no `^ref` and no `a..b` here. Each of those excludes what another ref can
    REACH, which is a decision about what belongs wearing a bound's clothes,
    and it is how the sixth topology defect got in - see `_relay_commits`.
    """
    return _parse_commits(_git(
        repo, "log", "--no-color", f"--max-count={LOG_MAX_COMMITS}",
        "--format=%ct%x1f%h%x1f%s", "HEAD"))


def _claimed_commits(repo, shas):
    """The commits `shas` names, whatever the walk did or did not reach.

    THE RULE ACC-DATA-009 WAS SIMPLIFIED TO (2026-08-26): a commit a baton
    claims and the repository confirms is this run's work - no window, no
    floor, no branch point. The walk cannot be what fetches it, because every
    topology that has broken this check broke it by narrowing the walk:
    `origin/HEAD` naming the run's own branch, a trunk called `develop` or
    `trunk`, and - the one that broke it in this repository - a branch already
    merged into its trunk, where everything HEAD carries is reachable from the
    trunk too and the walk comes back with nothing. Asking git for the objects
    BY NAME is immune to all of it: a sha the repository has is a sha
    `--no-walk` prints, from any HEAD, on any branch, in any clone.

    REACHABILITY IS DELIBERATELY NOT A CONDITION, and it is a real case rather
    than a hypothetical one: the live agent-service relay has two claimed shas
    (`096a713`, `5c9caf2`, merges that landed on `develop`) that its HEAD
    cannot reach. The claim is the evidence and the repository confirms the
    object, so the log carries them - which is also what its runner rows say.

    One process for every sha rather than one per sha, and none at all when
    the walk already reached them: `shas` is what the walk MISSED.
    """
    if not shas:
        return []
    return _parse_commits(_git(
        repo, "log", "--no-color", "--no-walk",
        "--format=%ct%x1f%h%x1f%s", *shas, "--"))


def _mtime(path):
    """The mtime of the regular file at `path`, or None when there is none.

    A record that carries no timestamp of its own is timed by the file that
    holds it, and this is the only way to ask. Never raises: the file may be
    gone, may be a directory, may be unreadable - each of those means the same
    thing here, which is that this record cannot be timed.
    """
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    return st.st_mtime if stat.S_ISREG(st.st_mode) else None


def _relay_records(relay_dir, runners, batons, checks, events):
    """What the relay has RECORDED about itself: `(has_records, floor)`.

    THE INVARIANT (ACC-DATA-009): the commit window is a property of what the
    relay has recorded on disk, and never a property of what the log derivation
    has so far produced. `events` is one input here and never the deciding one.

    That distinction is the whole reason this function exists. A relay one leg
    in - a single `running` leg holding the only baton - derives no entry at
    all: its baton is skipped as a landing because the leg has not landed, and
    its start is unknown because there is no previous baton to hand off from.
    Deriving the window from the entry list therefore gave it NO window, and
    every commit in the repository it happens to sit in became one of its own.
    Four fixes to that class landed and the class stayed open, because each was
    written against the shape in front of it rather than against the rule.

    `has_records` is the contract's degenerate carve-out read backwards. A
    relay has no window only when it has no baton, no running leg and no judged
    check - and there, showing recent commits is the only story there is. Every
    other relay has a window, INCLUDING one whose records have produced no
    visible entry yet.

    `floor` is the earliest of those records, or None when none of them can be
    timed - a relay can have a record whose time is unreadable, and a window
    with no floor is still a window: the budget still applies, and a claimed
    commit never needed a floor to begin with. Three sources, in decreasing
    order of how well they date themselves:

    * a baton's mtime is when its runner landed. Every baton counts, including
      the running leg's, which is exactly the one the entry list drops.
    * `legs.json` marks a leg `running`. That record carries no timestamp, so
      the file that holds it is the record and its mtime is when the relay last
      wrote it.
    * `state.json` records that a check was judged. Same reasoning, same file
      mtime.

    Derived event times join the union because the contract names them, but
    they can only ever agree with it: every entry the log derives is timed by a
    baton mtime already in the set, so including them can lower the floor and
    never raise it, and they never decide whether there is a floor at all.
    """
    running = any(row["status"] == "running" for row in runners)
    judged = any(_is_judged(check) for check in checks)
    has_records = bool(batons) or running or judged

    times = [baton["mtime"] for baton in batons.values()]
    if running:
        times.append(_mtime(relay_dir / "legs.json"))
    if judged:
        times.append(_mtime(relay_dir / "state.json"))
    times.extend(entry["t"] for entry in events)
    times = [t for t in times if t is not None]
    return has_records, (min(times) if times else None)


def _is_judged(check):
    """True when a judge has been over this check.

    A check nobody has judged is a plan, not a record: `pending`, no round, no
    fix leg, no judge. Any one of the four says the relay recorded something.
    """
    return (isinstance(check["round"], int)
            or check["status"] != "pending"
            or bool(check["fixLeg"])
            or bool(check["judgedBy"]))


def _relay_commits(repo, claimed):
    """Every commit the log may DRAW ON, newest first (ACC-DATA-009).

    `repo` is the work tree git reported for the relay (`_repo_dir`), or None
    where the relay owns no repository and there is nothing to draw on.

    This function makes both populations reachable; `_commit_entries` decides
    which of them belongs. NOTHING about the repository's topology is decided
    here, and after 2026-08-26 nothing about it is even asked:

    * every sha in `claimed` the repository confirms, fetched BY NAME so that
      no walk, and therefore no topology, can lose it (`_claimed_commits`).
    * one walk back from HEAD, bounded by `--max-count` and by nothing else.
      That is where the commits nobody claims come from, and the record floor
      in `_commit_entries` is what bounds them.

    THE WALK IS BOUNDED BY DEPTH, NEVER BY REACHABILITY. This function used to
    try `git log HEAD --not <default branch refs>` first and keep the result
    when it reached the floor. `--not` is not a depth bound - it drops every
    commit REACHABLE FROM another ref - so a trunk merged into the run's branch
    took unclaimed commits out of the log that HEAD carried and the floor
    admitted, for no reason a supervisor could read off the pane. Five legs
    died to a branch point that decided what belonged; the sixth died to one
    that was only supposed to bound a walk. There is no branch point here now.
    """
    if repo is None:
        return []
    commits = _git_log(repo)
    walked = {sha for _, sha, _ in commits}
    return commits + _claimed_commits(
        repo, [sha for sha in sorted(claimed) if sha not in walked])


def _floored(commits, floor):
    """The commits at or after `floor`; all of them when there is no floor.

    Inclusive: a runner that commits and writes its baton inside the same
    second is the ordinary case, not an edge one, and an exclusive floor loses
    that commit for a reason nobody could read off the pane.
    """
    if floor is None:
        return commits
    return [c for c in commits if c[0] >= floor]


def _commit_entries(repo, batons, records, budget, now):
    """Commit entries for the log: the run's own commits first (ACC-DATA-009).

    `records` is `(has_records, floor)` from `_relay_records`: what the relay
    has recorded about itself, read off disk. It is the floor of the unclaimed
    population here and the walk's coverage test in `_relay_commits`, and
    `budget` is how many unattributed commits may sit beside the relay's own
    events.

    RULE: `has_records` decides whether there is a window, and it is NOT
    "did the derivation produce an entry". A relay whose only records are a
    running leg and its baton derives no entry at all and still has a window -
    it is a relay one leg in, not a relay that has done nothing (ACC-DATA-009,
    as amended 2026-08-25). Gating on the entry list is how a run one leg in
    came to report forty of its project's unrelated commits as its own.

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

    RULE: ONE floor, and it governs one population (ACC-DATA-009, simplified
    2026-08-26). A commit a baton claims and the repository confirms is this
    run's work - no window, no floor, no branch point, and no topology can make
    that untrue, because the claim and the object are the whole of the
    evidence. A commit NO leg claims is floored at the relay's earliest record,
    because nothing else attests that it belongs to this run.

    This replaces two floors that both depended on the repository's topology.
    They were correct for the repository each was written against and wrong for
    the next one: the branch-point floor emptied the log of every claimed
    commit the day this relay's branch was merged into `main`, while the runner
    rows went on naming all twenty of them. A model that contradicts itself is
    a worse failure than one that omits, and under the rule above it cannot:
    the runner row and the commit entry read the same settled claim.

    A commit must still be absent from the log because it is out of window,
    never because the budget ran out before reaching it - the two are
    indistinguishable on the pane and only one of them is the property.

    Only commits are budgeted. A baton, a handoff or a check transition is
    never dropped to make room: they are the events a supervisor came to the
    pane for. And a relay with no records at all has neither a window nor
    anything to budget against, so the walk comes through as it came: see
    `_relay_commits`.
    """
    has_records, since = records
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

    # The claims are handed to the walk, not filtered out of it afterwards: a
    # claimed commit no walk on this topology would have reached is fetched by
    # name, which is what makes the rule above hold on ANY topology.
    commits = sorted(_relay_commits(repo, by_commit), key=lambda c: -c[0])
    if has_records:
        # One floor, one population. A claim carries its own evidence and is
        # kept unconditionally; the record floor bounds everything else.
        attributed = [c for c in commits if by_commit.get(c[1])]
        rest = _floored(
            [c for c in commits if not by_commit.get(c[1])], since)
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


def _derived_log(relay_dir, repo, runners, batons, checks, now):
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
    # THE INVARIANT: the window comes from the relay's records on disk, not
    # from `events`. `events` is what this function has so far DERIVED, and a
    # relay one leg in derives nothing while holding two records.
    records = _relay_records(relay_dir, runners, batons, checks, events)
    has_records = records[0]
    commits = _commit_entries(repo, batons, records, len(events), now)

    # The outer entry bound, applied once and last. It decides how much of the
    # log fits in a pane, and it must not decide which commits belong: that is
    # settled above. So it is spent in the order the pane is worth reading in.
    #
    # 1. Attributed commits: the run's own work, kept UNCONDITIONALLY. There
    #    used to be a `[:LOG_MAX_ENTRIES // 2]` here, and being newest-first it
    #    dropped the OLDEST attributed commits - a run's first legs, which is
    #    precisely the property this check exists to protect. At 160 legs each
    #    landing a commit it silently lost 24 of them. The contract says every
    #    commit attributable to a leg is kept, without a cap, and the git walk's
    #    own `LOG_MAX_COMMITS` (200, below the 300-entry bound) is what keeps
    #    the sum inside the log: at worst the log is 200 attributed commits and
    #    the 100 newest events, which is a full accounting of the run's work
    #    rather than a truncated one. Attributed commits cannot outnumber the
    #    relay's recorded events either way - each one is claimed by a baton,
    #    and every baton is an event.
    # 2. The relay's own events, newest first, in whatever room is left.
    # 3. Unattributed commits, with what remains, and never more of them than
    #    there are events to bury - unless the relay has no records at all,
    #    which has nothing to bury and nothing to count against (ACC-DATA-009).
    #    The predicate is the relay's records, not this function's output: a
    #    relay one leg in has records and no entries, and it is not the
    #    degenerate case.
    attributed = [e for e in commits if e["leg"]]
    room = LOG_MAX_ENTRIES - len(attributed)
    kept = events[:room]
    spare = room - len(kept)
    if has_records:
        spare = min(spare, max(0, len(kept) - len(attributed)))
    entries = kept + attributed + [e for e in commits if not e["leg"]][:spare]

    entries.sort(key=lambda e: (-e["t"], LOG_KIND_ORDER[e["kind"]], e["m"]))
    return entries[:LOG_MAX_ENTRIES]


def _log(extras, relay_dir, repo, runners, batons, checks, now, warnings):
    """The Progress Log, and where it came from.

    ACC-DATA-006: a coach who writes `dashboard.json.log` is quoted verbatim,
    down to whatever they put in `t` - it is their pane, and `logSource` tells
    a view it is reading prose rather than the model's own numbers.
    ACC-DATA-005: otherwise the log is derived. `logSource` is None when there
    was nothing to derive, so a view says "none" honestly instead of rendering
    `1-0 of 0`.

    RULE: `log` degrades the way every other dashboard field does - named, not
    silently. `tokens`, `title` and `path` each warn when a coach writes
    something the field cannot hold, and `log` was the one that did not: a
    `log` that is a dict or a string was dropped without a word, and a non-dict
    entry inside the array was quietly reshaped, which is not verbatim either.

    RULE: an EMPTY array is not a written log. A coach who writes `[]` has
    narrated nothing, and the derived log is better than an empty pane, so it
    falls through with no warning. That is a reading of the contract's wording
    rather than a defect, it is pinned by a test, and it is stated here so the
    next reader does not "fix" it.

    A non-dict entry cannot be passed through as it stands, because every view
    reads `t` and `m` off an entry; it is quoted into the entry shape with no
    time of its own - never a placeholder - and the reshaping is warned about.
    """
    raw = extras.get("log")
    if raw is not None and not isinstance(raw, list):
        warnings.append(f"dashboard.json: `log` is a {type(raw).__name__}, not "
                        "an array; the derived log is being used instead")
    if isinstance(raw, list) and raw:
        entries = []
        for i, entry in enumerate(raw):
            if isinstance(entry, dict):
                entries.append(entry)
                continue
            warnings.append(
                f"dashboard.json: log entry #{i} is a {type(entry).__name__}, "
                "not an object; it is being quoted as a message with no time "
                "of its own")
            entries.append({
                "t": None,
                "m": entry if isinstance(entry, str) else _render(entry),
                "cls": None,
            })
        return entries, "dashboard"

    entries = _derived_log(relay_dir, repo, runners, batons, checks, now)
    return (entries, "derived") if entries else ([], None)


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def build(relay_dir, now=_NO_CLOCK):
    """Build the reconciled view-model of the relay at `relay_dir`.

    `now` is the only clock, and it has three spellings:

    * omitted - `build(relay_dir)`, the documented call - reads the wall clock
      once, here, so every log entry carries a relative `age` and the running
      runner carries an elapsed `duration` (ACC-DATA-005). One read, at the top
      of the build, so every field in one model is measured against one instant
      and two panes cannot disagree about what "now" was.
    * an epoch float pins it: the model is byte-identical across builds, which
      is what a test and a frame capture need.
    * `None` refuses a clock outright: every now-derived field stays None and
      the model is deterministic without having to name a time. That used to be
      the default, and it is why ACC-DATA-005 was unsatisfiable under the
      signature its own evidence line names.

    Nothing below this line reads a clock, so determinism is a property of what
    the caller passes and of nothing else.

    Raises RelayNotFound when the path is not a directory, is empty, or is not
    a path at all - `build("")` names no relay, and building the process's
    working directory instead of saying so is how a view ends up drawing
    someone else's relay. Everything else degrades: a missing file is an empty
    panel, a malformed file is an empty panel plus a warning.
    """
    if now is _NO_CLOCK:
        now = time.time()
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
    leg_counts.update({s: sum(1 for row in leg_rows if row["status"] == s)
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

    # RULE: what is REPORTED and what is READ FROM are two answers, and they
    # come from two places. `relay.path` is the coach's own label for the
    # project, quoted as written - a label is not a directory (`~` is a shell
    # convention, and a path can name nothing at all). `repo` is the work tree
    # to read a repository from, and GIT answers that: `git rev-parse
    # --show-toplevel` is right from any subdirectory of a work tree, so
    # `<repo>/services/<svc>/.relay` reads its repository exactly as
    # `<repo>/.relay` does (ACC-DATA-009, amended 2026-08-26). An untrusted
    # string in a JSON file no longer redirects that read at all; where it
    # names something else, it is warned about.
    #
    # Resolved before the batons are read, because a baton's claimed commit is
    # settled against that repository before either the runner rows or the log
    # quotes it. Two panes naming different commits for one leg is the class of
    # defect this module exists to remove.
    repo = _repo_dir(relay_dir)
    written = _text_or_warn(extras.get("path"), "dashboard.json: `path`",
                            warnings)
    project = _project_dir(written, relay_dir, warnings, repo)
    path = written if written is not None else project

    batons = _read_batons(relay_dir, warnings)
    _settle_commits(repo, batons)
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

    log, log_source = _log(extras, relay_dir, repo, runners, batons, checks,
                           now, warnings)

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

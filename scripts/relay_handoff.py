#!/usr/bin/env python3
"""The structured JSON handoff a runner leaves for the coach.

    python3 scripts/relay_handoff.py validate LEG [--relay-dir .relay]
    python3 scripts/relay_handoff.py items    LEG [--relay-dir .relay]
    python3 scripts/relay_handoff.py summary  LEG [--relay-dir .relay]
    python3 scripts/relay_handoff.py new      LEG --stage STAGE [--relay-dir .relay]

Why it exists
-------------
A runner's handoff (the "baton") is currently prose Markdown whose header the
dashboard parses by regex. `references/execution.md` admits the failure mode:
write `**Status:**` instead of `**Status**:` and the status is not found at all,
so the dashboard shows the leg as Success whatever actually happened; drop the
backticks around the sha and the commit goes unattributed. A Factory Mission
uses a structured JSON handoff instead (successState, returnToOrchestrator,
verification.commandsRun[{command,exitCode,observation}], discoveredIssues
[{severity,description,suggestedFix}]). This script makes the JSON handoff
canonical for Relay and refuses one that is not well formed, so a malformed
handoff fails loudly at the moment it is written rather than silently reading
as success at the gate.

Exit status: 0 when there is nothing to act on; 1 when `validate` found
violations, `new` refused to overwrite, or a read found disposal items or
blocking issues to act on; 2 when the relay dir or the handoff file is absent
or unreadable (a FIFO, a directory, or a file past the 2 MiB ceiling).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path

__all__ = ["validate", "items", "summary", "scaffold", "main"]

#: The statuses a handoff may carry. Prose batons let a runner write "Success"
#: or "DONE" and the regex read it as success; here the word is fixed.
STATUSES = ("success", "partial", "failed", "blocked")

#: A discovered issue's severity. The coach disposes of every one; the
#: severity tells it how loudly.
SEVERITIES = ("blocking", "non_blocking", "suggestion")

#: A git object name, lower case, 7-40 hex chars -- the spelling `git log
#: --format=%h` gives a repository this size. Upper case is rejected here even
#: though git would resolve it, because a handoff that quotes a sha in the
#: wrong case is the kind of typo a structured field exists to catch.
_HEX_RE = re.compile(r"^[0-9a-f]{7,40}$")

#: The top-level keys a handoff carries, in the order the template writes them.
#: An unknown key is a violation -- a typo'd field is how a value goes silently
#: unread, which is the exact failure a structured handoff exists to prevent.
REQUIRED_KEYS = (
    "leg", "stage", "status", "commit", "sessionId", "returnToCoach",
    "salientSummary", "implemented", "leftUndone", "verification", "tests",
    "discoveredIssues", "procedureFollowed", "decisions", "repeatFriction",
)

#: Strings a runner writes meaning "there is nothing here". Kept consistent
#: with `relay_decide._NOTHING` and extended so a trailing full stop on any of
#: the three is dropped too -- the prose baton's `baton_items` drops "nothing"
#: and "none" with their dots but misses "n/a.", which this handoff corrects.
_NOTHING = {"nothing", "none", "n/a"}

# The most a handoff may weigh before the reader refuses it. Mirrors
# `relay_model.MAX_RELAY_FILE_BYTES` in spirit but at 2 MiB: a handoff is one
# leg's record, not a relay's, and a coach who pastes a build log into it must
# be refused rather than read.
MAX_HANDOFF_BYTES = 2 * 1024 * 1024

# O_NONBLOCK is the whole guard against a pipe: with it the open returns at
# once whether or not a writer exists, and `fstat` on the descriptor -- not on
# the path, which a rename could change underneath the check -- says what was
# really opened. Mirrors `relay_model._read_relay_file`; not imported from it
# because another agent is editing that module right now.
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


# --------------------------------------------------------------------------
# small untrusted-value helpers
# --------------------------------------------------------------------------

def _describe(value):
    """How a violation names what it found.

    `str '0'` and `bool True` read in a sentence; a list or a dict is named by
    its type, since interpolating a 12000-deep structure into a message is what
    blew the stack in the first place.
    """
    if isinstance(value, str):
        return f"str '{value}'"
    if isinstance(value, bool):
        return f"bool {value}"
    return type(value).__name__


def _shape(mode):
    for is_kind, name in _SHAPES:
        if is_kind(mode):
            return name
    return "of a kind this reader cannot read"


def _nonempty(value):
    return isinstance(value, str) and value.strip() != ""


def _is_int(value):
    # `True` is not an exit code or a mutation count; it is a bool wearing int's
    # clothes, and the prose baton read `exitCode: true` as success.
    return isinstance(value, int) and not isinstance(value, bool)


def _nothing(text):
    norm = text.strip().lower()
    return norm in _NOTHING or norm.rstrip(".") in _NOTHING


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------

def validate(data) -> list[str]:
    """Every violation of the handoff schema, as a sentence naming the JSON
    path and what was found. Returns ``[]`` when the handoff is well formed.

    Never raises, whatever ``data`` is -- a list, a string, ``None``, or a
    dict nested 12000 deep returns violations. Unknown top-level keys are a
    violation too, because a typo'd key is how a field goes silently unread.
    """
    try:
        return _validate(data)
    except RecursionError:
        return ["the handoff is nested too deeply to validate"]


def _validate(data) -> list[str]:
    v: list[str] = []
    if not isinstance(data, dict):
        v.append(f"top level: found {_describe(data)}, want an object")
        return v

    for key in data:
        if key not in REQUIRED_KEYS:
            v.append(f"{key}: unknown key; not part of the handoff schema")
    for key in REQUIRED_KEYS:
        if key not in data:
            v.append(f"{key}: required key is missing")

    status = data.get("status")
    status_done = isinstance(status, str) and status in ("success", "failed")

    _field_nonempty(v, "leg", data.get("leg"))
    _field_nonempty(v, "stage", data.get("stage"))
    _field_status(v, "status", status)
    _field_commit(v, "commit", data.get("commit"))
    _field_session(v, "sessionId", data.get("sessionId"))
    _field_bool(v, "returnToCoach", data.get("returnToCoach"))
    _field_nonempty(v, "salientSummary", data.get("salientSummary"))

    _field_str_list(v, "implemented", data.get("implemented"), nonempty=True)
    # An empty `implemented` is allowed only when the leg did no work -- a
    # blocked leg could not proceed and a partial leg has not filled it in yet.
    # A success or failed leg claims to be done, so an empty list there is the
    # silent-success failure this schema exists to catch. (The spec's wording
    # says "only when status is blocked"; the scaffold is a partial handoff
    # with empty lists that must validate clean, so partial is exempt too.)
    impl = data.get("implemented")
    if status_done and isinstance(impl, list) and not impl:
        v.append("implemented: is empty, allowed only when the leg did no work "
                 "(status is blocked or partial)")

    _field_str_list(v, "leftUndone", data.get("leftUndone"), nonempty=True)

    _field_verification(v, "verification", data.get("verification"), status_done)
    _field_tests(v, "tests", data.get("tests"))
    _field_issue_list(v, "discoveredIssues", data.get("discoveredIssues"))
    _field_procedure(v, "procedureFollowed", data.get("procedureFollowed"))
    _field_decisions(v, "decisions", data.get("decisions"))
    _field_str_list(v, "repeatFriction", data.get("repeatFriction"), nonempty=True)

    return v


def _field_nonempty(v, path, value):
    if not isinstance(value, str):
        v.append(f"{path}: found {_describe(value)}, want a non-empty string")
    elif not value.strip():
        v.append(f"{path}: is an empty string")


def _field_status(v, path, value):
    if not isinstance(value, str):
        v.append(f"{path}: found {_describe(value)}, want one of "
                 f"{', '.join(STATUSES)}")
    elif value not in STATUSES:
        v.append(f"{path}: '{value}' is not one of {', '.join(STATUSES)}")


def _field_commit(v, path, value):
    if value is None:
        return
    if not isinstance(value, str):
        v.append(f"{path}: found {_describe(value)}, want null or a 7-40 char "
                 "lowercase hex string")
    elif not _HEX_RE.match(value):
        v.append(f"{path}: '{value}' is not a 7-40 char lowercase hex string")


def _field_session(v, path, value):
    if value is None:
        return
    if not isinstance(value, str):
        v.append(f"{path}: found {_describe(value)}, want null or a non-empty "
                 "string")
    elif not value.strip():
        v.append(f"{path}: is an empty string")


def _field_bool(v, path, value):
    if not isinstance(value, bool):
        v.append(f"{path}: found {_describe(value)}, want a boolean")


def _field_str_list(v, path, value, nonempty):
    if not isinstance(value, list):
        v.append(f"{path}: found {_describe(value)}, want a list")
        return
    for i, item in enumerate(value):
        if not isinstance(item, str):
            v.append(f"{path}[{i}]: found {_describe(item)}, want a string")
        elif nonempty and not item.strip():
            v.append(f"{path}[{i}]: is an empty string")


def _field_verification(v, path, value, status_done):
    if not isinstance(value, dict):
        v.append(f"{path}: found {_describe(value)}, want an object")
        return
    cr = value.get("commandsRun")
    if "commandsRun" not in value:
        v.append(f"{path}.commandsRun: required key is missing")
    elif not isinstance(cr, list):
        v.append(f"{path}.commandsRun: found {_describe(cr)}, want a list")
    else:
        if status_done and not cr:
            v.append(f"{path}.commandsRun: is empty, allowed only when the leg "
                     "did no work (status is blocked or partial)")
        for i, entry in enumerate(cr):
            _check_command(v, f"{path}.commandsRun[{i}]", entry)

    ic = value.get("interactiveChecks")
    if "interactiveChecks" not in value:
        v.append(f"{path}.interactiveChecks: required key is missing")
    elif not isinstance(ic, list):
        v.append(f"{path}.interactiveChecks: found {_describe(ic)}, want a list")
    else:
        for i, entry in enumerate(ic):
            _check_interactive(v, f"{path}.interactiveChecks[{i}]", entry)


def _check_command(v, path, entry):
    if not isinstance(entry, dict):
        v.append(f"{path}: found {_describe(entry)}, want an object")
        return
    if "command" not in entry:
        v.append(f"{path}.command: required key is missing")
    else:
        c = entry["command"]
        if not isinstance(c, str):
            v.append(f"{path}.command: found {_describe(c)}, want a non-empty "
                     "string")
        elif not c.strip():
            v.append(f"{path}.command: is an empty string")
    if "exitCode" not in entry:
        v.append(f"{path}.exitCode: required key is missing")
    else:
        e = entry["exitCode"]
        if not _is_int(e):
            v.append(f"{path}.exitCode: found {_describe(e)}, want an integer")
    if "observation" not in entry:
        v.append(f"{path}.observation: required key is missing")
    else:
        o = entry["observation"]
        if not isinstance(o, str):
            v.append(f"{path}.observation: found {_describe(o)}, want a string")


def _check_interactive(v, path, entry):
    if not isinstance(entry, dict):
        v.append(f"{path}: found {_describe(entry)}, want an object")
        return
    for key in ("surface", "action", "observed", "evidence"):
        if key not in entry:
            v.append(f"{path}.{key}: required key is missing")
    for key in ("surface", "action", "observed"):
        if key in entry:
            val = entry[key]
            if not isinstance(val, str):
                v.append(f"{path}.{key}: found {_describe(val)}, want a "
                         "non-empty string")
            elif not val.strip():
                v.append(f"{path}.{key}: is an empty string")
    if "evidence" in entry:
        val = entry["evidence"]
        if not isinstance(val, str):
            v.append(f"{path}.evidence: found {_describe(val)}, want a string")


def _field_tests(v, path, value):
    if not isinstance(value, dict):
        v.append(f"{path}: found {_describe(value)}, want an object")
        return
    added = value.get("added")
    if "added" not in value:
        v.append(f"{path}.added: required key is missing")
    elif not isinstance(added, list):
        v.append(f"{path}.added: found {_describe(added)}, want a list")
    else:
        for i, item in enumerate(added):
            if not isinstance(item, str):
                v.append(f"{path}.added[{i}]: found {_describe(item)}, want a "
                         "string")
            elif not item.strip():
                v.append(f"{path}.added[{i}]: is an empty string")

    mut = value.get("mutations")
    if "mutations" not in value:
        v.append(f"{path}.mutations: required key is missing")
    elif not isinstance(mut, dict):
        v.append(f"{path}.mutations: found {_describe(mut)}, want an object")
    else:
        run = mut.get("run")
        run_ok = _is_int(run) and run >= 0
        if "run" not in mut:
            v.append(f"{path}.mutations.run: required key is missing")
        elif not run_ok:
            v.append(f"{path}.mutations.run: found {_describe(run)}, want a "
                     "non-negative integer")
        surv = mut.get("survived")
        surv_ok = _is_int(surv) and surv >= 0
        if "survived" not in mut:
            v.append(f"{path}.mutations.survived: required key is missing")
        elif not surv_ok:
            v.append(f"{path}.mutations.survived: found {_describe(surv)}, want "
                     "a non-negative integer")
        elif run_ok and surv > run:
            v.append(f"{path}.mutations.survived: {surv} is greater than "
                     f"run {run}")
        note = mut.get("note")
        if "note" not in mut:
            v.append(f"{path}.mutations.note: required key is missing")
        elif not isinstance(note, str):
            v.append(f"{path}.mutations.note: found {_describe(note)}, want a "
                     "string")

    cov = value.get("coverage")
    if "coverage" not in value:
        v.append(f"{path}.coverage: required key is missing")
    elif not isinstance(cov, str):
        v.append(f"{path}.coverage: found {_describe(cov)}, want a string")
    elif not cov.strip():
        v.append(f"{path}.coverage: is an empty string")


def _field_issue_list(v, path, value):
    if not isinstance(value, list):
        v.append(f"{path}: found {_describe(value)}, want a list")
        return
    for i, entry in enumerate(value):
        _check_issue(v, f"{path}[{i}]", entry)


def _check_issue(v, path, entry):
    if not isinstance(entry, dict):
        v.append(f"{path}: found {_describe(entry)}, want an object")
        return
    sev = entry.get("severity")
    if "severity" not in entry:
        v.append(f"{path}.severity: required key is missing")
    elif not isinstance(sev, str):
        v.append(f"{path}.severity: found {_describe(sev)}, want one of "
                 f"{', '.join(SEVERITIES)}")
    elif sev not in SEVERITIES:
        v.append(f"{path}.severity: '{sev}' is not one of "
                 f"{', '.join(SEVERITIES)}")
    desc = entry.get("description")
    if "description" not in entry:
        v.append(f"{path}.description: required key is missing")
    elif not isinstance(desc, str):
        v.append(f"{path}.description: found {_describe(desc)}, want a "
                 "non-empty string")
    elif not desc.strip():
        v.append(f"{path}.description: is an empty string")
    fix = entry.get("suggestedFix")
    if "suggestedFix" not in entry:
        v.append(f"{path}.suggestedFix: required key is missing")
    elif not isinstance(fix, str):
        v.append(f"{path}.suggestedFix: found {_describe(fix)}, want a string")


def _field_procedure(v, path, value):
    if not isinstance(value, list):
        v.append(f"{path}: found {_describe(value)}, want a list")
        return
    for i, entry in enumerate(value):
        _check_procedure(v, f"{path}[{i}]", entry)


def _check_procedure(v, path, entry):
    if not isinstance(entry, dict):
        v.append(f"{path}: found {_describe(entry)}, want an object")
        return
    step = entry.get("step")
    if "step" not in entry:
        v.append(f"{path}.step: required key is missing")
    elif not isinstance(step, str):
        v.append(f"{path}.step: found {_describe(step)}, want a non-empty string")
    elif not step.strip():
        v.append(f"{path}.step: is an empty string")
    foll = entry.get("followed")
    if "followed" not in entry:
        v.append(f"{path}.followed: required key is missing")
    elif not isinstance(foll, bool):
        v.append(f"{path}.followed: found {_describe(foll)}, want a boolean")
    note = entry.get("note")
    if "note" not in entry:
        v.append(f"{path}.note: required key is missing")
    elif not isinstance(note, str):
        v.append(f"{path}.note: found {_describe(note)}, want a string")


def _field_decisions(v, path, value):
    if not isinstance(value, list):
        v.append(f"{path}: found {_describe(value)}, want a list")
        return
    for i, entry in enumerate(value):
        _check_decision(v, f"{path}[{i}]", entry)


def _check_decision(v, path, entry):
    if not isinstance(entry, dict):
        v.append(f"{path}: found {_describe(entry)}, want an object")
        return
    dec = entry.get("decision")
    if "decision" not in entry:
        v.append(f"{path}.decision: required key is missing")
    elif not isinstance(dec, str):
        v.append(f"{path}.decision: found {_describe(dec)}, want a non-empty "
                 "string")
    elif not dec.strip():
        v.append(f"{path}.decision: is an empty string")
    reason = entry.get("reason")
    if "reason" not in entry:
        v.append(f"{path}.reason: required key is missing")
    elif not isinstance(reason, str):
        v.append(f"{path}.reason: found {_describe(reason)}, want a non-empty "
                 "string")
    elif not reason.strip():
        v.append(f"{path}.reason: is an empty string")


# --------------------------------------------------------------------------
# items, summary, scaffold
# --------------------------------------------------------------------------

def items(data) -> list[dict]:
    """The disposal items the coach must dispose of.

    Each is ``{"section": "leftUndone"|"discoveredIssues", "text": str,
    "severity": str|None}``. ``leftUndone`` strings carry ``severity`` None;
    ``discoveredIssues`` carry the issue's severity. Entries whose text is
    "nothing", "none" or "n/a" (any case, optional trailing full stop) are
    dropped, staying consistent with ``relay_decide.baton_items``.
    """
    out: list[dict] = []
    if not isinstance(data, dict):
        return out
    undone = data.get("leftUndone")
    if isinstance(undone, list):
        for entry in undone:
            if not isinstance(entry, str):
                continue
            text = entry.strip()
            if not text or _nothing(text):
                continue
            out.append({"section": "leftUndone", "text": text, "severity": None})
    issues = data.get("discoveredIssues")
    if isinstance(issues, list):
        for entry in issues:
            if not isinstance(entry, dict):
                continue
            desc = entry.get("description")
            sev = entry.get("severity")
            if not isinstance(desc, str):
                continue
            text = desc.strip()
            if not text or _nothing(text):
                continue
            out.append({"section": "discoveredIssues", "text": text,
                        "severity": sev if isinstance(sev, str) else None})
    return out


def summary(data) -> dict:
    """A one-line read of a handoff: leg, stage, status, commit, sessionId,
    the count of disposal items, and the count of blocking discovered issues.

    Tolerant of a malformed handoff -- missing keys become None and counts
    become 0 -- because the coach wants a one-line read even of a handoff that
    failed validation.
    """
    if not isinstance(data, dict):
        return {"leg": None, "stage": None, "status": None, "commit": None,
                "sessionId": None, "items": 0, "blocking": 0}
    leg = data.get("leg")
    stage = data.get("stage")
    status = data.get("status")
    issues = data.get("discoveredIssues")
    blocking = 0
    if isinstance(issues, list):
        for entry in issues:
            if isinstance(entry, dict) and entry.get("severity") == "blocking":
                blocking += 1
    return {
        "leg": leg if isinstance(leg, str) else None,
        "stage": stage if isinstance(stage, str) else None,
        "status": status if isinstance(status, str) else None,
        "commit": data.get("commit"),
        "sessionId": data.get("sessionId"),
        "items": len(items(data)),
        "blocking": blocking,
    }


def scaffold(leg, stage) -> dict:
    """A valid empty handoff for ``leg``: status ``partial``, no commit, no
    session, ``returnToCoach`` True, a placeholder summary, and empty lists
    with mutations at 0/0. ``validate(scaffold(...))`` returns ``[]``.
    """
    return {
        "leg": leg,
        "stage": stage,
        "status": "partial",
        "commit": None,
        "sessionId": None,
        "returnToCoach": True,
        "salientSummary": "<one paragraph: what this leg did and what it left "
                          "for the coach>",
        "implemented": [],
        "leftUndone": [],
        "verification": {
            "commandsRun": [],
            "interactiveChecks": [],
        },
        "tests": {
            "added": [],
            "mutations": {"run": 0, "survived": 0, "note": ""},
            "coverage": "<coverage summary or 'unknown'>",
        },
        "discoveredIssues": [],
        "procedureFollowed": [],
        "decisions": [],
        "repeatFriction": [],
    }


# --------------------------------------------------------------------------
# the guarded reader
# --------------------------------------------------------------------------

def _read_handoff_file(path):
    """``(raw, why)`` for a handoff file, without ever blocking.

    Mirrors ``relay_model._read_relay_file``: confirms a regular file via
    ``fstat`` on the descriptor before reading, refuses anything over
    ``MAX_HANDOFF_BYTES``, and never opens a FIFO, socket, directory or
    unreadable path. Returns ``(bytes, None)`` on success, ``(None, None)``
    when nothing is at the path, ``(None, why)`` when something is there that
    a handoff may not be.
    """
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"it could not be read ({(exc.strerror or type(exc).__name__).lower()})"
    except (TypeError, ValueError):
        return None, "it is not a usable path"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, f"it is {_shape(st.st_mode)}, not a regular file"
        if st.st_size > MAX_HANDOFF_BYTES:
            return None, (f"it is over {st.st_size} bytes, past the "
                          f"{MAX_HANDOFF_BYTES}-byte limit")
        chunks, total = [], 0
        while True:
            try:
                chunk = os.read(fd, _READ_CHUNK)
            except OSError as exc:
                return None, f"it could not be read ({(exc.strerror or type(exc).__name__).lower()})"
            if not chunk:
                return b"".join(chunks), None
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_HANDOFF_BYTES:
                return None, (f"it grew past the {MAX_HANDOFF_BYTES}-byte "
                             "limit while being read")
    finally:
        os.close(fd)


def _load_handoff(path):
    """``(data, status, why)`` where status is ok | absent | unreadable |
    unparseable. A regular file that is not valid JSON is unparseable, not
    absent -- the file is there, it is just not a handoff.
    """
    raw, why = _read_handoff_file(path)
    if raw is None:
        if why is None:
            return None, "absent", None
        return None, "unreadable", why
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "unparseable", "it is not valid UTF-8"
    try:
        return json.loads(text), "ok", None
    except ValueError:
        return None, "unparseable", "it is not valid JSON"
    except RecursionError:
        return None, "unparseable", "it is nested too deeply to parse"


def _emit(obj):
    json.dump(obj, sys.stdout, indent=2)
    sys.stdout.write("\n")


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("validate", "items", "summary", "new"))
    ap.add_argument("leg", help="the leg whose handoff is read or written")
    ap.add_argument("--stage", help="the stage for a new handoff (new only)")
    ap.add_argument("--relay-dir", default=".relay")
    args = ap.parse_args(argv)

    handoff = Path(args.relay_dir) / "batons" / f"{args.leg}.json"

    if args.command == "new":
        if not args.stage:
            ap.error("new needs --stage")
        if not os.path.isdir(args.relay_dir):
            print(f"relay dir '{args.relay_dir}' does not exist",
                  file=sys.stderr)
            return 2
        (Path(args.relay_dir) / "batons").mkdir(parents=True, exist_ok=True)
        if handoff.exists():
            print(f"handoff '{handoff}' already exists; refusing to overwrite",
                  file=sys.stderr)
            return 1
        data = scaffold(args.leg, args.stage)
        handoff.write_text(json.dumps(data, indent=2) + "\n")
        _emit(data)
        return 0

    data, status, why = _load_handoff(handoff)
    if status == "absent":
        print(f"handoff '{handoff}' does not exist", file=sys.stderr)
        return 2
    if status == "unreadable":
        print(f"handoff '{handoff}' could not be read: {why}", file=sys.stderr)
        return 2

    if args.command == "validate":
        if status == "unparseable":
            _emit({"leg": args.leg, "violations": [why]})
            return 1
        violations = validate(data)
        _emit({"leg": args.leg, "violations": violations})
        return 1 if violations else 0

    if args.command == "items":
        found = items(None if status == "unparseable" else data)
        _emit({"leg": args.leg, "items": found})
        return 1 if found else 0

    # summary
    s = summary(None if status == "unparseable" else data)
    _emit(s)
    return 1 if s.get("blocking") else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""The stack a relay runs against, declared once and started the way a user does.

    python3 scripts/relay_services.py up     [--relay-dir .relay] [--only NAME ...] [--timeout 60]
    python3 scripts/relay_services.py down   [--relay-dir .relay] [--only NAME ...]
    python3 scripts/relay_services.py status [--relay-dir .relay] [--json]
    python3 scripts/relay_services.py check  [--relay-dir .relay]
    python3 scripts/relay_services.py logs   NAME [--relay-dir .relay] [--lines 50]

Why it exists
-------------
Relay's behaviour judge is currently told to go and find the way into the
product - "README command, bin/, installed script, --help" - so every judge,
at every gate, rediscovers how to start the stack, and nothing declares a
port, a healthcheck or a dependency order. A real Factory Mission declares
each service in `services.yaml` with start/stop/healthcheck/port/depends_on
and ships an idempotent `init.sh`, and Factory's own documentation names this
as the prerequisite without which "the mission cannot reliably verify its own
work". This script is that declaration made executable: one command brings the
stack up in dependency order and waits on real healthchecks, so a judge starts
the product the way a user does instead of guessing.

The file format - `.relay/services.yaml`
---------------------------------------
A STRICT, DOCUMENTED SUBSET of YAML, parsed with the standard library only.
PyYAML is not imported: it is not guaranteed to be installed on a user's
machine, and a second code path that accepts YAML features the schema does not
support is a bug farm. Supported exactly:

  * comment lines (`#`) and blank lines, ignored.
  * a top-level mapping of service name -> mapping, two-space indentation per
    level, no tabs (a tab is an error naming the line number).
  * scalar values: plain strings (unquoted to end of line - a trailing `#`
    comment is NOT stripped, so put comments on their own line), 'single' or
    "double" quoted strings, integers, `true`/`false`, `null`/`~`.
  * one flow list form only: `[a, b, c]` (used by `depends_on`); `[]` is empty.
  * nothing else: no block lists (`- item`), no anchors, no multi-line scalars,
    no nested mappings below depth 2. Each unsupported construct is an error
    naming the line number and what was found.

Per-service keys: `start`, `stop`, `healthcheck` (each required, non-empty
string), `port` (optional int 1-65535), `logs` (optional string - the file the
service writes its log to, so an agent can read it after an action),
`depends_on` (optional flow list), `workdir` (optional string).

Every input is untrusted; nothing raises on bad content. A problem is an
error sentence naming the line number, never a traceback.

Exit status: 0 fine, 1 a validation problem or a service that would not come
healthy, 2 no relay dir or no `services.yaml`.
"""

import argparse
import json
import os
import pathlib
import stat
import subprocess
import sys
import time

__all__ = ["parse", "validate", "order", "stop_order", "main"]

# The most a services.yaml may weigh before the script refuses to read it.
# Mirrors `relay_model._read_relay_file`'s bound: a file read is a path, and a
# path is whatever the filesystem says it is. Two megabytes is far beyond any
# real declaration and keeps a FIFO or a device from blocking the judge.
MAX_SERVICES_BYTES = 2 * 1024 * 1024

# O_NONBLOCK is the whole guard against a pipe: with it the open returns at
# once whether or not a writer exists, and `fstat` on the descriptor - not on
# the path, which a rename could change underneath the check - says what was
# really opened. Mirrors `relay_model._OPEN_FLAGS`.
_OPEN_FLAGS = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
               | getattr(os, "O_NOCTTY", 0))
_READ_CHUNK = 1 << 16

#: The per-service keys this subset recognises. Anything else is a validation
#: error: a second code path that accepts keys the schema does not name is a
#: bug farm, so the schema is closed by construction.
KNOWN_KEYS = ("start", "stop", "healthcheck", "port", "logs", "depends_on",
               "workdir")
REQUIRED_KEYS = ("start", "stop", "healthcheck")

# Polling cadence for `up`: a healthcheck that hangs must be treated as
# unhealthy, not waited on for ever, but 0.5 s is fine-grained enough that a
# fast service reports `started` on its first poll.
_POLL_INTERVAL = 0.5
# A single healthcheck/start subprocess may not run longer than this; the
# overall `--timeout` bounds the polling loop, this bounds each call so a
# hung command cannot consume the whole budget on one invocation.
_SUBPROCESS_TIMEOUT = 10


# --------------------------------------------------------------------------
# parse - the strict YAML subset, pure, never raising
# --------------------------------------------------------------------------

def _parse_scalar(raw, line_no, errors):
    """One scalar value from its raw text, or None. Never raises.

    A trailing `#` comment is NOT stripped: the subset documents that inline
    comments are unsupported, so `port: 5432  # the db` parses as the plain
    string `5432  # the db` and fails validation for `port` not being an int.
    That is the declared behaviour, not a bug.
    """
    if raw == "":
        return None
    head = raw[0]
    if head in "\"'":
        return _parse_quoted(raw, line_no, errors)
    if head == "[":
        return _parse_flow_list(raw, line_no, errors)
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw in ("null", "~"):
        return None
    # An integer stays an integer; everything else is a plain string taken to
    # the end of the line. `int()` rejects `5432  # db`, which is the point.
    try:
        return int(raw)
    except ValueError:
        return raw


def _parse_quoted(raw, line_no, errors):
    """A 'single' or "double" quoted string, with the closing quote matched
    and a backslash escape honoured for the quote char and itself. Anything
    after the closing quote is an error - the subset does not support two
    values on one line."""
    quote = raw[0]
    j = 1
    out = []
    while j < len(raw):
        c = raw[j]
        if c == "\\" and j + 1 < len(raw):
            nxt = raw[j + 1]
            if nxt == quote or nxt == "\\":
                out.append(nxt)
            else:
                # An escape the subset does not name is kept verbatim, so the
                # value round-trips rather than silently dropping a character.
                out.append(c)
                out.append(nxt)
            j += 2
            continue
        if c == quote:
            rest = raw[j + 1:].strip()
            if rest:
                errors.append(f"line {line_no}: unexpected text after quoted "
                              f"string: {rest!r}")
                return None
            return "".join(out)
        out.append(c)
        j += 1
    errors.append(f"line {line_no}: unterminated {quote}-quoted string")
    return None


def _parse_flow_list(raw, line_no, errors):
    """`[a, b, c]` or `[]`. Items may be quoted or plain; an unclosed bracket
    or a missing comma is an error. This is the only list form the subset
    supports, and it is the one `depends_on` uses."""
    if not raw.endswith("]"):
        errors.append(f"line {line_no}: flow list is missing its closing `]`")
        return None
    inner = raw[1:-1].strip()
    if inner == "":
        return []
    items = []
    for piece in _split_flow(inner):
        piece = piece.strip()
        if piece == "":
            continue
        if piece[0] in "\"'":
            val = _parse_quoted(piece, line_no, errors)
        else:
            val = piece
        if val is not None:
            items.append(val)
    return items


def _split_flow(inner):
    """Split a flow list's interior on commas that are not inside quotes.

    `depends_on: ["a,b", c]` is two items, not three; a naive `split(",")`
    would break the quoted item that contains a comma.
    """
    parts, depth_quote, buf = [], None, []
    for c in inner:
        if depth_quote:
            buf.append(c)
            if c == depth_quote:
                depth_quote = None
            continue
        if c in "\"'":
            depth_quote = c
            buf.append(c)
            continue
        if c == ",":
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(c)
    if buf:
        parts.append("".join(buf))
    return parts


def parse(text):
    """(services, errors) for a services.yaml document. Never raises.

    `services` is an insertion-ordered dict of service name -> dict of the
    keys that parsed. `errors` is a list of sentences, each naming the line
    number of the problem. A service with a parse error in one of its keys
    still appears in the result so `validate` can report what else is wrong
    with it.
    """
    services = {}
    errors = []
    current = None
    seen_keys = {}
    for i, line in enumerate(text.splitlines(), 1):
        if "\t" in line:
            errors.append(f"line {i}: tab character found; use spaces for "
                          "indentation")
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # A block list (`- item`) is the construct YAML users reach for first
        # and the subset refuses point-blank; catching it here keeps it from
        # being read as a service name or a key.
        if stripped.startswith("- ") or stripped == "-":
            errors.append(f"line {i}: block list (`- item`) is not supported; "
                          "use a flow list `[a, b]` for `depends_on`")
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent % 2 != 0:
            errors.append(f"line {i}: indentation of {indent} spaces is not a "
                          "multiple of two")
            continue
        depth = indent // 2
        if depth == 0:
            name, sep, rest = stripped.partition(":")
            if sep != ":":
                errors.append(f"line {i}: expected `name:` at the top level, "
                              f"found {stripped!r}")
                continue
            rest = rest.strip()
            if rest:
                errors.append(f"line {i}: service `{name}` must have an empty "
                              f"value (a mapping follows); found {rest!r}")
                continue
            if name in services:
                errors.append(f"line {i}: duplicate service name `{name}`")
                current = name
                continue
            services[name] = {}
            seen_keys[name] = set()
            current = name
        elif depth == 1:
            if current is None:
                errors.append(f"line {i}: indented key with no service above it")
                continue
            key, sep, val = stripped.partition(":")
            if sep != ":":
                errors.append(f"line {i}: expected `key: value`, found "
                              f"{stripped!r}")
                continue
            key = key.strip()
            val = val.lstrip()
            if key in seen_keys[current]:
                errors.append(f"line {i}: duplicate key `{key}` in service "
                              f"`{current}`")
                continue
            seen_keys[current].add(key)
            services[current][key] = _parse_scalar(val, i, errors)
        else:
            errors.append(f"line {i}: indentation too deep (depth {depth}); "
                          "this subset supports no nesting below depth 2")
            continue
    return services, errors


# --------------------------------------------------------------------------
# validate - the schema, pure, never raising
# --------------------------------------------------------------------------

def _is_nonempty_str(value):
    return isinstance(value, str) and value.strip() != ""


def _find_cycle(services):
    """One cycle among the services, as a list of member names, or None.

    DFS with a recursion stack: when a node still on the stack is reached
    again, the slice of the stack from that node to the current end is the
    cycle. Services are visited in file order so the cycle reported is the
    one a reader tracing the file top-to-bottom would find first.
    """
    names = list(services)
    deps = {n: [d for d in (services[n].get("depends_on") or [])
                if isinstance(d, str) and d in services] for n in names}
    colour = {n: 0 for n in names}  # 0 unseen, 1 on stack, 2 done
    stack = []

    def visit(n):
        colour[n] = 1
        stack.append(n)
        for d in deps[n]:
            if colour[d] == 1:
                start = stack.index(d)
                return stack[start:]
            if colour[d] == 0:
                found = visit(d)
                if found:
                    return found
        stack.pop()
        colour[n] = 2
        return None

    for n in names:
        if colour[n] == 0:
            found = visit(n)
            if found:
                return found
    return None


def validate(services):
    """A list of error sentences for a parsed services dict. Never raises.

    Catches missing required keys, wrong types, a port out of range, a
    `depends_on` naming a service that does not exist, a dependency cycle
    (naming the cycle members), and a key the schema does not name.
    """
    errors = []
    for name, spec in services.items():
        if not isinstance(spec, dict):
            errors.append(f"service `{name}` is not a mapping")
            continue
        for key in spec:
            if key not in KNOWN_KEYS:
                errors.append(f"service `{name}`: unknown key `{key}`; "
                              f"recognised keys are {', '.join(KNOWN_KEYS)}")
        for req in REQUIRED_KEYS:
            if req not in spec:
                errors.append(f"service `{name}`: missing required key "
                              f"`{req}`")
            elif not _is_nonempty_str(spec[req]):
                errors.append(f"service `{name}`: `{req}` must be a non-empty "
                              "string")
        if "port" in spec and spec["port"] is not None:
            port = spec["port"]
            if not isinstance(port, int) or isinstance(port, bool):
                errors.append(f"service `{name}`: `port` must be an integer, "
                              f"not {type(port).__name__}")
            elif not (1 <= port <= 65535):
                errors.append(f"service `{name}`: `port` {port} is out of range "
                              "(1-65535)")
        if "logs" in spec and spec["logs"] is not None:
            if not isinstance(spec["logs"], str):
                errors.append(f"service `{name}`: `logs` must be a string")
        if "workdir" in spec and spec["workdir"] is not None:
            if not isinstance(spec["workdir"], str):
                errors.append(f"service `{name}`: `workdir` must be a string")
        if "depends_on" in spec and spec["depends_on"] is not None:
            deps = spec["depends_on"]
            if not isinstance(deps, list):
                errors.append(f"service `{name}`: `depends_on` must be a flow "
                              f"list, not {type(deps).__name__}")
            else:
                for d in deps:
                    if not isinstance(d, str):
                        errors.append(f"service `{name}`: `depends_on` entry "
                                      f"{d!r} is not a string")
                    elif d not in services:
                        errors.append(f"service `{name}`: `depends_on` names "
                                      f"`{d}`, which is not a declared service")
    cycle = _find_cycle(services)
    if cycle:
        errors.append("dependency cycle: " + " -> ".join(cycle + [cycle[0]]))
    return errors


# --------------------------------------------------------------------------
# order - a stable topological sort, dependencies first
# --------------------------------------------------------------------------

def order(services, reverse=False):
    """(names, errors) in start order, dependencies first.

    A stable topological sort: ties (services that could start in any order
    relative to each other) are broken by the order the services appear in the
    file, so a judge comparing two runs does not see the order move. Stop order
    is the reverse; pass `reverse=True` for it, or call `stop_order`.
    """
    names = list(services)
    errors = []
    deps = {n: [d for d in (services[n].get("depends_on") or [])
                if isinstance(d, str) and d in services] for n in names}
    indeg = {n: len(deps[n]) for n in names}
    result = []
    # Kahn's algorithm, picking the lowest-file-index ready node each step.
    # A node is ready when every dependency it names (that exists) is already
    # in the result; deps naming unknown services are ignored here because
    # `validate` reports them, and they must not make a service look cyclic.
    remaining = set(names)
    while remaining:
        ready = [n for n in names
                 if n in remaining and indeg[n] == 0]
        if not ready:
            # The remaining nodes are in or depend on a cycle; `_find_cycle`
            # names the members so the error is actionable, not just "no order".
            cycle = _find_cycle({n: services[n] for n in remaining})
            if cycle:
                errors.append("dependency cycle, cannot order: "
                             + " -> ".join(cycle + [cycle[0]]))
            else:
                errors.append("cannot order: remaining services "
                              + ", ".join(sorted(remaining)))
            break
        node = ready[0]
        result.append(node)
        remaining.discard(node)
        for m in names:
            if m in remaining and node in deps[m]:
                indeg[m] -= 1
    if reverse:
        result.reverse()
    return result, errors


def stop_order(services):
    """(names, errors) in stop order - the reverse of start order."""
    return order(services, reverse=True)


# --------------------------------------------------------------------------
# reading services.yaml - the one guarded door
# --------------------------------------------------------------------------

def _read_services_file(relay_dir):
    """(text, why) for services.yaml, without ever blocking.

    Mirrors `relay_model._read_relay_file`: opens nothing it has not confirmed
    is a regular file, refuses a file over the byte bound, and never blocks on
    a FIFO or a device. `why` is None when the text was read, otherwise a
    sentence naming what was found instead.
    """
    relay_dir = pathlib.Path(relay_dir)
    if not relay_dir.is_dir():
        return None, f"no relay directory at {relay_dir}"
    path = relay_dir / "services.yaml"
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except FileNotFoundError:
        return None, f"no services.yaml at {path}"
    except OSError as exc:
        return None, (f"services.yaml could not be opened "
                      f"({(exc.strerror or type(exc).__name__).lower()})")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "services.yaml is not a regular file"
        if st.st_size > MAX_SERVICES_BYTES:
            return None, (f"services.yaml is over {MAX_SERVICES_BYTES} bytes, "
                          "past the 2 MiB limit")
        chunks, total = [], 0
        while True:
            try:
                chunk = os.read(fd, _READ_CHUNK)
            except OSError as exc:
                return None, (f"services.yaml could not be read "
                              f"({(exc.strerror or type(exc).__name__).lower()})")
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_SERVICES_BYTES:
                return None, (f"services.yaml grew past {MAX_SERVICES_BYTES} "
                              "bytes while being read")
        try:
            return b"".join(chunks).decode("utf-8"), None
        except UnicodeDecodeError:
            return None, "services.yaml is not valid UTF-8"
    finally:
        os.close(fd)


def _load(relay_dir):
    """(services, errors, why) for a relay dir.

    `why` is set and the rest is None when the file could not be read (exit 2).
    Otherwise `services` is the parsed dict (possibly empty) and `errors` holds
    parse+validate problems (exit 1 when non-empty).
    """
    text, why = _read_services_file(relay_dir)
    if why is not None:
        return None, None, why
    services, perrs = parse(text)
    verrs = validate(services)
    return services, perrs + verrs, None


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------

def _cwd_for(service, relay_dir):
    """The working directory a service's commands run in: its declared
    `workdir`, or the relay's parent (the repo root) so a relative `logs`
    path lands where a judge expects to read it."""
    wd = service.get("workdir")
    if isinstance(wd, str) and wd.strip():
        return pathlib.Path(wd)
    return pathlib.Path(relay_dir).resolve().parent


def _run(cmd, cwd, timeout):
    """A shell command run with capture and a timeout, never raising.

    A hung command is `unhealthy`, not a traceback: `TimeoutExpired` is caught
    and reported as a non-zero return code so the caller treats it as failure.
    """
    try:
        return subprocess.run(cmd, shell=True, cwd=str(cwd),
                              capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timed out")


def _filter_only(names, only):
    if not only:
        return names
    keep = set(only)
    return [n for n in names if n in keep]


def _cmd_up(services, relay_dir, only, timeout):
    ordered, errors = order(services)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    ordered = _filter_only(ordered, only)
    failed = False
    for name in ordered:
        spec = services[name]
        cwd = _cwd_for(spec, relay_dir)
        hc = spec.get("healthcheck") or ""
        # Idempotent: a second judge must not start a second copy. Run the
        # healthcheck first and skip the start when it already passes.
        check = _run(hc, cwd, _SUBPROCESS_TIMEOUT)
        if check.returncode == 0:
            print(f"{name} already-healthy 0.0s")
            continue
        start = spec.get("start") or ""
        t0 = time.monotonic()
        started = _run(start, cwd, _SUBPROCESS_TIMEOUT)
        if started.returncode != 0:
            elapsed = time.monotonic() - t0
            print(f"{name} failed {elapsed:.1f}s")
            failed = True
            break
        # Poll the healthcheck until it passes or the timeout elapses. A
        # service that never becomes healthy exits 1 here so a stack is not
        # left half up - the state that produces a false failure at a gate.
        healthy = False
        deadline = t0 + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            check = _run(hc, cwd, min(_SUBPROCESS_TIMEOUT, max(0.1, remaining)))
            if check.returncode == 0:
                healthy = True
                break
            time.sleep(_POLL_INTERVAL)
        elapsed = time.monotonic() - t0
        if healthy:
            print(f"{name} started {elapsed:.1f}s")
        else:
            print(f"{name} unhealthy {elapsed:.1f}s")
            failed = True
            break
    return 1 if failed else 0


def _cmd_down(services, relay_dir, only):
    ordered, _ = stop_order(services)
    ordered = _filter_only(ordered, only)
    for name in ordered:
        spec = services[name]
        cwd = _cwd_for(spec, relay_dir)
        stop = spec.get("stop") or ""
        r = _run(stop, cwd, _SUBPROCESS_TIMEOUT)
        # A non-zero stop is reported but not fatal: a service already gone
        # should not keep `down` from stopping the rest.
        if r.returncode != 0:
            print(f"{name} stopped (exit {r.returncode})")
        else:
            print(f"{name} stopped")
    return 0


def _cmd_status(services, relay_dir, as_json):
    rows = []
    for name in services:
        spec = services[name]
        cwd = _cwd_for(spec, relay_dir)
        hc = spec.get("healthcheck") or ""
        r = _run(hc, cwd, _SUBPROCESS_TIMEOUT)
        rows.append({
            "name": name,
            "healthy": r.returncode == 0,
            "port": spec.get("port"),
            "logs": spec.get("logs"),
        })
    if as_json:
        json.dump(rows, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    for row in rows:
        state = "healthy" if row["healthy"] else "unhealthy"
        port = f" port={row['port']}" if row["port"] is not None else ""
        logs = f" logs={row['logs']}" if row["logs"] is not None else ""
        print(f"{row['name']} {state}{port}{logs}")
    return 0


def _cmd_check(services, errors, relay_dir):
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    ordered, oerrs = order(services)
    if oerrs:
        for e in oerrs:
            print(e, file=sys.stderr)
        return 1
    print("services:")
    for name in services:
        print(f"  {name}")
    print("start order:")
    print("  " + ", ".join(ordered))
    return 0


def _cmd_logs(services, name, lines):
    if name not in services:
        print(f"no service `{name}`", file=sys.stderr)
        return 1
    logs = services[name].get("logs")
    if not isinstance(logs, str) or not logs.strip():
        print(f"service `{name}` declares no logs file")
        return 0
    path = pathlib.Path(logs)
    try:
        with open(path, "r", errors="replace") as fh:
            tail = fh.readlines()[-lines:]
    except OSError as exc:
        print(f"could not read {path}: {(exc.strerror or type(exc).__name__).lower()}",
              file=sys.stderr)
        return 1
    sys.stdout.writelines(tail)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("up", "down", "status", "check", "logs"))
    ap.add_argument("name", nargs="?", help="service name for `logs`")
    ap.add_argument("--relay-dir", default=".relay")
    ap.add_argument("--only", nargs="*", default=None,
                    help="restrict up/down to these services")
    ap.add_argument("--timeout", type=int, default=60,
                    help="seconds to wait for a service to become healthy")
    ap.add_argument("--lines", type=int, default=50,
                    help="number of log lines to print")
    ap.add_argument("--json", action="store_true", help="status as JSON")
    args = ap.parse_args(argv)

    if args.command == "logs" and not args.name:
        ap.error("logs needs a service NAME")

    services, errors, why = _load(args.relay_dir)
    if why is not None:
        print(why, file=sys.stderr)
        return 2
    # `up` needs a valid, orderable set before it starts anything; `check`
    # reports the errors; the others run against whatever parsed.
    if args.command == "up":
        if errors:
            for e in errors:
                print(e, file=sys.stderr)
            return 1
        return _cmd_up(services, args.relay_dir, args.only, args.timeout)
    if args.command == "down":
        if errors:
            for e in errors:
                print(e, file=sys.stderr)
            return 1
        return _cmd_down(services, args.relay_dir, args.only)
    if args.command == "status":
        return _cmd_status(services, args.relay_dir, args.json)
    if args.command == "check":
        return _cmd_check(services, errors, args.relay_dir)
    if args.command == "logs":
        return _cmd_logs(services, args.name, args.lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())

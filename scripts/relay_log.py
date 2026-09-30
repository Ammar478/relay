#!/usr/bin/env python3
"""Append-only events for a Relay run.

    python3 scripts/relay_log.py append EVENT [--relay-dir .relay]
        [--field key=value ...]
    python3 scripts/relay_log.py tail [--relay-dir .relay]
        [--event EVENT] [--limit N]

`tail` prints one compact JSON object per line, rather than wrapping the
selected records in a JSON array.

Why it exists
-------------
Today the coach hand-writes a prose `log` array into `.relay/dashboard.json`,
so a run's history is narrated by the agent rather than recorded by the
system. A Factory Mission appends typed events to `progress_log.jsonl`:
`mission_run_started`, `worker_selected_feature`, `worker_started` with
`modelId` and `spawnId`, and `worker_completed` with `commitId`. This module
gives Relay the same append-only record. The coach can choose when to append
an event, but cannot editorialise an earlier event or make a failed write
look like a completed one.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time


EVENTS = (
    "relay_accepted",
    "relay_started",
    "relay_paused",
    "relay_resumed",
    "stage_started",
    "leg_dispatched",
    "leg_completed",
    "leg_failed",
    "handoff_disposed",
    "gate_started",
    "gate_cleared",
    "gate_failed",
    "check_marked",
    "debt_recorded",
    "attention_raised",
    "relay_finished",
)


def _event_error(event):
    return ValueError(f"unknown event {event!r}; allowed events: {EVENTS!r}")


def _safe_string(value):
    try:
        return str(value)
    except Exception:  # An object with a broken __str__ must not stop logging.
        return "<unserialisable>"


def _safe_field(value):
    """Return a value that json.dumps can write without refusing the event."""
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        try:
            encoded = json.dumps(value, ensure_ascii=False,
                                 allow_nan=False, default=str)
            return json.loads(encoded)
        except (TypeError, ValueError, OverflowError, RecursionError):
            return _safe_string(value)
    return value


def _timestamp(when):
    epoch = time.time() if when is None else when
    return (datetime.fromtimestamp(epoch, timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def record(event, when=None, **fields):
    """Build one typed event without touching the filesystem."""
    if not isinstance(event, str) or not event or event not in EVENTS:
        raise _event_error(event)

    result = {
        "ts": _timestamp(when),
        "event": event,
    }
    result.update((name, _safe_field(fields[name])) for name in sorted(fields))
    return result


def serialise(rec):
    """Encode one event as a compact JSONL record."""
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"


def parse_line(line):
    """Parse one JSONL line, returning None for anything unreadable."""
    try:
        if not line or not line.strip():
            return None
        value = json.loads(line)
    except (AttributeError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def read(text, event=None, limit=None):
    """Read records from a JSONL body, oldest first and newest last."""
    try:
        lines = text.splitlines()
    except AttributeError:
        return []

    records = []
    for line in lines:
        rec = parse_line(line)
        if rec is not None and (event is None or rec.get("event") == event):
            records.append(rec)

    if limit is not None:
        if limit <= 0:
            return []
        records = records[-limit:]
    return records


def _parse_fields(values):
    fields = {}
    for value in values:
        if "=" not in value or not value.split("=", 1)[0]:
            raise ValueError(f"malformed --field {value!r}; expected key=value")
        key, raw = value.split("=", 1)
        try:
            fields[key] = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            fields[key] = raw
    return fields


def _missing_relay_dir(path):
    if not path.is_dir():
        print(f"relay directory does not exist: {path}", file=sys.stderr)
        return True
    return False


def _append(args):
    try:
        fields = _parse_fields(args.field)
        rec = record(args.event, **fields)
    except (TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    relay_dir = Path(args.relay_dir)
    if _missing_relay_dir(relay_dir):
        return 2

    try:
        with (relay_dir / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(serialise(rec))
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    sys.stdout.write(serialise(rec))
    return 0


def _tail(args):
    if args.event is not None:
        try:
            record(args.event)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    relay_dir = Path(args.relay_dir)
    if _missing_relay_dir(relay_dir):
        return 2

    try:
        text = (relay_dir / "progress.jsonl").read_text(
            encoding="utf-8", errors="replace")
    except FileNotFoundError:
        text = ""
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for rec in read(text, event=args.event, limit=args.limit):
        sys.stdout.write(serialise(rec))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = ap.add_subparsers(dest="command", required=True)

    append = commands.add_parser("append")
    append.add_argument("event")
    append.add_argument("--relay-dir", default=".relay")
    append.add_argument("--field", action="append", default=[], metavar="K=V")

    tail = commands.add_parser("tail")
    tail.add_argument("--relay-dir", default=".relay")
    tail.add_argument("--event")
    tail.add_argument("--limit", type=int)

    args = ap.parse_args(argv)
    return _append(args) if args.command == "append" else _tail(args)


if __name__ == "__main__":
    sys.exit(main())

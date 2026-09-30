# The environment: how the product gets started and driven

A relay that cannot start the product cannot judge it. Everything in this file
exists to make one sentence true for every judge at every gate: **starting the
stack is a declared command, not a discovery exercise.**

The failure it prevents is specific. Told to "find the way in a user has — the
README command, `bin/`, an installed script, `--help`", a judge rediscovers the
stack every round, guesses at ports, misses a service, and reports a failure that
belongs to its own setup rather than to the code. `relay-control` spent seven gate
rounds inside a function because no way to run the product was ever declared.

## The readiness pre-flight, in Phase 0

Four questions. Ask them before the contract is written, because the answers
change what the contract can require, and a "no" is stage-1 work rather than a
reason to carry on and hope.

1. **Is there one command that brings the whole stack up?** Backend, frontend,
   database, queue, everything a user needs. If there are five commands in three
   terminals, the relay's first legs write the one.
2. **Do the logs land on disk?** An agent can read a file after an action; it
   cannot scroll back through a terminal it does not own. Every service needs a
   log path.
3. **Can the product be driven programmatically?** A browser driver for a web or
   Electron app, a terminal driver for a TUI, `curl` and a test runner for an API.
   If the product is none of those, the relay's own first deliverable is the
   harness that drives it.
4. **Does running it fit on this machine?** Judges run *beside* the app. A stack
   that eats the RAM turns a gate round into a timeout.

Record the answers in `relay.md` under `## Environment`, and write the gaps as
stage-1 legs. **A relay whose product cannot be started and driven cannot verify
its own work** — that is not a rule Relay invented, it is what every harness that
does this at scale says out loud.

## `.relay/services.yaml`

One declaration per service, and the only place a port, a healthcheck or a start
command is written down. `templates/services.yaml` is the shape.

```yaml
api:
  start: bash -c 'cd api && nohup uvicorn app:api --port 8000 > /tmp/relay-api.log 2>&1 & echo $!'
  stop: bash -c 'lsof -ti :8000 | xargs kill 2>/dev/null || true'
  healthcheck: curl -sf http://localhost:8000/health
  port: 8000
  logs: /tmp/relay-api.log
  depends_on: [postgres]
```

Bring it up, in dependency order, waiting on real healthchecks:

```bash
python3 scripts/relay_services.py up     --relay-dir .relay
python3 scripts/relay_services.py status --relay-dir .relay
python3 scripts/relay_services.py logs api --lines 80
python3 scripts/relay_services.py down   --relay-dir .relay
```

`up` is **idempotent**: a service whose healthcheck already passes is left alone.
That is what lets two judges run at once without one of them starting a second
copy of the database on the same port.

Three rules about the file itself:

- **A healthcheck is a command that exits 0, not a sleep.** `sleep 5 && echo ok`
  is not a healthcheck; it is a hope with a delay in front of it.
- **`depends_on` is the start order and the stop order reversed.** Declare it even
  when today's order happens to work.
- **`logs` points at a file.** Redact credentials and tokens before they reach it,
  restrict its permissions, and keep it out of version control and out of CI
  artefacts. A log on disk is a log that can leak.

## `.relay/init.sh`

Idempotent setup, run once per machine and again whenever a runner suspects the
environment. `templates/init.sh` is the shape. It **verifies and reports**; it
does not silently install. A script that quietly upgrades a dependency has
changed the thing under test.

```
[init] Verifying database on 127.0.0.1:5432 ... OK
[init] Verifying interpreter is 3.11 ... OK
[init] ERROR: node_modules missing in web/ — run `npm ci` there first.
```

Failing with the command the human should run beats installing something the
contract never mentioned.

## Driving the product

Name the driver in the check (`Tool:`) and in the judge's briefing. Do not leave a
judge to invent one, and do not accept evidence collected some other way.

| Surface | Driver | Evidence it yields |
|---|---|---|
| Web app | a browser driver (Playwright, or the harness's own browser automation skill) | screenshots, console errors, network status codes |
| Electron / desktop app | the same browser driver against the app's debug port | screenshots, accessibility snapshots |
| Terminal UI | a TUI driver that sends keys and captures frames | frame captures, exit codes |
| HTTP API | `curl`, plus the test runner for database-level assertions | status codes, response bodies, row counts |
| CLI | a shell, from **outside** the build tree | the exact command typed and its first output |

Most harnesses ship a browser driver and a TUI driver already. Find out which at
the capability pre-flight (`references/harness.md`) and name them in `relay.md`,
because a judge that has to choose a driver will choose a different one from the
last judge and their evidence will not compare.

## Isolation, and the concurrency ceiling

Disjoint `touches` keeps two runners out of each other's **files**. It says
nothing about the resources they share, and two legs that both start the stack
collide on a port, a database and a log file while their file sets stay perfectly
disjoint.

So every dispatched agent gets an **isolation context** in its briefing, and the
coach allocates it:

```
ISOLATION   port range: 8100-8109
            data namespace: relay_s2_leg3   (schema, prefix, or directory)
            credentials: the seeded user for this slot, not the shared one
            log file: /tmp/relay-<leg>.log
            scratch prefix: <leg-id>-        (every temp file starts with it)
```

And the plan carries a **ceiling**, written in `relay.md` and honoured at
dispatch:

```
## Concurrency

Max parallel runners: 3
Max parallel UI judges: 2        (each drives a browser, ~400 MB)
Max parallel API judges: 4
Total footprint budget: ~4 GB of this machine's 16 GB
```

Two runners in their own worktrees still share one repository and one stash
stack — `references/execution.md` has that story. Isolation is about ports,
databases, credentials and files, and none of it is visible in `touches`.

## What the judges inherit

A gate briefing says, in this order: bring the stack up with `relay_services.py
up`, confirm `status` is healthy, then start the product **the way a user does**
and walk the checks. A judge that could not bring the stack up does not go
looking for a workaround — "I could not start it" is a failure against the whole
stage, and the fix is a leg.

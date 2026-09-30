# Judging

## Writing checks that hold up

A check is a claim a stranger with no knowledge of the code could judge true or false
by using the system.

**Good:**

```
### ACC-CART-003 — Removing the last item empties the cart
With one item in the cart, clicking Remove leaves the cart page showing the
empty state and the header badge showing 0.
Tool: browser driver
Evidence: screenshot of empty state, GET /api/cart → {"items":[]}, badge text "0".
```

**Bad, and why:**

| Check | Problem |
|---|---|
| "The cart works correctly" | Not judgeable. No pass/fail line. |
| "`CartService.remove()` is called" | Implementational. Passes even when the UI is broken. |
| "`build()` returns a model" | Names an interface. Teaches the judge to judge through it. |
| "Removal is fast" | No threshold. Make it "under 300ms at p95". |
| "Errors are handled" | Which errors, and what should the user see? |

Each check carries a stable **ID** (`ACC-<AREA>-<NNN>`, which legs reference), a
one-line **title**, a **body** with the preconditions that make it reproducible, the
**tool** that exercises it, and the **evidence** that proves it. Group by user-facing
area, then add cross-area checks for the flows that span areas — where integration
bugs live.

### The fixture preamble

The contract opens with the fixtures every check reuses, named once: the users and
their roles, the tokens, the agent or account or tenant under test, the seed data, and
the single command that creates them. `templates/contract.md` has the shape.

Without it every judge invents its own fixtures, and then round 2's evidence cannot be
compared with round 1's — two judges disagreeing about a check when what actually
differed was the data underneath it. Name them `JWTM`, `JWTX`, `AG`, `AGX` or whatever
suits, and use those names in every check body.

### The `Tool:` line

Name the driver: browser driver, terminal driver, `curl`, the test runner, a shell from
outside the build tree. `references/environment.md` maps surfaces to drivers.

It earns its place twice. A judge stops choosing a driver, so two rounds of evidence
compare. And the contract becomes **shardable** — the coach can split 200 checks into
four judge legs by tool and by flow, which is the only way a large contract gets walked
honestly.

## The two judges

Both run as legs at the end of a stage: clean context, no knowledge of how the code was
written, in parallel with each other. **Neither can dispatch its own agents** — assume
the harness forbids it — so any fan-out a judge needs is dispatched by the coach and
handed to it as reports.

**Code judge** — the code's own claims:

- run the full test suite, the linter, the type checker; record exit codes
- read the per-leg review reports the coach dispatched as read-only legs, and
  synthesise one report from them
- look for regressions in untouched areas, error paths never exercised, integration
  seams between legs, tests that assert the implementation
- **judge structure, not only whether tests guard behaviour**: name the three largest
  files the stage touched with their line counts, plus duplication across legs, seams
  that leaked, and dead code. `tests/frame.py` reached 2900 lines over eight legs and no
  judge mentioned it — structure was not in its remit.
- report findings as blocking / non-blocking / suggestion, and **mark every
  `Convention` check** in `state.json` — all of them, whatever the project's standards
  demand, because they are measured from the tree. Nobody else can reach them, and a
  check nobody marks reads `blocked` for ever.
- **apply the floor before calling anything blocking**: a check passes once its
  behaviour holds and one mutation of the property it names fails the suite. A defect in
  the guard on that guard — a test about a test, a mutation-harness bug — is `debt` in
  `relay.md`, not a blocking finding, unless it hides a behavioural defect.

**Behaviour judge** — the system as a black box:

- **Bring the stack up from the declaration, not by discovery.**
  `python3 scripts/relay_services.py up --relay-dir .relay`, then `status`. The
  declaration exists so that no judge has to guess a port or miss a service; if it is
  missing or wrong, that is a finding, and the fix is a leg.
- **Start the product before reading a single check.** Find the way in a user has —
  README command, `bin/`, installed script, `--help` — and run it in a shell. **Run it
  from outside the tree that built it**: another directory at least, a clean checkout or
  a fresh venv where the project is only installed the way a user installs it. A judge
  sitting in the build tree watches it come up and files a truthful pass while the user
  gets `ImportError` — that is the exact failure this file exists to catch, and it
  catches itself. The report's first line is the exact command typed and its first
  output; a report without that line is not a report.
- **Never reach past that entrypoint to decide a verdict** — no importing,
  instantiating or calling a module of the system; a `python -c` that imports the
  package is an import, not a command. If the product is a library, its published
  package *is* the entrypoint: use it exactly as the README shows, nothing deeper.
  `relay-control` spent seven gate rounds inside `build()` because the contract named
  that interface, while no way to run the product existed at all.
- walk each check's flow through the running product with the driver its `Tool:` names,
  collecting its evidence into `.relay/evidence/<stage>/`
- **"I could not start it" blocks the stage** — a failure raised against the stage, not
  a note. Until something starts, every check it owns reads `blocked`.
- mark every check that is not a `Convention` one passed / failed / blocked, with the
  evidence attached or the reason it could not be obtained

A check with no evidence is not passed. It is blocked.

## Sharding the behaviour gate

One judge walking every check of a large stage in one context is invariant 3 broken at
the gate rather than during the build. Past roughly thirty checks it frays: the evidence
gets thinner towards the end, and the last checks get read rather than run.

So split the stage's checks into shards and dispatch one judge leg per shard, in
parallel under the concurrency ceiling:

- **Shard by `Tool:` first**, then by flow. A shard is a set of checks one judge can
  walk with one driver and one fixture set.
- **Each shard gets its own isolation context** — its own port range, data namespace and
  seeded credentials — or two shards will fight over one account and file failures that
  belong to each other. `references/environment.md` has the shape.
- **Each shard writes its own report** to
  `.relay/validation/<stage>/behaviour/<shard>.json`, and marks only its own checks.
- **The coach synthesises** them into
  `.relay/validation/<stage>/behaviour/synthesis.json`: every check's verdict, the
  blocking set grouped by root cause, and what could not be evidenced. That synthesis is
  what the fix loop reads.

Standing checks go in **every** shard set, at every gate. They are cheap and they are the
request.

## Independence

A judge's value comes entirely from not having built the thing: fresh context, no
implementation history in the briefing, a different provider from the runner where the
choice exists (same family, same blind spots), and judging against the contract only — a
wrong contract is a finding for the coach, not something to work around.

## The fix loop

Expect two to four rounds per stage, and roughly a third of your total legs to be fix
legs. This is the architecture working, not failing.

**The floor.** A check passes once its behaviour holds and one mutation of the property
it names fails the suite. A defect in the guard on that guard is `debt` in `relay.md`,
not a fix leg, unless it hides a behavioural defect — the guard on the guard is not
itself proof that the underlying behaviour is broken. `relay-control` had no floor and
spent gate rounds 5, 6 and 7 on guards on guards.

1. Collect every failure and blocking finding that does not clear the floor above.
2. Group them by root cause — one fix leg per cause, not per symptom.
3. Insert the fix legs at the head of the queue and run Phase 4 on them.
4. Re-judge the whole stage, not just the fixes. Fixes cause regressions.
5. The stage is **cleared** once every check in it reads `passed` or carries recorded
   `debt`.

**Write the round down.** What this round learned about verifying these surfaces — the
two browser contexts a two-user flow needs, the checker that passes vacuously on a
disabled control, the failure that was environmental — goes into
`.relay/library/<topic>.md` under the stage and round that found it. Round 2 of the same
gate should start from round 1's knowledge, and without this file it starts from nothing.

**The budget: three legs per check.** Count every leg against it — the leg that claims it
plus each leg whose `repairs` names it, read from `legs.json`. Stop and get a human at
the third failure, rather than writing a fourth leg — cut the check, change it, or mark
it `debt` with the reason. `ACC-DATA-009` took 10 legs and 7 gate rounds.

Stop and get a human when a fix breaks a previously passing check twice, when the two
judges disagree about whether something passed, or when passing a check would require
changing the contract.

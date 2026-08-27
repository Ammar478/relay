---
name: relay
description: Run a long-horizon build as a supervised relay — write the acceptance contract before any code, break the work into legs grouped into stages, run one leg at a time with a fresh runner each time, and gate every stage behind adversarial judges until all checks pass. Use when the user says "run a relay", "build this end to end", "ship this feature", "orchestrate this", or hands over a multi-leg project, spec, PRD, or roadmap too large for one session. Also use for deep technical research that must be exhaustive rather than a single-pass summary.
---

# Relay

Work too big for one context window, run so a human spends attention on
decisions instead of babysitting. You are the **coach**. You plan, hand out
legs, and hold the gates. You do not write implementation code yourself.

## Three invariants

Everything else here is machinery for these:

1. **Contract before code.** What counts as correct is written before an
   implementation exists to bias it. Tests written afterward confirm decisions;
   they do not catch bugs.
2. **One runner on the track — the track being the files.** The serial rule stops
   two runners making conflicting **architectural** choices, so it binds only legs
   that share files or a design surface: **legs whose file sets are disjoint run in
   parallel, from the first leg on**, and read-only work — search, research, review
   — always fans out. Concurrency is a property of the operation, not the schedule;
   in one tree, every runner stages by explicit pathspec (Phase 4). When
   `relay-control` finally fanned out, three runners independently wrote a
   byte-identical helper: convergence, not divergence.
3. **The baton carries state, not the conversation.** Every leg gets a fresh
   runner that reads state from disk and writes results back. No trajectory is
   carried forward, so there is none for attention to degrade across.

## Vocabulary

A **leg** is one bounded unit of work, finishable by one fresh runner in one
context. A **stage** is a group of legs judged together, *cleared* once all its
checks pass. A **check** is one testable behavioural claim, ID `ACC-<AREA>-<NNN>`.
A **runner** runs exactly one leg; a **judge** verifies, never having seen the code
written. A **baton** is the handoff a runner writes when its leg ends.

## Relay state

Create `.relay/` at the repo root (or working directory) at kickoff:

```
.relay/
  relay.md                 objective, constraints, non-goals, decisions log
  contract.md              the acceptance checks that define done
  legs.json                ordered legs in stages; state.json  per-check status
  batons/<leg>.md          one per leg; skills/<name>.md  procedures learned
  research/                read-only Phase 0 reports
  dashboard.json           live view fields; control.html  Relay Control, rendered
```

Templates for each are in `templates/`. Read them before writing the files.

## Relay Control

The human supervising is a project manager, not a co-author: they need one view
that answers "do I need to do something" without reading code. Regenerate it with
`python3 scripts/render_dashboard.py --relay-dir .relay` and send it after every
leg, at every stage gate, and the moment anything needs their attention. Read
`references/dashboard.md` before the first render — the attention band is what
earns the dashboard, and the part you write yourself.

## Phases

### Phase 0 — Scope

Ask the human the questions whose answers change the plan: what done looks like,
hard constraints, what is explicitly out of scope, what already exists. Ask once,
in a batch. Do not ask what you can answer by reading the repo.

Then fan out **read-only** research agents in parallel — one per angle, never one
per query: how the existing codebase does this today (trace the flows); how the
target library or API actually works (docs, source); production patterns and known
pitfalls; edge cases and failure modes.

Each returns a compressed report into `.relay/research/`. You read the reports,
not the raw sources. This is also the whole harness when the request is
research-only: stop after the synthesis and deliver the brief.
`references/research.md` has the gap-analysis loop that decides when research is
finished.

### Phase 1 — Acceptance contract

Write `.relay/contract.md` **before planning any implementation**.

A check is a testable behavioural claim with a stable ID, pass/fail criteria a
stranger could judge, and the evidence required to prove it:

```
### ACC-AUTH-001 — Valid credentials reach the dashboard
A user submits a correct email and password on /login and lands on /dashboard
with a session cookie set.
Evidence: screenshot of /dashboard, POST /api/auth/login → 200, no console errors.
```

Rules:

- Group by user-facing area, then add cross-area flow checks.
- Behavioural, not implementational. "Returns 200", not "calls `authService.login`".
- Aim for coverage, not volume — a real feature is dozens of checks, not five —
  and spend the time, because ambiguity here becomes rework later.
- **Write one to three standing checks in the user's own words.** One of them is
  always the sentence that says what the user runs. Quote sentences
  from the request verbatim, mark them `Standing`, and re-verify every one of them
  at **every** stage gate, not once. They are the request; the rest of the
  contract is only your reading of it.
- **A standing check is never marked passed by inspection** — not by reading code,
  not by citing a unit test, not from the program's own output. A judge does what a
  user does, starting the product the way the user starts it, or the check fails.
- **Turn the project's conventions into checks.** Read `CLAUDE.md`, the lint
  config and two neighbouring files, and write what they demand as checks a judge
  can measure: module size, duplication, seams, dead code. `relay-control` had
  none, and `tests/frame.py` reached 2900 lines.

### Phase 2 — Leg plan

Decompose into legs in `.relay/legs.json`. Each leg is bounded enough for one
fresh runner to finish in one context, lists `fulfills` (the check IDs it makes
true) and `dependsOn` (legs that must land first), and names its own verification
steps.

**Coverage gate:** every check is claimed by exactly one leg — no orphans, no
duplicates, and only the leg that makes a check fully testable claims it.

Group legs into **stages**. **Stage 1 ends in a walking skeleton: the user runs
the command they asked for and sees real output from real input, with no stub on
the path they walk.** Thin, ugly and incomplete is fine; not runnable is not. Plan
stage 1 backwards from that command — the entrypoint, how it is invoked, and the
check that proves it starts are stage-1 legs, never later ones.
Every later stage is a user-visible slice ending in one more thing the user can do.

**Never group stages by horizontal layer** — model, then chrome, then views, then
entrypoint. That plan put `relay-control`'s entrypoint check in stage 4 of 4:
30 hours, 2100 tests, and `No module named relay_control` the first time the human
typed the command.

End every stage with two **judge legs** (`<stage>/code-judge` and
`<stage>/behaviour-judge`) in the queue like any other, so judging shows in the
plan, the dashboard and the run count instead of hiding inside a gate.

### Phase 3 — Approval gate

Present to the human, then stop and wait:

```
RELAY      one line
CONTRACT   N checks across M areas
STAGES     S1: name (n legs) → S2: ...
SKELETON   the command the user can run once S1 clears
FIRST LEG  the opening leg
RISKS      the two or three things most likely to go wrong
```

Do not start before an explicit go.

### Phase 4 — Running the legs

Loop over pending legs **in order**. For each one:

1. Spawn a **fresh runner** with clean context. Give it: the leg spec, the full
   text of the checks it must fulfil, relevant research reports, any matching
   `.relay/skills/`, and the project conventions — never your own conversation.
2. The runner writes tests first, then implements, then runs its own verification
   steps. **Mutation testing has one purpose — prove the guard exists — and a
   default budget of about ten per leg, aimed at the properties that leg's checks
   name.** A runner may exceed it for a stated reason; `relay-control`'s batteries
   of 60–90 were 20 of its 30 hours, and the 60th found nothing the 10th did not.
3. The runner commits **by explicit pathspec** — never a bare `git add`, never a
   plain `git commit`, never `checkout --`/`stash`/`reset`, and a mutation is
   restored from its own backup, because parallel runners share one index. **Git is
   the exchange zone** — the next runner inherits the codebase, not a message.
4. The runner writes `.relay/batons/<leg>.md` with five fields: what was
   implemented, what was left undone, commands run with exit codes, issues
   discovered, and whether the specified procedure was followed.
5. You read the baton and **dispose of every item**. Each discovered issue
   becomes a follow-up leg or gets an explicit written dismissal in `relay.md`.
   Nothing is silently dropped.
6. Update `state.json` and `dashboard.json`, re-render Relay Control, send it.
   One leg done is one dashboard refresh.

Runners may spawn read-only subagents for search and doc lookup; they may not spawn
other runners, talk to each other, or change the contract. Keep your own context for
coaching — structural overview, baton synthesis, sequencing, the human — and push
every deep read into a subagent. `references/execution.md` has the runner briefing
template and the recovery plays for a stuck, slow or blocked leg.

### Phase 5 — Stage judging

When every implementation leg in a stage is done, its two judge legs run with
**fresh context and no implementation history**, in parallel with each other:

- **Code judge** — run the test suite, linter, type checker; then spawn a
  parallel review subagent per completed leg and synthesise their findings into
  one report. It reads the diff; it does not run the product.
- **Behaviour judge** — act like a QA engineer. Launch the application, drive the
  real interface, walk each check's flow — including every standing check, at
  every gate — and collect the evidence it names.

Neither judge has seen the code written — judging is adversarial by design. They
judge against the contract, never the implementation's own assumptions, and where
models differ a different provider judges than implements: a judge from the same
family accepts the same mistakes.

Update `state.json`: each check becomes `passed`, `failed`, or `blocked`.

### Phase 6 — Fix loop

**Judging does not pass on the first attempt. This is normal, not failure.**
Expect roughly a third of your total legs to be fix legs.

For each failure, create a targeted fix leg, insert it at the head of the queue,
and return to Phase 4. Repeat until every check in the stage reads `passed`, then
the stage is **cleared** and you advance — but the check that proves the product
starts is re-verified at every later gate. A cleared stage does not stay cleared
for free: a later leg that reorganises the package leaves that check reading
`passed` while the user gets an import error.

**The floor.** A check passes once its behaviour holds and one mutation of the
property it names fails the suite. A defect in the guard on that guard is written
into `relay.md` as debt, not turned into a fix leg, unless it hides a behavioural
defect. `relay-control` had no floor and spent gate rounds 5, 6 and 7 on guards on
guards — round 6 left 18 of 21 mutations green.

**The budget: three legs per check.** At a check's third failure, stop and put a
scope decision to the human — cut it, change it, or take it as written debt —
instead of writing a fourth leg. `ACC-DATA-009` took 10 legs and 7 gate rounds.

If progress stalls otherwise — a fix breaks a previously passing check — **stop
and hand control back to the human** with what you tried and what you believe is
wrong. Do not grind.

### Phase 7 — Finish

The relay completes when every check in `state.json` reads `passed`. Report:

```
SHIPPED    legs run, of which N were fixes
CONTRACT   N/N checks passed
JUDGING    rounds per stage
OPEN       dismissed items and accepted debt, with justification
NEXT       what a human should look at first
```

## Skills: the relay learns as it runs

A long relay repeats itself: the fourth runner rediscovers the build quirk the
first one hit and throws the finding away with its context. Closing that loop —
run, observe, encode the rule as a skill — makes hour ten cheaper than hour one.

Keep reusable procedure in `.relay/skills/<name>.md` and name it in the runner
briefing. Encode one when a baton shows the same friction twice — a non-obvious
build step, a test-harness gotcha, a convention the repo enforces that no runner
could infer from the code. Reuse before you write: check `.relay/skills/` at
planning time, and pull in project skills the repo already has.

A skill is a procedure, not a fact. "Run `pnpm -r build --filter crypto` before
testing sharing, or keywrap resolves stale" is a skill. "The project uses pnpm"
belongs in `relay.md`.

## Models: match the model to the role

No single model, and no single provider, is best at all three roles. Where you can
choose: the **coach** needs slow, careful reasoning (constraint analysis,
long-horizon decomposition); the **runner** needs code fluency and speed; the
**judge** needs strict instruction-following and **a different provider from the
runner**, because same-family models share the blind spot that produced the bug.
Keep the roles prompt-driven rather than pinned to a model — locking every role to
one family caps the relay at that family's weakest capability.

## Scaling down

Not every task deserves a relay — a single-file fix or a question costs more to
coordinate than to do. Use the harness when the objective spans multiple legs,
must survive multiple context windows, or must be verifiably rather than plausibly
correct. For small work keep only invariant one: state what done means first.

## References

- `references/research.md` — the deep-search loop and gap analysis
- `references/execution.md` — runner briefings, batons, parallel runners, recovery plays
- `references/validation.md` — writing checks that hold up, and the two judges
- `references/dashboard.md` — Relay Control: when to render, what to write
- `templates/` — the five state files; `assets/control.html` — dashboard template

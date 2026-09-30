---
name: relay
description: Run a long-horizon build as a supervised relay — write the architecture and the acceptance contract before any code, declare how the product starts, break the work into legs grouped into stages, run every leg with a fresh runner and fan out where their files and resources are disjoint, and gate every stage behind adversarial judges until all checks pass. Use when the user says "run a relay", "build this end to end", "ship this feature", "orchestrate this", or hands over a multi-leg project, spec, PRD, or roadmap too large for one session. Also use for deep technical research that must be exhaustive rather than a single-pass summary.
---

# Relay

Work too big for one context window, run so a human spends attention on decisions
instead of babysitting. You are the **coach**. You plan, hand out legs, and hold
the gates. You do not write implementation code yourself.

## Three invariants

Everything else here is machinery for these — and **every rule below binds you
too.** Almost every prohibition here names a runner or a judge, and the coach is
the only agent present for the whole run: in `relay-control` the coach reset three
live runners' trees with `git checkout --`, a thing runners are forbidden, and
marked six checks passed on inspection, a thing judges are forbidden. **You may not
mark a check passed. Only a judge does that. You may not record `debt` either —
only the human does.** You must never run `git checkout --`, `git stash`, or `git
reset` on a live runner's tree.

1. **Contract before code.** What counts as correct is written before an
   implementation exists to bias it. Tests written afterward confirm decisions;
   they do not catch bugs.
2. **One runner on the track — the track being the files *and* the resources.**
   The serial rule stops two runners making conflicting **architectural** choices,
   so it binds only legs that write the same files: **legs whose file sets are
   disjoint run in parallel, from the first leg on**, and read-only work always
   fans out. Disjoint files are not enough on their own — two legs that both start
   the stack collide on a port and a database while their `touches` stay perfectly
   disjoint, so every dispatch also carries an isolation context and obeys a
   concurrency ceiling (`references/environment.md`).
3. **The handoff carries state, not the conversation.** Every leg gets a fresh
   runner that reads state from disk and writes results back. No trajectory is
   carried forward, so there is none for attention to degrade across.

## Vocabulary

A **leg** is one bounded unit of work, finishable by one fresh runner in one
context. A **stage** is a group of legs judged together, *cleared* once all its
checks pass. A **check** is one testable claim, ID `ACC-<AREA>-<NNN>` — behavioural
unless marked `Convention`. A **runner** runs exactly one leg; a **judge** verifies,
never having seen the code written. A **handoff** (the **baton**) is the structured
JSON a runner writes at leg's end.

## Relay state

Create `.relay/` at the repo root (or working directory) at kickoff:

```
.relay/
  relay.md                 objective, constraints, non-goals, harness, environment, decisions
  architecture.md          the authoritative design the contract is derived from
  contract.md              the acceptance checks that define done
  services.yaml            how every service starts, stops and reports healthy
  init.sh                  idempotent environment setup, verifying not installing
  legs.json                ordered legs in stages; state.json  per-check status and run state
  batons/<leg>.json        one structured handoff per leg
  progress.jsonl           append-only typed event log, written by script
  skills/<name>.md         procedures learned;  library/<topic>.md  facts learned
  research/                read-only Phase 0 reports
  validation/<stage>/      judge synthesis;  evidence/<stage>/  the artefacts checks name
  dashboard.json           live view fields;  control.html  Relay Control, rendered
```

## The harness

**Run the capability pre-flight in `references/harness.md` before Phase 0** and
record the answers in `relay.md`. Name capabilities in briefings, never one
vendor's tool names, or the relay is welded to one product.

One answer is assumed rather than asked: **nested dispatch is unavailable.** A
dispatched agent may not dispatch another. So **the coach owns every fan-out** — a
code judge that needs eight per-leg reviews gets them as eight read-only legs you
dispatched and it synthesises. Relay depended on nesting twice and both were
silent collapses into one context, not errors.

## Relay Control

The human supervising is a project manager, not a co-author: one view answers "do
I need to do something" without reading code. Read `references/dashboard.md` first
for the regeneration command and when to send it — the attention band is the part
you write.

## Phases

### Phase 0 — Map the system, then scope

**Map first. A relay that starts inside one repository will keep discovering the
rest of the system as surprises, one interrupted week at a time.**

#### 0a — The system map, before anything else

Inventory **every** project the user has given you, not only the one the request
names. For each, read enough to answer what it is, what it talks to, and how —
**at least 500 lines**, concentrated on entry points, configuration, clients and
deployment manifests rather than skimming everywhere.

Then write `.relay/research/system-map.md`, and do not proceed without it:

- **every project**, one line each: what it is, who runs it, where it deploys
- **every edge between them**: HTTP calls, shared databases and tables, queues,
  shared config or secrets, build-time dependencies
- **every consumer of anything you might change** — frontends, widgets, SDKs,
  embedded integrations, other services, external identity providers, and any
  third party working from a documented contract
- **every external system in the authentication or data path** — an SSO provider,
  a token issuer, a payment gateway. These are edges you cannot read the source
  of, so their contract must be **observed**, not assumed
- **infrastructure that governs the code**: gitops repositories, Terraform,
  pipelines. Configuration that can revert your change is part of the system
- **what you could not determine**, named explicitly

Fan this out read-only and in parallel, one agent per project or per angle. You
read the reports, not the raw sources.

#### 0b — The environment readiness pre-flight

**A relay whose product cannot be started and driven cannot verify its own work.**
Answer four questions before the contract exists, because a "no" is stage-1 work
rather than something to discover at the first gate — full detail, and the shape
of both files, in `references/environment.md`:

1. Is there **one command** that brings the whole stack up? If not, write
   `.relay/services.yaml` and `.relay/init.sh`, and make them stage-1 legs.
2. Do the **logs land on disk**? An agent reads a file; it cannot scroll a
   terminal it does not own.
3. Can the product be **driven programmatically** — browser, terminal, HTTP? Name
   the driver per surface. If there is none, building it is the relay's first
   deliverable.
4. Does running it **fit on this machine**? Judges run beside the app.

Record the answers and the concurrency ceiling in `relay.md`.

#### 0c — Scope

Only now ask the human the questions whose answers change the plan: what done
looks like, hard constraints, what is explicitly out of scope. Ask once, in a
batch. **Never ask what the map should have told you.**

Then research the specific problem: how the codebase does this today (trace the
flows); how the target library or API actually works; pitfalls and failure modes.
`references/research.md` has the gap-analysis loop that ends research.

This is also the whole harness when the request is research-only: stop after the
synthesis and deliver the brief.

#### The test for a finished map

Point at any component you intend to change and ask: **who calls this, what do
they expect, and how would they find out it changed?** If any answer is "I do not
know", the map is not finished — and that unknown is exactly where the week gets
lost.

### Phase 1 — Architecture, then the acceptance contract

#### 1a — `.relay/architecture.md`, the authoritative design

**Write the design down before the contract, and mark it authoritative.** Serialising
legs stops two runners making conflicting architectural choices; it does not tell
either of them what the choice already was. Without this file every runner
re-derives the architecture from the code it happens to read, and the tenth one
derives a different answer from the first.

Keep it short and decided: the seams and who owns each side, the data model and
where state lives, the boundaries a leg may not cross, the shape of every edge the
system map named, and the choices that were considered and rejected with why.
`templates/architecture.md` is the shape. The contract is derived **from this
file**; when the two disagree, this file is wrong and gets fixed first.

#### 1b — `.relay/contract.md`, before planning any implementation

Open it with a **fixture preamble**: the named fixtures every check reuses — the
users and their roles, the tokens, the seed data, the one command that seeds them.
Without it each judge invents its own fixtures and two rounds of evidence do not
compare.

Then each check is a testable behavioural claim with a stable ID, pass/fail
criteria a stranger could judge, the **tool** that exercises it, and the evidence
required to prove it:

```
### ACC-AUTH-001 — Valid credentials reach the dashboard
A user submits a correct email and password on /login and lands on /dashboard
with a session cookie set.
Tool: browser driver
Evidence: screenshot of /dashboard, POST /api/auth/login → 200, no console errors.
```

`Tool:` is not decoration. It routes the check to a judge that has that driver, and
it is how a contract of 200 checks gets sharded at a gate instead of walked by one
judge in one context.

Rules:

- Group by user-facing area, then add cross-area flow checks.
- Behavioural, not implementational. "Returns 200", not "calls `authService.login`".
- Aim for coverage, not volume — a real feature is dozens of checks, not five —
  and spend the time, because ambiguity here becomes rework later.
- **Write one to three standing checks in the user's own words.** One of them is
  always the sentence that says what the user runs. Quote from the request
  verbatim, mark them `Standing`, and re-verify every one at **every** stage gate,
  not once. They are the request; the rest of the contract is your reading of it.
- **A standing check is never marked passed by inspection** — not by reading code,
  not by citing a unit test, not from the program's own claim about itself. A judge
  does what a user does, starting the product the way a user starts it, or it fails.
- **Turn the project's conventions into checks.** Read whichever of `AGENTS.md`,
  `CLAUDE.md` or the harness's own rules file exists, plus the lint config and two
  neighbouring files, and write what they demand as checks a judge can measure:
  module size, duplication, seams, dead code. Mark them `Convention` — the one
  exemption from "behavioural, not implementational", measured against the tree by
  the code judge, never reached for by the behaviour judge. `relay-control` had
  none, and `tests/frame.py` reached 2900 lines.

**Walk the system map and write a check for every edge it names.** A contract that
stops at one service's boundary is half a contract, and the missing half is where
the rework comes from. Cover the user-visible outcome whenever:

- **a response shape changes** — a removed field breaks anything that reads it,
  and the reader may not be in this repository
- **an outcome code changes** — 200 becoming 404, or a new 401. Name what the
  client does with it: log out, retry, render an empty state, show a raw error
- **data acquires a lifetime** — retention and archival are UI decisions before
  they are storage decisions. "What does a user see when it is older than the
  window" belongs in the contract, not deferred to the owner as policy
- **an external contract is involved** — an SSO provider, a token issuer, a
  gateway. Verify against a **real** token or response, never one you minted to
  your own assumptions. A fail-closed fix on an authentication path is one wrong
  assumption away from locking out every legitimate user rather than an attacker
- **a capability is removed for one class of caller** — user-visible even when
  entirely deliberate

The tell: if the only answer to *"how would anyone notice this?"* is *"a test
fails"*, the contract is incomplete.

**Advisors on, this step asks Jev per check** — one call per section, never per
document:

```bash
relay-contract --input .relay/contract.md --split-on '^### ACC-' --summary
```

Rewrite every check that comes back below the bar; the answer names the defect
(`references/advisors.md` has the question shapes and the rules).

### Phase 2 — Leg plan

Decompose into legs in `.relay/legs.json`. Each leg is bounded enough for one fresh
runner to finish in one context, and names `fulfills` (the check IDs it makes true),
`dependsOn` (legs that must land first), `touches` (every path in the work tree it
may write, a directory standing for all of it — this is what makes fan-out
computable), the `skillName` of the seat that runs it (`references/squad.md`), and
its own verification steps.

**Coverage gate:** every check is claimed by exactly one leg — no orphans, no
duplicates, and only the leg that makes a check fully testable claims it.

Group legs into **stages**. **Stage 1 ends in a walking skeleton: the user runs the
command they asked for and sees real output from real input, with no stub on the
path they walk.** Thin, ugly and incomplete is fine; not runnable is not. Plan
stage 1 backwards from that command — the entrypoint, how it is invoked, and the
check that proves it starts are stage-1 legs, never later ones. Every later stage is
a user-visible slice ending in one more thing the user can do.

**Never group stages by horizontal layer** — model, then chrome, then views, then
entrypoint. That plan put `relay-control`'s entrypoint check in stage 4 of 4:
30 hours, 2100 tests, and `No module named relay_control` the first time the human
typed the command.

Two things the plan carries besides legs:

- **The concurrency ceiling**, in `relay.md`: how many runners, how many UI judges,
  how many API judges, and the machine's footprint budget. Dispatch honours it.
- **The skills the legs will need.** Check `.relay/skills/` first and reuse; write
  the one or two a whole stage depends on **now**, at planning time, rather than
  discovering them at leg six. Name them in each leg's `skills`.

End every stage with two **judge legs** (`code-judge-<stage>` and
`behaviour-judge-<stage>`) in the queue like any other leg, so judging shows in the
plan, the dashboard and the run count instead of hiding inside a gate.

**Advisors on, this step asks Jev per leg and per stage** —
`relay-leg --input <leg or stage json>`: split every leg that answers `no` on
one-runner fit, re-slice every stage that answers `horizontal_layer`.

With advisors on, lint the finished contract and plan before the approval gate —
see `references/advisors.md`.

### Phase 3 — Approval gate

Present to the human, then stop and wait:

```
RELAY        one line
ARCHITECTURE the two or three decisions that shape everything else
CONTRACT     N checks across M areas
STAGES       S1: name (n legs) → S2: ...
SKELETON     the command the user can run once S1 clears
ENVIRONMENT  one command starts the stack: yes | writing it in S1
RUNS         ~N agent runs: L legs + 2 per stage, plus about a third again in fixes
CONCURRENCY  R runners, J judges at once; serial if the harness cannot fan out
FIRST LEG    the opening leg
RISKS        the two or three things most likely to go wrong
ADVISORS     on | off  (see references/advisors.md; off if code may not leave the org)
```

**`RUNS` is not optional.** A human approving a twelve-hour spend is entitled to the
number before it starts, and the arithmetic is simple: one run per leg, two judges
per stage, and roughly a third again in fix legs. Say it is a floor, because
judging finds work.

Do not start before an explicit go. Log `relay_accepted` when it comes.

### Phase 4 — Running the legs

**Dispatch together every pending leg in this stage whose `dependsOn` have landed,
whose `touches` are disjoint from every other in flight, and that still fits under
the concurrency ceiling.** Compute it, never reason it out:

```bash
python3 scripts/relay_decide.py dispatch --relay-dir .relay
```

1. Spawn a **fresh runner** with clean context. Give it: the leg spec, the full
   text of the checks it must fulfil, its **isolation context** (ports, data
   namespace, credentials, log path, scratch prefix), relevant research reports,
   any matching `.relay/skills/` and `.relay/library/`, `architecture.md`, and the
   project conventions — never your own conversation. A judge leg gets
   `contract.md` whole: it judges against every check, not a `fulfills` list, which
   is empty for judges.
2. The runner writes tests first, then implements, then runs its own verification
   steps. **Mutation testing has one purpose — prove the guard exists — and a
   default budget of about ten per leg, aimed at the properties that leg's checks
   name.** A runner may exceed it for a stated reason; `relay-control`'s batteries
   of 60–90 were 20 of its 30 hours, and the 60th found nothing the 10th did not.
3. The runner commits **by explicit pathspec**, per `references/execution.md`'s
   shared-tree rules. **Git is the exchange zone** — the next runner inherits the
   codebase, not a message.
4. The runner writes `.relay/batons/<leg>.json` in the shape
   `templates/handoff.json` gives, and **validates it before reporting done**:

   ```bash
   python3 scripts/relay_handoff.py validate <leg> --relay-dir .relay
   ```

   A handoff that does not validate is not a handoff. The prose baton this replaced
   failed silently: one misplaced colon and the dashboard read a failed leg as
   Success.
5. You read the handoff and **dispose of every item** — `leftUndone` and
   `discoveredIssues`, blocking ones first:

   ```bash
   python3 scripts/relay_handoff.py items <leg> --relay-dir .relay
   ```

   Each becomes a follow-up leg or gets an explicit written dismissal in `relay.md`.
   Nothing is silently dropped. `repeatFriction` goes to `.relay/skills/`, a fact a
   later judge will need goes to `.relay/library/`, and `decisions` go to
   `relay.md`'s decisions log. **Advisors on, ask Jev per item first** —
   `relay-baton`, one call per item, its disposition suggestion weighed under the
   rules in `references/advisors.md`.
6. Record the events, then refresh the view:

   ```bash
   python3 scripts/relay_log.py append leg_completed --field leg=<leg> \
       --field status=success --field commit=<sha> --field sessionId=<id>
   ```

   Update `state.json` and `dashboard.json`, re-render Relay Control, send it. One
   leg done is one dashboard refresh. **Write the runner's session id into the
   leg**, so a supervisor can open the transcript of the agent that did it.

Runners may not dispatch other agents, talk to each other, or change the contract.
Keep your own context for coaching and push every deep read into a read-only leg.
**Brief every runner from `references/execution.md`'s template**, which also has the
recovery plays.

### Phase 5 — Stage judging

When every implementation leg in a stage is done, its judge legs run with **fresh
context and no implementation history**, in parallel. **Brief them from
`references/validation.md`** — the full remit for each lives there, including the
code judge's structure duties; a judge not handed it is the judge that read
`relay-control` through `build()` for seven rounds.

Bring the stack up first, from the declaration rather than by discovery:

```bash
python3 scripts/relay_services.py up --relay-dir .relay && \
python3 scripts/relay_services.py status --relay-dir .relay
```

The **code judge** runs the suite, the linter and the type checker, marks every
`Convention` check, and judges structure. Its per-leg reviews are read-only legs
**you** dispatch, because a judge cannot dispatch its own.

The **behaviour judge** acts like a QA engineer: it starts the product the way a
user reaches it, walks each check's flow — including every standing check, at every
gate — and collects the evidence named. **It never reaches past the entrypoint to
decide a verdict, and "I could not start it" is a failure against the whole stage,
not a note.**

**Shard behaviour judging by `Tool:` and by flow** once a stage carries more checks
than one context can walk honestly — around thirty is the point it starts to fray.
Each shard is its own judge leg with its own isolation context and its own slice of
check IDs, they run in parallel under the ceiling, and you synthesise their reports
into `.relay/validation/<stage>/behaviour/synthesis.json`. One judge walking 200
checks in one context is the exact failure invariant 3 exists to prevent.

Judging is adversarial: judge against the contract, never the implementation's own
assumptions, and where models differ let a different provider judge than
implements. Each judge marks in `state.json` only the checks its own evidence
covers — `passed`, `failed` or `blocked` — so two never write the same entry.

### Phase 6 — Fix loop

**Judging does not pass first time. That is normal** — expect roughly a third of
your legs to be fix legs. Advisors on, triage each failure with
`relay-failure --input "<the check's reason from state.json>"` before writing a
fix leg: a `contract` answer goes to the human, never a fix leg. For each
failure, create a targeted fix leg naming in
`repairs` the checks it is for, insert it at the head of the queue, and return to
Phase 4. Repeat until every check in the stage reads `passed` or carries recorded
`debt`, then the stage is **cleared** and you advance — but the check that proves
the product starts is re-verified at every later gate. A cleared stage does not stay
cleared for free: a later leg that reorganises the package leaves that check reading
`passed` while the user gets an import error.

**Write down what the round taught you.** Every gate round produces facts the next
round would otherwise rediscover: how this surface is verified, which assertion
needs two browser contexts, which failure was environmental. They go in
`.relay/library/<topic>.md` under the stage and round that found them. A relay that
skips this pays for the same discovery at every gate.

**The floor.** A check passes once its behaviour holds and one mutation of the
property it names fails the suite. A defect in the guard on that guard is written
into `relay.md` as debt, not turned into a fix leg, unless it hides a behavioural
defect. `relay-control` had no floor and spent gate rounds 5, 6 and 7 on guards on
guards — round 6 left 18 of 21 mutations green.

**The budget: three legs per check.** Count every leg against it — the leg that
claims it in `fulfills`, plus each leg whose `repairs` names it. Stop at the third
failure and put a scope decision to the human — cut it, change it, or mark it `debt`
in `state.json` with the reason — rather than writing a fourth leg. `ACC-DATA-009`
took 10 legs and 7 gate rounds.

When a fix breaks a passing check, revert it, make the regression its own check, and
re-plan — once. If that same check breaks again, **stop and hand control back to the
human** with what you tried and what you believe is wrong.

### Phase 7 — Finish

The relay completes when every check reads `passed` or recorded `debt`. Report:

```
SHIPPED    legs run, of which N were fixes
CONTRACT   N/N checks passed
JUDGING    rounds per stage
OPEN       dismissed items and accepted debt, with justification
NEXT       what a human should look at first
```

## Pause, resume and redirect

A relay is not fire-and-forget, and the human steering it types plain language, not
commands. Four interventions cover almost everything. All four are **state on disk**,
in `state.json`'s `run` block, so a resumed relay knows what happened:

| The human says | You do |
|---|---|
| "pause, the schema changed" | Set `run.state` to `paused`, log `relay_paused`, finish nothing new. Update `architecture.md` and the contract **before** re-scoping the remaining legs. Never let the code and the contract drift apart. |
| "this leg is stuck, mark it done and move on" | Capture whatever handoff exists, mark the leg `partial`, turn its unmet checks into their own legs, and carry on. Do not silently pass its checks. |
| "why is nothing happening" | Re-read `dispatch`, say which legs are waiting and on what, and put it in the attention band. A relay that looks busy while nothing converges is worse than one that says it is stuck. |
| "we are doing X instead now" | Pause, re-scope, and say plainly which finished legs are now wasted work. |

Resuming reads `.relay/` and nothing else. If that is not enough to resume from,
the handoffs are not being written properly.

## Skills and the library: the relay learns as it runs

A long relay repeats itself: the fourth runner rediscovers the build quirk the first
hit and throws it away with its context. Two files catch it, and the difference
between them matters.

**`.relay/skills/<name>.md` is a procedure** — something an agent *does*. "Run
`pnpm -r build --filter crypto` before testing sharing, or keywrap resolves stale."
Encode one when a handoff shows the same friction twice, and name it in the briefing.
Reuse before you write.

**`.relay/library/<topic>.md` is a fact** — something an agent needs to *know*. How
this surface is verified and with which driver; that the accessibility checker skips
disabled controls so a read-only form audits vacuously clean; what round 1 of this
gate got wrong and why. Facts are most of what a gate round produces, and the rule
"a skill is a procedure, not a fact" used to throw all of them away.

"The project uses pnpm" is neither. It belongs in `relay.md`.

## Scaling

Not every task deserves a relay — a single-file fix costs more to coordinate than to
do. Use it when the objective spans multiple legs or must be verifiably rather than
plausibly correct. For small work keep invariant one: define done first.

Advisors on, kickoff asks Jev first — `relay-scale --input <objective>` — and its
`single_session` answer is weighed here. A low-confidence answer is not a verdict:
it is weighed, and the human's explicit instruction outranks it.

There is an upper bound too. Past roughly a few hundred legs, one contract stops
being reviewable and one dashboard stops being readable: **split it into several
relays with a written interface between them**, run in sequence, each with its own
contract. And do not convert a long ordinary session into a relay — start a fresh
one, because the planning, the contract and the architecture are the value, and a
session that has been coding for three hours has already made those choices
implicitly.

## References

- `references/harness.md` — the capability pre-flight, and what each harness calls things
- `references/environment.md` — `services.yaml`, `init.sh`, drivers, isolation, the ceiling
- `references/research.md` — the deep-search loop and gap analysis
- `references/execution.md` — briefings, handoffs, parallel runners, recovery plays
- `references/validation.md` — checks that hold up, and the two judges
- `references/dashboard.md` — Relay Control: when to render, what to write
- `references/squad.md` — the seats, and why they do not message each other
- `references/governance.md` — review ownership, the merge gate, environments
- `references/advisors.md` — optional second opinions, and what they may never do
- `templates/` — the shapes for `relay.md`, `architecture.md`, `contract.md`,
  `legs.json`, `state.json`, `services.yaml`, `init.sh` and `handoff.json`; read one
  before writing that file. `assets/control.html` — dashboard template

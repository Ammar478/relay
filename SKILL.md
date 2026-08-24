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
2. **One runner on the track. Readers fan out.** Anything that mutates state
   runs strictly serially. Anything read-only — search, research, review — runs
   in parallel. Concurrency is a property of the operation, not the schedule.
3. **The baton carries state, not the conversation.** Every leg gets a fresh
   runner that reads state from disk and writes results back. No trajectory is
   carried forward, so there is none for attention to degrade across.

## Vocabulary

| Term | Meaning |
|---|---|
| **relay** | The whole run, from objective to shipped |
| **leg** | One bounded unit of work, finishable by one fresh runner in one context |
| **stage** | A group of legs worth judging together; *cleared* once all its checks pass |
| **check** | One testable behavioural claim, ID `ACC-<AREA>-<NNN>` |
| **coach** | You. Plans, hands out legs, disposes of batons, holds the gates |
| **runner** | A fresh agent that runs exactly one leg |
| **judge** | A fresh agent that verifies, having never seen the code written |
| **baton** | The structured handoff a runner writes when its leg ends |

## Relay state

Create `.relay/` at the repo root (or working directory) at kickoff:

```
.relay/
  relay.md                 objective, constraints, non-goals, decisions log
  contract.md              the acceptance checks that define done
  legs.json                ordered legs, grouped into stages
  state.json               per-check and per-leg status
  batons/<leg>.md          one baton per completed leg
  skills/<name>.md         procedures learned during the run
  research/                read-only reports gathered in Phase 0
  dashboard.json           live view fields state.json cannot hold
  control.html             Relay Control, regenerated
```

Templates for each are in `templates/`. Read them before writing the files.

## Relay Control

The human supervising is a project manager, not a co-author. They need one view
that answers "do I need to do something" without reading code. Regenerate it and
send it to them after every leg, at every stage gate, and the moment anything
needs their attention:

```
python3 scripts/render_dashboard.py --relay-dir .relay
```

Read `references/dashboard.md` before the first render — the attention band is
the part that earns the dashboard, and the part you write yourself.

## Phases

### Phase 0 — Scope

Ask the human the questions whose answers change the plan: what done looks like,
hard constraints, what is explicitly out of scope, what already exists. Ask once,
in a batch. Do not ask what you can answer by reading the repo.

Then fan out **read-only** research agents in parallel — one per angle, never one
per query:

- how the existing codebase does this today (search, trace the flows)
- how the target library, API, or framework actually works (docs, source)
- production patterns and known pitfalls for this problem
- edge cases and failure modes

Each returns a compressed report into `.relay/research/`. You read the reports,
not the raw sources. This is also the whole harness when the request is
research-only: stop after the synthesis and deliver the brief.

See `references/research.md` for the gap-analysis loop that decides when research
is actually finished.

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
- Ambiguity here becomes rework later. Spend the time.
- Aim for coverage, not volume — but a real feature is dozens of checks, not five.

### Phase 2 — Leg plan

Decompose into legs in `.relay/legs.json`. Each leg:

- is bounded enough for one fresh runner to finish in one context
- lists `fulfills`: the check IDs it makes true
- lists `dependsOn`: legs that must land first
- names its own verification steps

**Coverage gate:** every check is claimed by exactly one leg. No orphans, no
duplicates. Only the leg that makes a check fully testable claims it.

Group legs into **stages** — a logical unit of functionality worth judging as a
whole. Stages set how often you catch drift, so use few for simple work and more
for complex work.

End every stage with two **judge legs**, in the queue like any other:

```
sharing/code-judge-sharing
sharing/behaviour-judge-sharing
```

Judging is visible work with a real cost. Putting it in the queue means it shows
in the plan, the dashboard, and the run count instead of hiding in a gate.

### Phase 3 — Approval gate

Present to the human, then stop and wait:

```
RELAY      one line
CONTRACT   N checks across M areas
STAGES     S1: name (n legs) → S2: ...
FIRST LEG  the opening leg
RISKS      the two or three things most likely to go wrong
```

Do not start before an explicit go.

### Phase 4 — Running the legs

Loop over pending legs **in order**. For each one:

1. Spawn a **fresh runner** with clean context. Give it: the leg spec, the full
   text of the checks it must fulfil, relevant research reports, any matching
   `.relay/skills/`, and the project conventions — never your own conversation.
2. The runner writes tests first, then implements, then runs its own
   verification steps.
3. The runner commits. **Git is the exchange zone** — the next runner inherits
   the codebase, not a message.
4. The runner writes `.relay/batons/<leg>.md` with five fields: what was
   implemented, what was left undone, commands run with exit codes, issues
   discovered, and whether the specified procedure was followed.
5. You read the baton and **dispose of every item**. Each discovered issue
   becomes a follow-up leg or gets an explicit written dismissal in `relay.md`.
   Nothing is silently dropped.
6. Update `state.json` and `dashboard.json`, re-render Relay Control, send it.
   One leg done is one dashboard refresh.

Runners may spawn read-only subagents for search and doc lookup. Runners may not
spawn other runners, may not talk to each other, and may not change the contract.

Keep your own context for coaching: structural overview, baton synthesis,
sequencing, and the human. Push every deep read into a subagent.

`references/execution.md` has the runner briefing template and the recovery plays
for a stuck, slow, or blocked leg.

### Phase 5 — Stage judging

When every implementation leg in a stage is done, its two judge legs run with
**fresh context and no implementation history**, in parallel with each other:

- **Code judge** — run the test suite, linter, type checker; then spawn a
  parallel review subagent per completed leg and synthesise their findings into
  one report. It reads the diff; it does not run the product.
- **Behaviour judge** — act like a QA engineer. Launch the application, drive the
  real interface, walk each check's flow, collect the evidence it names.

Neither judge has ever seen the code being written. That is the point: judging is
adversarial by design. They judge against the contract, never against the
implementation's own assumptions. Where models differ, prefer a different
provider for judging than for implementing — a judge from the same family
accepts the same mistakes.

Update `state.json`: each check becomes `passed`, `failed`, or `blocked`.

### Phase 6 — Fix loop

**Judging does not pass on the first attempt. This is normal, not failure.**
Expect roughly a third of your total legs to be fix legs.

For each failure, create a targeted fix leg, insert it at the head of the queue,
and return to Phase 4. Repeat until every check in the stage reads `passed`, then
the stage is **cleared** and you advance.

If progress stalls — the same check fails three rounds, or a fix breaks a
previously passing check — **stop and hand control back to the human** with what
you tried and what you believe is wrong. Do not grind.

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

A long relay repeats itself. The fourth runner rediscovers the build quirk the
first one hit, burns the same twenty minutes, and throws the finding away with
its context. Closing that loop is what makes hour ten cheaper than hour one:

```
new leg → run → observe what went wrong → learn the rule → encode a skill
   ↑                                                             │
   └─────────────────────────────────────────────────────────────┘
```

Keep reusable procedure in `.relay/skills/<name>.md` and name it in the runner
briefing. Encode one when a baton shows the same friction twice — a non-obvious
build step, a test-harness gotcha, a convention the repo enforces that no runner
could infer from the code. Reuse before you write: check `.relay/skills/` at
planning time, and pull in project skills the repo already has.

A skill is a procedure, not a fact. "Run `pnpm -r build --filter crypto` before
testing sharing, or keywrap resolves stale" is a skill. "The project uses pnpm"
belongs in `relay.md`.

## Models: match the model to the role

No single model — and no single provider — is best at all three roles. Where you
can choose, choose deliberately:

| Role | What it needs |
|---|---|
| Coach | Slow, careful reasoning. Strategic questions, constraint analysis, long-horizon decomposition. |
| Runner | Code fluency and speed. Fast generation, confident tool use. |
| Judge | Strict instruction-following, and **a different provider from the runner** — same-family models share the blind spot that produced the bug. |

Keep the roles prompt-driven rather than pinned to a model, so the harness
improves as models do. Locking every role to one family caps the whole relay at
that family's weakest capability.

## Scaling down

Not every task deserves a relay. A single-file fix, a routine edit, or a question
does not — coordination will cost more than the work. Use the harness when the
objective spans multiple legs, needs to survive multiple context windows, or must
be verifiably correct rather than plausibly correct. For small work, keep only
invariant one: state what done means before you start.

## References

- `references/research.md` — the deep-search loop and gap analysis
- `references/execution.md` — runner briefings, batons, and recovery plays
- `references/validation.md` — writing checks that hold up, and the two judges
- `references/dashboard.md` — Relay Control: when to render, what to write
- `templates/` — relay.md, contract.md, legs.json, state.json, baton.md
- `assets/control.html` — dashboard template; `scripts/render_dashboard.py` fills it

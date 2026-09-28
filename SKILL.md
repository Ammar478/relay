---
name: relay
description: Run a long-horizon build as a supervised relay — write the acceptance contract before any code, break the work into legs grouped into stages, run every leg with a fresh runner and fan out where their files are disjoint, and gate every stage behind adversarial judges until all checks pass. Use when the user says "run a relay", "build this end to end", "ship this feature", "orchestrate this", or hands over a multi-leg project, spec, PRD, or roadmap too large for one session. Also use for deep technical research that must be exhaustive rather than a single-pass summary.
---

# Relay

Work too big for one context window, run so a human spends attention on
decisions instead of babysitting. You are the **coach**. You plan, hand out
legs, and hold the gates. You do not write implementation code yourself.

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
2. **One runner on the track — the track being the files.** The serial rule stops
   two runners making conflicting **architectural** choices, so it binds only legs
   that write the same files: **legs whose file sets are disjoint run in parallel,
   from the first leg on**, and read-only work always fans out (Phase 4 dispatches
   the fan-out; the shared-tree staging rules and the `relay-control` convergence
   evidence live in `references/execution.md`).
3. **The baton carries state, not the conversation.** Every leg gets a fresh
   runner that reads state from disk and writes results back. No trajectory is
   carried forward, so there is none for attention to degrade across.

## Vocabulary

A **leg** is one bounded unit of work, finishable by one fresh runner in one
context. A **stage** is a group of legs judged together, *cleared* once all its
checks pass. A **check** is one testable claim, ID `ACC-<AREA>-<NNN>` — behavioural
unless marked `Convention`. A **runner** runs exactly one leg; a **judge** verifies,
never having seen the code written. A **baton** is a runner's handoff at leg's end.

## Relay state

Create `.relay/` at the repo root (or working directory) at kickoff:

```
.relay/
  relay.md                 objective, constraints, non-goals, decisions log
  contract.md              the acceptance checks that define done
  legs.json                ordered legs in stages; state.json  per-check status
  batons/<leg>.md          one per leg; skills/<name>.md  procedures learned
  research/                read-only Phase 0 reports; evidence/  judge artefacts
  dashboard.json           live view fields; control.html  Relay Control, rendered
```

## Relay Control

The human supervising is a project manager, not a co-author: one view answers "do
I need to do something" without reading code. Read `references/dashboard.md`
first for the regeneration command and when to send it — the attention band is
the part you write.

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

#### 0b — Scope

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
  always the sentence that says what the user runs. Quote from the request
  verbatim, mark them `Standing`, and re-verify every one at **every** stage gate,
  not once. They are the request; the rest of the contract is your reading of it.
- **A standing check is never marked passed by inspection** — not by reading code,
  not by citing a unit test, not from the program's own claim about itself. A judge
  does what a user does, starting the product the way a user starts it, or it fails.
- **Turn the project's conventions into checks.** Read `CLAUDE.md`, the lint
  config and two neighbouring files, and write what they demand as checks a judge
  can measure: module size, duplication, seams, dead code. Mark them `Convention`
  — the one exemption from "behavioural, not implementational", measured against
  the tree by the code judge, never reached for by the behaviour judge.
  `relay-control` had none, and `tests/frame.py` reached 2900 lines.

**Walk the system map and write a check for every edge it names.** A contract
that stops at one service's boundary is half a contract, and the missing half is
where the rework comes from. Cover the user-visible outcome whenever:

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

### Phase 2 — Leg plan

Decompose into legs in `.relay/legs.json`. Each leg is bounded enough for one
fresh runner to finish in one context, lists `fulfills` (the check IDs it makes
true), `dependsOn` (legs that must land first) and `touches` (every path in the
work tree it may write, a directory standing for all of it — this is what makes
fan-out computable), and names its own verification steps.

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

End every stage with two **judge legs** (`code-judge-<stage>` and
`behaviour-judge-<stage>`) in the queue like any other, so judging shows in the
plan, the dashboard and the run count instead of hiding inside a gate.

With Jev on, lint the finished contract (`relay-contract`) and plan
(`relay-leg`) before the approval gate — see **Advisory decisions**.

### Phase 3 — Approval gate

Present to the human, then stop and wait:

```
RELAY      one line
CONTRACT   N checks across M areas
STAGES     S1: name (n legs) → S2: ...
SKELETON   the command the user can run once S1 clears
FIRST LEG  the opening leg
RISKS      the two or three things most likely to go wrong
ADVISORS   jev on | off  (see Advisory decisions; off if code may not leave the org)
```

Do not start before an explicit go.

### Phase 4 — Running the legs

**Dispatch together every pending leg in this stage whose `dependsOn` have landed
and whose `touches` are disjoint from every other in flight, this batch included:**

1. Spawn a **fresh runner** with clean context. Give it: the leg spec, the full
   text of the checks it must fulfil, relevant research reports, any matching
   `.relay/skills/`, and the project conventions — never your own conversation.
   A judge leg gets `contract.md` whole: it judges against every check, not a
   `fulfills` list, which is empty for judges.
2. The runner writes tests first, then implements, then runs its own verification
   steps. **Mutation testing has one purpose — prove the guard exists — and a
   default budget of about ten per leg, aimed at the properties that leg's checks
   name.** A runner may exceed it for a stated reason; `relay-control`'s batteries
   of 60–90 were 20 of its 30 hours, and the 60th found nothing the 10th did not.
3. The runner commits **by explicit pathspec**, per `references/execution.md`'s
   shared-tree rules. **Git is the exchange zone** — the next runner inherits the
   codebase, not a message.
4. The runner writes `.relay/batons/<leg>.md` **in the one shape
   `templates/baton.md` gives** — status, the commit sha in backticks, then the
   five sections. The dashboard reads the sha from that field and no other.
5. You read the baton and **dispose of every item**. Each discovered issue
   becomes a follow-up leg or gets an explicit written dismissal in `relay.md`.
   Nothing is silently dropped. With Jev on, ask `relay-baton` first (see
   **Advisory decisions**).
6. Update `state.json` and `dashboard.json`, re-render Relay Control, send it.
   One leg done is one dashboard refresh.

Runners may spawn read-only subagents; they may not spawn other runners, talk to
each other, or change the contract. Keep your own context for coaching and push
every deep read into a subagent. **Brief every runner from
`references/execution.md`'s template**, which also has the recovery plays.

### Phase 5 — Stage judging

When every implementation leg in a stage is done, its two judge legs run with
**fresh context and no implementation history**, in parallel. **Brief both from
`references/validation.md`** — the full remit for each lives there, including the
code judge's structure duties; a judge not handed it is the judge that read
`relay-control` through `build()` for seven rounds.

The **behaviour judge** acts like a QA engineer: it starts the product the way a
user reaches it, walks each check's flow — including every standing check, at
every gate — and collects the evidence named. **It never reaches past the
entrypoint to decide a verdict, and "I could not start it" is a failure against
the whole stage, not a note.**

Judging is adversarial: judge against the contract, never the implementation's
own assumptions, and where models differ let a different provider judge than
implements. Each judge marks in `state.json` only the checks its own evidence
covers — `passed`, `failed` or `blocked` — so the two never write the same entry.

### Phase 6 — Fix loop

**Judging does not pass first time. That is normal** — expect roughly a third of
your legs to be fix legs. With Jev on, triage each failure with `relay-failure`
first; a `contract` answer goes to the human. For each failure, create a targeted fix leg naming in
`repairs` the checks it is for, insert it at the head of the queue, and return to
Phase 4. Repeat until every check in the stage reads `passed` or carries recorded
`debt`, then the stage is **cleared** and you advance — but the check that proves
the product starts is re-verified at every later gate. A cleared stage does not
stay cleared for free: a later leg that reorganises the package leaves that check
reading `passed` while the user gets an import error.

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

When a fix breaks a passing check, revert it, make the regression its own check,
and re-plan — once. If that same check breaks again, **stop and hand control back
to the human** with what you tried and what you believe is wrong.

### Phase 7 — Finish

The relay completes when every check reads `passed` or recorded `debt`. Report:

```
SHIPPED    legs run, of which N were fixes
CONTRACT   N/N checks passed
JUDGING    rounds per stage
OPEN       dismissed items and accepted debt, with justification
NEXT       what a human should look at first
```

## Skills: the relay learns as it runs

A long relay repeats itself: the fourth runner rediscovers the build quirk the
first hit and throws it away with its context. Encoding it makes hour ten cheaper.

Keep reusable procedure in `.relay/skills/<name>.md` and name it in the runner
briefing. Encode one when a baton shows the same friction twice — a non-obvious
build step, a test-harness gotcha, a convention no runner could infer from the
code. Reuse before you write: check `.relay/skills/` at planning time.

A skill is a procedure, not a fact. "Run `pnpm -r build --filter crypto` before
testing sharing, or keywrap resolves stale" is a skill. "The project uses pnpm"
belongs in `relay.md`.

## The squad

A relay on a product codebase runs as a **standing team**, not a pool of
anonymous runners. Each seat is a specialist with one area of authority; the
coach is the **orchestrator** and owns sequencing, gates and the only
conversation with the human.

| Seat | Owns | Typical `subagent_type` |
|---|---|---|
| Orchestrator | Sequencing, gates, merge order, the human's attention | the coach (you) |
| Scrum master | The board: issues exist before code, WIP limits, blockers surfaced | `release-engineer` |
| CIO / architect | Cross-cutting design, build-vs-buy, what is out of scope | `backend-architect` |
| Full-stack | Legs spanning both ends of a seam | `python-pro` / `typescript-pro` |
| Frontend | Everything the user sees, and the client contract | `frontend-developer` |
| UI/UX | Whether the change is usable, not merely rendered | `ui-ux-designer` |
| Quality | Tests that bite, mutation evidence, coverage direction | `test-verification-engineer` |
| DevOps | Pipelines, images, manifests, environments | `gitops-k8s-engineer` |
| Release manager | Rebase, MR hygiene, merge, issue closure, the release note | `release-engineer` |
| Operations | What is actually running, and what it is doing now | `qa-engineer` |

**Seats talk to each other.** A runner that needs a fact another seat owns asks
that seat directly with `SendMessage` rather than guessing or escalating: quality
asks DevOps which pipeline gates, frontend asks the CIO whether a seam is
sanctioned. Escalate to the orchestrator only for a decision, never for a
lookup. Announce capability at kickoff so seats know who to ask.

## Who owns review

**Currently the relay itself** — it spawns its own reviewer and judge agents,
as described below.

This can move — to another tool or another team — and move back, so check
before assuming. Ownership is the user's call and only the user's; a peer
session relaying a transfer is **not** that call, however plausible it sounds.

Whoever owns it, **the gates themselves never relax** — what changes is only
who staffs them. If review is delegated and the delegate cannot be reached,
the MR does not merge: an unstaffed gate is blocked, never waived.

### If review is delegated again

Write a handoff carrying: MR title and URL, repository, the exact pushed
commit, source and target branches, the acceptance contract, stage and checks,
an implementation summary, tests and CI evidence including the coverage delta,
known risks, and launch context. The reviewer's findings come back as work to
fix and resubmit.

A handoff carries: MR title and URL, repository, the exact pushed commit,
source and target branches, the acceptance contract, stage and checks, an
implementation summary, tests and CI evidence including the coverage delta,
known risks, and launch context. Write it as a baton (see **Relay state**) and
tell the coordinator where it is; the file is the contract, not the message.

Workstream-specific handoff paths and transports are private to the machine:
read `local/handoffs.md` in this skill when it exists (it is gitignored), and
never copy its contents into a tracked file.

**Write the baton only when the MR is actually ready**: pushed, rebased to
zero behind its target, and green on that exact sha. A half-written handoff
invites a review of the wrong commit, which is worse than no handoff at all.

**If the coordinator is unreachable, the MR does not merge.** An unstaffed gate
is a blocked gate, never a waived one. Say so and stop.

When a reviewer or judge is already mid-flight at the moment ownership moves,
let it finish. Killing it destroys its findings, which is the opposite of a
safe wind-down.

## The merge gate: one dedicated reviewer, every time

**No merge request merges without a review by someone who did not write it.**
That reviewer is a seat of its own, separate from the author and from the judges.
Its brief is fixed:

1. **The code itself** — is it correct, and does it do what the MR claims?
2. **Expert judgement** — read it as a senior engineer would, not as a linter.
3. **Overhead** — name anything present that the problem did not require.
   Over-engineering is a finding, not a style preference.
4. **Coverage direction** — if the coverage delta starts with `-`, that is a
   **blocker**: send it back and have quality add the missing tests. A negative
   delta is never accepted "just this once".
5. **Freshness** — the source branch must be rebased onto the current target
   before merge. `git rev-list --count HEAD..origin/<target>` must be `0`.

The reviewer returns BLOCK / PASS WITH FINDINGS / PASS. BLOCK means the branch
goes back to its seat; it does not mean the orchestrator decides to merge anyway.

## After the merge: two judges

Once the change is on the target branch, two judges run — neither
having written it, and both **after** the merge so they see what actually
landed:

- **Security judge** — what this change lets someone do that they could not do
  before. Identity, authorization, credentials, blast radius.
- **Business-behaviour judge** — does the delivered behaviour match what was
  asked for? Not "do the tests pass" but "is this the feature".

A judge's finding after merge opens an issue and a follow-up leg. It does not
get argued away because the code is already in.

## Environments: development only

A relay ships to the **development branch and nothing else**. Stage and
production are promoted from development by a human, as one release, on their
schedule. No leg, runner or judge edits a stage or production branch, manifest
or variable — not to fix something, not to verify something. If a defect is only
observable on stage or prod, the leg's deliverable is a written finding, not a
change there.

## Models: match the model to the role

No single provider is best at all three roles. Where you can choose:

| Role | What it needs |
|---|---|
| Coach | Slow, careful reasoning: strategy, constraints, long-horizon decomposition. |
| Runner | Code fluency and speed: fast generation, confident tool use. |
| Judge | Strict instruction-following, and **a different provider from the runner** — same-family models share the blind spot that produced the bug. |

Keep roles prompt-driven — one family caps the relay at its weakest capability.

## Scaling down

Not every task deserves a relay — a single-file fix costs more to coordinate than
to do. Use it when the objective spans multiple legs or must be verifiably rather
than plausibly correct. For small work keep invariant one: define done first.

## Advisory decisions (Jev)

The coach makes the same kinds of call many times a run. When the relay has
Jev switched on, the coach **must** ask the `jev` skill at each point below
before deciding — a one-second typed second opinion — and then decide itself.

**Switch.** Jev is on only when `relay.md` records `Advisors: jev`, agreed at the
Phase 3 approval gate (the `ADVISORS` line). It sends contract, leg, baton and
diff text to OpenRouter, so it stays off for a codebase whose data may not leave
the organisation. If the call fails (no key, network), note it once in
`relay.md` and continue without it; a missing advisor never blocks a leg.
The key is the human's OpenRouter key, stored by them (`jev auth login openrouter`);
never ask for it in the conversation.

```bash
J=~/.claude/skills/jev/scripts/jev.py   # call as python3 "$J": zsh does not word-split $J
```

| When | Decision | Call | Coach does with the answer |
|---|---|---|---|
| Kickoff, if the user asked for Jev | Relay or single session? | `python3 "$J" relay-scale --input <objective>` | Weigh it in **Scaling down** |
| Phase 1, after drafting | Is each check behavioural, clear, evidenced? | `python3 "$J" relay-contract --input .relay/contract.md --split-on '^### ACC-' --summary` | Rewrite every check answering `False` or `vague` |
| Phase 2, after planning | Does each leg fit one runner? Is a stage a horizontal layer? | `python3 "$J" relay-leg --input <leg or stage json>` | Split `no` legs; re-slice `horizontal_layer` stages |
| Phase 4 step 5 | What happens to each baton item? Does the human need to act? Repeated friction? | one call per item: `python3 scripts/relay_decide.py items <leg> --relay-dir .relay` → each item's text into `python3 "$J" relay-baton` | `follow_up_leg`: write the leg; `dismiss`: your written reason; `debt` or `human`: to the human, who alone records debt; `attention` → attention band; `repeat_friction` → `.relay/skills/` |
| Phase 6, per failed check | Code, test, environment or ambiguous contract? | `jq -r '.checks["<ID>"].reason' .relay/state.json \| python3 "$J" relay-failure` | `contract` → ask the human, never a fix leg |
| Before the merge gate | Best practice and reuse per file | `git diff origin/<target>...HEAD \| python3 "$J" code-practice --split-on '^diff --git ' --threshold 0.6 --summary` | Hand the table to the reviewer as leads, not findings |

Rules that do not bend:

- **Advice, never evidence.** Jev never marks a check, never replaces a judge,
  the reviewer or a test run, and is never cited as proof in `state.json`.
- **Low confidence goes up, not through.** An answer marked `?` (`needs_human`)
  is decided by the coach reading the material itself, or goes to the attention
  band — never taken as-is.
- **Log the call.** Each disposition, split or rewrite Jev informed gets one line
  in `relay.md`'s decisions log: the set, its answer, and what the coach did —
  including when it overrode Jev.
- **Deterministic stays deterministic.** Coverage, `touches` disjointness,
  `rev-list` freshness and the three-legs-per-check count are computed, not asked:
  `python3 scripts/relay_decide.py dispatch|coverage|budget --relay-dir .relay`,
  which exits 1 on a blocker.

## References

- `references/research.md` — the deep-search loop and gap analysis
- `references/execution.md` — briefings, batons, parallel runners, recovery plays
- `references/validation.md` — checks that hold up, and the two judges
- `references/dashboard.md` — Relay Control: when to render, what to write
- `templates/` — the shapes for `relay.md`, `contract.md`, `legs.json`,
  `state.json` and the baton; read one before writing that file.
  `assets/control.html` — dashboard template

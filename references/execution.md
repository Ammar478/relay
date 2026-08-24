# Running legs: runners, batons, recovery

## Runner briefing

Every runner starts fresh. It knows nothing about the relay except what the
briefing contains, and that is deliberate — it is why the tenth leg is built as
carefully as the first. Give it exactly this, and nothing from your own
conversation:

```
LEG          sharing-invite-flow
STAGE        sharing
GOAL         one paragraph: what exists after you are done

CHECKS       you must make these true (full text, not just IDs):
             ACC-SHARE-002 — ...
             ACC-SHARE-003 — ...

CONTEXT      files and modules that matter, and why
             relevant findings from .relay/research/
             skills that apply: .relay/skills/monorepo-build-order.md
             conventions this repo follows

PROCEDURE    1. write the tests for the checks first
             2. implement until they pass
             3. run: <exact verification commands>
             4. commit with message "<leg>: <summary>"
             5. write .relay/batons/sharing-invite-flow.md

BOUNDARIES   do not touch: <paths, configs, schemas outside scope>
             do not change the acceptance contract
             do not spawn other runners
             ports/resources you may use: <range>
             if you cannot finish inside these boundaries, stop and report
```

Runners may spawn read-only subagents to search the codebase or read docs.
Runners may not write outside their leg, negotiate with other runners, or
escalate their own permissions.

## The baton

Five fields. The runner writes it; you read it; every item gets disposed of.

```markdown
# Baton — sharing-invite-flow
STATUS: success | partial | failed

## Implemented
- what actually landed, in specifics

## Left undone
- anything skipped, stubbed, or deferred — say why

## Commands run
- `pnpm test sharing` → exit 0
- `pnpm typecheck` → exit 1 (pre-existing, see issues)

## Issues discovered
- things noticed that are outside this leg's scope

## Procedure followed
- yes/no per step; where it diverged from the spec, and why
- anything a future runner would waste time rediscovering
```

**Disposal rule:** for every item under "left undone" and "issues discovered",
you either create a follow-up leg or write an explicit dismissal in `relay.md`
with a real justification. Silent dropping is how a relay finishes with green
gates and a broken product.

Anything under "procedure followed" that a future runner would hit again becomes
a file in `.relay/skills/`. Encode on the second occurrence, not the first.

## Why one runner at a time

Serial looks slower and is not. On a long run, two runners making independently
reasonable but conflicting architectural choices does not stay local — every
downstream leg inherits the inconsistency, and the cost surfaces at integration
when it is most expensive to fix. Parallelism buys hours. Coherence buys the run.

The exchange zone is git, not a message channel. Each runner inherits the
codebase at the last commit. There is no runner-to-runner channel, because peer
messaging creates divergent local views of state that nothing reconciles.

What does parallelise, because it only reads:

- codebase search and flow tracing inside a leg
- documentation and API research
- the code judge's per-leg review subagents
- the two judges at a stage gate

## Recovery plays

| Symptom | Play |
|---|---|
| Runner frozen, no tool calls | Stop it. Read its last output and the partial diff. Re-brief a fresh runner with what was learned. |
| Runner grinding on one problem | Cut it. Mark the leg partial, capture the baton, and either narrow the leg or escalate to the human. |
| Leg keeps failing its checks | After three rounds, stop. The leg spec or the check is wrong, not the code. Re-scope. |
| Fix breaks a passing check | Regression. Revert, make the regression itself a check, and re-plan the fix. |
| Human changes direction mid-run | Pause. Update `relay.md` and the contract first, then re-scope remaining legs. Never let the code and the contract drift apart. |
| Blocked on something external | Halt the relay and hand back to the human with the exact blocker. Do not invent a workaround that violates the contract. |

## Context discipline

Your context is for coaching: the structural map of the repo, baton summaries,
sequencing decisions, and the conversation with the human. Push every deep read —
tracing a call chain, enumerating edge cases, reading a long file — into a
subagent that returns a compressed report.

When you notice yourself holding details that live in a file, drop them and
re-read the file. That is what the file is for.

# Relay: <name>

**Started:** <date>
**Status:** planning | running | judging | paused | blocked | complete

## Objective

One paragraph. What exists in the world after this relay that does not now.

## Constraints

- stack, versions, and anything that cannot change
- deadlines, budgets, environments
- conventions this codebase already follows

## Non-goals

Explicitly out of scope. This list prevents scope creep more than any other
section, so write it even when it feels obvious.

## Harness

Filled in by the capability pre-flight in `references/harness.md`, before Phase 0.
A briefing written against a capability this harness does not have produces either
a hallucinated tool call or silence.

```
Harness: <name and version>
Fresh agent: <the primitive, or "fresh top-level session">
Parallel: yes, <n> at once | no
Nested dispatch: no
Model per role: <mechanism, or "one model for every role">
Conventions: AGENTS.md | CLAUDE.md | both | none found
Dashboard: HTML render | live TUI in its own window
Advisors: <none> | jev
```

## Environment

Answered in Phase 0b, before the contract. A "no" here is stage-1 work, not a
reason to carry on and hope — `references/environment.md`.

| Question | Answer |
|---|---|
| One command starts the whole stack | yes, `relay_services.py up` \| writing it in S1 |
| Logs land on disk | <paths, or the leg that makes it true> |
| Web surface driven by | <browser driver, or none needed> |
| Terminal surface driven by | <terminal driver, or none needed> |
| API surface driven by | <curl and the test runner> |
| Footprint of a full stack | <~N GB of this machine's M GB> |

## Concurrency

The ceiling dispatch honours. Disjoint files are not disjoint resources.

```
Max parallel runners: 3
Max parallel UI judges: 2
Max parallel API judges: 4
Port blocks handed out: 8100-8109, 8110-8119, 8120-8129
Data namespaces: relay_<stage>_<leg>
Footprint budget: ~4 GB
```

## Governance

What is actually true here, which beats `references/governance.md` where they
differ, because this is the file a runner reads.

- **Ships to:** <the development branch, and nothing else>
- **Review owner:** the relay itself | <team or tool>
- **Merge gate:** one reviewer who did not write it; negative coverage delta blocks
- **Never touched:** <stage and production branches, manifests, variables>

## Decisions log

Append-only. Every architectural choice made during the relay, with the reason.
Decisions that shape the whole build belong in `architecture.md`; this is the log
of what was decided *as the relay ran*, including the calls an advisor informed and
the ones where the coach overrode it. Runners read this to stay consistent with
earlier legs.

| Date | Decision | Reason |
|---|---|---|
| | | |

## Dismissed items

Handoff items deliberately not turned into legs. Each needs a real justification,
not "out of scope".

| Item | From leg | Justification |
|---|---|---|
| | | |

## Accepted debt

**Recorded by the human only.** A coach may propose debt and a judge may say a
check failed; neither may write a row here.

| Check | Why it is debt rather than a fix | Agreed by | Date |
|---|---|---|---|
| | | | |

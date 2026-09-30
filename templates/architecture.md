# Architecture: <relay name>

**Authoritative.** The contract is derived from this file, and every runner is
briefed with it. When this file and the code disagree, this file is the one that
gets fixed first — then the code follows.

Written in Phase 1a, before the contract and before any leg. Short and decided:
a runner reads it to avoid re-deriving a choice, not to learn the domain.

## Shape

<Two or three paragraphs, or a diagram in text. What the pieces are, which
process each runs in, and what crosses the boundary between them.>

## Seams

Every place two parts meet, and who owns each side. A leg works on one side of a
seam; a leg that works on both is a full-stack leg and says so.

| Seam | Owner, this side | Owner, that side | What crosses it |
|---|---|---|---|
| | | | |

## State

Where data lives, what is authoritative, and what is a cache or a projection.
Name the store, not the technology family: "PostgreSQL on 127.0.0.1:55432, db
`app`" rather than "a relational database".

| Data | Lives in | Authoritative? | Lifetime |
|---|---|---|---|
| | | | |

## Edges

One row for every edge the system map named — every consumer of anything this
relay changes, and every external system in the authentication or data path.
These are the rows that become cross-area checks.

| Edge | Direction | Contract | Observed or assumed? |
|---|---|---|---|
| | | | |

**"Assumed" is a risk, not a fact.** An external contract — an SSO provider, a
token issuer, a gateway — must be observed against a real token or response
before a check depends on it.

## Boundaries a leg may not cross

The things no leg changes without coming back to the human first: a schema, a
public response shape, an authentication path, a deployment manifest, a file
owned by someone outside this relay.

- <boundary, and what to do instead>

## Decisions, and what was rejected

The choices that shape everything downstream, each with the reason and the
alternative that lost. A runner that disagrees with a decision reads the reason
here instead of quietly making the other choice in one leg.

| Decision | Reason | Rejected alternative |
|---|---|---|
| | | |

## Known unknowns

What this architecture does not yet answer, and which leg or which question to
the human settles it. An unknown named here is cheap; the same unknown found at
leg nine is a re-plan.

- <unknown, and how it gets settled>

# Judging a backend cutover

## Row parity is not behavioural equivalence

Five pre-flip gates were green — schema at head, row counts, parity 7/7, image contains the
resolver, all PATs resolve — and the flip would still have killed every chat request in dev.

`catalog.agents.provider` is written lowercased and read straight back as the domain field
`agentType`. DynamoDB stores `"Snowflake"` verbatim, so the round trip is identity there and only
PostgreSQL diverges. `dynamic_chat_service.py:290` compares `agent_type != "Snowflake"` exactly.

**The parity check passed because it lowercased provider on both sides before comparing** — the
exact transformation that breaks the runtime. Any canonicalisation inside a parity check is a
blind spot shaped like a bug.

So: before judging a cutover cleared, instantiate **both** repositories side by side inside the
running pod and diff full items field by field, and exercise at least one real consumer path
against the new backend. Ask the behaviour judge for "a concrete reason the flip breaks that the
gates would NOT catch" — that question is what found this.

## `/health` green proves nothing about a cutover

Post-flip the service still reports healthy, the registry still loads all 7 agents, and every row
is byte-perfect. The failure is entirely in the read-path contract. Health checks, row counts and
sync status are all insensitive to it.

## Mutation-test the guard, not the feature

Four of five mutations were killed by real behavioural tests. The fifth — replacing the whole body
of `_owner_scoped_filter` with `raise RuntimeError` — left the suite **byte-identical** to
baseline: `5 failed, 1626 passed`. The guard is never called by any test.

Two files named `test_chat_session_ownership.py` and `test_chat_thread_ownership.py`, 1,091 lines
between them, exercise the *chat* path through `session_client` — not the Mongo-backed service
behind the REST session/thread endpoints. **A test file named after a guard is not evidence it
covers it.**

Use the unconditional-`raise` mutation first. If the suite is unchanged, the function is not
reached at all and no weaker mutation would have been caught either.

## Give the runner the failing inputs, forbid hardcoding them

The mask predicate accepted `abcd****wxyz` as a real credential and rotated it over a working PAT
in Secrets Manager, with no warning logged. Listing the failing shapes in the briefing is
necessary; the fix must generalise beyond them, so state explicitly that the examples are symptoms
and must not appear in the implementation.

## Parallel fix legs need one worktree each

Three fixes ran concurrently only because each got its own detached worktree with a symlinked
`.venv`. Sharing a tree makes every baseline count untrustworthy — collected-vs-executed is the
tell (see `coaching-the-relay.md`).

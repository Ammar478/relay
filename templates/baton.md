# Baton — <leg id>

**Status**: success | partial | failed
**Commit**: `<sha>`

Both header fields are read by machine and both are fussy about punctuation. Keep
the colon **outside** the bold and the sha **inside** backticks, exactly as above.
Written `**Status:**` the status word is not found at all, and the dashboard then
shows the leg as Success whatever actually happened; written without backticks the
commit is never attributed to the leg.

## Implemented

- Specifics, not summaries. What a reviewer would see in the diff.

## Left undone

- Anything skipped, stubbed, or deferred, and why. Write "nothing" if nothing.

## Commands run

| Command | Exit |
|---|---|
| `pnpm test auth` | 0 |
| `pnpm typecheck` | 1 — pre-existing failure in `src/legacy/`, see Issues |

## Issues discovered

- Problems noticed outside this leg's scope. The coach must dispose of every one
  of these — either a follow-up leg or a written dismissal in relay.md.

## Procedure followed

- Yes / no, per step of the assigned procedure. Say plainly where you diverged
  from the leg spec and why.
- Any architectural decision made here that later legs should follow.
- Anything a future runner would waste time rediscovering — this is what becomes
  a skill in `.relay/skills/`.

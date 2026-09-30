# Baton — <leg id>  (legacy prose form)

**`templates/handoff.json` is the current shape.** A runner writes
`.relay/batons/<leg>.json` and validates it:

```bash
python3 scripts/relay_handoff.py validate <leg> --relay-dir .relay
```

This file remains because the dashboard still reads a prose baton when it finds
one, so a relay part-way through an older run keeps rendering. Do not write a new
one, and do not invent a variant of either shape.

Why it was replaced: both header fields below are parsed by regex and both are
fussy about punctuation. Written `**Status:**` rather than `**Status**:` the status
is not found at all, and the dashboard then shows the leg as **Success** whatever
actually happened; written without backticks the commit is never attributed to the
leg, and one run shipped a log with zero attributed commits. A schema that refuses
a malformed handoff at the moment it is written cannot fail that way.

**Status**: success | partial | failed
**Commit**: `<sha>`

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

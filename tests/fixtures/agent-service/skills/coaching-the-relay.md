# Coaching this relay

## Marking a leg `running` is not the same as launching it

A leg was marked `"status": "running"` in `legs.json` during baton disposal and no runner was ever
dispatched for it. Every status report afterwards read the state file and reported a runner that did
not exist. It cost about ninety minutes of apparent progress that was nothing at all.

**The state file records intent. It is not evidence.** Before reporting that a leg is in flight,
confirm it against something outside `.relay/`:

```
git log --oneline -1 <branch>                # has the runner committed?
ls .relay/batons/                            # has a baton landed?
ls -la <tasks-dir>/<agentId>.output          # follow the symlink, check the target mtime
```

A transcript whose last write is more than ~20 minutes old, with no commit and no baton, is stuck or
absent — not working.

## Dispatch and mark in the same action

Disposal and dispatch drifted apart because they were separate steps in separate turns. Mark a leg
`running` only in the same turn you launch its runner, and record the agent id next to it so the
mapping from leg to agent is checkable later rather than held in memory.

## One runner on the track, readers in parallel

Anything that mutates the tree runs strictly serially. Judges, research and review are read-only and
fan out. When a mutating runner holds the track, pin read-only work to a **detached worktree at a
fixed commit** rather than the live branch:

```
git worktree add --detach /private/tmp/judge-sN <commit>
ln -sfn <repo>/services/agent-service/.venv /private/tmp/judge-sN/services/agent-service/.venv
```

An agent earlier in this project reported a file "shrank while I was reading it" because it was
analysing a tree a runner was actively rewriting. Findings from a moving target cannot be acted on.

## Judge the commit the stage ended at, not HEAD

A judge pinned to HEAD will report defects that a later leg already fixed, and you will waste a round
deciding whether the finding is real. The S1 code judge correctly flagged the credential guard as
inert — against the S1 tip. The fix had landed two commits later. Pin the judge, and when it reports,
check the finding against the current branch before opening a fix leg.

## Parallel legs share one worktree — baselines taken during a parallel run are not trustworthy

A runner recorded a first baseline of `1372 passed`, then every later run including a clean-tree stash
read `1422`, with **1427 collected both times**. Fifty tests did not execute: a parallel runner was
mid-write in the same worktree.

Collected-versus-executed is the tell. When two legs run concurrently:

- take the baseline **before** dispatching either, and hand it to both in the briefing
- treat a mid-run count as indicative, never as evidence for a check
- if a count must be authoritative, re-run it once the track is clear

File ownership prevents conflicting *writes*; it does not stop one runner's half-written file from
perturbing another's test run.

## Verify a research claim against the consumer, not just the producer

Research reported that a dropped repository column would send `engineering_ai`'s jobs to the wrong RQ
queue after cutover. It was inferred from the repository's `_select()` omission alone. The consumer,
`src/config/agents.py`, rebuilds every agent at boot from a hand-listed key set that drops the field
anyway — so the behaviour is identical on both backends and predates the migration entirely.

A claim about impact needs the whole path: producer, transport, and consumer. I propagated that one
into three briefings before a runner checked the other end.

## A stalled runner is usually a briefing that invited exploration

`agent-write-tests` stalled having produced one line: "I'll start by reading the relay context docs
and the existing test files." Its briefing named several documents and left the reading order open,
so it began an open-ended survey and the watchdog killed it at ten minutes.

The retry named exactly three files, in order, with an explicit instruction to start after them and to
prioritise committing correct work over full coverage if time ran short. Long briefings are fine;
briefings without a first action are not.

Before retrying a stalled leg, check the tree: a runner killed mid-write leaves partial edits, one
killed before starting leaves nothing. This one left nothing, so a fresh dispatch was cleaner than
resuming a near-empty transcript.

## tests/pg share one database — concurrent runs corrupt each other

`tests/pg` defaults to database `aihub`, and its fixtures `DELETE FROM catalog.agents / iam.users /
iam.groups` on every test. Two agents running pg tests at once therefore delete each other's seeds:
one judge saw its counts swing by more than 20 between identical runs before diagnosing it.

When more than one agent may touch `tests/pg`, give each an isolated database on the same server
(`CREATE DATABASE aihub_<role>`, schema to alembic head) and pass it explicitly. File ownership does
not help here — the collision is in shared external state, not the tree.

## Mutations that are not restored poison the next agent

A behaviour judge arrived to find the pinned worktree carrying two uncommitted live mutations — a
deleted guard and a reverted dependency — left by earlier agents whose mutation runs never restored.
Its first two suite runs were against that tampered tree.

Require every runner to prove restoration with `git diff --stat` before committing, and check the tree
yourself when a leg reports mutation testing. Pinning judges to their own worktree contained the blast
radius here: the main tree was clean and both guards intact.

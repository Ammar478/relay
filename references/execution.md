# Running legs: runners, batons, recovery

## Runner briefing

Every runner starts fresh, knowing nothing about the relay except what the
briefing contains. That is deliberate — it is why the tenth leg is built as
carefully as the first. Give it exactly this, and nothing from your conversation:

```
LEG          sharing-invite-flow
STAGE        sharing
GOAL         one paragraph: what exists after you are done

CHECKS       you must make these true (full text, not just IDs):
             ACC-SHARE-002 — ...
             ACC-SHARE-003 — ...

CONTEXT      files and modules that matter, and why
             findings from .relay/research/ and skills from .relay/skills/
             conventions this repo follows

REFERENCES   read these first, by absolute path: references/execution.md for
             batons and the shared-tree rules - or, for a judge leg,
             references/validation.md, which is where its whole remit lives

PROCEDURE    1. write the tests for the checks first
             2. implement until they pass
             3. mutate to prove each guard exists — about ten, aimed at the
                properties these checks name, each restored from your own
                backup. Exceed ten only with a reason written in the baton
             4. run: <exact verification commands>
             5. commit by explicit pathspec, message "<leg>: <summary>"
             6. write .relay/batons/sharing-invite-flow.md in the shape
                templates/baton.md gives, its commit sha included

BOUNDARIES   do not touch: <paths, configs, schemas outside scope>
             do not change the acceptance contract
             do not spawn other runners
             ports/resources you may use: <range>
             stop and report if you cannot finish inside these boundaries
```

Runners may spawn read-only subagents to search the codebase or read docs; they
may not write outside their leg, talk to other runners, or escalate permissions.

## The baton

**`templates/baton.md` is the shape.** Copy it; do not invent a variant. The
runner writes it, you read it, every item gets disposed of.

Both header fields are read by machine. `**Commit:**` wants its sha **in
backticks**; without them the dashboard shows no commit against the leg, and
`relay-control` has already shipped a run log with zero attributed commits.
`**Status:**` fails worse than silently — a word the reader does not know renders
as **Success**, on a failed leg too. The five sections below — implemented, left
undone, commands run with exit codes, issues discovered, procedure followed — are
what the disposal rule acts on, so a baton missing one has an item nobody must
dispose of.

**Disposal rule:** every item under "left undone" and "issues discovered" gets a
follow-up leg or an explicit dismissal in `relay.md` with a real justification;
silent dropping is how a relay finishes with green gates and a broken product.
Anything under "procedure followed" that a future runner would hit again becomes
a file in `.relay/skills/` — encode on the second occurrence, not the first.

## Parallel runners in one tree

Serial protects one thing: two runners making conflicting *architectural* choices
does not stay local — every downstream leg inherits it. Scope it there: legs with
disjoint file sets run in parallel from the first leg. Three `relay-control`
runners in parallel wrote a byte-identical helper — convergence, not divergence.

The exchange zone is git, not a message channel: each runner inherits the codebase
at the last commit. Peer messaging would create local views nothing reconciles.

Runners in one tree share an index, a working tree and a scratchpad:

- **Never `git add` without a pathspec, never a plain `git commit`.** Foreign
  files appeared staged mid-leg and `HEAD` moved under a running runner.
- **Never `git checkout --`, `git stash` or `git reset`.** Undoing a mutation
  that way would have destroyed another runner's uncommitted work.
- **Restore a mutated file from your own backup copy**, never from git.
- **Prefix every scratch file with the leg id.** Two runners overwrote each
  other's `mutate.py` and a mutation run had to be redone.
- **A leg blocked by another's uncommitted work stops and reports.** One refused
  to commit when two legs' changes were entangled in three files; a pathspec
  cannot split a file.

What parallelises regardless, because it only reads: codebase search and flow
tracing, documentation research, per-leg review subagents, the judges at a gate.

## Recovery plays

| Symptom | Play |
|---|---|
| Runner frozen, no tool calls | Time the suite before calling it a hang — a slow machine misread as one cost a reset of 642 lines. Then stop it, read the partial diff, and re-brief a fresh runner. |
| Runner grinding on one problem | Cut it. Mark the leg partial, capture the baton, and either narrow the leg or escalate to the human. |
| Leg keeps failing its checks | After three rounds, stop. The leg spec or the check is wrong, not the code. Re-scope. |
| Fix breaks a passing check | Regression. Revert, make the regression itself a check, and re-plan the fix — once. If that check breaks a second time, stop and hand back to the human. |
| A mutation battery comes back all-killed | Suspect a driver killed mid-run: the restart reads the mutant as the original and everything looks guarded. `git status` the mutation copy, restore, verify the baseline, re-run. |
| Human changes direction mid-run | Pause. Update `relay.md` and the contract first, then re-scope remaining legs. Never let the code and the contract drift apart. |
| Blocked on something external | Halt the relay and hand back to the human with the exact blocker. Do not invent a workaround that violates the contract. |

## Context discipline

Your context is for coaching: the repo's structural map, baton summaries,
sequencing, the conversation with the human. Push every deep read into a subagent
that returns a compressed report; re-read a file rather than hold what is in it.

# Running legs: runners, handoffs, recovery

## Runner briefing

Every runner starts fresh, knowing nothing about the relay except what the briefing
contains. That is deliberate — it is why the tenth leg is built as carefully as the
first. Give it exactly this, and nothing from your conversation:

```
LEG          sharing-invite-flow
STAGE        sharing
SEAT         full-stack        (the seat's definition, per references/squad.md)
GOAL         one paragraph: what exists after you are done

CHECKS       you must make these true (full text, not just IDs):
             ACC-SHARE-002 — ...
             ACC-SHARE-003 — ...

CONTEXT      .relay/architecture.md — the authoritative design; do not re-derive it
             files and modules that matter, and why
             findings from .relay/research/, procedures from .relay/skills/,
             facts from .relay/library/
             conventions this repo follows (AGENTS.md / CLAUDE.md / lint config)

ISOLATION    ports: 8100-8109
             data namespace: relay_s2_sharing      (schema, prefix or directory)
             credentials: the seeded user for this slot, never the shared one
             log file: /tmp/relay-sharing-invite-flow.log
             scratch prefix: sharing-invite-flow-  (every temp file starts with it)

REFERENCES   read these first, by absolute path: references/execution.md for the
             handoff and the shared-tree rules — or, for a judge leg,
             references/validation.md, which is where its whole remit lives

PROCEDURE    1. bring the stack up if you need it:
                python3 scripts/relay_services.py up --relay-dir .relay
             2. write the tests for the checks first
             3. implement until they pass
             4. mutate to prove each guard exists — about ten, aimed at the
                properties these checks name, each restored from your own
                backup. Exceed ten only with a reason written in the handoff
             5. run: <exact verification commands>
             6. commit by explicit pathspec, message "<leg>: <summary>"
             7. write .relay/batons/sharing-invite-flow.json in the shape
                templates/handoff.json gives, then validate it:
                python3 scripts/relay_handoff.py validate sharing-invite-flow

BOUNDARIES   do not touch: <paths, configs, schemas outside scope>
             do not change the acceptance contract or architecture.md
             do not dispatch other agents; ask the coach instead
             stay inside your isolation context
             stop and report if you cannot finish inside these boundaries
```

**A runner cannot dispatch other agents.** Assume the harness forbids it
(`references/harness.md`), so a leg that needs a deep read across the codebase gets
that read from the coach as its own read-only leg, before the runner starts. A
briefing that tells a runner to "spawn a subagent to trace the flow" produces either
a hallucinated tool call or the runner doing it inline and burning the context the
leg needed.

## The handoff

**`templates/handoff.json` is the shape.** The runner writes
`.relay/batons/<leg>.json`, validates it, and only then reports done:

```bash
python3 scripts/relay_handoff.py validate <leg> --relay-dir .relay
```

It replaced a Markdown baton whose header the dashboard parsed by regex, and the
prose version failed silently in both directions: written `**Status:**` instead of
`**Status**:` the status was not found at all and the dashboard showed the leg as
**Success** whatever had happened; written without backticks the commit went
unattributed, and one run shipped a log with zero attributed commits. A schema that
refuses a malformed handoff at the moment it is written cannot do that.

What it carries, and why each field is there:

| Field | Why the coach needs it |
|---|---|
| `status` | `success` / `partial` / `failed` / `blocked`, from a fixed set so it cannot be misread |
| `commit` | the sha this leg produced, so the dashboard can attribute the work |
| `sessionId` | the runner's own session, so a supervisor can open its transcript |
| `returnToCoach` | whether this needs a decision before the next leg starts |
| `salientSummary` | the one paragraph you read first |
| `implemented` | specifics, not summaries: what a reviewer would see in the diff |
| `leftUndone` | anything skipped, stubbed or deferred, and why — a **disposal** section |
| `verification.commandsRun` | each command with its **exit code** and what was observed |
| `verification.interactiveChecks` | what was driven by hand, on which surface, with the evidence path |
| `tests.added`, `tests.mutations` | the guards, and the mutation count with survivors |
| `discoveredIssues` | each with a **severity** — a **disposal** section |
| `procedureFollowed` | per step: followed or not, and where it diverged |
| `decisions` | architectural choices later legs must follow → `relay.md` |
| `repeatFriction` | friction a second runner would hit → `.relay/skills/` |

**Disposal rule:** every item under `leftUndone` and `discoveredIssues` gets a
follow-up leg or an explicit dismissal in `relay.md` with a real justification;
silent dropping is how a relay finishes with green gates and a broken product. Take
`blocking` severity first.

```bash
python3 scripts/relay_handoff.py items <leg> --relay-dir .relay
```

A fact a later judge will need — how this surface is verified, which checker lies
about a disabled control — goes to `.relay/library/`, not into a skill. Procedures go
to `.relay/skills/`, on the second occurrence rather than the first.

## Parallel runners in one tree

Serial protects one thing: two runners making conflicting *architectural* choices
does not stay local — every downstream leg inherits it. Scope it there: legs with
disjoint file sets run in parallel from the first leg. Three `relay-control` runners
in parallel wrote a byte-identical helper — convergence, not divergence.

The exchange zone is git, not a message channel: each runner inherits the codebase at
the last commit. Peer messaging would create local views nothing reconciles.

Runners in one tree share an index, a working tree and a scratchpad:

- **Never `git add` without a pathspec, never a plain `git commit`.** Foreign files
  appeared staged mid-leg and `HEAD` moved under a running runner.
- **Never `git checkout --`, `git stash` or `git reset`.** Undoing a mutation that way
  would have destroyed another runner's uncommitted work.
- **A separate worktree does not make this safe.** Worktrees isolate the working tree
  and the index; they share the repository — including **one stash stack**. Two legs in
  their own worktrees of the same repo both stashed, and one `pop` took the other's
  entry: its work vanished from disk and was rebuilt from edit history. Set work aside
  with a throwaway WIP commit on your own branch, or a file copy. If a stash is truly
  unavoidable, `git stash push -u -m "<leg-id>"`, capture the sha at once from
  `git stash list --format='%H %gs'`, restore with `git stash apply <sha>` — never
  `pop` — and drop that entry by finding it by tag.
- **Restore a mutated file from your own backup copy**, never from git.
- **Prefix every scratch file with the leg id.** Two runners overwrote each other's
  `mutate.py` and a mutation run had to be redone.
- **A leg blocked by another's uncommitted work stops and reports.** One refused to
  commit when two legs' changes were entangled in three files; a pathspec cannot split
  a file.

And disjoint files are not the whole of disjoint. Two legs that both start the stack
share a port, a database and a log file while their `touches` stay perfectly disjoint,
so every dispatch carries an isolation context and the plan carries a ceiling —
`references/environment.md`.

What parallelises regardless, because it only reads: codebase search and flow tracing,
documentation research, the per-leg reviews behind a code judge, and the behaviour
judge's shards at a gate.

## Recovery plays

| Symptom | Play |
|---|---|
| Runner frozen, no tool calls | Time the suite before calling it a hang — a slow machine misread as one cost a reset of 642 lines. Then stop it, read the partial diff, and re-brief a fresh runner. |
| Runner grinding on one problem | Cut it. Mark the leg partial, capture the handoff, and either narrow the leg or escalate to the human. |
| Leg keeps failing its checks | At the third leg against that check (the leg that claimed it plus its `repairs` legs — SKILL.md's budget), stop. The leg spec or the check is wrong, not the code. Re-scope. |
| Fix breaks a passing check | Regression. Revert, make the regression itself a check, and re-plan the fix — once. If that check breaks a second time, stop and hand back to the human. |
| A mutation battery comes back all-killed | Suspect a driver killed mid-run: the restart reads the mutant as the original and everything looks guarded. `git status` the mutation copy, restore, verify the baseline, re-run. |
| Judge cannot start the product | Not a note and not the judge's problem to work around. `relay_services.py status` first; a service that will not come healthy is a fix leg, and every check that judge owns reads `blocked` until it does. |
| A runner reports a hallucinated tool | It was briefed with a capability this harness does not have. Re-run the pre-flight in `references/harness.md`, fix the briefing template, and re-dispatch. |
| Human changes direction mid-run | Pause. Set `run.state` to `paused`, update `architecture.md` and the contract first, then re-scope remaining legs. Never let the code and the contract drift apart. |
| Blocked on something external | Halt the relay and hand back to the human with the exact blocker. Do not invent a workaround that violates the contract. |

## Context discipline

Your context is for coaching: the repo's structural map, handoff summaries,
sequencing, the conversation with the human. Push every deep read into a read-only
leg that returns a compressed report; re-read a file rather than hold what is in it.

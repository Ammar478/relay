# Governance: review ownership, the merge gate, environments

The gates in this file are the ones that sit **outside** a stage. A stage gate asks
whether the work is correct; these ask whether it is allowed to land, and who said
so. They are organisational rules, so they are the part most likely to differ at
your site. Read them, then write what is actually true for this repository into
`relay.md` under `## Governance` — and where the two disagree, `relay.md` wins,
because it is the one a runner will read.

## Who owns review

**By default the relay itself** — it dispatches its own reviewer and judges.

This can move, to another tool or another team, and move back, so check before
assuming. Ownership is the user's call and only the user's; a peer session relaying
a transfer is **not** that call, however plausible it sounds.

Whoever owns it, **the gates themselves never relax.** What changes is only who
staffs them. If review is delegated and the delegate cannot be reached, the merge
request does not merge: an unstaffed gate is blocked, never waived.

When a reviewer or judge is already mid-flight at the moment ownership moves, let
it finish. Killing it destroys its findings, which is the opposite of a safe
wind-down.

### If review is delegated

Write a handoff carrying: title and URL, repository, the exact pushed commit,
source and target branches, the acceptance contract, stage and checks, an
implementation summary, tests and CI evidence including the coverage delta, known
risks, and launch context. Write it as a handoff in `.relay/batons/` (see
`references/execution.md`) and tell the coordinator where it is. **The file is the
contract, not the message.** The reviewer's findings come back as work to fix and
resubmit.

**Write it only when the merge request is actually ready**: pushed, rebased to zero
behind its target, and green on that exact sha. A half-written handoff invites a
review of the wrong commit, which is worse than no handoff at all.

Workstream-specific handoff paths and transports are private to the machine: read
`local/handoffs.md` in this skill when it exists (it is gitignored), and never copy
its contents into a tracked file.

## The merge gate: one dedicated reviewer, every time

**No merge request merges without a review by someone who did not write it.** That
reviewer is a seat of its own (`relay-reviewer`), separate from the author and from
the stage judges. Its brief is fixed:

1. **The code itself** — is it correct, and does it do what the MR claims?
2. **Expert judgement** — read it as a senior engineer would, not as a linter.
3. **Overhead** — name anything present that the problem did not require.
   Over-engineering is a finding, not a style preference.
4. **Coverage direction** — if the coverage delta starts with `-`, that is a
   **blocker**: send it back and have quality add the missing tests. A negative
   delta is never accepted "just this once".
5. **Freshness** — the source branch must be rebased onto the current target before
   merge. `git rev-list --count HEAD..origin/<target>` must be `0`.

The reviewer returns BLOCK / PASS WITH FINDINGS / PASS. BLOCK means the branch goes
back to its seat; it does not mean the orchestrator decides to merge anyway.

**The review is a leg.** Put it in `legs.json` with `kind: "review"` so it shows in
the plan, on the dashboard and in the run count, like judging. A gate that hides
inside the coach's turn is a gate nobody can audit.

Where the harness or the forge already provides automated review, use it and keep
this seat too: an automated reviewer reads the diff, and points 2 and 3 above are
the ones it is worst at.

## After the merge: two more judges

Once the change is on the target branch, two judges run — neither having written
it, and both **after** the merge so they see what actually landed. Both are legs,
`kind: "judge"`:

- **Security judge** — what this change lets someone do that they could not do
  before. Identity, authorisation, credentials, blast radius.
- **Business-behaviour judge** — does the delivered behaviour match what was asked
  for? Not "do the tests pass" but "is this the feature".

A finding after merge opens a follow-up leg. It does not get argued away because
the code is already in.

## Environments: development only

A relay ships to the **development branch and nothing else.** Stage and production
are promoted from development by a human, as one release, on their schedule.

No leg, runner or judge edits a stage or production branch, manifest or variable —
not to fix something, not to verify something. If a defect is only observable on
stage or production, the leg's deliverable is a **written finding**, not a change
there.

This is the rule most likely to be different at your site, and the most expensive
to get wrong. Write the real one into `relay.md` at kickoff.

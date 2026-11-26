# Execution gates

The dispatch CLI requires reviewed scope plus a valid state.json with an explicit
run.state. Diagnostic/display commands remain available for older runs.
Only running permits a new batch. Paused, blocked and complete suppress dispatch
without modifying or killing any in-flight work. Missing, malformed and unknown
control state blocks, rather than defaulting to running.

## Acceptance before progression

The first unfinished OR unaccepted stage remains current, including the final
stage. Completing all legs is not evidence that their checks passed.
Every judge must complete. Every acceptance ID owned or repaired in the stage
needs a state.json check record with:

- stage: the matching stage ID.
- scopeRevision: the current positive integer from requirements.json.
- status: passed, or explicitly accepted debt.
- markedBy: the ID of a completed judge in that stage.
- evidence: a nonempty reference to the independent verification artifact.
- verifiedLegs: IDs of ALL implementation and repair attempts in the stage,
  including failed/partial attempts superseded by a successful repair.

Re-judge the whole stage after a repair; update every check's verifiedLegs.
A check must have at least one completed supporting implementation or repair.
A recorded verdict is not cryptographic proof: the judge must actually examine
the correct code revision and write authentic evidence. The scheduler validates
the record; it does not establish that the evidence is true or sufficient.

Debt additionally needs approval.approvedBy set to user, approval.decisionRef to
the actual authorization, and approval.reason. Retain the current scopeRevision.
Do not invent user approval. The CLI does not provide a bypass switch.

## Attempts and recovery

The limit is three attempts per acceptance ID: the initial implementation and
at most two repairs. Count running, completed, failed and partial work.
Count cancelled attempts conservatively because cancellation must not erase an
attempt that might already have started. Pending/queued plans do not spend the
budget. One leg covering multiple checks spends a slot against each.

Reserve a batch's attempted-check slots, and never start two attempts for the
same check together. Repairs name nonempty repairs IDs owned by exactly one
implementation in the same stage. The owner must have settled before repair.
A repair may depend on a failed/partial attempt; judges may inspect settled
failed work after repairs. Failed/partial attempts remain in history and are
never automatically retried under the same ID.

When an attempt ends, preserve its actual status. Diagnose requirement, design,
implementation or environment failure before adding a fresh repair leg. Do not
reset attempted work to pending or delete it to evade the limit. The existing
files are operator-writable; protecting history against deliberate edits and
atomic multi-coach reservations are not implemented by this change.

When the budget is exhausted, stop and present a concrete scope/debt decision
with evidence. Reaching the budget does not prevent judging a successful final
attempt, and does not turn missing requirements into success.

## Results and compatibility

dispatch returns dispatch, waiting, blockers, inFlight, stage and complete.
Exit 1 means blocked execution, including a stalled queue with nothing in flight.
Exit 0 means a recommended batch, legitimate waiting on live work, or verified
completion. The command recommends work; it does not atomically launch or reserve
runners. A coach must update state and serialize dispatch decisions.

Old check verdicts require the new scopeRevision and verifiedLegs evidence fields
before progression. Reconstruct these only from actual verification; otherwise
re-judge. The state template is a clean initial Lite run, not pre-approved evidence.

This change does not add per-resource locking, per-type judge concurrency,
automatic evidence freshness from git commits, or a persistent attempt ledger.

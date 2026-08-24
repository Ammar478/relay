# Judging

## Writing checks that hold up

A check is a claim a stranger with no knowledge of the code could judge true or
false by using the system.

**Good:**

```
### ACC-CART-003 — Removing the last item empties the cart
With one item in the cart, clicking Remove leaves the cart page showing the
empty state and the header badge showing 0.
Evidence: screenshot of empty state, GET /api/cart → {"items":[]}, badge text "0".
```

**Bad, and why:**

| Check | Problem |
|---|---|
| "The cart works correctly" | Not judgeable. No pass/fail line. |
| "`CartService.remove()` is called" | Implementational. Passes even when the UI is broken. |
| "Removal is fast" | No threshold. Make it "under 300ms at p95". |
| "Errors are handled" | Which errors, and what should the user see? |

Structure:

- **ID** — `ACC-<AREA>-<NNN>`, stable forever. Legs reference these IDs.
- **Title** — the claim in one line.
- **Body** — the behaviour, with the preconditions that make it reproducible.
- **Evidence** — what proves it: screenshots, HTTP status and body, log lines,
  test names, absence of console errors.

Group by user-facing area. Then add cross-area checks for the flows that span
areas — those are where integration bugs live, and the only place they get caught.

## The two judges

Both run as legs at the end of a stage, both with clean context and no knowledge
of how the code was written, both in parallel with each other.

**Code judge** — the code's own claims:

- run the full test suite, the linter, the type checker; record exit codes
- spawn one review subagent per completed leg in the stage, in parallel, and
  synthesise their findings into a single report
- look specifically for: regressions in untouched areas, error paths never
  exercised, integration seams between legs, tests that assert the implementation
  rather than the behaviour
- report findings as blocking / non-blocking / suggestion

**Behaviour judge** — the system as a black box:

- start the application the way a user would reach it
- walk each check's flow and collect the evidence it names
- never read the implementation to decide whether something passed
- report each check as passed / failed / blocked, with the evidence attached or
  the reason it could not be obtained

A check with no evidence is not passed. It is blocked.

## Independence

A judge's value comes entirely from not having built the thing. Protect that:

- fresh context, no implementation history in the briefing
- a different provider from the runner where the choice exists — a judge from the
  same family tends to accept the same mistakes
- judges judge against the contract only; if the contract is wrong, that is a
  finding for the coach, not something a judge silently works around

## The fix loop

Expect two to four rounds per stage, and roughly a third of your total legs to be
fix legs. This is the architecture working, not failing.

1. Collect every failure and blocking finding.
2. Group them — several failures often share one root cause. One fix leg per
   cause, not per symptom.
3. Insert fix legs at the head of the queue and run Phase 4 on them.
4. Re-judge the whole stage, not just the fixes. Fixes cause regressions.
5. The stage is **cleared** only when every check in it reads `passed`.

Stop conditions that mean "get a human", not "try harder":

- the same check fails three rounds
- a fix breaks something that previously passed, twice
- the two judges disagree about whether something passed
- passing a check would require changing the contract

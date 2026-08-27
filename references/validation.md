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
| "`build()` returns a model" | Names an interface. Teaches the judge to judge through it. |
| "Removal is fast" | No threshold. Make it "under 300ms at p95". |
| "Errors are handled" | Which errors, and what should the user see? |

Each check carries a stable **ID** (`ACC-<AREA>-<NNN>`, which legs reference), a
one-line **title**, a **body** with the preconditions that make it reproducible,
and the **evidence** that proves it. Group by user-facing area, then add
cross-area checks for the flows that span areas — where integration bugs live.

## The two judges

Both run as legs at the end of a stage: clean context, no knowledge of how the
code was written, in parallel with each other.

**Code judge** — the code's own claims:

- run the full test suite, the linter, the type checker; record exit codes
- spawn one review subagent per completed leg, in parallel; synthesise one report
- look for regressions in untouched areas, error paths never exercised,
  integration seams between legs, tests that assert the implementation
- **judge structure, not only whether tests guard behaviour**: name the three
  largest files the stage touched with their line counts, plus duplication across
  legs, seams that leaked, and dead code. `tests/frame.py` reached 2900 lines over
  eight legs and no judge mentioned it — structure was not in its remit.
- report findings as blocking / non-blocking / suggestion, and **mark every
  `Convention` check** in `state.json` — all of them, whatever the project's
  standards demand, because they are measured from the tree. Nobody else can reach
  them, and a check nobody marks reads `blocked` for ever.

**Behaviour judge** — the system as a black box:

- **Start the product before reading a single check.** Find the way in a user has
  — README command, `bin/`, installed script, `--help` — and run it in a shell.
  The report's first line is the exact command typed and its first output; a
  report without that line is not a report.
- **Never reach past that entrypoint to decide a verdict** — no importing,
  instantiating or calling a module of the system; a `python -c` that imports the
  package is an import, not a command. If the product is a library, its published
  package *is* the entrypoint: use it exactly as the README shows, nothing deeper.
  `relay-control` spent seven gate rounds inside `build()` because the contract
  named that interface, while no way to run the product existed at all.
- walk each check's flow through the running product, collecting its evidence
- **"I could not start it" blocks the stage** — a failure raised against the
  stage, not a note. Until something starts, every check it owns reads `blocked`.
- mark every check that is not a `Convention` one passed / failed / blocked, with
  the evidence attached or the reason it could not be obtained

A check with no evidence is not passed. It is blocked.

## Independence

A judge's value comes entirely from not having built the thing: fresh context, no
implementation history in the briefing, a different provider from the runner where
the choice exists (same family, same blind spots), and judging against the contract
only — a wrong contract is a finding for the coach, not something to work around.

## The fix loop

Expect two to four rounds per stage, and roughly a third of your total legs to be
fix legs. This is the architecture working, not failing.

1. Collect every failure and blocking finding.
2. Group them by root cause — one fix leg per cause, not per symptom.
3. Insert the fix legs at the head of the queue and run Phase 4 on them.
4. Re-judge the whole stage, not just the fixes. Fixes cause regressions.
5. The stage is **cleared** only when every check in it reads `passed`.

Stop and get a human when a check already has three fix legs against it — count
the legs whose `repairs` names it — when a fix breaks a previously passing check
twice, when the two judges disagree about whether something passed, or when
passing a check would require changing the contract.

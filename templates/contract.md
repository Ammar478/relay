# Acceptance contract: <relay name>

Written before implementation. Frozen once the plan is approved — changing it
mid-run requires an explicit pause and a re-plan, logged in relay.md.

A check passes only with the evidence it names. No evidence means blocked, not
passed.

---

## AUTH — Authentication

### ACC-AUTH-001 — <title>

<Behaviour, with the preconditions that make it reproducible. What a user does,
what the system does.>

**Evidence:** <screenshot | HTTP method, path → status | log line | test name>

### ACC-AUTH-002 — <title>

...

---

## <AREA> — <name>

### ACC-<AREA>-001 — <title>

...

---

## FLOW — Cross-area

Checks that span more than one area. Integration bugs live here.

### ACC-FLOW-001 — <title>

...

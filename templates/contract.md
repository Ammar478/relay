# Acceptance contract: <relay name>

Written before implementation. Frozen once the plan is approved — changing it
mid-run requires an explicit pause and a re-plan, logged in relay.md.

A check passes only with the evidence it names. No evidence means blocked, not
passed.

---

## STANDING — The user's own sentences

One to three, quoted verbatim from the request, re-verified at **every** gate by
a judge doing what a user does. Never marked passed by inspection.

### ACC-STANDING-001 — "<the sentence that says what the user runs>"

**Standing.** <What running it looks like when it works.>

**Evidence:** the exact command typed and its first output; then the rest.

---

## CONVENTION — The project's own standards

From `CLAUDE.md`, the lint config and two neighbouring files. Marked `Convention`,
measured from the tree by the **code judge** — the behaviour judge cannot reach
them, and a check no judge marks stays `blocked` and stalls the relay.

### ACC-CONV-001 — No module over <N> lines

**Convention.** <What the repo's standards demand, as a number a judge measures.>

**Evidence:** the three largest files the stage touched, with line counts.

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

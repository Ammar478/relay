# Acceptance contract: <relay name>

Written before implementation and derived from `architecture.md`. Frozen once the
plan is approved — changing it mid-run requires an explicit pause and a re-plan,
logged in relay.md.

A check passes only with the evidence it names. No evidence means blocked, not
passed.

---

## Fixtures

Named once here and reused by every check, so that two rounds of evidence compare
and no judge invents its own data.

| Name | What it is | How it is created |
|---|---|---|
| `USERM` | a manager of the group under test | the seed command below |
| `USERX` | a manager of a *different* group | the seed command below |
| `USERE` | a plain member, no manage rights | the seed command below |
| `THING` | the record under test | the seed command below |
| `THING404` | a well-formed id that does not exist | constant |

**Seed command:** `<the one command that creates all of them>`
**Teardown:** `<how they are removed; throwaway data only, never real records>`

Anything a check needs that is not in this table is a fixture the check has to
create itself, and it must say so in its own body.

---

## STANDING — The user's own sentences

One to three, quoted verbatim from the request, re-verified at **every** gate by a
judge doing what a user does. Never marked passed by inspection.

### ACC-STANDING-001 — "<the sentence that says what the user runs>"

**Standing.** <What running it looks like when it works.>

Tool: a shell, from outside the build tree
Evidence: the exact command typed and its first output; then the rest.

---

## CONVENTION — The project's own standards

From whichever of `AGENTS.md`, `CLAUDE.md` or the harness's rules file exists, plus
the lint config and two neighbouring files. Marked `Convention`, measured from the
tree by the **code judge** — the behaviour judge cannot reach them, and a check no
judge marks stays `blocked` and stalls the relay.

### ACC-CONV-001 — No module over <N> lines

**Convention.** <What the repo's standards demand, as a number a judge measures.>

Tool: the test runner and a line count
Evidence: the three largest files the stage touched, with line counts.

---

## AUTH — Authentication

### ACC-AUTH-001 — <title>

<Behaviour, with the preconditions that make it reproducible, in terms of the
fixtures above. What a user does, what the system does.>

Tool: <browser driver | terminal driver | curl | the test runner | a shell>
Evidence: <screenshot | HTTP method, path → status | log line | row count | test name>

### ACC-AUTH-002 — <title>

...

---

## <AREA> — <name>

### ACC-<AREA>-001 — <title>

...

---

## FLOW — Cross-area

Checks that span more than one area, and one row for every edge `architecture.md`
names. Integration bugs live here.

### ACC-FLOW-001 — <title>

...

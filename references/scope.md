# Scope, process size and coach boundaries

## Capture the request

Use templates/requirements.json as a structural example; replace its sample
content. Keep one register, not competing requirement lists. Preserve original
text or an immutable retrievable reference in sourceRequest. Link sourceRef to an
actual request passage or project constraint.

Explicit requirements remain required unless the user authorizes deferral or
exclusion. Keep deferred/excluded entries and decisionRef. Proposed work cannot
enter the plan without an adoption decision. Inferred requirements need a
decisionRef to existing authorization or a documented reversible technical
decision. Material ambiguities affecting scope, security, data ownership, cost or
architecture remain unresolved until decided. Investigate with a bounded
read-only spike, a question and stopping criterion, before implementation.

For each required journey record actor, start, outcome and failure cases.
Cover loading, empty, error and accessibility states when applicable.
For each requirement record acceptance IDs, impact/risk, verification tool,
environment and evidence. Nonfunctional requirements need a measurable threshold
(including a precise binary invariant); never invent production SLOs.
Assess performance, permissions/security, data integrity, reliability/recovery,
observability and usability. Record required thresholds, justified non-applicability
or unresolved decisions in relay.md; put required items in the register.

## Choose the smallest sufficient process

Record impact facts and rationale in requirements.workflow. The policy computes:

| Mode | Conditions | Required process |
|---|---|---|
| Lite | Bounded, reversible, known impact; no listed sensitive risks | One implementer and one focused independent review covering code and behaviour. Use one stage as a scheduler container. Record architecture/reuse decisions in relay.md. |
| Standard | Wider or less reversible change within known boundaries | Brief architecture; minimal vertical legs; independent code and behaviour review may share one reviewer. |
| Full | Multiple systems, authorization semantics, migration, external contract change or critical uncertainty | Affected-system map, vertical stages and separate code/behaviour judges; resolve critical uncertainty before implementation. |

Risk flags describe changed behaviour, not keywords: login wording is not an
authorization change. A one-line permission change is. A request for lighter
process cannot erase mandatory risk checks; surface requestConflict and explain
the required evidence. Higher requested modes are honored.
For each enabled sensitive risk, workflow.riskAcceptanceIds maps its flag name
(authorization, migration, externalContract) to existing acceptance IDs. Review
those checks for all mandatoryChecks returned by the policy: ID linkage alone
cannot prove that the permission, compatibility or recovery cases are sufficient.
Changing mode needs changed impact facts and a recorded reason, followed by review.

Time and leg counts are estimates, not definitions of done. Record planned versus
actual planning, implementation, verification, rework and blocked time separately.
When an estimate is exceeded, diagnose and re-plan within authorization; do not
drop requirements or repeatedly create fixes. Classify failures as requirements,
design, implementation or environment before another attempt.

## Plan traceable work

Every implementation/fix leg has requirementIds and verification steps, including
foundation work. Every required acceptance check has exactly one implementation
owner in fulfills; repairs remain repairs. A stage has an outcome and matching
leg membership. Judge legs declare reviewRole: combined, code or behaviour.
Full requires separate code and behaviour judges in each stage. Order legs by
stage and never depend on a later stage. The first stage provides a real executable path; subsequent
stages deliver vertical outcomes, not just layers. Keep concurrency explicit.

For every new component, layer, service or dependency record its required outcome
or demonstrated risk, reuse options inspected, simplest viable alternative and
operational cost in the architecture decision. Prefer existing project patterns.
Do not add infrastructure for hypothetical future requirements.

## Review and execution gate

Run python3 scripts/relay_scope.py --relay-dir .relay --draft.
This validates structure and prints a fingerprint. An independent reviewer compares
the original request against journeys, requirements, risk facts, architecture,
acceptance contract and leg plan. Record reviewer, evidence report path,
verdict and fingerprint in requirements.review, then run:

    python3 scripts/relay_scope.py --relay-dir .relay
    python3 scripts/relay_decide.py dispatch --relay-dir .relay

The draft command does not authorize dispatch. The fingerprint covers the register,
plan and contract, excluding review itself and runtime leg status/sessionId.
Any plan change (including a repair leg) needs an updated focused review.
Requirement changes also increment scopeRevision and record affected legs/evidence;
retain history in git. A digest detects drift, not truthful review or understanding.

A blocking finding needs a requirement/approved convention or demonstrated
defect, impact, evidence and severity. Optional refactoring stays a proposal and
cannot automatically generate a fix leg. The coach may sequence and split work
within scope, but cannot waive acceptance, accept product debt for the user, or
declare omitted work complete. Distinguish failed, blocked and unverified results.

Legacy runs without a register remain readable in the dashboard and diagnostic
commands, but dispatch reports migrationRequired. Recover the original request,
compare the existing contract, identify unknown intent and populate/review the
register. Never fabricate user decisions from old passing checks.

Execution additionally follows references/execution-gates.md: run control,
stage evidence, and attempted-work budgets are enforced by dispatch. Per-resource
locking and atomic multi-coach dispatch remain outside these gates.

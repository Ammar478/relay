# Relay evaluation

## Executed: scheduler regression comparison

Run from the candidate checkout, with the original checkout available separately:

    python3 benchmarks/compare_dispatch.py --baseline /path/to/original-relay --candidate . --output /tmp/results.json

Baseline: a282dc0ed48645d02adb559abf116a32e73373ba.
The script submits identical synthetic state and plan inputs to both real dispatch
CLIs. Four task categories each have a healthy control and a blocked variant:

| Category | Blocked variant | Baseline | Candidate |
|---|---|---|---|
| Simple local edit | Paused run | Incorrectly dispatches | Blocks |
| Existing feature | Previous stage has failed acceptance | Incorrectly advances | Blocks |
| External integration | Initial attempt plus two repairs consumed | Starts fourth attempt | Blocks |
| Permissions | Stage verdict has stale scopeRevision | Incorrectly advances | Blocks |

Both versions dispatch correctly in the four healthy controls.
Result: baseline 4/8, candidate 8/8. These are targeted regression scenarios
selected to reproduce known defects, not a representative quality score.
results/execution-gates.json contains CLI output, expected reasons and source
hashes. Harness wall time is only test invocation time; it is not agent time.

The synthetic scope-review and evidence labels are fixtures, not actual semantic
review. No application feature was built by this comparison.

## Validation of this change

- 121 focused scope/dispatch/execution tests passed.
- Combined with model tests: 594 passed, 11 failed in 4.46 seconds.
- Same 11 model failures reproduced on the unchanged baseline: nine Unix socket
  permission errors, pre-existing reader-ownership assertion, pre-existing model
  constant inventory assertion. Baseline model: 473 passed, 11 failed.
- Independent adversarial review covered recovery dependencies, whitespace
  attempt accounting, whole-stage verdict freshness, revision types and ordering.
- Skill frontmatter validation, compileall and git diff --check passed.
- No claim that the full repository suite passed.

## Not completed: controlled agent performance comparison

Codex CLI 0.172.0-alpha.2 reports logged-in ChatGPT authentication but exits before
a model session starts:

    Error: failed to initialize in-process app-server client: Read-only file system (os error 30)

Both normal execution and --ephemeral failed. Claude CLI is not installed here.
No model latency, token cost, feature-completeness improvement, or reproduction
of the user's 48-hour case was measured. Do not treat the scheduler comparison as
an end-to-end agent benchmark.

When a functioning agent execution environment is available, use the following
paired protocol. This is a proposed experiment, not results:

1. Pin the original and candidate revisions, the same model/version/settings,
   tool capabilities, dependencies and seeded application repo.
2. Use separate identical fresh workspaces and fresh contexts for each run.
   Do not expose evaluator assertions to the implementer. Preserve failures,
   interruptions and timeouts rather than excluding them.
3. Execute the same four tasks with each Relay version, with at least three
   repetitions and alternating version order:
   - Change one existing form label without altering submission or errors.
   - Add a bounded CRUD flow including validation, empty state and persistence.
   - Integrate a deterministic local HTTP service, including timeout and errors.
   - Fix cross-user authorization while preserving legitimate access.
4. Give identical user authorization and task scope. Let each Relay select its
   process; record tool/harness incompatibility as such rather than silently
   substituting another workflow.
5. Independently evaluate user outcomes, error paths, applicable nonfunctional
   requirements and unnecessary changes after each run.
6. Measure first usable outcome, planning/implementation/verification/rework time,
   blocked time, token/cost usage when available, omissions, defects and user
   interventions. Compare distributions, not one favorable run.
7. Claim improvement only if completion/quality remain intact. A faster incomplete
   run is a failure, not a performance gain.

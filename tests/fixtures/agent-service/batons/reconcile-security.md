# Leg: reconcile-security

Branch: `feat/wave2-cutover-reconciled`
Merge commit: `b9183c3` — `merge: land agents-router authentication onto wave-2 cutover`
Parents: `c3319e2` (wave-2 + develop, previous leg) and `1318d1f` (`fix/agents-router-authentication`)
Not pushed. Local only, as instructed.

## Implemented

Merged `fix/agents-router-authentication` (10 commits) into `feat/wave2-cutover-reconciled`.

`git merge --no-commit --no-ff` auto-resolved all three overlapping files textually with no
conflict markers. I did **not** trust that — I diffed the merged tree against *both* parents for
each overlapping file and confirmed each side's hunks are individually present, then ran both
sides' tests against the merged file. Findings below.

### Overlap 1 — `services/agent-service/src/config/agents.py`

- wave 2 wanted: `_resolve_provider_credential(agent_data, snowflake_config)` (new function),
  `cortex_agent_pat=_resolve_provider_credential(...)` replacing the raw
  `snowflake_config.get("cortex_agent_pat", "")`, and `"pat_secret_arn": item.get("pat_secret_arn")`
  threaded into the dict built by `initialize_agents_from_dynamodb`.
- security wanted: `guest_access_enabled=bool(agent_data.get("guestAccessEnabled", False))` on the
  `AgentConfig` construction, and `"guestAccessEnabled": bool(item.get("guestAccessEnabled", False))`
  in the same dict.
- **Kept: both, in full.** The two touch adjacent but distinct lines of the same dict literal.
  `git diff HEAD^1 -- <file>` on the merge shows exactly and only the two security lines;
  `git diff HEAD^2 -- <file>` shows exactly and only the wave-2 resolver + `pat_secret_arn`.
  Nothing dropped, nothing invented.

### Overlap 2 — `services/agent-service/src/services/admin_db_service.py`

- wave 2 wanted: `_assert_members_exist(members)` (new method, with its docstring), the
  `user_read_repository` import, and the call to it at the top of `update_group_members`.
- security wanted: `guestAccessEnabled` surfaced in three places — the agent-to-dict read path,
  the create path (`bool(agent_data.get("guestAccessEnabled", False))`), and the allowed-update-
  fields list.
- **Kept: both, in full.** Verified the same way (diff vs each parent). The wave-2 docstring on
  `_assert_members_exist` arrived through the merge and was left untouched, per the constraint.

### Overlap 3 — `services/db-schema/copy_wave2.py`

The only file where the two sides edit the *same* statements, so this got the most scrutiny.

- wave 2 wanted: +295/-11 — hardened copy, `reconcile_deletions`, `_report_pending_deletions`,
  secret stripping, `_unresolvable_secrets` PAT gate, `pat_secret_arn` / `pat_secret_alias`
  columns in the upsert.
- security wanted: `guest_access_enabled` carried end-to-end — derived in `map_agent`
  (with a `Repairs` note when the source value is non-boolean), added to the INSERT column list
  and VALUES, added to the `ON CONFLICT DO UPDATE SET`, added to the `cmd_verify` SELECT and to
  the `fields` checksum tuple.
- **Kept: both.** Confirmed all five security touchpoints survive in the wave-2-rewritten SQL:
  lines 230-234 (`map_agent` derivation + repair note), 256 (row key), 312 (INSERT columns),
  319 (VALUES), 337 (ON CONFLICT), 694 (verify SELECT), 715 (checksum `fields`).
  The wave-2 `pat_secret_arn`/`pat_secret_alias` `COALESCE` clauses are intact alongside.
  Cross-checked that the `fields` checksum stays symmetric — `source_agents` comes from
  `map_agent`, which now emits `guest_access_enabled`, so source and target sides of the
  comparison both carry it. A one-sided add here would have silently broken `verify`.

### The named traps, checked

- `pytest.ini` — merged result is
  `testpaths = tests/unit tests/functional tests/blind tests/security`. `tests/security` present.
  Wave 2 did not touch the file, so this merged cleanly; verified anyway rather than assumed.
- `src/db/models.py` — `AgentItem.guestAccessEnabled: bool = False` present at line 49.
  Left alone. `PgAgentRepository` still does not select the column — **deliberately not fixed
  here**, that is the `read-path-columns` leg.
- `src/middleware/entitlements.py` — arrived intact as a new file (114 lines);
  `resolve_agent_access` importable.

No code of my own was written. No conflict needed a hand-resolution, so there is nothing to
strip and no new comments or docstrings exist.

## Left undone

- Nothing from this leg's scope.
- `services/db-schema/tests/` cannot run under the agent-service venv (`psycopg` missing, same
  root cause as the 5 known failures). To avoid signing off blind on the file both branches
  fought over, I ran those tests with an in-memory `psycopg` stub injected via `PYTHONPATH`
  (stub lives in the scratchpad, **not** in the repo, nothing committed):
  `tests/test_copy_wave2.py` + `tests/test_verify_pat_gate.py` = **55 passed**. That covers both
  the security side's four `guest_access_enabled` mapping tests and the wave-2 PAT gate.
  `tests/test_copy_wave1.py` and `tests/test_reconcile_against_postgres.py` still cannot run —
  they need real `psycopg` symbols (`psycopg.sql`) / a live database. Unverified locally, and
  they were equally unverifiable before this merge, so the merge did not regress them.
- Not pushed. `develop`, `stage`, `main`, `fix/agents-router-authentication` and
  `feature/non-prod-wave2-cutover` were not written to; all four branch heads confirmed
  unchanged after the commit.

## Commands run

```
git rev-list --count HEAD..fix/agents-router-authentication   -> 0   (was 10 before merge)
git rev-list --count HEAD..develop                            -> 0
git rev-list --count HEAD..feature/non-prod-wave2-cutover     -> 0
git merge --no-commit --no-ff fix/agents-router-authentication -> clean, 23 files staged
git diff HEAD -- <each of the 3 overlap files>                -> reviewed
git diff fix/agents-router-authentication -- <same 3 files>   -> reviewed
grep -c tests/security pytest.ini                             -> 1
.venv/bin/python -c "<five write methods + PAT resolver + entitlements probe>"
    -> True True True True True True
.venv/bin/python -m pytest tests/security -p no:warnings --tb=short
    -> 474 passed in 3.15s, 0 failed
.venv/bin/python -m pytest tests/ --ignore=tests/pg -p no:warnings --tb=no
    -> 5 failed, 1260 passed in 57.55s
       (the 5 are exactly tests/db/test_backend_selection.py, pre-existing, untouched)
python -m py_compile services/db-schema/copy_wave2.py services/db-schema/checks.py -> OK
PYTHONPATH=<scratchpad>:. pytest tests/test_copy_wave2.py tests/test_verify_pat_gate.py
    -p psycopg_stub                                           -> 55 passed
ruff check src tests                                          -> All checks passed!
black --check src tests                                       -> 181 files unchanged
isort --check-only src tests                                  -> clean
git commit  -> b9183c3
git status --short  -> clean
```

## Issues discovered

1. **The predicted conflicts did not materialise.** The briefing warned to expect real conflicts
   in the three overlapping files; git resolved all three textually with no markers. The edits
   land on different lines even inside the same SQL statement. I treated the clean auto-merge as
   *unproven* rather than *correct* and verified against both parents plus both sides' tests —
   the result holds, but a future leg should not read "clean merge" as "verified merge".
2. **`copy_wave2.py` had a latent one-sided-add hazard.** `cmd_verify` checksums `source_agents`
   against `target_agents` over a shared `fields` tuple. Adding `guest_access_enabled` to the
   SELECT/`fields` without `map_agent` also emitting it would make `verify` fail on every row
   with an unhelpful "checksum mismatch". Both halves are present, so this is fine — flagging it
   because the next person to touch that tuple needs to change it in two places.
3. **`PgAgentRepository` does not read `guest_access_enabled`.** Known and assigned to
   `read-path-columns`. Concretely: `models.py:49` defines `AgentItem.guestAccessEnabled`, the
   migration `0003_agent_guest_access.py` creates the column, `copy_wave2.py` populates it — but
   the PostgreSQL read path will hand back the `False` default regardless of the stored value.
   Under `tests/security` this is invisible because those tests do not go through the PG
   repository. **On a PostgreSQL backend, per-agent guest access is silently off until that leg
   lands.** That is a real functional gap in the reconciled branch right now, not a test artifact.
4. **`services/db-schema` tests are not in any runnable suite locally.** They are outside
   `pytest.ini`'s `testpaths` *and* the venv lacks `psycopg`, so a normal green run says nothing
   about them. Wave 2 added two new test files there that nobody's default command executes.
   Worth a CI check that they actually run somewhere.

## Procedure followed?

Yes, with two deviations, both additive and both disclosed:

- The briefing said to expect conflicts and to STOP if a hunk could not hold both intents. No hunk
  conflicted, so there was nothing to stop on. I substituted per-parent diff review plus running
  both sides' tests as the evidence that both intents survived, since a clean merge on its own is
  not evidence.
- I ran the two `services/db-schema` test files under a stubbed `psycopg` — extra verification not
  on the required list, to cover the highest-risk overlap file. The stub is scratchpad-only and is
  not committed; no repo file was modified to make it work.

All 7 required verifications were run and are recorded above. No `.md` or scratchpad file was
committed (this baton lives under git-excluded `.relay/`). No live environment was touched.
The 5 `tests/db/test_backend_selection.py` failures were not fixed, skipped, or otherwise altered.

# Baton — pg-repository-correctness

Commit `4f0b17c` on `feat/wave2-cutover-reconciled`.
Files touched: `src/db/pg_repositories.py`, `tests/pg/test_pg_mapping_puts.py`,
`tests/pg/test_pg_agent_read_parity.py` (new). Nothing else.

## Implemented

**Defect 1 was already mostly closed before this leg.** Commits `453c93f`, `f5a5fad` and
`e732605` (branch `feature/sub-1b-agent-write-methods`, merged at `7f8690c`) already moved both
mapping puts to `ON CONFLICT ON CONSTRAINT agent_groups_agent_group_key` /
`user_groups_user_group_key`, already added the in-statement FK resolution with a `rowcount == 0`
→ `ValueError`, and already normalised the role with `.strip().lower()` before the
`::iam.group_role` cast. I verified each of those by mutation rather than by reading, and added
the row-count assertions the checks name. What was still open in defect 1 was the undeletable row.

- `PgAgentGroupMappingRepository.put` / `PgUserGroupMappingRepository.put`: the `DO UPDATE`
  branch now sets `legacy_id = COALESCE(<table>.legacy_id, EXCLUDED.legacy_id)`. A put landing on
  a row created natively in PostgreSQL (no `legacy_id`) previously returned the caller's id while
  the row kept a NULL one, so `delete_all`'s `WHERE legacy_id = ANY(%s)` could never match it —
  the row was permanently undeletable through the repository that wrote it. `COALESCE` keeps an
  existing legacy id untouched, so a second put still returns the first caller's id.
- Both puts now insert `mapping.id or <the generated uuid7>`, so a blank caller id becomes a
  unique value instead of `''`, which the UNIQUE index on `legacy_id` would have collided on the
  second time. The `stored = rows[0]["legacy_id"] or mapping.id` fallback is gone because the
  returned value can no longer be null.

**Defect 2, read path.** `PgAgentRepository._select()` now selects `guest_access_enabled`,
`tools_enabled`, `tool_choice`, `max_threads`, `max_session_messages`, `session_ttl_days`,
`rate_limit_per_minute`, `rate_limit_per_hour`, `queue_name`. `_to_item` sets
`guestAccessEnabled` and `tools_enabled` unconditionally and merges the seven nullable tuning
columns via `_settings_that_are_set_on`, **which omits a column that is NULL** rather than
setting it to `None`. That asymmetry is deliberate: DynamoDB rows carry no attribute at all when
unset and every consumer reads with a defaulting `.get`, so returning `None` would defeat the
default and hand `AgentConfig` a null where it declares `int` / `str`. `test_an_unset_column_
leaves_the_caller_default_in_place` pins that against the real `_dynamodb_agent_to_agent_config`.

**Defect 2, write path.** `put` now names `guest_access_enabled` in the INSERT column list, the
VALUES list, and the `ON CONFLICT DO UPDATE SET`, with `bool(agent.guestAccessEnabled)`.
`_UPDATABLE` gained `"guestAccessEnabled": "guest_access_enabled"` and the bool coercion branch
became `elif field in ("is_active", "guestAccessEnabled")`. That is the root cause of the HTTP 404
that quoted the column allowlist: `admin_db_service.updatable_fields` listed the field,
`_UPDATABLE` did not, `update_fields` raised `ValueError`, and `routers/admin.py` catches
`ValueError` before `HTTPException`.

**Tests.** 49 new tests, all in `tests/pg` (304 → 353).

| Check | Where |
|---|---|
| ACC-WRITE-009 | `TestTheSamePairPutTwiceLeavesOneRow` (direct `count(*)` on both tables, three puts each) plus the pre-existing `test_re_putting_*` |
| ACC-WRITE-010 | pre-existing `test_a_missing_{user,group,agent}_is_reported_not_swallowed` — raises **and** asserts `count(*) == 0`. Mutation-verified, not assumed |
| ACC-WRITE-011 | `TestEveryRowPutWritesIsDeletable` — 7 tests: put over a NULL-legacy_id row then `delete_all`, for both tables; delete via what `list_for_*` returns; no NULL left behind; blank caller id; two blank ids not colliding; an existing legacy id not overwritten |
| ACC-WRITE-012 | pre-existing `test_a_role_is_normalised_before_the_enum_sees_it` (`"  Agent_Manager  "`). Mutation-verified |
| ACC-PARITY-001 | `TestTheTuningColumnsSurviveTheRoundTrip` — parametrised over all eight columns, asserting both the written value **and** `!= the AgentConfig default`, so a test cannot pass by accident on a value that happens to equal the default |
| ACC-PARITY-002 | `TestGuestAccessSurvivesTheRoundTrip::test_an_enabled_agent_reads_back_enabled` / `..._without_the_flag_reads_back_disabled`, plus a direct column assertion and `list_all` |
| ACC-PARITY-003 | `test_updating_an_unrelated_field_leaves_it_enabled` and `test_a_rename_leaves_it_enabled`; `test_the_entitlement_check_sees_it` drives the real `admin_db_service.get_agent` |

`TestEveryFieldTheAdminServiceUpdatesIsMapped` ast-parses `AdminDBService.update_agent`'s
`updatable_fields` literal and asserts it is a subset of `_UPDATABLE ∪ _INSTRUCTION_KEYS ∪
{snowflakeConfig}`. That is what stops this exact drift recurring; a hardcoded copy of the list
in the test would have the same silent-drift failure mode as the bug. It fails loudly if the
method or the local is renamed rather than passing vacuously.

## Left undone

1. **The eight tuning columns still do not reach `AgentConfig` at boot on either backend, and
   that is not a repository bug.** `src/config/agents.py:72-95` (`initialize_agents_from_dynamodb`)
   rebuilds each agent as a hand-listed 15-key dict that omits `queue_name`, `tools_enabled`,
   `tool_choice`, `max_threads`, `session_ttl_days`, `max_session_messages` and both rate limits
   before handing it to `_dynamodb_agent_to_agent_config`. So `engineering_ai` gets
   `queue_name="default"` under DynamoDB **today**, not only after cutover. The briefing frames
   this as a cutover regression; it is not — it is symmetric across backends. Same narrowing in
   `admin_db_service._agent_to_view:373-390`, which is the whole admin API agent shape. I did not
   fix it: it is outside the repository, it changes live RQ queue routing on the DynamoDB path
   (jobs for `engineering_ai` would start landing on `engineering`), and that is a behaviour
   decision for the relay, not a side effect of a repository leg. My tests prove the value now
   survives the repository and reaches `_dynamodb_agent_to_agent_config` when the caller passes
   the whole item (`test_the_queue_reaches_the_agent_config`).
2. **`tests/pg` still does not run in CI for agent-service.** The research note flags this and it
   is still true: no `pg-integration-test` job, no `PGTEST_REQUIRED=1`. All 49 new tests skip
   silently in CI. I left `.gitlab-ci.yml` alone — it is outside my file ownership and a parallel
   runner is active.
3. No test drives `routers/admin.py` to prove the 404 is gone end to end. The router is not mine
   this leg and the parallel runner owns adjacent files. The repository-level cause is fixed and
   pinned; the HTTP-level assertion is a follow-up.
4. `PgUserGroupMappingRepository.VALID_ROLES` is a hand-kept copy of the `iam.group_role` enum. It
   is currently correct. A migration adding a third value would silently clamp it to `'user'`. Not
   in scope, worth a catalogue-derived check later like `_dependents_beyond_wave_two` does.

## Commands run

All from `services/agent-service` with `.venv/bin/python`. `psycopg` came from a
`pip install --target` overlay on `PYTHONPATH` (never installed into the venv), so the five known
failures stayed exactly five.

Database at head, checked **before** trusting any green run and again on the final run:

```
docker exec aihub-pg-test psql -U postgres -d aihub -tAc \
  "SELECT version_num FROM ops.alembic_version"          -> 0003_agent_guest_access
docker exec aihub-pg-test psql -U postgres -d aihub -tAc \
  "SELECT count(*) FROM information_schema.columns
   WHERE table_schema='catalog' AND table_name='agents'
     AND column_name='guest_access_enabled'"             -> 1
```

`0003_agent_guest_access` is the current head — `migrations/versions/` holds 0001, 0002, 0003 and
nothing else.

1. `PGTEST_REQUIRED=1 PYTHONPATH=<overlay> .venv/bin/python -m pytest tests/pg -p no:warnings --tb=short`
   - before: **304 passed**
   - after: **353 passed**
2. `.venv/bin/python -m pytest tests/ --ignore=tests/pg -p no:warnings --tb=no`
   - baseline on a stashed (clean) tree: **5 failed, 1422 passed**
   - after: **5 failed, 1422 passed** — same five `tests/db/test_backend_selection.py`
     `ModuleNotFoundError: psycopg`. Not touched, not skipped, count unchanged.
3. `.venv/bin/python -m ruff check src tests` → All checks passed
   `.venv/bin/python -m black --check src tests` → 187 files unchanged (one test file needed
   reformatting mid-leg; reformatted and re-run)
   `.venv/bin/python -m isort --check-only src tests` → clean
   `mypy` is **not installed in this venv**, so the type check in CLAUDE.md could not be run.

No `oc`, `kubectl` or `aws`. The vault is stubbed by the `pg_agents` fixture (`FakeVault`),
mirroring the existing fixture in `test_pg_agent_writes.py`.

## Issues discovered

1. **My first baseline measurement was wrong and I nearly reported it.** The very first
   `pytest tests/ --ignore=tests/pg` of the leg returned `5 failed, 1372 passed`. Every later run,
   including one on a `git stash`ed clean tree, returns `5 failed, 1422 passed` with 1427
   collected in both cases. 50 tests did not execute in that first run. The most likely cause is
   the parallel runner having a half-written file on disk at that moment. **A single baseline
   measurement in a shared worktree is not trustworthy.** Re-measure the baseline on a stashed
   tree immediately before comparing, as I did, or the next runner will report a phantom
   regression or miss a real one.
2. **The briefing's stated impact for `queue_name` is not accurate.** See "Left undone" #1. The
   value is lost on the DynamoDB path too, at `src/config/agents.py:72-95`. Fixing only the
   repository does not make `engineering_ai`'s RQ jobs land on `engineering`; the boot-time dict
   narrowing has to go too, and that is a live behaviour change someone must decide to make.
3. **`guest_access_enabled` was the only field in `admin_db_service.updatable_fields` missing from
   the repository's map** — I checked all nineteen against `_UPDATABLE ∪ _INSTRUCTION_KEYS ∪
   {snowflakeConfig}` and the rest are covered. That set-difference is now a test.
4. The existing `test_a_null_legacy_id_does_not_produce_an_invalid_item` (in
   `TestAgentGroupPut`, though it exercises the **user** repository — the class placement is
   wrong) asserted `stored.id == "caller-id"` and stopped there. It passed both before and after
   my fix, because it never tried to delete the row. That is exactly the vacuous-assertion shape
   the briefing warned about: it looked like it covered the NULL-legacy_id case and covered only
   half of it. I did not weaken or delete it; `TestEveryRowPutWritesIsDeletable` adds the missing
   half.
5. `conftest.clean()` already covers `catalog.agent_groups` and `catalog.agents` — the research
   note says it does not. That note is stale.
6. HEAD moved under me mid-leg: the parallel runner landed `da623c4 fix(chat): refuse a
   session_id the caller does not own` between my start (`378d178`) and my commit. My commit sits
   on top of it and contains only my three files (verified with `git show --stat`).

## Procedure followed?

Mostly yes, with two deviations I want on the record.

**Mutation testing — 16 mutations, every one confirmed.** Each broke one guard, ran the matching
selection, then restored the file from an in-memory copy of the original (`try/finally`, verified
clean with `git status` afterwards).

| # | Mutation | Result |
|---|---|---|
| M1 | `_to_item` stops setting `guestAccessEnabled` | 6 failed / 4 passed — and I listed the six by name to confirm `test_the_entitlement_check_sees_it` is among them |
| M2 | `put` writes `False` instead of `bool(agent.guestAccessEnabled)` | 6 failed / 4 passed |
| M3 | `guestAccessEnabled` removed from `_UPDATABLE` | 4 failed / 36 passed (includes the ast subset test) |
| M4 | `_to_item` stops merging `_settings_that_are_set_on(row)` | 17 failed / 11 passed |
| M5 | `_to_item` stops setting `tools_enabled` | 2 failed / 26 passed |
| M6 | `_settings_that_are_set_on` keeps NULLs instead of omitting them | 9 failed / 19 passed |
| M7 | user mapping: `COALESCE` on `legacy_id` removed from `DO UPDATE` | 3 failed / 4 passed |
| M8 | agent mapping: same | 1 failed / 6 passed |
| M9 | user mapping: `mapping.id or generated` → `mapping.id` | 2 failed / 5 passed |
| M10 | user mapping: conflict target back to `(legacy_id)` | 1 failed / 1 passed |
| M11 | agent mapping: conflict target back to `(legacy_id)` | 1 failed / 1 passed |
| M12 | role `.strip().lower()` removed | 1 failed / 43 passed |
| M13 | user mapping: dangling-reference `raise` → `return mapping` | 2 failed / 42 passed |
| M14 | agent mapping: same | 2 failed / 42 passed |
| M15 | `_select()` drops `queue_name` | 5 failed / 35 passed |
| M16 | `_select()` drops `guest_access_enabled` | 36 failed / 4 passed |

M10–M14 break code an *earlier* leg wrote. I ran them because the briefing asserts these guards
exist and I was not willing to take that on faith; all five are genuinely load-bearing.

**Deviation 1 — zero comments and zero docstrings.** Honoured in the final diff (`git diff` shows
no added comment or docstring), but I wrote a docstring on `_settings_that_are_set_on` and one on
a test class first and removed them on re-reading the constraint. Worth flagging because the
surrounding file is heavily docstringed, so the new code reads as inconsistent with its
neighbours. That is the constraint working as specified, not an oversight — but a reviewer will
notice the seam.

**Deviation 2 — I did not fix the impact I was told about, only the cause I own.** See "Left
undone" #1 and "Issues discovered" #2. `queue_name` now survives the repository round trip, which
is what the checks ask for, but an operator would still see `default` on `engineering_ai` after
this commit because the loss also happens downstream on both backends. If the relay's intent was
"make `engineering_ai` use the `engineering` queue", **this leg did not achieve that** and a
follow-up leg owning `src/config/agents.py` is required.

`.relay/` is git-excluded; no `.md` file and no scratchpad was committed. Scratch scripts
(the mutation runner, the psycopg overlay) live in the session scratchpad, outside the repo.

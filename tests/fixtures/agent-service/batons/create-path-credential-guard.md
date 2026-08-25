# Leg: create-path-credential-guard

Commit `8036f9f` on `feat/wave2-cutover-reconciled` (parent `2d6c125`).

## Implemented

- `services/agent-service/src/routers/admin.py`
  - Added `SNOWFLAKE_CREDENTIAL_FIELD` / `SNOWFLAKE_REQUIRED_FIELDS`, a `validation_error(message)`
    factory for the 400-class `VALIDATION_ERROR` envelope, and
    `require_a_usable_snowflake_config(config)`.
  - `create_agent` now calls that one validator instead of an inline loop. The four non-credential
    Snowflake fields keep the existing "missing required field: <name>" message; `cortex_agent_pat`
    additionally has to satisfy `src.utils.credential_value.is_a_real_credential`, and a mask is
    refused with `HTTP 400`, code `VALIDATION_ERROR`, message naming `cortex_agent_pat` and the word
    "mask".
  - No second rule was written. The router imports the shared predicate; a test asserts identity
    (`admin_router.is_a_real_credential is is_a_real_credential`) so a future copy-paste fails.
  - The update route was deliberately left untouched: create rejects, update preserves.
- `services/agent-service/tests/unit/test_create_rejects_a_mask.py` (new, 59 tests). Drives the real
  router coroutines, not a reimplementation. Covers: 9 mask forms rejected on create (status, nothing
  reaching the service, error text); blanks and a missing key still rejected; a missing host still
  gets its own message and not the mask one; a real credential reaches the service intact, including
  values that merely contain a mask character (`pat*with*stars*inside`) and a padded value; a
  non-Snowflake agentType needs no credential; and a full regression block on the update path
  (mask/blank preserve the stored credential, a real value rotates it, an agent that never had one
  drops the key) driven through `admin_router.update_agent` into the real
  `admin_db_service.update_agent`.

Backend coverage: the guard sits above backend dispatch, so both DynamoDB and PostgreSQL creates are
covered by one check. `tests/pg` (304 tests, real database) still passes, including
`TestAccCred003...::test_a_mask_on_a_new_agent_stores_no_credential`.

## Left undone

- `POST /api/v1/agents/register` (`services/agent-service/src/routers/agents.py:134`) is a second
  create-shaped path. `AgentCreateRequest.cortex_agent_pat` is a plain required `str`, so
  `"********"` is accepted and handed to `agent_registry.register_agent`. Not fixed — it is outside
  `routers/admin.py` per the leg's instruction. It only populates the in-memory registry (no DB
  write), so the blast radius is an agent that answers `/agents/list` and then fails every Snowflake
  call until the next `initialize_agents_from_dynamodb()` reload. Fixing it is one call to
  `is_a_real_credential` in that handler.
- Defence in depth below the router was not added. `admin_db_service.create_agent`
  (`src/services/admin_db_service.py:437`) copies `snowflakeConfig` through verbatim, so on DynamoDB
  the router is the only barrier; on PostgreSQL `PgAgentRepository._store_credential` independently
  declines to vault a mask. Anything that calls the service directly (a script, a future route)
  bypasses the new guard on DynamoDB.
- `tests/db/test_backend_selection.py` still fails 5 (`ModuleNotFoundError: psycopg`), untouched and
  unchanged in count.

## Commands run

All from `services/agent-service` with `.venv/bin/python`.

1. `-m pytest tests/ --ignore=tests/pg -p no:warnings --tb=no`
   → **5 failed, 1358 passed** (the 5 are the known `tests/db/test_backend_selection.py` psycopg
   failures; count did not grow).
2. `-m pytest tests/security -p no:warnings --tb=no` → **474 passed**.
3. `PGTEST_REQUIRED=1 PYTHONPATH=<scratch>/pgoverlay -m pytest tests/pg -p no:warnings --tb=short`
   → **304 passed** against the real `aihub-pg-test` container (127.0.0.1:55432).
   Schema verified at head first: `ops.alembic_version` = `0003_agent_guest_access` (matches the
   newest file in `services/db-schema/migrations/versions/`), and
   `catalog.agents.guest_access_enabled` present (count 1).
   The venv has no `psycopg`/`psycopg_pool` and no pip, so both were installed with
   `pip install --target` into a scratch directory and put on `PYTHONPATH` for that run only. The
   venv itself is unchanged — verification 1 was re-run afterwards and the failure count is still 5.
4. `-m ruff check src tests` → All checks passed.
   `-m black src tests` → reformatted the new test file (call wrapping only), then
   `--check` → 185 files unchanged.
   `-m isort --check-only src tests` → clean.

## Issues discovered

- **`create_teams_channel` writes a Teams channel into the agents repository.**
  `src/services/admin_db_service.py:705` — `agent_repository.put(AgentItem.model_validate(item))`
  inside `create_teams_channel`, where every neighbouring channel helper uses
  `self.get_table(settings.AWS_DYNAMODB_TABLE_TEAMS_CHANNELS)`. Every `POST /admin/teams-channels`
  therefore inserts a row shaped `{id: "teams-…", name, webhookUrl}` into the **agents** table/
  `catalog.agents`. No credential hole (the item carries no `snowflakeConfig`, so it defaults to
  `{}`), which is why this leg did not fix it — but it is table pollution on a live write path and,
  on PostgreSQL, `AgentItem` → `catalog.agents` means a bogus agent row with a `teams-` slug. Also
  note `create_teams_channel` returns `await self.get_teams_channel(channel_id)`, which reads the
  Teams table the write never reached, so the endpoint's response is likely already wrong. Worth its
  own leg.
- **`POST /api/v1/agents/register` mask hole** — see "Left undone".
- The PG create path's behaviour on a mask, before this fix, was *silent partial success*: the row was
  inserted, `_store_credential` returned `None`, and `pat_secret_arn`/`pat_secret_alias` were simply
  omitted — a created agent with no credential and no error anywhere. That is why a router-level
  reject, not a repository-level one, is the right place for the create rule.
- `tests/pg` reports a *missing driver* as an unreachable database only when `PGTEST_REQUIRED` is
  unset; with it set the run produced 285 errors that looked like schema failures but were the
  missing `psycopg_pool`. Confirming the driver imports before trusting a `tests/pg` result is worth
  adding to the test-suite skill.

## Procedure followed?

Yes, with these specifics:

- Mutation-checked every assertion group. Each mutation was applied to a restored-from-backup copy of
  the file, the suite run, then the file restored and re-verified green (59 passed).
  - **M1 — delete the mask guard** (revert create to the presence-only check):
    **27 failed** — exactly the three mask assertion groups × 9 masks. Blank/missing-field tests still
    passed, confirming they test the pre-existing rule and not mine.
  - **M2 — invert the guard** (`if is_a_real_credential(...)`, i.e. reject real values):
    **31 failed** — the 27 above plus all 4 "a real credential still creates an agent" tests.
  - **M3 — make update reject like create** (call the validator in `update_agent` too):
    **20 failed**, all in `TestTheUpdatePathStillPreserves`, `tests/unit/test_credential_survives_an_edit.py`
    unaffected. This is the assertion that the create/update asymmetry is actually load-bearing.
  - **M4 — replace the error message with `"invalid snowflake config"`**:
    **9 failed** — the message/field-naming assertions only.
  - **M5 — neuter the shared predicate** (`is_a_real_credential` returns `True` always):
    **45 failed**. Proves the create guard really routes through the shared predicate rather than any
    local logic.
- No existing test was weakened, skipped, or deleted; no existing test contradicted the requirement.
- Zero comments and zero docstrings in the code and tests added.
- No live environment touched: no `oc`/`kubectl`/`aws`, no real hosts. `tests/pg` ran against the
  local throwaway `aihub-pg-test` container; the vault is stubbed by the existing fixtures.
- Nothing pushed. Commit is local on `feat/wave2-cutover-reconciled`.

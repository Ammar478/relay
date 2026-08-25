# Running the agent-service test suite

## Do not pass `-q`

`services/agent-service/pytest.ini` already sets `addopts = -q`. Passing `-q` again double-quiets and
**suppresses the `N failed, M passed` summary line**, so a runner cannot report counts.

Use:

```
.venv/bin/python -m pytest tests/ --ignore=tests/pg -p no:warnings --tb=no
```

Run it from `services/agent-service`, with `.venv/bin/python`, never a bare `pytest`.

## The 5 failures that are not yours

`tests/db/test_backend_selection.py` fails 5 tests with `ModuleNotFoundError: No module named 'psycopg'`.
The local venv lacks the driver and has no `pip`. These are pre-existing and identical on a clean
`develop` worktree. **Do not fix them, do not skip them, do not let the count grow past 5.**

## `tests/pg` is opt-in and skips silently

`testpaths` in `pytest.ini` does not include `tests/pg`, and its `conftest.py` skips when no database is
reachable. A green run therefore proves nothing about the PostgreSQL path unless you pass the path
explicitly AND set `PGTEST_REQUIRED=1`:

```
PGTEST_REQUIRED=1 .venv/bin/python -m pytest tests/pg -p no:warnings --tb=short
```

To bring a database up: `postgres:16` on port 55432, apply `services/db-schema/sql/0000_bootstrap_roles.sql`,
then alembic to **head** — not `sql/0001`, because `guest_access_enabled` only exists from revision `0003`.

## Correct paths (three briefings have got these wrong)

| Thing | Real path |
|---|---|
| wave-2 copy script | `services/db-schema/copy_wave2.py` — under **db-schema**, not agent-service |
| PAT resolver | `services/agent-service/src/vault/client.py` — `src/vault/`, not `src/services/vault/` |
| schema baseline | `services/db-schema/sql/0001_initial_schema.sql` |
| alembic revisions | `services/db-schema/migrations/versions/` |
| alembic version table | `ops.alembic_version` — **not** `public`, so a default-search-path query reports it missing |

A path-scoped `find` under `services/agent-service` will falsely report `copy_wave2.py` as missing.

## services/db-schema tests

They are outside agent-service's `testpaths` and the agent-service venv has no `psycopg`, so a normal
local run executes none of them. CI does: the `db-schema-test` job runs `pytest tests/` with
`PGTEST_REQUIRED=1` against a real `postgres:16-alpine`, after a full upgrade / idempotency /
downgrade / upgrade cycle. To run them locally, build a throwaway venv from
`services/db-schema/requirements-dev.txt` rather than reusing the agent-service one.

## Verify the test database is at alembic HEAD before trusting tests/pg

The `aihub-pg-test` container was found mid-relay to have no `ops.alembic_version` row at all — it had
been built from raw `sql/0001` and was missing `guest_access_enabled`. Any leg that ran `tests/pg`
against it was testing the wrong schema and would not know.

Check before you trust a green run:

```
psql ... -tAc "SELECT version_num FROM ops.alembic_version"   # must be the current head
psql ... -tAc "SELECT count(*) FROM information_schema.columns
               WHERE table_schema='catalog' AND table_name='agents'
                 AND column_name='guest_access_enabled'"       # must be 1
```

If it is not at head, rebuild from empty and run `alembic upgrade head`.

`conftest._reachable` catches bare `Exception`, so a **missing psycopg driver** is reported as an
unreachable database. The venv has no driver and no pip: use a `pip install --target` overlay on
`PYTHONPATH` rather than installing into the venv, so the 5 known failures stay exactly 5.

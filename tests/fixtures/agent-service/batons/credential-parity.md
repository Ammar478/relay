# Baton — credential-parity

## Implemented

**The defect was reproduced first, against a real PostgreSQL at alembic head.** Before any edit,
`services/agent-service/src/db/pg_repositories.py::PgAgentRepository` wrote `'********'`, `'••••••'`
and `'***'` to the (stubbed) vault as the PAT, both through `update_fields` directly and through
`admin_db_service.update_agent`. Verbatim output of the reproduction script:

```
after create, vault calls: [('a1', 'pat-real-abc123')]
pointers: ('arn:aws:secretsmanager:...:aihub/test/agent-pat/a1-AbCdEf', 'a1')
mask '********' -> vault calls [('a1', '********')]
mask '••••••' -> vault calls [('a1', '••••••')]
mask '***'    -> vault calls [('a1', '***')]
through update_agent, vault calls: [('a1', '********')]
```

After the fix the same script prints `vault calls []` for all four.

### 1. One shared predicate — `services/agent-service/src/utils/credential_value.py`

New leaf module exporting `MASK_CHARACTERS` and `is_a_real_credential(value) -> bool`. It is the
rule that used to live inline in `admin_db_service._is_a_real_credential`, moved verbatim.

**Why there, and not somewhere else.** The two call sites sit on opposite sides of the layering:
`src/services/admin_db_service.py` already imports `src.db.repositories`, so the rule cannot live in
`src/db/` without `src.db` becoming an importer of `src.services` or the rule being reachable only by
a late import. `src/utils/` is the existing home for leaf helpers (`logger`, `pagination`,
`timezone_utils`), imports nothing from `src.db`, `src.services` or `src.vault`, and so can be
imported at module scope from both sides with no cycle. It is also importable without `psycopg`
installed, which matters because `tests/unit` runs in a venv that has no driver.

Both call sites now bind the same function object; `tests/unit/test_one_credential_rule.py` asserts
that identity (`pg_repositories.is_a_real_credential is is_a_real_credential`), so a future
re-inlined copy fails a test rather than drifting silently — that drift is what caused this defect.

### 2. `admin_db_service.update_agent` — the guard was inert under PostgreSQL

`_is_a_real_credential` now delegates to the shared function (its docstring is unchanged and still
accurate).

The guard condition changed from `if stored_pat and not ...` to `if "cortex_agent_pat" in value and
not is_a_real_credential(incoming)`. The old condition could never fire on the PostgreSQL path:
`existing_config` comes from `PgAgentRepository.get_or_none`, which builds `snowflakeConfig` from
`provider_config`, and `agents_provider_config_no_secret_ck` guarantees that column holds no PAT — so
`stored_pat` was always `None`. The new condition keys off what the *client sent*, which is the same
signal on both backends.

The outcome branches:
- a real stored credential exists → restore it into the merged config (previous behaviour);
- nothing real is stored → **drop the key entirely** rather than write the junk. On DynamoDB that
  stops a mask being stored as the credential of an agent that had none; on PostgreSQL the key never
  reaches `_store_credential`, so no vault call is made and the pointer columns are untouched.

The warning log lost the clause "kept the stored one", which was no longer true in the drop branch,
and gained a structured `kept_the_stored_one` field instead. Nothing asserts on that message.

### 3. `PgAgentRepository._store_credential` — masks reached Secrets Manager

```python
pat = (config or {}).get(self._CREDENTIAL_KEY)
if pat is None:
    return None
if isinstance(pat, str) and not is_a_real_credential(pat):
    return None
pat = str(pat)
```

A string the shared rule rejects (blank, whitespace, mask-only) returns `None`, so no vault call
happens and no pointer column is assigned — which is exactly "preserve", not "raise", as the leg
requires. `put` preserves the existing pointers through the existing `COALESCE` in the upsert;
`update_fields` simply assigns nothing.

### 4. `agents_pat_secret_pair_ck` — pointers can only move together

New `PgAgentRepository._pointer_columns(agent_id, secret_ref)` returns `{}` when there is no
reference and both columns when there is. `put` and `update_fields` both go through it, so neither
can write one column without the other. Previously `put` built the pair inline
(`secret_ref, agent.id if secret_ref else None`) and `update_fields` built it again in a second
place — one edit away from a half-written pair.

### 5. Tests

- `services/agent-service/tests/pg/test_credential_parity.py` (new, 139 tests) — real PostgreSQL,
  stubbed vault, covers ACC-CRED-001…006. The DynamoDB half of the parity table runs against **moto**
  with the real `AgentRepository`, not a hand-written double, so both halves exercise shipped code.
- `services/agent-service/tests/unit/test_one_credential_rule.py` (new, 26 tests) — the shared rule
  itself, the "one predicate, two call sites" identity assertions, and `_store_credential`'s agreement
  with the rule on string inputs. No database needed, so it runs in the default suite.

## Left undone

- **Non-string values are the one input class where the two repositories still differ, and I left it
  that way deliberately.** `is_a_real_credential(12345)` is `False`, so `admin_db_service` refuses to
  let `12345` replace a stored credential on either backend. But `PgAgentRepository._store_credential`
  still coerces a non-string to `str` and stores it (`isinstance(pat, str) and ...` — non-strings skip
  the rule). That is not drift so much as a different question being asked at a different layer:
  DynamoDB's `AgentRepository.put` also stores a numeric `cortex_agent_pat` as-is, so both repositories
  *retain* it; neither loses it. An existing test pins this —
  `tests/pg/test_pg_agent_writes.py::TestANonStringCredentialIsNotSilentlyLost::test_a_numeric_credential_is_stored_rather_than_dropped`
  — and making `_store_credential` apply the rule to non-strings would break it. Per the leg's
  constraint I did not weaken it. **This is reported, not hidden**: if the contract is meant to say
  "non-strings are not credentials anywhere", that existing test contradicts it and someone with the
  authority to change it should. The admin HTTP path is not exposed either way: `routers/admin.py`
  create rejects a non-string `cortex_agent_pat` with 400, and `update_agent` now strips it.
- `mypy` was not run — it is not in the leg's verification list and the pre-existing
  `mypy_output.txt` shows the service is far from clean.
- `services/db-schema/tests/` were not run. They need their own venv (the agent-service venv has no
  `psycopg` in it) and nothing under `services/db-schema/` was touched by this leg. The adversarial
  key set was *copied* from `services/db-schema/tests/test_copy_wave2.py` (the `CASES` table, every
  entry the database rejects) into the new pg test, as the leg asked.

## Commands run

Environment setup (all local containers and throwaway venvs; nothing touched a live environment, no
`oc`/`kubectl`/`aws`, and the vault is stubbed in every test):

```
# a throwaway venv for alembic only — services/db-schema/requirements.txt
uv venv $SCRATCH/dbvenv --python 3.11
uv pip install --python $SCRATCH/dbvenv/bin/python -r services/db-schema/requirements.txt

# the previous aihub-pg-test container was at raw-SQL 0001 with NO ops.alembic_version
# row and no guest_access_enabled column. Rebuilt it from empty, to head.
docker rm -f aihub-pg-test
docker run -d --name aihub-pg-test -e POSTGRES_PASSWORD=testpass -e POSTGRES_DB=aihub \
    -p 55432:5432 postgres:16
PGHOST=127.0.0.1 PGPORT=55432 PGDATABASE=aihub PGUSER=postgres PGPASSWORD=testpass \
  MIGRATOR_PASSWORD=migratorpass AGENT_SERVICE_DB_PASSWORD=agentpass \
  $SCRATCH/dbvenv/bin/python services/db-schema/checks.py bootstrap
PGHOST=127.0.0.1 PGPORT=55432 PGDATABASE=aihub PGUSER=aihub_migrator \
  PGPASSWORD=migratorpass $SCRATCH/dbvenv/bin/alembic upgrade head
#   -> 0001_initial_schema -> 0002_pat_alias_case -> 0003_agent_guest_access
#   ops.alembic_version = 0003_agent_guest_access, guest_access_enabled present

# psycopg for the agent-service venv, as a PYTHONPATH overlay rather than an install,
# so the venv stays byte-identical and the 5 known failures stay exactly 5
uv pip install --python .venv/bin/python --target $SCRATCH/pgdeps psycopg[binary]==3.2.9
uv pip install --python .venv/bin/python --target $SCRATCH/pgdeps --no-deps psycopg-pool==3.2.6
```

Verification, from `services/agent-service`:

```
PYTHONPATH=$SCRATCH/pgdeps PGTEST_REQUIRED=1 .venv/bin/python -m pytest tests/pg \
    -p no:warnings --tb=short
        304 passed          (baseline before this leg on the same database: 165 passed)

.venv/bin/python -m pytest tests/ --ignore=tests/pg -p no:warnings --tb=no
        5 failed, 1299 passed
        the 5 are tests/db/test_backend_selection.py, ModuleNotFoundError: psycopg.
        Pre-existing, unchanged, not fixed, count did not grow.

.venv/bin/python -m pytest tests/security -p no:warnings --tb=no
        474 passed

.venv/bin/ruff check src tests          All checks passed!
.venv/bin/black --check src tests       184 files would be left unchanged
.venv/bin/isort --check-only src tests  clean
```

`tests/pg` cannot run at all without the overlay: the venv has no `psycopg`, `conftest._reachable`
swallows the ImportError, and `PGTEST_REQUIRED=1` then reports "no PostgreSQL reachable" — i.e. a
missing driver is indistinguishable from a missing database. The next runner needs
`PYTHONPATH=<overlay>` or a venv with `psycopg[binary]` + `psycopg-pool` installed.

## Mutation results

Every mutation was applied to the source, the suites re-run, then the source restored from a
byte-for-byte backup. Suites run for each:
`tests/pg/test_credential_parity.py tests/unit/test_one_credential_rule.py
tests/unit/test_credential_survives_an_edit.py tests/pg/test_pg_agent_writes.py` (317 tests green
unmutated).

| # | Mutation | Result | Caught by |
|---|---|---|---|
| M1 | `_store_credential` back to `if pat is None or (isinstance(pat,str) and not pat.strip())` — the original defect | **28 failed** | ACC-CRED-003 (all four mask classes), `test_a_string_the_rule_rejects_never_reaches_the_vault` |
| M2 | `update_agent` guard back to `if stored_pat and not ...` — the inert guard | **10 failed** | `TestAccCred004ANonCredentialIsRejectedEvenWithNothingToPreserve::test_dynamodb_does_not_store_it`, `::test_the_two_agree_that_nothing_was_stored` |
| M3 | shared predicate `return True` (mask rule deleted) | **59 failed** | the whole rule table, plus the pre-existing `test_credential_survives_an_edit.py` |
| M4 | `_pointer_columns` returns only `pat_secret_arn` | **64 failed** | ACC-CRED-006 + `agents_pat_secret_pair_ck` CheckViolation on the write |
| M5 | `_store_credential` uses a local `not pat.strip()` instead of the shared rule | **28 failed** | ACC-CRED-003, `test_a_string_the_rule_rejects_never_reaches_the_vault` |
| M6 | `update_fields` stops assigning the pointer columns | **3 failed** | `test_a_rotation_reaches_the_vault_and_repoints_the_row`, `test_adding_a_credential_to_an_agent_that_had_none_sets_both_pointers`, plus the pre-existing `TestUpdateFields::test_updating_the_config_routes_the_pat_to_the_vault` |

**Two mutations survived the first round and the tests were rewritten because of it.** This is the
part worth reading:

- **M2 survived.** The parity table alone could not see the inert guard, because on the PostgreSQL
  path the repository-level guard (fix 3) catches the mask first and the outcome looks identical. The
  guard only shows up when there is *nothing to preserve* — an agent with no credential yet, where the
  old condition wrote the mask straight into DynamoDB. Added
  `TestAccCred004ANonCredentialIsRejectedEvenWithNothingToPreserve`, three tests × five inputs, which
  fails on M2.
- **M6 survived.** `test_a_rotation_...` asserted only `arn.startswith("arn:aws:secretsmanager:")`,
  and since the row already carried an ARN of that shape from the seed, dropping the re-assignment
  changed nothing observable. Fixed by making `StubVault` mint a distinct ARN per call
  (`-v1AbCdEf`, `-v2AbCdEf`, …) and asserting the ARN actually *changed*, plus a new test that adds a
  credential to an agent that had none. Both now fail on M6 — and as a side effect the
  "pointer columns unchanged" assertions in ACC-CRED-002/003 became meaningful rather than vacuous,
  since a spurious vault call would now produce a visibly different ARN.

No assertion I wrote survives its own mutation. Nothing was deleted or weakened to make something
pass.

## Issues discovered

1. **The `aihub-pg-test` container the previous leg left running was not at alembic head, and had no
   `ops.alembic_version` row at all** — its schema had been applied from raw
   `sql/0001_initial_schema.sql`, so `guest_access_enabled` (revision `0003`) was missing. Any leg
   that ran `tests/pg` against it was testing a schema the service does not target. Rebuilt from
   empty via `checks.py bootstrap` + `alembic upgrade head`; it is now at `0003_agent_guest_access`.
2. **`tests/pg` reports "no PostgreSQL reachable" when the real problem is a missing driver.**
   `conftest._reachable` wraps `import psycopg` in the same `try/except Exception` as the connection
   attempt, so `PGTEST_REQUIRED=1` raises a message that sends the reader to look at the database.
   That cost time here and will cost the next runner the same. Not fixed — it is outside this leg and
   changing the skip logic mid-relay seemed worse than reporting it.
3. **The DynamoDB `create_agent` path has no equivalent of this guard.** `routers/admin.py` validates
   that `cortex_agent_pat` is a non-blank string, but a *mask* passes that check, so creating an agent
   with `cortex_agent_pat: "********"` yields an active agent whose stored credential is the mask.
   On PostgreSQL the repository now refuses to store it (the agent is created with null pointers, which
   is at least honest and visible); on DynamoDB the mask is written into the item. Out of scope for a
   leg about *preserving* a credential, but it is the same class of bug on the create path and nothing
   currently catches it.
4. **`_is_a_real_credential` remains on `AdminDBService` as a one-line delegate.** It is kept because
   `tests/unit/test_credential_survives_an_edit.py::test_the_helper_agrees` calls it directly, and the
   leg forbids weakening existing tests. It is now a thin alias; a future leg could inline the callers
   and delete it together with that assertion.
5. See "Left undone" for the non-string divergence and the existing test that pins it. That is the one
   place where this leg did not achieve literal ACC-CRED-004 parity at the repository layer, and I
   would rather it be argued about than quietly resolved by editing someone else's test.

## Procedure followed?

Yes, with the deviations below stated plainly.

- Read `.relay/skills/running-the-test-suite.md` first. Did not pass `-q`. Did not touch the 5
  `tests/db/test_backend_selection.py` failures; the count is still exactly 5. Ran from
  `services/agent-service` with `.venv/bin/python` throughout.
- Reproduced the defect before fixing it, with the reproduction output recorded above.
- One shared predicate, not two copies, with an identity test to keep it that way.
- Mask → preserve, never raise. Blank → preserve. Verified for both.
- Real PostgreSQL at alembic head for every storage-level assertion; only the vault is stubbed.
- Zero comments and zero docstrings in the code I wrote, with two exceptions I am flagging rather than
  hiding: (a) the pre-existing docstring on `AdminDBService._is_a_real_credential` was left in place
  rather than deleted, since removing existing documentation was not the assignment; (b) the new test
  files carry no docstrings, so the acceptance-check ids live in the class names
  (`TestAccCred001…`, `TestAccCred006…`).
- No workarounds, no bypass flags, no hardcoding. The `psycopg` overlay is an environment change made
  outside the repository (a `--target` directory on `PYTHONPATH`), chosen specifically so the venv is
  not mutated and the known-failure count cannot move.
- No live environment touched. No `oc`, `kubectl`, `aws`, or request to a real host. The only network
  access was `uv pip install` and `docker pull postgres:16`.
- No `.md` files or scratchpads committed; `.relay/` is git-excluded (`git check-ignore` confirmed).
- Committed on `feat/wave2-cutover-reconciled`. Nothing pushed anywhere.

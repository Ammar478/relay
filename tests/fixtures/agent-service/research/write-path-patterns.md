# Research: PostgreSQL write-path patterns and the wave-2 contract

## Helpers

`src/db/postgres.py:74-93` — `query(sql, params) -> list[dict]`, `execute(sql, params) -> int` (rowcount).
Each opens its own pooled connection and **autocommits per call**. There is no transaction
spanning two `execute` calls.

`src/db/uuid7.py:42-68` — `uuid7()` returns a `uuid.UUID`, passed to psycopg directly.
Every table has `CHECK (ops.is_uuid_v7(id))` and the schema supplies **no default**.

## Reference implementation: PgGroupRepository (wave 1, in production)

`put` (`pg_repositories.py:88-119`): caller's DynamoDB id becomes `legacy_id`; a fresh
`uuid7()` is minted each call and discarded by the DO UPDATE branch; `ON CONFLICT (legacy_id)`;
NOT NULL / CHECK columns are normalised **in Python**, not left to the constraint; returns the
input model without re-reading.

`update_fields` (`:121-140`): an explicit **allow-list dict** maps DynamoDB attribute to column.
Unmapped keys (including `updatedAt`) are dropped. Column names are f-string interpolated and
that is safe **only** because they come from the allow-list; values always travel as params.
A value that would violate a CHECK is dropped rather than sent. `updated_at = now()` always
appended. No matching row is a silent no-op.

`delete` (`:142-150`): `DELETE ... WHERE legacy_id = %s`. Missing row silent. FK cascade removes
memberships.

## Contract to match (DynamoDB, `src/db/base_repository.py`)

| Method | Semantics |
|---|---|
| `put(model) -> TModel` | Unconditional overwrite. Returns the model passed in. |
| `update_fields(*key_values, updates: dict) -> None` | `updates` is **keyword-only**. Raises `ValueError` on empty. No condition → updating a missing id **creates a stub**. |
| `delete(*key_values) -> None` | Silent on missing. |

Divergences wave 1 deliberately accepted, and wave 2 should match: empty updates is a silent
no-op (not `ValueError`); update on a missing row affects 0 rows (no stub). Callers guard both.

## Call sites — all in `src/services/admin_db_service.py`, none in tests

| Line | Caller | Notes |
|---|---|---|
| `:476` | `create_agent` | Return discarded. Model carries **arbitrary extras** (`extra="allow"`, `models.py:22`) — read extras off the model, not just declared fields. |
| `:564` | `update_agent` | `updates` is camelCase **plus `updatedAt`**. Called keyword-style. `snowflakeConfig` is already merged and **deliberately re-injects the stored PAT** at `:540-541`. |
| `:590` | `delete_agent` | Mappings deleted first (redundant under cascade). |
| `:622` | `_create_agent_group_mapping` | Read-then-write duplicate check; comment says the race is closed by "the PostgreSQL unique constraint". |
| `:260` | `_create_user_group_mapping` | **No duplicate pre-check at all.** |

No caller catches a specific exception type. An exception here propagates to a bare `except`
and can become a *wrong answer* rather than an error.

## THE CENTRAL TRAP — ON CONFLICT target on the mappings

Both mapping callers mint `str(uuid.uuid4())` as the item id **on every call**, so `legacy_id`
is always new and `ON CONFLICT (legacy_id)` **can never fire**. The conflict that actually
happens is on the pair:

| Table | Unique constraints | Correct ON CONFLICT target |
|---|---|---|
| `catalog.agents` | `legacy_id`; **`slug` separately** | `(legacy_id)` — a slug clash still raises |
| `catalog.agent_groups` | `legacy_id`; `agent_groups_agent_group_key (agent_id, group_id)` | **`(agent_id, group_id)`** |
| `iam.user_groups` | `legacy_id`; `user_groups_user_group_key (user_id, group_id)` | **`(user_id, group_id)`** |

`copy_wave2.py:377-394` targets `(legacy_id)` — correct for a one-shot migration replaying
stable ids, **wrong to copy into the runtime put**. Under DynamoDB a duplicate pair silently
produced a second row; under PostgreSQL it raises `UniqueViolation` unless the pair is targeted.

Mapping inserts must resolve FKs **in-statement**, not with a separate SELECT:

```sql
INSERT INTO iam.user_groups (id, legacy_id, user_id, group_id, role)
SELECT %s, %s, u.id, g.id, %s::iam.group_role
FROM iam.users u, iam.groups g
WHERE u.legacy_id = %s AND g.legacy_id = %s
ON CONFLICT (user_id, group_id) DO UPDATE SET role = EXCLUDED.role, updated_at = now()
```

`rowcount == 0` means a dangling reference. DynamoDB accepted these silently. `agent_repository`
has **no INSERT grant on `iam.users`**, so a missing user cannot be created here.
`role` must be cast `%s::iam.group_role`; values are exactly `('user','agent_manager')`.
Normalise case first — a stored `"Agent_Manager"` was silently dropped once (commit `b22939e`).

**`legacy_id` must always be written non-NULL.** `PgAgentGroupMappingRepository._to_item` reads
`row["legacy_id"]` with no fallback → a NULL raises `ValidationError` on every `list_for_agent`.
`PgUserGroupMappingRepository.delete_all` matches `WHERE legacy_id = ANY(%s)`, so a NULL-legacy_id
row is **un-deletable**.

## catalog.agents constraints that will bite

- `slug NOT NULL`, `~ '^[a-z0-9]+(-[a-z0-9]+)*$'`, **plus a separate UNIQUE index**. `AgentItem`
  has no slug field — derive it, mirroring `copy_wave2.py:143-151 slugify(name, fallback=legacy_id)`.
- `provider NOT NULL` slug-shaped. `AgentItem.agentType` defaults to **`"Snowflake"`**, which
  **fails the regex**. `copy_wave2.py:194-199` lowercases with fallback `"snowflake"`.
- `name` non-empty; `description NOT NULL` (naming the column bypasses the default — pass `''`).
- `priority >= 0`; `is_active` is `int | bool` in the model and callers pass `1`/`0` — coerce.
- `agents_provider_config_no_secret_ck` **and** `agents_instructions_no_secret_ck`.
- `agents_pat_secret_pair_ck` — both null or both set, so a half-COALESCE is a violation.
- `guest_access_enabled boolean NOT NULL DEFAULT false` — from migration `0003`, **not** in `sql/0001`.
- `instructions` jsonb packs five attributes; `provider_config` ← `snowflakeConfig`.

## COALESCE on the PAT pointer

`AGENT_UPSERT` uses `pat_secret_arn = COALESCE(EXCLUDED.pat_secret_arn, catalog.agents.pat_secret_arn)`
so a re-run cannot null an already-migrated ARN. `admin_db_service.create_agent` **never populates**
`pat_secret_arn`, so an unguarded `SET pat_secret_arn = EXCLUDED.pat_secret_arn` on upsert
**destroys the agent's credential reference**.

## Read path is not closed

`PgAgentRepository._select()` does not select `guest_access_enabled` and `_to_item` never sets
`guestAccessEnabled`, so it always falls back to the model default `False`. `entitlements.py:58`
gates guest access on exactly that field. Under the wave-2 PG backend **guest access is silently
off for every agent**. The write path and the read path must be fixed in the same change.

## Tests

`tests/pg/` has `conftest.py` and `test_pg_wave1.py`. **No wave-2 test exists.**

`conftest.py` `pg` fixture skips when no database is reachable. Two identities on purpose: the
repo connects as `aihub_agent_service`, setup/teardown uses admin as `postgres` because the
service role has no INSERT on `iam.users`. **`clean()` deletes only `iam.user_groups`, `iam.users`,
`iam.groups`** — wave 2 must extend it with `catalog.agent_groups` and `catalog.agents`.

Patterns to mirror from `test_pg_wave1.py`: put-twice-assert-count-1; DynamoDB id stored as
`legacy_id`; generated id is uuid **v7** (`bytes[6] >> 4 == 7`); update_fields sets only mapped
columns; unmapped key dropped not interpolated; a value containing SQL stored literally
(**injection pins are mandatory**); deleting an absent row is silent.

Bring a database up: `postgres:16` on port 55432, `sql/0000_bootstrap_roles.sql`, then alembic to
**head** (head, not `sql/0001` — `guest_access_enabled` comes from `0003`).

## CI gap

**`tests/pg` never ran in CI for agent-service.** auth-service got a `pg-integration-test` job
plus `PGTEST_REQUIRED=1`; agent-service has neither, so new wave-2 tests **will skip silently in
CI** unless a job and the required-flag are added.

## Open decisions (flagged, not guessed)

1. Slug collision policy for the runtime `put` — `copy_wave2` hard-blocks; nothing specifies what
   the admin API should do.
2. Whether a runtime write routes a new agent's `cortex_agent_pat` into Secrets Manager (as
   `migrate_pat` does at migration time) or simply strips it. `update_agent` actively *preserves*
   the PAT in the dict handed to `update_fields`, so stripping is a silent behaviour change.
3. `create_teams_channel:683` writes a Teams channel into the **agents** table via
   `agent_repository.put`. Under PostgreSQL it has no `agentType` and will hit `agents_provider_ck`
   / `agents_slug_ck`. Flagged for decision, not designed around.

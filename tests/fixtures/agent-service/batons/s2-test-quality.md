# Leg: s2-test-quality

Commit `55732a4` on `feat/wave2-cutover-reconciled` (parent `73c022c`).
Tests only. `git diff --stat` shows six test files plus one new test file and **no `src/` change**.

## Implemented

### 1. `/register` mask guard — was zero coverage, now 50 tests

- `services/agent-service/tests/unit/test_register_rejects_a_mask.py` (new, 50 tests). Drives the
  real coroutine `src.routers.agents.register_agent` with a stubbed
  `agent_registry.register_agent`, so the guard at `src/routers/agents.py:190` is the thing under
  test, not a reimplementation of it.
  - 9 mask forms (`*`, `***`, `********`, `  ********  `, `••••••`, `······`, `●●●●●●`, `∙∙∙∙∙∙`,
    `*•·●∙`) × three assertions each: status is 400, the detail names `cortex_agent_pat`, and
    nothing reaches the registry.
  - 4 blank forms (`""`, `"   "`, `"\t"`, `"\n  \t "`) × the same three.
  - 4 real credentials (plain, padded, one containing mask characters `pat*with*stars*inside`,
    228 chars) × two assertions: the call succeeds and the credential reaches the registry
    byte-identical.
  - The registry receives the whole agent (id, name, `queue_name`), not just the credential.
  - Ordering: a mask is refused even when the registry would itself have refused, and a registry
    refusal of a *real* credential produces a different message — so the two 400s cannot be
    confused for one another.
- `services/agent-service/tests/unit/test_one_credential_rule.py` — added the third call-site
  identity assertion, `agents_router.is_a_real_credential is is_a_real_credential`, alongside the
  existing two. Class renamed `TestBothCallSitesShareOneRule` → `TestEveryCallSiteSharesOneRule`
  because there are now three.

### 2. `ACC-WRITE-011` on `catalog.agent_groups`

- `services/agent-service/tests/pg/test_pg_mapping_puts.py`, class
  `TestEveryRowPutWritesIsDeletable` — four agent-side cases mirroring the user-side ones that
  already existed:
  - a blank caller id is replaced rather than stored, and the row is deletable by what `put`
    returned;
  - a blank-id put leaves no `legacy_id IS NULL` row behind;
  - the row is deletable by what the **read path** returns (`list_for_agent`), not only by the put
    return value;
  - two blank caller ids on two different agents do not collide on the unique `legacy_id`.

### 3. `ACC-WRITE-013` can now tell normalising from hardcoding

- `services/agent-service/tests/pg/test_pg_agent_writes.py`, new class
  `TestAccWrite013AProviderThatIsNotSnowflakeIsCarriedThroughNotRewritten` (9 tests). Every
  `agentType` in `tests/pg` was previously a casing of "snowflake"; these are not:
  - `agentType="OpenAI"` → stored `provider == "openai"`, and reads back as `"openai"`;
  - `"  OPENAI  "` → `"openai"` (trim applies to a non-default provider too);
  - `"Azure-OpenAI"` → `"azure-openai"` (the hyphen branch of the slug regex is real);
  - update off snowflake (`Snowflake` → `OpenAI`) and back on again;
  - two agents in one table do not share one provider;
  - the two fallbacks kept honest: `"Open AI!!"` (not a slug) and `None` both land on
    `"snowflake"`.

### 4. Five tests that passed against broken code

- `tests/security/test_chat_thread_ownership.py:359`
  `test_a_thread_refusal_body_carries_none_of_the_other_users_content` — added
  `status_code == 404`, `inference.calls == []` and `inference.threads == []` above the existing
  `BOB_SECRET not in response.text`.
- `tests/security/test_chat_session_ownership.py:302`
  `test_a_foreign_session_refusal_body_carries_none_of_the_other_users_content` — added
  `status_code == 404` and `dispatched_messages == []`, following its sibling at `:276`.
- `tests/security/test_chat_session_ownership.py:379` — was named
  `test_a_legacy_session_stamped_with_the_placeholder_owner_is_refused` but exercised only a plain
  owner mismatch (session owner `"user"` vs caller `"user-alice"`), which the ordinary S1 rule
  already refuses; the placeholder was decoration. Now
  `test_a_placeholder_owned_session_is_refused_even_to_the_placeholder_caller`, parametrized over
  callers `[ALICE, PLACEHOLDER_PRINCIPAL]` (8 params). The `PLACEHOLDER_PRINCIPAL` param is the one
  the name was always claiming: caller id `"user"` **equals** the stored owner `"user"`, so only
  `PLACEHOLDER_OWNER_IDS` in `require_caller_owns_session` stops it. Also asserts the refusal body
  carries none of the other user's content.
- `tests/pg/test_pg_agent_read_parity.py:120` — `assert guestAccessEnabled is False` replaced by
  four tests that separate the three sources of that `False`:
  - the column holds a written `False`, not `NULL` (`row["guest_access_enabled"] is False`);
  - putting *without* the flag over an already-enabled agent turns it off — kills "the write never
    mentions the column and the column default supplies the False";
  - a natively-flipped column (`UPDATE ... SET guest_access_enabled = TRUE`) is what the read
    returns — kills "the reader ignores the column and the model default supplies the False";
  - two agents in one `list_all` read back `{a1: True, a2: False}` — the enabled and disabled cases
    are distinguishable in a single read.
- `tests/pg/test_pg_agent_read_parity.py:87` `test_the_queue_reaches_the_agent_config` — renamed to
  `test_the_converter_maps_the_queue_when_it_is_handed_the_whole_row`, which is what it actually
  proves. Two new tests drive the **real** boot path
  (`src.config.agents.initialize_agents_from_dynamodb` against the PostgreSQL repository, a fresh
  `AgentRegistry` and a stubbed `_get_agent_group_ids`):
  - `test_the_boot_path_at_least_reaches_the_registry_with_the_agent` — passes; the wiring is real
    and the agent does arrive.
  - `test_the_boot_path_carries_the_stored_queue_into_the_registry` — `xfail(strict=True)`. It
    asserts the behaviour that *should* hold and currently does not, because
    `src/config/agents.py:72-95` rebuilds the row without the eight tuning settings. Strict xfail
    means the day `src/` is fixed the test turns red as an XPASS and someone must delete the
    marker; it does not certify the bug as correct, and it does not require a `src/` change from
    this leg.

## Left undone

- **`src/config/agents.py:72-95` still drops all eight tuning settings** on the real boot path
  (`tools_enabled`, `tool_choice`, `max_threads`, `session_ttl_days`, `max_session_messages`,
  `rate_limit_per_minute`, `rate_limit_per_hour`, `queue_name`). Every agent in the live registry
  therefore runs on `AgentConfig` defaults regardless of what is stored. This is a `src/` defect and
  this leg does not own `src/`; it is pinned by the strict-xfail test above. The fix is to carry
  those keys through the dict comprehension — verified during mutation testing that doing so makes
  the xfail test XPASS, i.e. one three-line change closes it.
- `tests/db/test_backend_selection.py` still fails 5 (`ModuleNotFoundError: psycopg`). Untouched;
  count did not grow.
- The now-parametrized `:379` test overlaps `test_a_caller_whose_own_identifier_is_the_placeholder_matches_no_session`
  at `:393` for one of its eight params. Left in place rather than deleting either — both are
  load-bearing under the placeholder mutation and this leg does not delete tests.
- `tests/pg/test_pg_mapping_puts.py::test_a_blank_agent_mapping_put_leaves_no_null_legacy_id_behind`
  does not itself catch the ACC-WRITE-011 mutation (with `mapping.id` the empty string is stored as
  `''`, which is not `NULL`). Its three siblings do. Kept because it pins the invariant the class
  is named after.

## Commands run

All from `services/agent-service` with `.venv/bin/python`. Overlay =
`/private/tmp/claude-501/-Users-ammar-Documents-Work-Projescts-AI-internal-aihub/78392d53-d5f5-4d58-9e14-a713912743c5/scratchpad/pgoverlay`.

1. `PYTHONPATH=<overlay> PGTEST_REQUIRED=1 -m pytest tests/pg -p no:warnings --tb=short`
   → **428 passed, 1 xfailed** (baseline 411 passed; +17 passed, +1 strict xfail).
2. `-m pytest tests/ --ignore=tests/pg -p no:warnings --tb=no`
   → **5 failed, 1556 passed** (baseline 5 failed / 1500 passed; the same 5 pre-existing
   `tests/db/test_backend_selection.py` psycopg failures, count did not grow).
3. `-m pytest tests/security -p no:warnings --tb=no` → **620 passed** (baseline 616; +4 from the
   extra `caller` parametrize).
4. `git diff --stat` → six test files, one new test file, **no `src/`**. Re-checked after every
   mutation restore; `git diff --stat -- services/agent-service/src` is empty.
5. `-m ruff check src tests` → All checks passed. `-m black tests` → reformatted one file
   (`tests/pg/test_pg_agent_read_parity.py`, call wrapping), then `--check` → 92 files unchanged.
   `-m isort --check-only src tests` → clean.

## Issues discovered

- **`src/config/agents.py:72-95` is a live defect, not a test defect.** The boot path materialises
  a fresh dict from the stored row and lists the keys it copies; the eight tuning settings are not
  among them. `_dynamodb_agent_to_agent_config` then supplies `queue_name="default"`,
  `max_threads=100`, `rate_limit_per_minute=60` and so on for **every** agent, on both backends.
  The repository round trip is correct — `tests/pg` proves the columns store and read back — so the
  loss is entirely in this rebuild. The strict-xfail test names it at the exact line.
- **`AgentGroupMappingItem.id` is a required `str` with no default**, unlike the user-side item's
  usage pattern, so "absent id" can only be expressed as `id=""`. The blank-id tests use `""`; a
  genuinely absent id is not constructible through the model. Worth knowing if the DynamoDB copy
  ever emits a mapping without one.
- **The `agent_groups` and `user_groups` puts are near-identical code with asymmetric tests** — this
  leg closed the blank-id gap, but the pattern will recur: `list_all` exists on the user repository
  and not the agent one, and the `role` normalisation has no agent-side analogue. A shared
  parametrized suite over both repositories would stop the asymmetry coming back.
- **`streaming_error` returns HTTP 200 with an error payload**, but the ownership refusals raise
  before it, so `/chat/stream` genuinely returns 404 for both refusal classes. Asserting the status
  on the stream suffix is therefore meaningful and not accidentally trivially true — but any future
  refusal routed through `streaming_error` instead of `HTTPException` would silently become a 200,
  and no test would notice.

## Procedure followed?

Yes. Every test touched or added was mutation-checked: the mutation was applied to a
restored-from-backup copy of the `src/` file, the affected suite run, then the file restored and
re-verified green. No mutation was left in place; `git diff --stat -- .../src` was confirmed empty
before committing.

| # | Mutation | Expected catch | Result |
|---|---|---|---|
| M1 | Delete the `/register` mask guard (`src/routers/agents.py:190-194`) | item 1 | **40 failed, 51 passed** — all mask + blank refusals and both ordering tests. Before this leg the same mutation left the whole suite byte-identical. |
| M2 | Replace the router's imported predicate with a local `bool(value)` reimplementation | item 1 identity assertion | **39 failed, 52 passed** — including `test_the_agents_router_uses_the_shared_predicate`, so a copy-pasted second rule is caught by identity even if it behaved the same on these inputs. |
| M3 | `mapping.id or generated` → `mapping.id` on **agent_groups** (`pg_repositories.py:709`) | item 2 | **3 failed, 425 passed, 1 xfailed** — the three new blank-id agent tests. The judge measured 404 passed / survives before this leg. |
| M4 | Replace both provider normalisations with the literal `"snowflake"` (put and `update_fields`) | item 3 | **6 failed, 422 passed, 1 xfailed** — the whole new non-snowflake class except the two fallback tests, which correctly still pass. The judge measured 404 passed / survives before this leg. |
| M5 | Drop `guest_access_enabled = EXCLUDED.guest_access_enabled` from the upsert | item 4d, "column default" source | **1 failed** — `test_putting_without_the_flag_over_an_enabled_agent_turns_it_off`. The original `assert ... is False` survived this. |
| M6 | Reader ignores the column (`bool(row.get("no_such_column"))`) | item 4d, "model default / bool(None)" source | **8 failed** — including the two new reader tests. |
| M7 | Delete the thread guard (`require_caller_owns_thread` returns immediately) | item 4a | target test **4 failed / 4 params** (40 failed overall). Before this leg it passed under this exact mutation. |
| M8 | Delete the session guard (`require_caller_owns_session` returns immediately) | item 4b | target test **4 failed / 4 params** (16 failed in the selected set). Before this leg it passed under this exact mutation. |
| M9 | Remove placeholder handling (`if requester_id and requester_id not in PLACEHOLDER_OWNER_IDS` → `if requester_id`) | item 4c | **8 failed** — the four `PLACEHOLDER_PRINCIPAL` params of the renamed test plus its sibling at `:393`. Before the rename the test's four params all passed under this mutation, which is exactly why its name was a lie. |
| M10 | Carry the eight tuning settings through `src/config/agents.py:72-95` (i.e. **fix** the defect) | item 4e | `[XPASS(strict)]` → **1 failed**. Proves the strict-xfail test is capable of failing and will force its own removal when `src/` is corrected. |

Other specifics:

- No existing test was deleted, skipped, or weakened. Two were renamed to stop claiming reach they
  had not proven; both kept every assertion they had and gained more.
- Zero comments and zero docstrings in everything added. Intent is in the test names.
- `src/` untouched: verified by `git diff --stat -- services/agent-service/src` returning empty
  immediately before `git add`.
- No live environment: no `oc`, `kubectl` or `aws`. `tests/pg` ran against the local throwaway
  `aihub-pg-test` container on 127.0.0.1:55432; the vault is stubbed by the existing `FakeVault`
  fixtures.
- No `.md` files or scratchpads committed. Nothing pushed; the commit is local on
  `feat/wave2-cutover-reconciled`.

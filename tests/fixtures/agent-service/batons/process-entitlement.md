# Leg: process-entitlement

Commit: `42a735f` on `feat/wave2-cutover-reconciled` (parent `8036f9f`).

## Implemented

### `POST /process/` — entitlement to the body agent (the actual hole)

`src/routers/chat.py`

- Added `entitled_process_agent(request: ProcessRequest, current_user=Depends(get_current_user)) -> AgentAccess`,
  which calls `resolve_agent_access(current_user, request.agent_id)`. This is the exact shape
  `agents.py` already uses for a body-carried agent id (`entitled_chat_agent`, `agents.py:115`) —
  no new mechanism invented.
- `process_message` now takes `access: AgentAccess = Depends(entitled_process_agent)` instead of
  `current_user: dict = Depends(get_current_user)`, and derives `user_id` from `access.user`, so the
  handler cannot run at all unless entitlement resolved.
- Tier chosen: **entitled-to-the-named-agent** (guest bound agent + `guestAccessEnabled` opt-in,
  group assignment for ordinary users, platform-admin bypass), refusing as **404** via the shared
  layer so a caller cannot enumerate agents. This is the correct tier because the route runs
  inference against a caller-supplied agent id and spends that agent's Cortex PAT — exactly the
  `/agents/chat` threat model, which already sits at this tier.

### `POST /process/batch` — left at platform-admin

Already carries `dependencies=ADMIN_ONLY` (`chat.py:224`) and I verified by mutation that the gate is
real (403 for an entitled non-admin, 401/403 for a guest). **Admin-only is strictly stronger than
entitlement** here: `entitlements.resolve_agent_role` grants `PLATFORM_ADMIN` for any agent to exactly
the callers `get_current_admin` admits, so layering `entitled_agent` on top would add zero refusals.

I deliberately did **not** invent a per-item entitlement check. `process_batch` takes an untyped
`Dict[str, Any]` and forwards an opaque `batch_data` list; there is no schema saying a batch item
carries an `agent_id`, and the downstream consumer does not exist (see Issues). Adding one would be
speculative structure over dead code. If batch is ever brought to life with a real item schema, the
entitlement check must be added per item at that point — flagged below.

### `GET /process/job/{job_id}` — ownership check confirmed present, but unreachable

`require_job_owner` is still called before the job body is returned and still 403s a non-owner
non-admin. Code order is correct. **However the route can never reach it** — see Issues; the
`queue_service.get_job_status` call raises `AttributeError` first and the handler converts that to a
500. I left the route's logic alone: the guard is right, its dependency is missing, and supplying the
missing queue methods is a different leg.

### `GET /process/snowflake/health` — left at platform-admin

Already `ADMIN_ONLY`. That is the right tier: on success the body carries `settings.SNOWFLAKE_ACCOUNT`,
which is deployment-wide infrastructure identity, not per-agent data — so per-agent entitlement is the
wrong axis for it and admin is the correct one. Added a test asserting a non-admin gets 403 and never
sees the account identifier.

### Admin-predicate consolidation

- `chat.py:91` `caller_is_admin` — deleted; `require_job_owner` now uses `is_admin_claim`.
- `dynamic_chat.py:127` `_caller_is_admin` — deleted; `_require_conversation_owner` now uses
  `is_admin_claim`.
- `is_admin_claim` itself untouched, as instructed.

### Explicitly out of scope, untouched

`/api/v1/chat` and `/api/v1/chat/stream` entitlement in `dynamic_chat.py` (GitLab issue #1, deferred
by the owner). Only the admin-predicate duplicate in that file was changed.

## Left undone

- `POST /process/batch` per-item entitlement — intentionally not added (justified above). It becomes
  required the moment `batch_data` gains a real item schema and a live consumer.
- The three missing `QueueService` methods (`add_message_job`, `get_job_status`, `add_batch_job`) —
  reported below, not fixed. Fixing them would make `/process/job/{job_id}` reachable for the first
  time, which is a behaviour change beyond this leg.
- `require_job_owner`'s switch to `is_admin_claim` has **no test coverage**, because the route it
  guards returns 500 before reaching it. The change is a strict tightening (string `"false"` no longer
  reads as admin) and is exercised nowhere. Stated plainly so it is not mistaken for tested.
- `mypy` was not run: the venv has no `mypy` module and no `pip`. ruff/black/isort all ran clean.

## Commands run

All from `services/agent-service` with `.venv/bin/python`.

1. Full suite — `.venv/bin/python -m pytest tests/ --ignore=tests/pg -p no:warnings --tb=no`
   → **5 failed, 1372 passed in 58.72s**. The 5 are exactly the pre-existing
   `tests/db/test_backend_selection.py` psycopg failures; count did not grow.
2. Security suite — `.venv/bin/python -m pytest tests/security -p no:warnings --tb=no`
   → **488 passed, 0 failed** (was 474; +14 new tests). Requirement was >= 474 and 0 failed.
3. `.venv/bin/python -m ruff check src tests` → All checks passed
   `.venv/bin/python -m black --check src tests` → 185 files unchanged
   `.venv/bin/python -m isort --check-only --diff src tests` → clean, no diff
4. `.venv/bin/python -m mypy ...` → `No module named mypy` (not available in this venv).
5. `PYTHONPATH=. .venv/bin/python -c "..."` on `QueueService` to confirm the missing methods.

## Mutation results

Every mutation was applied to a real source file, the suite re-run, then the file restored from a
byte-for-byte backup (`git diff` verified clean between mutations). Tests added: 14, in
`tests/security/test_agent_authorization.py::TestR11ProcessRequiresEntitlementToTheAgentNamedInTheRequestBody`.

Each refusal test also asserts `dispatched_agent_ids == []` — a stub on
`snowflake_hybrid_service.generate_response` recording every agent id it was invoked with. A refusal
therefore has to prove the agent's credential was never spent, not merely that a status code came
back. A 500 cannot satisfy the 404/401/403 equality assertions.

| # | Mutation | Result |
|---|---|---|
| M1 | `process_message` reverted to `Depends(get_current_user)` (the guard removed entirely — the pre-fix state) | **5 failed**: guest-alpha-as-beta, guest-on-non-guest-enabled, entitled-to-other-agent, groupless-user, refusal-indistinguishable. Bodies showed `200` with `"agent_id":"agent-beta"` — the exploit reproduced. |
| M2 | `entitlements.resolve_agent_role`: guest binding check reduced to `if not bound_agent_id` (ignore which agent the token is bound to) | **1 failed**: guest-alpha-cannot-run-inference-as-beta. Precisely the cross-agent test, nothing else. |
| M3 | `entitlements`: `if not await agent_allows_guest_access(...)` → `if False` (ignore the per-agent guest opt-in) | **1 failed**: guest-on-agent-that-is-not-guest-enabled. |
| M4 | `resolve_agent_role` returns `None` unconditionally (over-restriction) | **3 failed**: the three positive controls — guest-may-still-run-as-alpha, entitled-member-may-run, platform-admin-may-run. Confirms the allow assertions are not vacuous. |
| M5 | `dependencies=ADMIN_ONLY` removed from `/process/batch` and `/process/snowflake/health` | **3 failed**: admin-only-routes-stay-closed, guest-cannot-reach-admin-routes, non-admin-never-sees-snowflake-account. |
| M6 | Router-level `Depends(get_current_user)` removed **and** `process_message` left with no user dependency at all | **8 failed**, including anonymous-is-refused and forged-admin-token-is-refused. Those two assertions are guarded by two independent layers, so M1 alone did not move them — M6 is the mutation that proves them. |
| M7 | `entitlements`: `assignedGroupIds` membership check removed | **3 failed**: entitled-to-other-agent, refused-caller-learns-nothing, refusal-indistinguishable. |

Note on one assertion: under M1, `test_a_refused_caller_learns_nothing_about_the_agent_they_asked_for`
initially still passed — a 200 inference response happens not to contain the agent's host/PAT. That is
the failure mode the briefing warned about, so I added an explicit `status_code == 404` assertion to
that test; it now fails under M1 and M7. No assertion in the new class survives removal of the guard
it is meant to protect.

## Issues discovered

1. **`QueueService` is missing three methods that `chat.py` calls.** `src/services/queue_service.py`
   defines only `initialize / close / enqueue_message / dequeue_message / get_queue_size /
   clear_queue / get_queue_stats`. `chat.py` calls `queue_service.add_message_job` (line 136),
   `queue_service.get_job_status` (line 181) and `queue_service.add_batch_job` (line 229). Verified
   by import: all three are `False` for `hasattr`. Consequences:
   - `POST /process/` with `stream: true` → `AttributeError` → caught by the broad `except Exception`
     → **500 "Internal server error"**. The streaming branch has never worked.
   - `GET /process/job/{job_id}` → **500 on every call**, for every caller. `require_job_owner` is
     dead code today.
   - `POST /process/batch` → **500 on every call**.
   Only the non-streaming `POST /process/` path is live — which is precisely the path that was
   exploitable, so the fix lands on the one branch that matters. Someone should own restoring or
   deleting the queue-backed branches; a 500 from a missing attribute is indistinguishable from a
   real outage in logs.

2. **`process_batch` accepts an untyped body.** `request: Dict[str, Any]` with `batch_data` pulled
   out by `.get`. No validation, no per-item schema. Currently harmless because the route is
   admin-only and its consumer does not exist, but it is an entitlement hole waiting to open the
   moment batch is implemented: whoever wires `add_batch_job` must add per-item entitlement in the
   same change.

3. **`is_admin_claim` is genuinely stricter than what it replaced**, in a way worth knowing: the old
   predicates were `bool(x.get("isAdmin") or x.get("is_admin"))`, so any truthy value (including the
   string `"false"`) meant admin; `is_admin_claim` requires literal `True`, or `"admin"` in the
   legacy roles list. For `get_current_user` output the two agree exactly (it normalises to a real
   bool and sets `roles` to legacy role names), so no behaviour change for real callers. The one
   observable difference: a **legacy HS256 token carrying `roles: ["admin"]` but no `isAdmin`** now
   counts as admin in `require_job_owner` and `_require_conversation_owner` where it did not before.
   That matches `middleware/admin_auth.require_admin`, which has always used `is_admin_claim`, so it
   is a consistency fix rather than a widening — but it is a real semantic delta and I am not hiding it.

4. **No existing test was weakened or deleted.** The 474 pre-existing security tests all still pass
   unmodified. The only edits to `test_agent_authorization.py` are additive: one import, two module
   constants, one body helper, one fixture, one new test class.

## Procedure followed?

Yes, with these specifics worth auditing:

- Read `.relay/skills/running-the-test-suite.md` first. Never passed `-q`; ran from
  `services/agent-service` with `.venv/bin/python`. The 5 psycopg failures were left untouched and
  the count did not grow.
- **`tests/pg` was NOT run.** No database was brought up and no `PGTEST_REQUIRED=1` run was made.
  This leg touches only routing/authorisation and adds no SQL or schema dependency, so I judged it
  out of scope — but per the skill doc, a green run here proves nothing about the PostgreSQL path,
  and I am recording that explicitly rather than implying coverage I do not have.
- Tests were added **to the existing `tests/security/test_agent_authorization.py`** rather than a new
  file, so they reuse the suite's `sealed_service_boundaries` autouse fixture (pinned
  `settings.JWT_SECRET`, stubbed `admin_db_service.get_group_role_map` / `get_agent`,
  `agent_registry.get_agent`, and the guest-token repository) instead of duplicating 100 lines of
  fixture. The inference stub is a separate function-scoped fixture used only by the new class, so no
  existing test's behaviour changes.
- No live environment was touched: no `oc`, `kubectl`, `aws`, no network calls. Snowflake is stubbed
  at the service boundary (`snowflake_hybrid_service.generate_response`).
- Zero comments and zero docstrings in code I wrote — verified in the diff. Pre-existing docstrings
  on untouched functions were left as they were.
- No `.md` file or scratchpad is in the commit; the commit contains exactly three files, two `src`
  and one `tests`. `.relay/` is git-excluded and this baton is not staged.
- Branch: committed to `feat/wave2-cutover-reconciled`. Nothing pushed; `develop`, `stage` and `main`
  untouched.

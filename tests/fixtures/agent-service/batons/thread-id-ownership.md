# Leg: thread-id-ownership

## Implemented

A caller-supplied `thread_id` on `POST /agents/chat` and `POST /agents/chat/stream` is now verified
before it is handed to `snowflake_hybrid_service.generate_response` or written anywhere. A thread the
caller has no claim to is refused with **404 `"Thread not found"`**, matching the shape and status the
entitlement layer and the session guard already use, so threads are not enumerable.

**What a thread is actually scoped to — investigated before writing the check.**

The briefing was right to demand this first, and the answer is not "session ownership already covers
it". Findings:

1. **A thread has no owner record of its own anywhere in this repo.** `create_thread` in both backends
   (`snowflake_service.py:514`, `snowflake_sdk_service.py:379`) just mints
   `int(datetime.utcnow().timestamp() * 1000)` and returns it — no store, no principal, no agent
   binding persisted. `session-service` has no thread resource: `openapi-specs/session-service-openapi.yaml`
   offers only `PUT /sessions/{id}/thread`, and `thread_id` is a **field on the session record**
   (`SessionResponse.session.thread_id`). There is no way to look a thread up by id.
2. **The only thread→principal binding in the codebase is the session that carries the thread**, and
   the repo already treats it as authoritative: `DELETE /{agent_id}/threads/{thread_id}` authorizes via
   `agent_session_service.delete_thread`, which resolves the thread by finding sessions whose
   `thread_id` matches, owner-scoped with `_owner_scoped_filter(..., requester_id)`. "You own a thread"
   already means "you own a session that carries it".
3. **Session ownership does not imply thread ownership on chat**, because `session_id` and `thread_id`
   are two *independent* request fields. Both attacks are live on `da623c4`:
   - `{"session_id": <mine>, "thread_id": <yours>}` — the session guard passes, the foreign thread is
     still forwarded to Cortex.
   - `{"thread_id": <yours>}` with **no** `session_id` — a fresh session is created for the attacker
     and the foreign thread is forwarded anyway. `/chat` never even calls `set_thread_id` when a
     `thread_id` was supplied, so nothing records the mismatch.
   In both cases Cortex resumes the victim's thread: their conversation becomes the model's context and
   the attacker's message is appended to it.

So the fix is **not** a parallel owner lookup keyed by thread — that is impossible, there is no index —
and **not** a no-op on the grounds that the session guard covers it. The fix is to make the binding the
delete route already relies on hold on the write path: **a supplied `thread_id` is accepted only if it
is the thread recorded on the session the caller has just been proven to own.** No session supplied
means no session can vouch for the thread, so a supplied thread is refused before anything is created.

This is also the only coherent rule *within* one user's data: splicing thread A into session B mixes
two conversations, and now cannot happen by construction.

**One root-cause fix was required to avoid breaking legitimate use.** `/chat/stream` created a thread
and never recorded it (`/chat` does, via `set_thread_id`). A streaming client that echoed back the
`thread_id` the stream itself just handed it would therefore have been refused — the guard would have
denied a thread the service had issued to that very caller seconds earlier. The root cause is stream's
missing write, not the guard, so stream now records the thread on the session exactly as `/chat` does.
Mutation M9 proves that write is load-bearing, and a two-turn test proves the round trip works.

**Platform admins keep the unscoped reach they already have** on the read/delete routes
(`is_platform_admin` short-circuit, mirroring `requester_id -> None`). Asserted in two tests so it is a
recorded decision, and a one-line change to revoke.

**Fails closed everywhere.** No session supplied, session carries no thread, recorded thread of a
non-integer type, session-service unreachable (`get_session` swallows and returns `None`) → nothing
established → 404. Nothing is admitted on the strength of an unanswered question.

**The swallowed-404 trap.** Both handlers still wrap their body in `except Exception` returning HTTP
200 `success: false`. The previous leg's `except HTTPException: raise` sits ahead of it in both, so my
404 survives — but I did not assume it: `TestT4` asserts the concrete 404 and non-`text/event-stream`
content type, and mutations M7/M8 (removing the re-raise from each handler) kill 22 tests each.

**`PLACEHOLDER_OWNER_IDS` is not duplicated** — the thread guard needs no placeholder test of its own,
see "Issues" #1 for why that is correct rather than an omission.

Files changed (only files this leg owns; `src/db/pg_repositories.py` and `tests/pg/` never opened for
edit):

- `src/routers/agents.py` — `require_caller_owns_thread`, `THREAD_NOT_VISIBLE_DETAIL`, guard wired into
  both handlers ahead of session creation, `set_thread_id` added to the stream handler.
- `src/services/session_client.py` — `get_session_thread_id`, plus `_get_session_record` factored out of
  `get_session_owner` so both facts unwrap the two published response shapes one way.
- `tests/security/test_chat_thread_ownership.py` — new, 78 tests.

Test coverage (every assertion is a concrete status code; no `not in (401, 403)`): foreign thread via
own session refused, and via no session refused, on both endpoints on both mounts; refused thread never
reaches the inference backend, creates no session, appends nothing; refusal body carries none of the
other user's content; a never-issued thread refused; a foreign *session* is refused as a session before
its thread is probed (ordering); the owner's own recorded thread is accepted **and forwarded** to
`generate_response` (asserted on the captured kwarg, not just a 200); wrapped session shape; no thread
supplied → fresh thread minted, forwarded, and recorded on the session; the thread handed back is
accepted on the next turn (both endpoints); session with no recorded thread, non-numeric recorded
thread, boolean recorded thread all refused; legacy `"user"`-owned session and placeholder-identified
caller refused; unreachable session-service refuses; admin unscoped both with a mismatched session and
with no session. Session-service is stubbed at the HTTP transport boundary with `httpx.MockTransport`,
so `get_session_thread_id`'s own parsing is under test rather than mocked out.

## Left undone

- **`POST /chat` (`routers/dynamic_chat.py`) has the same defect and is untouched** — it reads
  `request.threadId or conversation.get("threadId")` (line 222, and 420 in the streaming twin) and
  forwards it to Cortex. Its scoping unit is a *conversation*, not a session, so the guard here does not
  transfer as written. Out of this leg's stated scope (`/agents/chat`), but it is the same class of hole
  and the next one to close. `routers/chat.py:158` also forwards `request.context["thread_id"]`
  unchecked.
- `DELETE /{agent_id}/threads/all` and `POST /{agent_id}/threads/cleanup` are admin-only and were not
  reviewed.
- `SessionClient.get_session_messages` still not touched — see the previous leg's Issue #1, which
  stands.

## Commands run

From `services/agent-service` with `.venv/bin/python`:

1. `.venv/bin/python -m pytest tests/ --ignore=tests/pg -p no:warnings --tb=no`
   → **5 failed, 1500 passed** (was 5 failed / 1422 passed; +78 from this leg). The 5 are exactly the
   pre-existing `tests/db/test_backend_selection.py` psycopg failures; the count did not grow. Re-run
   after the parallel runner's `4f0b17c` landed underneath my commit: same 5 failed / 1500 passed.
2. `.venv/bin/python -m pytest tests/security -p no:warnings --tb=no`
   → **616 passed, 0 failed** (was 538; +78). Meets the ≥538 floor.
3. `.venv/bin/python -m pytest tests/security/test_chat_thread_ownership.py -p no:warnings` → 78 passed.
4. `ruff check src tests` → All checks passed.
   `black --check` and `isort --check-only` on the three files this leg owns → clean.
   (`tests/pg/test_pg_mapping_puts.py` was still failing `black --check` when I started; it belongs to
   the parallel runner and I did not touch it. It is now committed in their `4f0b17c` — worth them
   re-checking.)

**Mutation results — every assertion mutation-checked, one thing broken at a time, file restored
between runs, all 78 tests re-run each time.** Baseline 78 passed.

| # | Mutation | Result |
|---|---|---|
| M1 | thread guard call deleted from `/chat/stream` | 18 failed |
| M2 | thread guard call deleted from `/chat` | 18 failed |
| M3 | guard returns unconditionally (`if True: return`) | 36 failed |
| M4 | admin bypass removed from the thread guard | 4 failed |
| M5 | unestablished thread fails open (`is not None and !=`) | 20 failed |
| M6 | session never consulted (`thread_id_of_owned_session = thread_id`) | 28 failed |
| M7 | `except HTTPException: raise` removed from `/chat` | 22 failed |
| M8 | `except HTTPException: raise` removed from `/chat/stream` | 22 failed |
| M9 | stream no longer records the thread it created | 4 failed |
| M10 | recorded-thread type normalisation dropped entirely | 4 failed |
| M11 | wrapped session shape no longer unwrapped | 2 failed |
| M12 | only the `bool` half of the normalisation dropped | 4 failed |
| M13 | only the `isinstance(..., int)` half dropped | **78 passed — survived** |

Nothing survived on the first battery this time; M12/M13 were added deliberately to split M10 and find
out *which* half of the normalisation is doing the work. The answer is honest and worth recording:

- The **`bool` half is load-bearing** and is why the recorded value is normalised at all. JSON `true`
  is an `int` in Python, so a session recording `thread_id: true` would have vouched for a request
  asking for thread `1`. `test_a_boolean_recorded_thread_does_not_vouch_for_thread_one` covers it.
- The **`isinstance(..., int)` half is unreachable by construction** — `ChatRequest.thread_id` is
  `Optional[int]`, so a non-integer recorded value (e.g. the string `"7001"`) can never compare equal to
  it and is refused either way. I am not manufacturing a test for it. It stays because it is what makes
  the `Optional[int]` return type honest, exactly as the previous leg kept its own unobservable
  normalisation (their M10).

One more mutation I did **not** run as a test-killer because it is safe either way:
`if not thread_id` → `if thread_id is None` in the guard. `thread_id: 0` is falsy, so the handler
discards it and mints a fresh thread regardless; `not thread_id` keeps the guard's trigger condition
identical to the set of values the handler will actually forward, which is why it is written that way.

## Issues discovered

1. **The placeholder-owner case is real but is not a branch of the thread guard, and I did not fake a
   test for one.** `PLACEHOLDER_OWNER_IDS` guards the *requester* side of session ownership. The thread
   guard never compares principals — it compares the supplied thread to the thread recorded on a session
   whose ownership `require_caller_owns_session` has already established. A legacy `"user"`-stamped
   session is therefore refused one step earlier, as a *session*, and its thread is never read. I cover
   the combination anyway (legacy session + its thread, and a caller whose own `sub` is `"user"`, both
   404 with no dispatch) so the ordering is pinned, but the honest statement is: importing
   `PLACEHOLDER_OWNER_IDS` into the thread guard would have been dead code, so I did not.
2. **The guard costs one extra `GET /sessions/{id}` per chat request that carries a `thread_id`** —
   `get_session_owner` and `get_session_thread_id` each fetch the record. I chose two narrow methods over
   one that returns the raw record because handing the router the record would put session-service
   response-shape knowledge back in the router, which is what the previous leg deliberately removed. If
   this shows up in latency, the fix is a per-request memo inside `SessionClient`, not a wider interface.
3. **Chat now has a hard read-after-write dependency on session-service persisting `thread_id`.** If
   `PUT /sessions/{id}/thread` silently fails (the return value is ignored by both handlers, as before),
   or if the deployed service omits `thread_id` from `GET /sessions/{id}`, the caller's *own* next turn
   is refused with 404 instead of continuing. That is fail-closed and correct for a security guard, but
   it is an availability coupling that did not exist before and someone should confirm against the
   running session-service — it is the same "client vs published contract" doubt the previous leg raised
   in its Issue #1. **This is the one thing in this leg I would want verified against a live
   session-service before it reaches production.**
4. **Thread ids are guessable, which is what made this exploitable in the first place.**
   `create_thread` uses `int(datetime.utcnow().timestamp() * 1000)` in both backends. Anyone who knows
   roughly when a victim started chatting can brute-force a ~thousand-value window per second. The guard
   closes the door regardless of guessability, but a thread id should not be a timestamp; and note the
   ids are not namespaced per agent either, so two agents can mint the same id.
5. **`snowflake_sdk_service.py:130` interpolates `thread_id` straight into a SQL string**
   (`'thread_id': {thread_id if thread_id else "NULL"}`). Today `ChatRequest.thread_id` is
   `Optional[int]` so pydantic keeps it numeric, and `dynamic_chat_service` passes it through typed —
   but the injection safety rests entirely on that annotation, with no coercion at the SQL boundary. One
   caller passing a string (e.g. `routers/chat.py:158`, which reads `request.context.get("thread_id")`
   out of an untyped dict) breaks it. Worth a bind/`int()` at that boundary.
6. **`/agents/chat/stream` never persists any of the conversation.** It reads history via
   `get_session_messages` but calls `add_message` zero times, so a streamed exchange is invisible to the
   next turn. I added only the `set_thread_id` write that the ownership binding requires; the missing
   message writes are a behavioural bug outside this leg.

## Procedure followed?

Yes, with the following notes.

- Read `.relay/skills/running-the-test-suite.md` first; ran from `services/agent-service` with
  `.venv/bin/python -m pytest`, never passed `-q`, never touched the 5 psycopg failures (still exactly
  5, before and after the parallel runner's commit landed under mine).
- Read `.relay/batons/chat-session-ownership.md` and followed its pattern rather than inventing one:
  check before the downstream call, off the entitlement layer's resolved identity; 404 to match the
  entitlement layer; admin short-circuit consistent with the read/delete routes; the client gained only
  the single fact it is authoritative about (`get_session_thread_id`) and the router learned nothing new
  about response shapes.
- **File ownership respected.** Changed only `src/routers/agents.py`, `src/services/session_client.py`
  and a new `tests/security/` file. `src/db/pg_repositories.py` and `tests/pg/` were never edited, and
  my commit contains exactly those three files — verified with `git show --stat`. The parallel runner
  committed `4f0b17c` while I was working; my commit sits on top of it cleanly.
- Zero comments and zero docstrings in the code written here. `require_caller_owns_thread`,
  `thread_id_of_owned_session`, `get_session_thread_id`, `THREAD_NOT_VISIBLE_DETAIL` and the test names
  carry the intent.
- No existing test weakened, deleted, or skipped, and none contradicted the requirement. The previous
  leg's 50 session-ownership tests still pass unchanged, including the two that assert
  `session_service.threads == {}` on a refused request — my stream `set_thread_id` only fires on the
  success path.
- No workaround, no bypass flag, no hardcoding. The one behavioural addition (stream recording its
  thread) is a root-cause fix for a missing write, not a loosening of the guard.
- No live environment touched — no `oc`/`kubectl`/`aws`, no real host. Session-service is stubbed at the
  httpx transport boundary; Cortex is stubbed at `snowflake_hybrid_service`.
- No `.md` files or scratchpads committed; the mutation driver lived in the session scratchpad and is
  not in the repo. Committed as `7d031a3` on `feat/wave2-cutover-reconciled`; nothing pushed anywhere,
  and nothing near `develop`, `stage` or `main`.

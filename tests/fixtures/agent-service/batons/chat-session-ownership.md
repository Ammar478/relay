# Leg: chat-session-ownership

## Implemented

A caller-supplied `session_id` on `POST /agents/chat` and `POST /agents/chat/stream` is now
verified against the session's recorded owner before any history is read or any message appended.
A session the caller does not own is refused with **404 `"Session not found"`** — the same shape and
status the entitlement layer already uses for an agent the caller may not see, so chat cannot be used
to enumerate other users' sessions.

**The decision, and why.**

Option chosen: **enforce ownership before the call, in the chat handlers, using the identity the
entitlement layer already resolved.** The two rejected options and the reasoning:

- *Pass the requester to session-service and let it enforce.* I checked what the client actually
  sends rather than assuming. `SessionClient.get_session` calls `GET /sessions/{id}` and
  `add_message` calls `PUT /sessions/{id}/messages` — **neither carries any requester parameter**,
  and neither operation accepts one in `openapi-specs/session-service-openapi.yaml`. Only
  `/sessions/{id}/history` and `/sessions/stats` take a `user_id`, and chat calls neither. The
  service's source is not in this repo, so I can neither add enforcement there nor verify a claim
  that it enforces. Enforcing at this service's boundary is the only thing this repo can guarantee.
- *Scope inside `SessionClient`.* It is an httpx wrapper with no notion of a principal. Ownership is
  an authorization decision and belongs where identity lives — the router. What the client gained is
  only the fact it is the authority on: **who session-service says owns a session**
  (`get_session_owner`), which hides the response shape from the router.

**The legacy-owner question — investigated, not assumed.** The briefing warned that sessions created
through this path historically stored the literal string `"user"` as owner, and that an ownership
check assuming a correct stored owner could lock users out of their own history. Confirmed at source:
before `ac8b835`, `chat_with_agent` called
`session_client.create_session(request.agent_id, "user", ...)  # In production, this would come from authentication`.
Every session created via `/agents/chat` before that commit is stamped `user_id == "user"`.

The data **can** support a safe check, and refusing those sessions locks nobody out of anything they
can currently reach, because:

1. `"user"` is not an owner — it is the absence of one. Every pre-`ac8b835` session carries the same
   string, so honouring it would make one shared conversation readable by every entitled caller. That
   is the defect, not a legitimate access pattern to preserve.
2. Those sessions are **already unreadable** through `GET /{agent_id}/sessions/{session_id}` and
   `/messages`. The previous leg's `_owner_scoped_filter` matches `user_id == requester_id`, and no
   real principal is `"user"`, so a legacy session already 404s on the guarded read routes. Refusing
   it in chat removes a bypass; it does not remove reach a user has today.

So the check reuses `PLACEHOLDER_OWNER_IDS` (`{"", "user"}`) already defined in
`agent_session_service`, imported rather than re-declared so there is one source of truth for what
counts as a non-principal. It guards the **requester** side: a caller whose own id is `""` or `"user"`
matches nothing, which is what stops a legacy session from being reachable by a principal that happens
to carry the placeholder string. The owner side needs no separate placeholder test — a requester that
is a real principal can never equal `"user"`.

**Fails closed everywhere.** Owner unknown, session absent, `user_id` missing or non-string, or
session-service unreachable (`get_session` swallows exceptions and returns `None`) all resolve to
"no owner established" → 404. Nothing is admitted on the strength of an unanswered question.

**Platform admins keep the unscoped reach the read routes already grant them** (`is_platform_admin`
short-circuits, mirroring `requester_id -> None` in `agent_session_service`). An admin can already
read and delete any user's sessions on this branch; making chat stricter than delete would be
inconsistent without deciding a policy question this leg was not asked to decide. It is asserted in a
test so it is a recorded decision rather than an accident, and is a one-line change to revoke.

**A swallowed-404 trap that had to be fixed for any of this to work.** Both chat handlers wrap their
whole body in `except Exception`, which returns a **200** `ChatResponse(success=False)` /
`streaming_error`. An `HTTPException(404)` raised inside that block would have been converted into a
200 and the guard would have been decorative. Both handlers now re-raise `HTTPException` ahead of the
generic handler, matching `register_agent`. Mutations M7/M8 confirm the tests catch its removal.

Files changed (both owned by this leg — `src/db/pg_repositories.py` and `tests/pg/` untouched):

- `src/routers/agents.py` — `require_caller_owns_session`, `SESSION_NOT_VISIBLE_DETAIL`, guard wired
  into both chat handlers, `except HTTPException: raise` in both.
- `src/services/session_client.py` — `get_session_owner`.
- `tests/security/test_chat_session_ownership.py` — new, 50 tests.

Test coverage (all assert concrete status codes, never `not in (401, 403)`): foreign session refused
on both endpoints on both mounts; the refused session is never loaded as model context, never appended
to, and its content never appears in the refusal body; owner reaches their own session and their
history *is* loaded and the exchange *is* appended; legacy `"user"` session refused; caller whose own
id is the placeholder matches nothing; session with no `user_id` refused; unknown session refused;
unreachable session-service refuses rather than admits; no-`session_id` path still creates a session
owned by the caller; admin unscoped. Session-service is stubbed at the HTTP boundary with
`httpx.MockTransport`, so `get_session_owner`'s own parsing is under test rather than mocked out.

## Left undone

- **`thread_id` is caller-supplied and unscoped.** Same class of defect, different key. Both chat
  handlers pass `request.thread_id` straight to `snowflake_hybrid_service.generate_response`, and
  `/chat` also writes it onto the session with `set_thread_id`. Nothing checks the thread belongs to
  the caller. Out of this leg's stated goal (session read/append), but it is the next one.
- `SessionClient.get_session_messages` was not changed — see Issues.
- `POST /chat` (the `dynamic_chat` router) was not reviewed; this leg was scoped to `/agents/chat`.

## Commands run

From `services/agent-service` with `.venv/bin/python`:

1. `.venv/bin/python -m pytest tests/ --ignore=tests/pg -p no:warnings --tb=no`
   → **5 failed, 1422 passed**. The 5 are exactly the pre-existing
   `tests/db/test_backend_selection.py` psycopg failures; count did not grow.
2. `.venv/bin/python -m pytest tests/security -p no:warnings --tb=no`
   → **538 passed, 0 failed** (was 488; +50 from this leg). Meets the ≥488 floor.
3. `.venv/bin/python -m pytest tests/security/test_chat_session_ownership.py ...` → 50 passed.
4. `ruff check src tests` → All checks passed.
   `black --check` / `isort --check-only` on the three files this leg owns → clean.
   (`black --check src tests` also flags `tests/pg/test_pg_mapping_puts.py`, which belongs to the
   parallel runner — deliberately not touched, see Issues.)

**Mutation results — every assertion mutation-checked, one guard broken at a time, restored between
runs, full 50-test file re-run each time.** Baseline 50 passed.

| # | Mutation | Result |
|---|---|---|
| M1 | guard call deleted from `/chat/stream` | 14 failed |
| M2 | guard call deleted from `/chat` | 16 failed |
| M3 | guard returns unconditionally (`if True: return`) | 30 failed |
| M4 | placeholder requester accepted (drop `not in PLACEHOLDER_OWNER_IDS`) | 4 failed |
| M5 | unknown owner fails open (`is not None and !=`) | 16 failed |
| M6 | admin bypass removed (`if False: return`) | 2 failed |
| M7 | `except HTTPException: raise` removed from `/chat` | 12 failed |
| M8 | `except HTTPException: raise` removed from `/chat/stream` | 12 failed |
| M9 | `get_session_owner` reads only the flat shape | 2 failed |
| M10 | `get_session_owner` accepts an empty/non-string `user_id` | **50 passed — survived** |

M4 **survived the first battery** and that was a real gap in my tests, not a false alarm: with only
`user-alice`-style callers, a stored owner of `"user"` fails the equality check anyway, so the
placeholder guard was untested. I added
`test_a_caller_whose_own_identifier_is_the_placeholder_matches_no_session` — a caller entitled to the
agent whose token `sub` *is* `"user"` — which is precisely the case where dropping the guard hands
over every legacy session. M4 now fails 4 tests.

M10 survives and I am not manufacturing a test for it: it is unreachable by construction rather than
untested. For an empty or non-string `user_id` to change the outcome, the requester id would have to
be empty or non-string too, and such a requester is already refused one branch earlier by
`if requester_id and requester_id not in PLACEHOLDER_OWNER_IDS`. The normalisation stays because it is
what makes the `Optional[str]` return type honest, but no external behaviour can distinguish it.

## Issues discovered

1. **`SessionClient` and the published session-service contract disagree, and one of them is wrong.**
   `openapi-specs/session-service-openapi.yaml` says `GET /sessions/{id}` returns
   `{"success": ..., "session": {...}}` with **no `messages` key**, and that messages are added with
   `POST /sessions/{id}/messages`. The client reads `session["messages"]` off the top level and adds
   messages with `PUT /sessions/{id}/messages`, and creates sessions at `POST /sessions/create`, which
   the spec does not describe. If the spec is right, **`get_session_messages` has always returned `[]`
   and chat has never had conversation memory.** I did not change it — that is a behavioural fix
   outside this leg and needs the live service to adjudicate. My guard is deliberately immune to the
   disagreement: `get_session_owner` reads the owner from either shape (M9 proves the wrapped path is
   exercised), so ownership holds whichever contract the deployed service honours. **Someone should
   diff the client against the running session-service and correct whichever is stale.**
2. **`session-service` enforces no ownership on any endpoint reachable by this client.** Anything that
   can reach it can read and write any session by id. This leg closes the hole at agent-service's
   door; the service itself remains open to any other caller on the network. That is a
   session-service-side fix in a repo not present here.
3. **`thread_id` is the same defect, unfixed** — see Left undone.
4. **Chat's error contract makes security assertions awkward.** Both handlers return HTTP 200 with
   `success: false` for real failures ("Agent not found", "Agent is not active"), so a status-code
   assertion cannot distinguish refusal from success without inspecting the body. The 404 introduced
   here is the first status-code-visible refusal on chat. Worth normalising the rest.
5. **A parallel-runner formatting conflict, deliberately not touched.**
   `tests/pg/test_pg_mapping_puts.py` currently fails `black --check`. It belongs to the runner that
   owns `tests/pg/`, so per the ownership rules I left it alone and formatted only my three files.
   Whoever owns that file needs to run black on it before the branch is clean.

## Procedure followed?

Yes, with the following notes.

- Read `.relay/skills/running-the-test-suite.md` first; used `.venv/bin/python -m pytest` from
  `services/agent-service`, never passed `-q`, and did not touch the 5 psycopg failures (still exactly
  5).
- **File ownership respected.** Changed only `src/routers/agents.py`, `src/services/session_client.py`
  and a new test file. `src/db/pg_repositories.py` and `tests/pg/` were not read into or written by
  this leg. One cross-file need arose — `PLACEHOLDER_OWNER_IDS` lives in
  `src/services/agent_session_service.py` — and I **imported** the existing constant rather than
  editing that file or duplicating the set, so no file outside my ownership was modified.
- Zero comments and zero docstrings in the code written here. `require_caller_owns_session`,
  `get_session_owner`, `SESSION_NOT_VISIBLE_DETAIL` and the test names carry the intent.
- No existing test weakened, deleted, or skipped; no existing test contradicted the requirement. The
  existing chat tests in `test_agent_authorization.py` use `is_active=False` agents and send no
  `session_id`, so they never reach the guard and are unaffected.
- No workaround, no bypass flag, no hardcoding: the fix is at the point where a caller-supplied
  identifier is first trusted.
- No live environment touched — no `oc`/`kubectl`/`aws`, no real host. Session-service is stubbed at
  the HTTP transport boundary with `httpx.MockTransport`.
- No `.md` files or scratchpads committed; the mutation driver lived in the session scratchpad
  directory and is not in the repo. Committed on `feat/wave2-cutover-reconciled`; nothing pushed to
  `develop`, `stage` or `main`.

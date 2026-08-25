# reconcile-develop

## Implemented

- Created `feat/wave2-cutover-reconciled` from `feature/non-prod-wave2-cutover` (commit `7f8690c`). `feature/non-prod-wave2-cutover` was not mutated.
- Merged `develop` (`8e66244`) into it with `git merge --no-ff`. **Clean auto-merge, zero conflicts**, exactly as the briefing predicted. `admin_db_service.py` auto-merged via the `ort` strategy with no conflict markers.
- Merge commit: `c3319e2` — `merge: land develop credential-preservation fix onto wave-2 cutover`.
- Files brought in by the merge (3 files, +248/-10):
  - `services/agent-service/src/routers/admin.py`
  - `services/agent-service/src/services/admin_db_service.py`
  - `services/agent-service/tests/unit/test_credential_survives_an_edit.py` (new, 206 lines)
- **Zero new code written.** The merge required none, so none was written — no comments, no docstrings added by me.

ACC-MERGE-001 is satisfied. Evidence in "Commands run".

## Left undone

- **The branch is not pushed.** `feat/wave2-cutover-reconciled` exists locally only. Pushing was permitted but not required, and I did not want to trigger CI as a side effect of a leg that was only asked to commit. The next runner inherits the branch from the local repo; push it if the next leg needs a remote.
- `feature/non-prod-wave2-cutover` itself is still 3 behind `develop` — deliberate, it is the fallback. Whatever fast-forwards or replaces it is a later leg's call.
- The 5 pre-existing `tests/db/test_backend_selection.py` failures were left alone as instructed.
- `tests/pg/` was not run (ignored per the briefing; it needs a real PostgreSQL).

## Commands run

**1. `git checkout -b feat/wave2-cutover-reconciled feature/non-prod-wave2-cutover`** — exit 0
```
Switched to a new branch 'feat/wave2-cutover-reconciled'
7f8690c Merge branch 'feature/sub-1b-agent-write-methods' into 'feature/non-prod-wave2-cutover'
```

**2. `git merge --no-ff develop -m "merge: land develop credential-preservation fix onto wave-2 cutover"`** — exit 0
```
Auto-merging services/agent-service/src/services/admin_db_service.py
Merge made by the 'ort' strategy.
 services/agent-service/src/routers/admin.py        |   3 +-
 .../agent-service/src/services/admin_db_service.py |  49 ++++-
 .../tests/unit/test_credential_survives_an_edit.py | 206 +++++++++++++++++++++
 3 files changed, 248 insertions(+), 10 deletions(-)
```
No conflicts. Nothing to resolve.

**3. `git rev-list --count HEAD..develop`** — exit 0
```
0
```
Every commit on `develop` is present on `feat/wave2-cutover-reconciled`.

**4. grep counts in `services/agent-service/src/services/admin_db_service.py`** — exit 0
```
_is_a_real_credential=2
stored_pat=3
_assert_members_exist=2
user_read_repository=2
```
All non-zero. Reachability confirmed by line, not just by count:
```
 24:    user_read_repository,                              <- wave-2 import
118:    def _assert_members_exist(...)                     <- wave-2 definition
129:        known = user_read_repository.emails_by_id()
142:            self._assert_members_exist(members)        <- wave-2 CALL SITE in update_group_members
384:            "snowflakeConfig": self._redact_snowflake_config(item.snowflakeConfig),
392:    def _is_a_real_credential(value)                    <- develop definition
408:    def _redact_snowflake_config(cfg)                   <- develop rewrite
556:                        stored_pat = (existing_config or {}).get("cortex_agent_pat")
558:                        if stored_pat and not self._is_a_real_credential(incoming):
559:                            value["cortex_agent_pat"] = stored_pat   <- develop guard in update_agent
```
Both sides survived and both are reachable. `_redact_snowflake_config` is develop's DROP-the-key version (`return {k: v for k, v in cfg.items() if k != "cortex_agent_pat"}`), not the old blanking version.

**5. wave-2 write-method symbol check** — exit 0
```
$ .venv/bin/python -c "from src.db import pg_repositories as P; print(all(hasattr(P.PgAgentRepository,m) for m in ('put','update_fields','delete')), hasattr(P.PgAgentGroupMappingRepository,'put'), hasattr(P.PgUserGroupMappingRepository,'put'))"
True True True
```

**6. other wave-2 artifacts spot-checked present** — exit 0
```
src/vault/client.py:266:    def store_agent_pat(...)
src/vault/client.py:290:    def get_agent_pat(...)
src/config/agents.py: _resolve_provider_credential  (2 occurrences)
services/db-schema/copy_wave2.py                     present
services/agent-service/tests/pg/test_pg_agent_writes.py  present
```

**7. `.venv/bin/python -m pytest tests/ --ignore=tests/pg -q -p no:warnings --tb=no`** — exit 1
```
5 failed, 677 passed in 23.37s
FAILED tests/db/test_backend_selection.py::TestBackendSelection::test_postgres_is_selected_when_asked
FAILED tests/db/test_backend_selection.py::TestBackendSelection::test_selection_tolerates_case_and_padding
FAILED tests/db/test_backend_selection.py::TestPerWaveIsolation::test_wave_one_is_unaffected_by_holding_wave_two_back
FAILED tests/db/test_backend_selection.py::TestPerWaveIsolation::test_a_wave_can_opt_in_while_the_default_stays_dynamodb
FAILED tests/db/test_backend_selection.py::TestPerWaveIsolation::test_an_empty_override_falls_through_to_the_default
```
Exactly the 5 pre-existing failures, no more. Cause confirmed as the local venv, not the merge:
```
E   ModuleNotFoundError: No module named 'psycopg'
src/db/repositories.py:237: ModuleNotFoundError: No module named 'psycopg'
```
Not fixed, not skipped, not touched.

**8. develop's new test file run on the merged tree** — exit 0
```
$ .venv/bin/python -m pytest tests/unit/test_credential_survives_an_edit.py -p no:warnings --tb=line
32 passed in 0.39s
```

**9. linters** — all exit 0
```
$ .venv/bin/python -m ruff check src tests      -> 0   "All checks passed!"
$ .venv/bin/python -m black --check src tests   -> 0   "177 files would be left unchanged."
$ .venv/bin/python -m isort --check-only src tests -> 0   (silent)
```

**10. `git status --short`** — exit 0, empty. Working tree clean, nothing stray staged, no `.md` or scratchpad committed. `git check-ignore -v .relay/batons/reconcile-develop.md` confirms `.git/info/exclude:7:.relay/` still covers this baton.

## Issues discovered

1. **`pytest.ini` sets `addopts = -q`**, so passing `-q` again on the command line double-quiets and *suppresses the final `N failed, M passed` count line*. The briefing's exact command (step 4) therefore prints the FAILED list but no totals — easy to misread as "no summary, must have hung". I recovered the counts by re-running with `--tb=line` (no extra `-q`). Worth fixing the briefing's command for later legs, or dropping `-q` from the invocation.
2. **`copy_wave2.py` is at `services/db-schema/copy_wave2.py`, not under `services/agent-service`.** The briefing lists it alongside agent-service paths; a runner scoping a `find` to the service dir will conclude it is missing. It is present and untouched by this merge.
3. **`src/vault/client.py`, not `src/services/vault/client.py`.** Same shape of trap as above — the briefing's path for the PAT resolver is off by one directory. Functions are present at lines 266/290.
4. **Three stale prunable worktrees** are registered against this repo under `~/.claude/jobs/412bf319/tmp/` (`as-ce`, `as-d`, `as-p1`), all marked prunable. Harmless here, but `git worktree prune` would tidy them and they could confuse a later leg that lists worktrees.
5. The repo was on branch `fix/agents-router-authentication` when I started (clean tree). I branched from `feature/non-prod-wave2-cutover` explicitly as instructed, so the starting branch did not matter — noting it only because the next runner will find HEAD moved.
6. Merged-in code from `develop` contains docstrings (e.g. on `_is_a_real_credential`, `_redact_snowflake_config`). The zero-comment/zero-docstring constraint binds code *I* write; I wrote none, and I did not strip develop's — stripping them would be an unrequested rewrite of someone else's commit inside a merge. Flagging in case the coach wants that reconciled with the standing "no comments" rule.

## Procedure followed?

Yes, exactly, with one deliberate omission and no deviations in substance.

- Branched from `feature/non-prod-wave2-cutover`, did not mutate it. ✔
- Merged `develop` into the new branch. ✔
- Never pushed to or modified `develop`, `stage`, or `main`. ✔ (no push at all)
- Hit no conflict, so the STOP-and-report clause never fired; nothing was resolved creatively. ✔
- Wrote zero code, hence zero comments and zero docstrings. ✔
- No workarounds, no bypass flags, no hardcoding. ✔
- No `.md`/progress/scratchpad files committed; `.relay/` left git-excluded. ✔
- Did not touch the 5 pre-existing failures; count did not grow. ✔
- No `oc`/`kubectl`/`aws`/live-host calls. ✔

**Deviation to note:** I did not push the branch. The briefing said pushing "is fine", not that it was required, and the exchange zone is git — the branch is committed locally and inheritable. If the next leg expects a remote ref, it must push first.

**Verification-command deviation:** step 4's pytest invocation was run verbatim first (recorded above, exit 1, count line suppressed — see Issue 1), then re-run once with `--tb=line` purely to read the totals. No flags were changed to alter which tests ran or their outcome.

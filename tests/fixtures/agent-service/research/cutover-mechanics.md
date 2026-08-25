# Research: cutover mechanics, secrets, rollback

## The switch

`src/db/repositories.py:213-223` and `services/auth-service/src/db/repositories.py:111-121`, identical:

```python
def _backend(wave: int) -> str:
    specific = (getattr(settings, f"DB_BACKEND_WAVE{wave}", "") or "").strip().lower()
    if specific:
        return specific
    return (settings.DB_BACKEND or "dynamodb").strip().lower()
```

Order: `DB_BACKEND_WAVE<N>` → `DB_BACKEND` → `"dynamodb"`. A misspelling is truthy, is returned, and is
not `== "postgres"`, so every builder takes the DynamoDB branch — inert, no error, no log.
Binding happens at **module import** into module-level singletons, so an env change needs a pod restart.

**auth-service also has a wave-2 repository**: `user_group_mapping_repository` (`iam.user_groups`).
`PgUserRepository._manager_legacy_ids()` calls it to derive `agent_manager` in `roles[]`, so wave 1's
login output already depends on the wave-2 flag. On failure it logs and returns an empty set — it
**silently drops agent_manager**, never errors. Both values files must therefore flip in ONE commit.

## Proving an image is safe — symbol probe, not tag comparison

```bash
oc exec -n aihub-dev-be deploy/agent-service -- python -c "
import src.db.repositories as R
from src.db import pg_repositories as P
from src.vault.client import VaultClient
import src.config.agents as A
print('per-wave resolver :', hasattr(R,'_backend') and not hasattr(R,'_wave1_backend'))
print('PAT resolver      :', hasattr(VaultClient,'get_agent_pat') and hasattr(A,'_resolve_provider_credential'))
print('wave2 writes      :', all(hasattr(P.PgAgentRepository,m) for m in ('put','update_fields','delete'))
                              and hasattr(P.PgAgentGroupMappingRepository,'put')
                              and hasattr(P.PgUserGroupMappingRepository,'put'))
"
```

Result on the currently deployed `dev-662.8e662443`: per-wave resolver **True**, PAT resolver **False**,
wave-2 writes **False**. The image is the blocker.

## Live dev readiness (everything except the image)

| Item | State |
|---|---|
| `ops.alembic_version` | `0002_pat_alias_case`. `0003_agent_guest_access` NOT applied; no `guest_access_enabled` column |
| `catalog.agents` / `agent_groups` / `iam.user_groups` | 7 / 19 / 49 (DynamoDB 7 / 19 / 51; the 2 missing are dangling FK rows, correctly skipped) |
| `pat_secret_arn` populated | 5 of 7. `agent-ec00bc8c`, `agent-ee6950bd` have no PAT in either store |
| Secrets Manager | all 5 `aihub/dev/agent-pat/<legacy_id>` exist |
| IRSA `aihub-dev-vault` | inline policy **already grants** GetSecretValue, DescribeSecret, CreateSecret, PutSecretValue, TagResource on `aihub/dev/agent-pat/*`. Ready. (Repo docs say ws-creds only — the docs are stale, the live policy is extended.) |
| PG grants | `aihub_agent_service` has SELECT/INSERT/UPDATE/DELETE on `catalog.agents`, `catalog.agent_groups`, `iam.user_groups`. Ready |

## Rollback — reverting restores routing only, and strands writes

There is **no dual write**. The repository object is replaced wholesale, so from the flip DynamoDB is
frozen. On revert the live view snaps back to that frozen snapshot; everything written to PostgreSQL
in between vanishes from the application's view (rows remain, orphaned).

| Endpoint | PG table | Stranded on revert |
|---|---|---|
| `POST /admin/agents` | `catalog.agents` | the whole new agent; its Secrets Manager secret is orphaned |
| `PUT /admin/agents/{id}` | `catalog.agents` | all edits including a **rotated PAT** |
| `DELETE /admin/agents/{id}` | `catalog.agents` | the agent **reappears** |
| agent↔group assignment | `catalog.agent_groups` | revoked assignments **come back** |
| `PUT /admin/groups/{id}/members` | `iam.user_groups` | role grants including `agent_manager` revert |
| `DELETE /admin/groups/{id}` | both | memberships and assignments **resurrect** |
| auth `PUT /admin/users/{id}` with groups | `iam.user_groups` | whole membership set reverts |

Claims are frozen at login, so a revert's privilege change only appears after re-login.

**Wave-2-specific asymmetry absent from wave 1**: a PAT rotated through the admin UI after the flip
writes to Secrets Manager and stores only `pat_secret_arn` in PostgreSQL; the constraint forbids the
inline PAT so DynamoDB never sees it. **After a revert the agent uses the OLD, rotated-out PAT** →
Cortex 401 with no config change visible anywhere.

Re-running `copy_wave2.py` repairs a *forward* gap only. It cannot recover PostgreSQL-only writes.

## Read-parity gaps `copy_wave2 verify` will NOT catch

`PgAgentRepository._select()` omits: `queue_name`, `tool_choice`, `max_threads`, `session_ttl_days`,
`max_session_messages`, `rate_limit_per_minute`, `rate_limit_per_hour`, `tools_enabled`.
The verify checksum compares only `slug,name,provider,is_active` (+`guest_access_enabled` on the branch).

Concrete dev impact: `engineering_ai` has `queue_name='engineering'` in both stores, but the repository
never returns it, so after cutover it falls back to the `AgentConfig` default `"default"` — its RQ jobs
land on the wrong queue. Silent.

`guest_access_enabled` is likewise not selected and `_to_item` never sets `guestAccessEnabled`, so it
always reads the model default `False`.

## The gitops change

Exactly two files: `apps/dev/agent-service/values.yaml`, `apps/dev/auth-service/values.yaml`.

```diff
   DB_BACKEND: "dynamodb"
   DB_BACKEND_WAVE1: "postgres"
+  DB_BACKEND_WAVE2: "postgres"
```

Nothing in `helm/` changes; `DB_BACKEND_WAVE2` is already declared in both settings modules and the
ConfigMap template ranges over `.Values.config` generically.

**dev ArgoCD is fully automated** — `{"automated":{"prune":true,"selfHeal":true}}`, targetRevision
`main`. A merge to gitops main auto-deploys; there is no manual gate. `checksum/config` on both
deployments means the ConfigMap change triggers a rolling restart, which is required because `envFrom`
binds at container start. `replicas: 1` on both, so old (DynamoDB) and new (PostgreSQL) pods briefly
coexist writing to different stores — keep admin traffic off during the roll.

Local gitops clone may be behind: ArgoCD reported synced revision `244b9cce`, not present locally.
Fetch before writing the diff.

## Wave 1's proven sequence (D28: copy → verify → switch reads → unblock)

1. Deliver creds and PG config with the flag still absent.
2. Copy, then parity-gate against the real dev database.
3. Flip both values files in one commit.
4. Soak before the next wave.

Values-file warnings worth preserving verbatim in the wave-2 comment block: the image tag is part of
the setting; reverting restores routing but not data; absence of a wave key means DynamoDB so a wave
cannot move by accident.

## The mask divergence (found during the merge trial, not in the original research)

`_store_credential` returns `None` for `None` or whitespace, so a blank PAT leaves the pointer columns
untouched and the credential is preserved. But it accepts **any other non-empty string**, including a
mask. `admin_db_service._is_a_real_credential` rejects masks by stripping `*•·●∙`, but that guard is
**inert under PostgreSQL** because it reads `stored_pat` from `provider_config`, which by constraint
can never hold a PAT.

Net effect, PostgreSQL only: an admin form rendering a mask writes the mask to Secrets Manager as the
credential and destroys the real PAT. DynamoDB is unaffected, so no existing test fails.
Fix: `_store_credential` must use the same predicate as `_is_a_real_credential`, shared, not its own
weaker check.

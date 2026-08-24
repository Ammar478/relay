# Acceptance contract: wave 2 PostgreSQL cutover (dev)

Written before implementation. Frozen once the plan is approved; changing it mid-run requires an
explicit pause and a re-plan logged in relay.md.

A check passes only with the evidence it names. No evidence means blocked, not passed.

---

## MERGE — branch reconciliation

### ACC-MERGE-001 — develop lands on the wave-2 branch with nothing lost
`feature/non-prod-wave2-cutover` contains every commit currently on `develop`, and both sides'
changes to `admin_db_service.py` survive: `_is_a_real_credential`, the `stored_pat` merge guard,
and `_assert_members_exist` are all present and reachable.
**Evidence:** `git rev-list --count <branch>..develop` = 0; grep counts for all three symbols; test run.

### ACC-MERGE-002 — the security branch lands with nothing lost
The per-agent entitlement layer, the guest opt-in flag, session ownership, `/process` auth and the
strict admin predicate are all present and their tests still pass after reconciliation.
**Evidence:** `tests/security` passes in full; `git rev-list --count <branch>..fix/agents-router-authentication` = 0.

### ACC-MERGE-003 — no method is lost in reconciliation
All five wave-2 write methods exist on the reconciled branch: `PgAgentRepository.put`,
`.update_fields`, `.delete`; `PgAgentGroupMappingRepository.put`; `PgUserGroupMappingRepository.put`.
**Evidence:** symbol probe against the reconciled tree.

---

## WRITE — PostgreSQL write methods are substitutable for DynamoDB

### ACC-WRITE-001 — creating an agent stores it and it reads back identically
Creating an agent through the admin path writes one row to `catalog.agents`, and reading it back
returns the same name, description, agentType, instructions and provider config.
**Evidence:** pg integration test against a real PostgreSQL; row count 1; field-by-field assertion.

### ACC-WRITE-002 — put is an upsert, not a duplicate
Putting the same agent twice leaves exactly one row, with the second write's values.
**Evidence:** pg test asserting `count(*) = 1` and last-write-wins.

### ACC-WRITE-003 — the DynamoDB id is stored as legacy_id and the primary key is a uuid v7
**Evidence:** pg test asserting `legacy_id` equals the caller's id and `id.bytes[6] >> 4 == 7`.

### ACC-WRITE-004 — updating an agent changes only the fields supplied
An update carrying one field leaves every other column untouched.
**Evidence:** pg test comparing the full row before and after.

### ACC-WRITE-005 — an unknown update field is refused, not silently dropped
`update_fields` with a field that maps to no column raises rather than ignoring it, so the two
backends cannot diverge silently.
**Evidence:** pg test asserting the raise and the message naming the field.

### ACC-WRITE-006 — update_fields is not an injection surface
A field name that is not in the allow-list is never interpolated, and a value containing SQL is
stored literally.
**Evidence:** pg tests mirroring `TestUpdateFieldsIsNotAnInjectionSurface` from wave 1.

### ACC-WRITE-007 — deleting an agent removes it and cascades its mappings
**Evidence:** pg test asserting the agent row is gone and `catalog.agent_groups` rows for it are gone.

### ACC-WRITE-008 — deleting an absent agent is silent
**Evidence:** pg test asserting no exception and no row change.

### ACC-WRITE-009 — a mapping put conflicts on the natural pair, not on legacy_id
Assigning the same user to the same group twice, or the same agent to the same group twice, leaves
exactly one row. Both callers mint a fresh uuid4 per call, so a `legacy_id` conflict target can
never fire and would raise `UniqueViolation`.
**Evidence:** pg test putting the same pair twice and asserting `count(*) = 1` for both mapping tables.

### ACC-WRITE-010 — a mapping put with a dangling reference is refused, not silently dropped
Assigning a group membership for a user id that does not exist in `iam.users` does not create a row
and does not fail silently.
**Evidence:** pg test asserting the observable outcome and that no partial row exists.

### ACC-WRITE-011 — a mapping row is always deletable
No mapping row is written with a NULL `legacy_id`, because `delete_all` matches on `legacy_id` and a
NULL row would be permanently un-deletable.
**Evidence:** pg test writing via `put` then deleting via `delete_all` and asserting the row is gone.

### ACC-WRITE-012 — a group role survives a round trip regardless of case
A stored role of `Agent_Manager` is not silently dropped.
**Evidence:** pg test asserting the enum value after put.

### ACC-WRITE-013 — constraint-shaped inputs are normalised, not rejected at the database
An agent whose `agentType` is `Snowflake` (uppercase) and whose name yields a slug is written
successfully, because the repository normalises before insert rather than letting the CHECK fail.
**Evidence:** pg test creating such an agent and asserting the stored `provider` and `slug`.

---

## CRED — credential handling cannot destroy a PAT

### ACC-CRED-001 — a real new PAT is stored in Secrets Manager and pointed to from the row
**Evidence:** pg test with a stubbed vault asserting `store_agent_pat` was called and both
`pat_secret_arn` and `pat_secret_alias` are set on the row.

### ACC-CRED-002 — a blank PAT leaves the stored credential untouched
An update carrying an empty or whitespace `cortex_agent_pat` does not change `pat_secret_arn` or
`pat_secret_alias`, and does not call the vault.
**Evidence:** pg test asserting both columns unchanged and no vault call.

### ACC-CRED-003 — a masked PAT leaves the stored credential untouched
An update carrying `********`, `••••••` or `***` does not overwrite the credential. This currently
DIVERGES: the DynamoDB guard rejects masks, the PostgreSQL path stores them.
**Evidence:** pg test per mask value asserting the vault was not called and the pointers are unchanged.

### ACC-CRED-004 — the two backends agree on what counts as a credential
For the same set of inputs, the DynamoDB guard and the PostgreSQL write path reach the same decision.
**Evidence:** a table-driven test over both paths asserting identical outcomes.

### ACC-CRED-005 — no credential ever reaches a database column
An agent written with a PAT anywhere in its config or instructions, under any key spelling the
constraint would catch, is stored with that value removed.
**Evidence:** pg test asserting `ops.contains_secret` is false for the stored row, over the adversarial
key set already used by the copy tests.

### ACC-CRED-006 — the pointer columns are never half-written
`pat_secret_arn` and `pat_secret_alias` are always both set or both null.
**Evidence:** pg test; `agents_pat_secret_pair_ck` not violated on any path.

---

## PARITY — the read path returns what the write path stored

### ACC-PARITY-001 — every column the application uses survives a round trip
An agent written with a non-default `queue_name`, `tool_choice`, `max_threads`, `session_ttl_days`,
`max_session_messages`, `rate_limit_per_minute`, `rate_limit_per_hour` and `tools_enabled` reads back
with those values, not with `AgentConfig` defaults.
**Evidence:** pg test asserting each field after a put/get cycle.

### ACC-PARITY-002 — guest access survives a round trip
An agent with `guestAccessEnabled` true reads back true, and one without reads back false.
**Evidence:** pg test asserting both directions.

### ACC-PARITY-003 — a read-modify-write cycle does not silently disable guest access
Updating an unrelated field on a guest-enabled agent leaves it guest-enabled.
**Evidence:** pg test.

### ACC-PARITY-004 — the live dev data matches between stores after the copy
Row counts and a field-level checksum agree between DynamoDB and PostgreSQL for agents, agent
groups and user groups, and the checksum covers the columns in ACC-PARITY-001.
**Evidence:** `copy_wave2.py verify` output, run against dev.

---

## IMAGE — the deployed artefact carries what the flag needs

### ACC-IMAGE-001 — the deployed dev image contains the per-wave resolver, the PAT resolver and all five write methods
**Evidence:** the symbol probe in `.relay/research/cutover-mechanics.md`, run against the running pod,
printing True for all three lines.

### ACC-IMAGE-002 — the image is deployed and soaking with the flag still absent
The new image runs in dev with `DB_BACKEND_WAVE2` unset and the service is healthy, proving the image
independently of the flag.
**Evidence:** pod image tag, pod Running with 0 restarts, `DB_BACKEND_WAVE2` absent from pod env.

### ACC-IMAGE-003 — migration 0003 is applied to dev
`catalog.agents.guest_access_enabled` exists and `ops.alembic_version` reads `0003_agent_guest_access`.
**Evidence:** two SQL queries against dev.

---

## CUTOVER — the flip

### ACC-CUTOVER-001 — both services flip in a single commit
`DB_BACKEND_WAVE2: "postgres"` is added to the agent-service and auth-service dev values in one
commit, because `iam.user_groups` is shared and splitting them puts it in two stores.
**Evidence:** the commit diff.

### ACC-CUTOVER-002 — every wave-2 repository binds to its PostgreSQL implementation after the roll
**Evidence:** in-pod probe printing `Pg*` for `agent_repository`, `agent_group_mapping_repository`,
`user_group_mapping_repository` in agent-service and `user_group_mapping_repository` in auth-service.

### ACC-CUTOVER-003 — five of seven agents register with a resolved credential
The two known credential-less agents are unchanged; no other agent loses its credential.
**Evidence:** pod logs, plus a count of agents whose resolved PAT is non-empty.

### ACC-CUTOVER-004 — a real chat succeeds against a migrated agent
A live request through an entitled path returns a Cortex response, proving the credential resolves
end to end from Secrets Manager.
**Evidence:** HTTP status and a non-empty response body.

### ACC-CUTOVER-005 — agent administration works against PostgreSQL
Creating, updating and deleting an agent through the admin API each succeed and are visible in
`catalog.agents`.
**Evidence:** three API calls with status codes, and the row state after each.

### ACC-CUTOVER-006 — group membership administration works against PostgreSQL
Adding and removing a group member round-trips through `iam.user_groups`.
**Evidence:** API call plus row state.

### ACC-CUTOVER-007 — login still works and agent_manager is still derived
Wave 1's login path, which reads wave-2 data to derive `agent_manager`, is unaffected.
**Evidence:** the internal by-entra lookup returning a user with their groups and roles intact.

### ACC-CUTOVER-008 — the two credential-less agents fail no worse than before
Their behaviour is unchanged by the cutover.
**Evidence:** before-and-after comparison of the same request.

---

## ROLLBACK — the revert is understood before it is needed

### ACC-ROLLBACK-001 — the revert procedure is written down and its data cost is stated
A documented procedure names the exact change, and states which writes are stranded: agents created,
edited or deleted, group assignments, memberships and role grants, and a rotated PAT that DynamoDB
never saw.
**Evidence:** the MR description or a GitLab issue containing it.

### ACC-ROLLBACK-002 — reverting restores service
Setting the flag back and rolling returns every wave-2 repository to its DynamoDB implementation and
the service is healthy.
**Evidence:** in-pod probe after the revert, if the revert is exercised.

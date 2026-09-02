# Domain Operations and Django Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add permission-protected, audited, Worker-backed AD user/computer bulk operations and comprehensive Django admin registrations.

**Architecture:** Extend the existing durable task queue with a domain-operation task type and dedicated executor. Keep LDAP behavior behind a narrow client interface, enforce action allowlists and base-DN boundaries server-side, and consume password payloads exactly once through an encrypted short-lived model.

**Tech Stack:** Python 3.12+, Django 5.2+, ldap3 2.9+, cryptography/Fernet, Django auth permissions, Bootstrap templates, threaded Worker.

**Spec:** `docs/superpowers/specs/2026-09-01-asset-dashboard-domain-operations-design.md`

## Global Constraints

- Permission codename is `net.manage_domain_operations`; superusers inherit it and other users may receive it directly or through groups.
- Domain lists remain read-only for callers without the permission; settings, test, sync, and writes require it.
- No plaintext or ciphertext password may enter task snapshots, logs, messages, audit summaries, exports, or admin lists.
- Domain computer actions include OU move, group add/remove, enable, disable, and supported unlock.
- Real LDAP is not contacted by automated tests.

---

### Task 1: Add domain operation, audit, permission, and one-time secret models

**Files:**
- Create: `net/domain_models.py`
- Modify: `net/asset_models.py`
- Modify: `net/models.py`
- Modify: `net/task_models.py`
- Modify: `requirements.txt`
- Create: `net/migrations/0016_domain_operations.py`
- Test: `index/test_domain_operation_models.py`

**Interfaces:**
- Produces: stable domain object identity fields, `DomainOperation`, `DomainOperationSecret`, `TaskRun.TaskType.DOMAIN_OPERATION`, and `manage_domain_operations` permission.
- Consumes: existing `TaskRun` and `TaskTargetRun` state contracts.

- [ ] **Step 1: Write failing model and permission tests**

```python
def test_domain_operation_has_custom_permission_and_no_password_snapshot(self):
    operation = DomainOperation.objects.create(
        action='disable', object_type='account', requested_by=self.user,
        parameter_summary={'reason': '离职'}, target_count=1,
    )
    permission = Permission.objects.get(codename='manage_domain_operations')
    self.assertEqual(permission.content_type.app_label, 'net')
    self.assertNotIn('password', json.dumps(operation.parameter_summary).lower())

def test_domain_secret_is_one_to_one_and_not_exported_from_models_public_api(self):
    self.assertFalse(hasattr(models, 'DomainOperationSecret'))
```

- [ ] **Step 2: Run tests and verify missing models**

Run: `.venv\Scripts\python.exe manage.py test index.test_domain_operation_models -v 2`

Expected: FAIL because operation models/task type do not exist.

- [ ] **Step 3: Add operation/audit model**

Create UUID `DomainOperation` with choices for account/computer object types and all approved actions, requester FK using `settings.AUTH_USER_MODEL`, status, target count, JSON parameter summary, task one-to-one, timestamps, and immutable audit fields. Define custom permission in `Meta.permissions`.

- [ ] **Step 4: Add stable domain object identities**

Add nullable unique `object_guid` and indexed `distinguished_name` fields to `Domain_Account` and `Domain_Computer`. Update domain synchronization to persist both attributes. Existing rows remain valid; write actions reject targets without a resolvable DN and may refresh them from LDAP by login/computer name before execution.

- [ ] **Step 5: Add private one-time secret model**

Create `DomainOperationSecret` in `domain_models.py` but do not re-export it from `net.models`. Store operation one-to-one, encrypted payload `BinaryField`, created/expires timestamps, and SHA-256 purpose fingerprint. Add `cryptography>=45,<47` to requirements.

- [ ] **Step 6: Extend task type and target choices**

Add `DOMAIN_OPERATION`, target types `domain_account`/`domain_computer`, and route it through immutable snapshots containing only action, target IDs/DNs, and non-sensitive parameters.

- [ ] **Step 7: Run model and migration tests**

Run: `.venv\Scripts\python.exe manage.py test index.test_domain_operation_models index.test_phase2_task_models -v 2`

Run: `.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`

Expected: PASS and no migration drift.

- [ ] **Step 8: Commit**

```powershell
git add py/net/net/domain_models.py py/net/net/asset_models.py py/net/net/models.py py/net/net/task_models.py py/net/net/migrations/0016_domain_operations.py py/net/requirements.txt py/net/index/test_domain_operation_models.py
git commit -m "feat: model audited domain operations"
```

---

### Task 2: Implement AD action service and one-time secret consumption

**Files:**
- Create: `net/domain/__init__.py`
- Create: `net/domain/actions.py`
- Create: `net/domain/client.py`
- Create: `net/domain/secrets.py`
- Create: `net/domain/validation.py`
- Modify: `net/services/ad_sync.py`
- Modify: `net/settings.py`
- Test: `index/test_domain_actions.py`
- Test: `index/test_domain_secrets.py`

**Interfaces:**
- Produces: `validate_domain_action(object_type, action, parameters)`, `DomainClient.execute(...)`, `store_operation_secret(...)`, and `consume_operation_secret(...)`.
- Consumes: singleton domain config and `DOMAIN_OPERATION_ENCRYPTION_KEY`.

- [ ] **Step 1: Write failing action-allowlist and DN-boundary tests**

```python
def test_computer_action_allowlist(self):
    for action in ('move_ou', 'add_group', 'remove_group', 'enable', 'disable', 'unlock'):
        validate_domain_action('computer', action, self.valid_parameters(action))
    with self.assertRaises(ValidationError):
        validate_domain_action('computer', 'password_never_expires', {})

def test_destination_must_be_inside_base_dn(self):
    with self.assertRaises(ValidationError):
        validate_dn_within_base('OU=Outside,DC=other,DC=test', 'DC=example,DC=test')
```

- [ ] **Step 2: Write failing single-consumption secret tests**

Store a password, consume it once, assert the private row is deleted in the same transaction, a second consume raises a non-secret `ValidationError`, and neither `repr`, exception text, nor captured logs contains plaintext/ciphertext.

- [ ] **Step 3: Run tests and verify missing-service failures**

Run: `.venv\Scripts\python.exe manage.py test index.test_domain_actions index.test_domain_secrets -v 2`

Expected: FAIL on missing modules.

- [ ] **Step 4: Implement normalized action validation**

Define exact account and computer allowlists, required parameters by action, DN canonicalization, base-DN suffix checks using parsed RDN components rather than string suffixes, and target-count bounds.

- [ ] **Step 5: Implement Fernet key handling and one-time consume**

Read `DOMAIN_OPERATION_ENCRYPTION_KEY` from environment, reject missing/invalid keys only when a password action is requested, encrypt UTF-8 JSON, and consume with `select_for_update()` plus delete before returning the in-memory dict. Expired payloads are deleted and rejected.

- [ ] **Step 6: Implement LDAP client methods**

Wrap ldap3 operations for add user, modify DN, group member add/remove, Unicode password reset, UAC enable/disable, lockout clear, and password flags. Return typed non-sensitive result objects and map LDAP result codes to Chinese operator errors.

- [ ] **Step 7: Refactor connection construction for reuse**

Extract existing SSL/server/bind behavior from `ad_sync.py` into `DomainClient.connect()` while preserving connection-test and synchronization behavior, including prior list/int port normalization fixes.

- [ ] **Step 8: Run service and existing domain-sync tests**

Run: `.venv\Scripts\python.exe manage.py test index.test_domain_actions index.test_domain_secrets index.tests.DomainControllerSettingsTests index.tests.DomainWorkspaceTests -v 2`

Expected: PASS with mocked ldap3 only.

- [ ] **Step 9: Commit**

```powershell
git add py/net/net/domain py/net/net/services/ad_sync.py py/net/net/settings.py py/net/index/test_domain_actions.py py/net/index/test_domain_secrets.py
git commit -m "feat: implement safe AD action service"
```

---

### Task 3: Queue and execute domain operations in the threaded Worker

**Files:**
- Create: `net/tasks/domain.py`
- Create: `net/tasks/executors/domain.py`
- Modify: `net/tasks/worker.py`
- Modify: `net/tasks/state.py`
- Modify: `index/views/tasks.py`
- Test: `index/test_domain_worker.py`

**Interfaces:**
- Produces: `enqueue_domain_operation(...) -> DomainOperation`, `prepare_domain_task_context(task) -> DomainTaskContext`, `execute_domain_target(..., domain_context)`, and failed-target retry support.
- Consumes: validated actions, one-time secrets, and `TaskRun` lease fencing.

- [ ] **Step 1: Write failing queue snapshot tests**

Assert that enqueue creates one target per selected account/computer, captures stable target DN/name, links operation/task, and that serialized snapshots contain no submitted password or encrypted payload.

- [ ] **Step 2: Write failing Worker partial-success tests**

Mock two successes and one LDAP failure; run `TaskWorker.run_once()`, then assert operation/task are partial, target statuses are success/success/failed, audit summary is non-sensitive, and only failed targets are selected by retry.

- [ ] **Step 3: Run tests and verify missing executor failures**

Run: `.venv\Scripts\python.exe manage.py test index.test_domain_worker -v 2`

Expected: FAIL because the enqueue/executor dispatch does not exist.

- [ ] **Step 4: Implement transactional enqueue**

Validate permission at the view boundary and validation again inside enqueue. Lock selected objects, create the operation, task, target snapshots, and optional secret atomically. Use a deterministic scope key to reject duplicate active actions for the same action/targets.

- [ ] **Step 5: Implement domain target executor**

Fence writes by task claim generation. Add a frozen `DomainTaskContext` containing only the claimed task identity and optional in-memory password. `prepare_domain_task_context()` runs once before the thread pool is created, atomically consumes the one-time secret, and returns the context. Execute allowlisted actions through `DomainClient`, persist redacted errors, and update operation timestamps/status after task aggregation.

- [ ] **Step 6: Route domain tasks through Worker**

Dispatch `DOMAIN_OPERATION` to domain executor and domain-specific failure persistence. Extend `_execute_claimed_task` to prepare the domain context once and pass it read-only to `_run_target`; non-domain tasks receive `None`. Preserve the existing bounded thread pool and lease behavior; serialize actions targeting the same DN within one task. Clear the context reference when the executor shuts down.

- [ ] **Step 7: Implement failed-target retry**

Create a new task/operation referencing only failed target snapshots after revalidation. Password actions must request a new password and never reuse consumed secret material.

- [ ] **Step 8: Run Worker and lease regression tests**

Run: `.venv\Scripts\python.exe manage.py test index.test_domain_worker index.test_phase2_worker index.test_phase2_queue index.test_final_fix_evidence -v 2`

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add py/net/net/tasks/domain.py py/net/net/tasks/executors/domain.py py/net/net/tasks/worker.py py/net/net/tasks/state.py py/net/index/views/tasks.py py/net/index/test_domain_worker.py
git commit -m "feat: execute domain operations in worker"
```

---

### Task 4: Add permission-protected domain management UI

**Files:**
- Create: `index/forms/domain.py`
- Create: `index/views/domain_operations.py`
- Modify: `index/views/domain.py`
- Modify: `index/views/__init__.py`
- Modify: `index/urls.py`
- Modify: `index/templates/domain_controller_settings.html`
- Create: `index/templates/domain/operation_modal.html`
- Modify: `index/templates/domain_object_list.html`
- Modify: `index/templates/tasks/detail.html`
- Test: `index/test_domain_permissions_ui.py`

**Interfaces:**
- Produces: domain operation create/retry endpoints and permission-aware settings/actions.
- Consumes: `enqueue_domain_operation` and Django permission `net.manage_domain_operations`.

- [ ] **Step 1: Write failing four-role permission tests**

Test anonymous/current-site caller, authenticated unprivileged user, permitted staff user, and superuser. Lists remain read-only; settings POST, test, sync, operation create, and retry return 403 for unprivileged callers and succeed for permitted/superuser callers.

- [ ] **Step 2: Write failing computer-operation UI tests**

Assert selected domain computers expose move OU, add/remove group, enable, disable, and unlock; password actions do not appear. Account forms expose the account-specific password/expiry actions.

- [ ] **Step 3: Run tests and verify permission/UI failures**

Run: `.venv\Scripts\python.exe manage.py test index.test_domain_permissions_ui -v 2`

Expected: FAIL because current settings POST is unrestricted and operation routes do not exist.

- [ ] **Step 4: Split read-only workspace from privileged settings**

Always render account/computer summaries and lists. Render the connection-settings button and forms only when `request.user.has_perm('net.manage_domain_operations')`; enforce `permission_required(..., raise_exception=True)` on every mutation endpoint. Never prepopulate bind password.

- [ ] **Step 5: Add bulk-selection and confirmation modal**

Use checked row IDs, object type, action, and validated action-specific fields. Show action/target count/destination before POST. Keep password inputs out of browser persistence attributes and never echo submitted password after validation failure.

- [ ] **Step 6: Add operation progress/retry links**

Task detail shows domain action, redacted parameters, target names/DNs, success/failure counts, and result errors. Show retry only for permitted users and terminal tasks with failed targets.

- [ ] **Step 7: Run domain UI and public-output tests**

Run: `.venv\Scripts\python.exe manage.py test index.test_domain_permissions_ui index.test_public_urls index.test_phase4_people_ui -v 2`

Expected: PASS and no secret values in response bodies.

- [ ] **Step 8: Commit**

```powershell
git add py/net/index/forms/domain.py py/net/index/views/domain_operations.py py/net/index/views/domain.py py/net/index/views/__init__.py py/net/index/urls.py py/net/index/templates/domain_controller_settings.html py/net/index/templates/domain/operation_modal.html py/net/index/templates/domain_object_list.html py/net/index/templates/tasks/detail.html py/net/index/test_domain_permissions_ui.py
git commit -m "feat: add privileged domain management UI"
```

---

### Task 5: Complete Django admin and targeted directory organization

**Files:**
- Rewrite: `net/admin.py`
- Create: `net/admin_assets.py`
- Create: `net/admin_operations.py`
- Create: `net/admin_records.py`
- Create: `net/admin_tasks.py`
- Modify: `net/apps.py`
- Test: `index/test_admin_registry.py`

**Interfaces:**
- Produces: complete admin registrations with search/filter/read-only contracts.
- Consumes: all existing and new public models; never registers `DomainOperationSecret`.

- [ ] **Step 1: Write failing registry tests**

```python
def test_required_models_are_registered(self):
    for model in REQUIRED_ADMIN_MODELS:
        self.assertIn(model, admin.site._registry)

def test_domain_secret_is_not_registered(self):
    self.assertNotIn(DomainOperationSecret, admin.site._registry)

def test_task_snapshots_and_audit_fields_are_read_only(self):
    task_admin = admin.site._registry[TaskRun]
    self.assertIn('profile_snapshot', task_admin.readonly_fields)
    self.assertIn('target_scope_snapshot', task_admin.readonly_fields)
```

- [ ] **Step 2: Run tests and verify missing registrations**

Run: `.venv\Scripts\python.exe manage.py test index.test_admin_registry -v 2`

Expected: FAIL for People, domain accounts, tasks, schedules, alerts, and records.

- [ ] **Step 3: Split admin definitions by responsibility**

Import focused admin modules from `net/admin.py`. Configure assets/domain, records/errors, tasks/schedules/integrations, and operations/alerts separately. Add list displays, searches, filters, raw-ID/autocomplete relations, date hierarchy where useful, and read-only generated/audit fields.

- [ ] **Step 4: Enforce secret-safe admin behavior**

Do not register `DomainOperationSecret`. Mask saved domain bind password with a custom form that leaves blank to preserve. Exclude all device credential fields from list displays and searches.

- [ ] **Step 5: Run admin and Django checks**

Run: `.venv\Scripts\python.exe manage.py test index.test_admin_registry -v 2`

Run: `.venv\Scripts\python.exe manage.py check`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add py/net/net/admin.py py/net/net/admin_assets.py py/net/net/admin_operations.py py/net/net/admin_records.py py/net/net/admin_tasks.py py/net/net/apps.py py/net/index/test_admin_registry.py
git commit -m "feat: complete monitoring admin"
```

---

### Task 6: Final integration verification and documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/deployment.md`
- Create: `.env.example`
- Test: full suites

**Interfaces:**
- Consumes: Tasks 1–5 and the two preceding implementation plans.
- Produces: one verified, deployable release with permission and key setup documented.

- [ ] **Step 1: Document permissions and encryption-key generation**

Document how to create a Fernet key, set `DOMAIN_OPERATION_ENCRYPTION_KEY`, grant `net.manage_domain_operations` to a Django group, run the Worker, and interpret partial domain-operation results. State that password-task Worker interruption requires resubmission.

- [ ] **Step 2: Seed non-sensitive domain-operation demo history**

Extend `seed_demo_data` with terminal move/enable examples only; never seed password payloads. Confirm idempotency by running the command twice against `demo-runtime/demo.sqlite3`.

- [ ] **Step 3: Run the complete verification matrix**

Run all Django and JS tests, `manage.py check`, migration drift check, `pip check`, demo migration/seed, and focused response secret scans. Confirm the original `db.sqlite3` hash is unchanged.

- [ ] **Step 4: Review scoped Git diff**

Inspect only `py/net/**`, ensure no unrelated parent-repository changes are staged, run `git diff --check`, and verify generated runtime files are ignored.

- [ ] **Step 5: Commit release documentation and demo updates**

```powershell
git add py/net/README.md py/net/docs/deployment.md py/net/.env.example py/net/net/management/commands/seed_demo_data.py
git commit -m "docs: deploy domain operations safely"
```

# Phase 4 Integrations and Configuration Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add previewed Feishu/DingTalk personnel synchronization, source-scoped deactivation, network/security configuration downloads, deterministic complete demo data, and Windows/Linux Worker service documentation.

**Architecture:** Third-party directory adapters normalize paginated API responses into one employee shape, while a transactional sync service previews and applies changes by employee number and source. Configuration export adapters return supported/unsupported/failed results and feed single-file or ZIP responses using the already validated asset filter.

**Tech Stack:** Django, Requests, Python ZIP/CSV/JSON, existing Worker and collector adapters, Windows service wrapper documentation, systemd.

**Spec:** `docs/superpowers/specs/2026-08-30-automation-alerting-and-records-design.md`

## Global Constraints

- Personnel matching uses employee number only.
- Missing employees are deactivated only within the same API source; manual/CSV/other-source people are untouched.
- API sync always supports connection test and preview before apply.
- Network/security config export preserves vendor raw text/JSON but excludes all application credentials.
- Unsupported vendor APIs are reported explicitly rather than exported as empty successful files.
- Demo seeding and tests never call real external systems.
- Every behavior change follows RED → GREEN → focused tests → full tests → commit.

---

### Task 1: Personnel synchronization source and normalized adapter contract

**Files:**
- Create: `net/models/integrations.py`
- Modify: `net/models/__init__.py`
- Create: `net/migrations/0004_people_sync_sources.py`
- Create: `net/integrations/__init__.py`
- Create: `net/integrations/people/base.py`
- Create: `index/test_phase4_people_contract.py`

**Interfaces:**
- Produces `PeopleSyncSource` with type, name, credentials, root department ids, enabled, last tested/synced timestamps.
- `DirectoryPerson(employee_id, name, email, department, leader, external_user_id)`.
- `DirectoryAdapter.test_connection()`, `.iter_people()`, and `.source_key`.

- [ ] **Step 1: Write failing contract/model tests**

Test required employee number, stable normalization, masked credentials, source uniqueness, and no secret inclusion in serializers/templates/string values.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase4_people_contract -v 2`

- [ ] **Step 3: Implement source model and adapter protocol**

Use explicit exceptions: `DirectoryAuthenticationError`, `DirectoryRateLimitError`, `DirectoryPayloadError`. Adapter pages yield normalized people and do not write the database.

- [ ] **Step 4: Generate migration, run tests, commit**

```bash
git add py/net/net/models py/net/net/migrations py/net/net/integrations py/net/index/test_phase4_people_contract.py
git commit -m "feat: define external personnel sync sources"
```

### Task 2: Feishu and DingTalk directory adapters

**Files:**
- Create: `net/integrations/people/feishu.py`
- Create: `net/integrations/people/dingtalk.py`
- Create: `index/test_phase4_directory_adapters.py`

**Interfaces:**
- Both adapters implement the phase-four base contract and accept an injected HTTP session for tests.

- [ ] **Step 1: Write failing API fixture tests**

Use local response fixtures to cover token acquisition, department traversal, pagination, employee-number extraction, missing employee number skip/report, rate limit, malformed response, timeout, and secret-redacted errors.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase4_directory_adapters -v 2`

- [ ] **Step 3: Implement Feishu adapter**

Fetch tenant token, traverse configured department roots, follow page tokens, and normalize official contact fields. Never persist access tokens.

- [ ] **Step 4: Implement DingTalk adapter**

Fetch application token, traverse configured department roots, follow cursor pagination, and normalize official contact fields. Never persist access tokens.

- [ ] **Step 5: Run tests and commit**

```bash
git add py/net/net/integrations/people py/net/index/test_phase4_directory_adapters.py
git commit -m "feat: read feishu and dingtalk personnel directories"
```

### Task 3: Preview and source-scoped synchronization service

**Files:**
- Create: `net/integrations/people/sync.py`
- Create: `index/test_phase4_people_sync.py`

**Interfaces:**
- `preview_people_sync(source, adapter) -> SyncPreview`.
- `apply_people_sync(source, preview) -> SyncResult`.
- Preview contains creates, updates, unchanged, deactivations, skipped, and validation messages.

- [ ] **Step 1: Write failing sync behavior tests**

Cover create, update by employee number, idempotent repeat, deactivate missing same-source person, preserve CSV/manual/other-API person, duplicate employee number in remote response, and transaction rollback on invalid preview.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase4_people_sync -v 2`

- [ ] **Step 3: Implement pure preview diff**

Normalize remote records by employee number before comparing. Preview is serializable and bound to a short-lived session token/hash so apply cannot silently use changed source data.

- [ ] **Step 4: Implement transactional apply**

Lock same-source `People` rows, upsert creates/updates, mark missing same-source rows inactive, and set last sync metadata. Do not delete people.

- [ ] **Step 5: Run tests and commit**

```bash
git add py/net/net/integrations/people/sync.py py/net/index/test_phase4_people_sync.py
git commit -m "feat: preview and apply source scoped personnel sync"
```

### Task 4: Personnel API import UI

**Files:**
- Create: `index/forms/integrations.py`
- Create: `index/views/integrations.py`
- Modify: `index/views/__init__.py`
- Modify: `index/urls.py`
- Modify: `index/templates/import_modal.html`
- Create: `index/templates/integrations/source_modal.html`
- Create: `index/templates/integrations/preview_modal.html`
- Create: `index/test_phase4_people_ui.py`

**Interfaces:**
- Routes: source save, connection test, preview, apply.
- Apply requires preview token plus POST/CSRF.

- [ ] **Step 1: Write failing UI tests**

Assert CSV/Feishu/DingTalk tabs, masked source forms, test result, preview counts/sample rows, explicit confirmation, stale preview rejection, apply messages, and same-source deactivation display.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase4_people_ui -v 2`

- [ ] **Step 3: Implement source/test/preview/apply flow**

Only the people import modal renders API options. Failures reopen the relevant modal and preserve non-secret fields.

- [ ] **Step 4: Run tests and commit**

```bash
git add py/net/index/forms/integrations.py py/net/index/views/integrations.py py/net/index/templates/integrations py/net/index/test_phase4_people_ui.py
git commit -m "feat: import personnel from feishu and dingtalk"
```

### Task 5: Configuration export adapters and ZIP service

**Files:**
- Create: `net/exports/configuration.py`
- Create: `net/exports/adapters/__init__.py`
- Create: `net/exports/adapters/network.py`
- Create: `net/exports/adapters/security.py`
- Create: `index/views/config_exports.py`
- Modify: `index/views/__init__.py`
- Modify: `index/urls.py`
- Modify: network/security list and detail templates
- Create: `index/test_phase4_config_export.py`

**Interfaces:**
- `ConfigurationResult(status, filename, media_type, content, message)`.
- `latest_configuration(asset) -> ConfigurationResult`.
- `build_configuration_zip(assets) -> bytes` with `manifest.csv`.

- [ ] **Step 1: Write failing export/security tests**

Cover network text, security JSON, unsupported adapter, failed collection, missing latest result, safe filenames, duplicate names, filtered target set, manifest rows, and byte-for-byte absence of passwords/tokens/Webhook URLs.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase4_config_export -v 2`

- [ ] **Step 3: Implement adapter results**

Network export reads the latest successful `config_info`. Security export calls only registered vendor config adapters and otherwise returns `unsupported`.

- [ ] **Step 4: Implement single and filtered ZIP views**

Single download returns 404/409 with a clear message for missing/unsupported results. Batch export reuses phase-one filtered queryset and always includes a manifest for success, missing, unsupported, and failed targets.

- [ ] **Step 5: Run tests and commit**

```bash
git add py/net/net/exports py/net/index/views/config_exports.py py/net/index/urls.py py/net/index/templates py/net/index/test_phase4_config_export.py
git commit -m "feat: export device configurations"
```

### Task 6: Complete demo data, service examples, and final acceptance

**Files:**
- Modify: `net/management/commands/seed_demo_data.py`
- Create: `deploy/systemd/network-inspection-worker.service`
- Create: `deploy/windows/README.md`
- Modify: `README.md`
- Create: `index/test_phase4_demo.py`

**Interfaces:**
- Demo seed includes every phase without outbound calls.
- Service examples execute `.venv` Python with `manage.py run_task_worker` and configurable thread/poll/lease values.

- [ ] **Step 1: Write failing final demo tests**

Assert deterministic counts for sync sources, inactive same-source people, schedules, task states, analyses, alert/recovery events, delivery outcomes, and supported/unsupported config records. Patch HTTP/SMTP/SSH and assert no calls.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase4_demo -v 2`

- [ ] **Step 3: Complete deterministic seed command**

Use `.invalid` endpoints and clearly fake credentials. `--reset` deletes only application rows in explicit dependency order and prints created counts.

- [ ] **Step 4: Add cross-platform service instructions**

Document environment variables, MySQL requirement, Web and Worker as separate services, Windows service wrapper command, systemd unit, log paths, graceful restart, and a one-shot Worker health check.

- [ ] **Step 5: Run complete automated verification**

Run: `.venv\Scripts\python.exe manage.py check`

Run: `.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`

Run: `.venv\Scripts\python.exe manage.py test`

Run: `node --test static/js/table_workspace.test.js`

- [ ] **Step 6: Perform full browser acceptance**

Verify desktop and 390×844 dashboard, every static/dynamic/detail route, compact filters, filtered CSV, import modal, Feishu/DingTalk preview, manual tasks, schedules, alert settings/results, log ranges, config downloads, preference persistence, table-contained overflow, and zero console errors.

- [ ] **Step 7: Commit**

```bash
git add py/net/net/management/commands/seed_demo_data.py py/net/deploy py/net/README.md py/net/index/test_phase4_demo.py
git commit -m "docs: complete deployment and demo workflow"
```

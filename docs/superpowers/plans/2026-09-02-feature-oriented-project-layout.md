# Feature-Oriented Project Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the monitoring system into clear personnel, Active Directory, device, inspection, alert, integration, UI, static, agent, test, and deployment areas without changing its database or public interfaces.

**Architecture:** Keep `net` and `index` as the only existing Django applications and move implementation into feature-oriented packages inside them. Preserve database identity, URLs, startup modules, migration imports, and operator-facing Python imports with thin compatibility facades. Execute one reviewable phase at a time and wait for user approval after every phase.

**Tech Stack:** Python 3, Django 5, Django REST Framework, SQLite-compatible Django ORM, PowerShell, POSIX shell, Bootstrap, vanilla JavaScript, Node test runner, Git.

**Spec:** `docs/superpowers/specs/2026-09-02-feature-oriented-project-layout-design.md`

## Global Constraints

- Keep the Django application labels `net` and `index` unchanged.
- Keep existing model names, database tables, fields, relationships, permissions, and migration history unchanged.
- Do not edit or relocate files under `net/migrations/` except to verify they remain byte-for-byte present.
- Keep page paths, API paths, URL names, script download paths, and log upload paths unchanged.
- Keep `net.settings`, `net.urls`, `net.wsgi`, `net.asgi`, `deploy.demo`, and worker management commands importable.
- Keep historical migration imports `net.task_models`, `net.alert_models`, and `net.domain_models` importable with their referenced validators.
- Do not move or overwrite `db.sqlite3`, `demo-runtime/`, uploaded logs, processed/failed log directories, generated configurations, `.venv/`, `.worktrees/`, or `.task6-artifacts/`.
- Use `apply_patch` for source edits and `git mv` only for exact, reviewed file moves.
- Preserve unrelated user changes.
- Run only the focused checks listed for the current task; run the complete suite once in Task 7.
- Stop after each task, present the commit and checks, and wait for user approval.

---

### Task 1: Add Package Skeleton and Compatibility Contracts

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/architecture/__init__.py`
- Create: `tests/architecture/test_compatibility_contracts.py`
- Create: `net/people/__init__.py`
- Create: `net/devices/__init__.py`
- Create: `net/devices/pc/__init__.py`
- Create: `net/devices/server/__init__.py`
- Create: `net/devices/network/__init__.py`
- Create: `net/devices/security/__init__.py`
- Create: `net/inspections/__init__.py`
- Create: `net/data_exchange/__init__.py`
- Create: `net/dashboard/__init__.py`
- Create: `net/infrastructure/__init__.py`
- Create: `index/common/__init__.py`
- Create: `index/dashboard/__init__.py`
- Create: `index/people/__init__.py`
- Create: `index/domain/__init__.py`
- Create: `index/devices/__init__.py`
- Create: `index/devices/pc/__init__.py`
- Create: `index/devices/server/__init__.py`
- Create: `index/devices/network/__init__.py`
- Create: `index/devices/security/__init__.py`
- Create: `index/inspections/__init__.py`
- Create: `index/alerts/__init__.py`
- Create: `index/integrations/__init__.py`

**Interfaces:**
- Consumes: Existing `net.apps.NetConfig`, `net.models`, `net.urls`, `index.urls`, and Django model metadata.
- Produces: Feature package import paths and `CompatibilityContractTests`, which every later task must keep passing.

- [x] **Step 1: Write contract tests before creating feature packages**

Create `tests/architecture/test_compatibility_contracts.py` with concrete assertions:

```python
from importlib import import_module

from django.apps import apps
from django.test import SimpleTestCase
from django.urls import reverse


class CompatibilityContractTests(SimpleTestCase):
    def test_django_application_labels_remain_stable(self):
        self.assertEqual(apps.get_app_config("net").label, "net")
        self.assertEqual(apps.get_app_config("index").label, "index")

    def test_startup_and_legacy_modules_remain_importable(self):
        modules = (
            "net.settings", "net.urls", "net.wsgi", "net.asgi",
            "net.models", "net.task_models", "net.alert_models",
            "net.domain_models", "net.tasks", "index.urls", "index.views",
        )
        for module_name in modules:
            with self.subTest(module=module_name):
                self.assertIsNotNone(import_module(module_name))

    def test_migration_validators_remain_exported(self):
        task_models = import_module("net.task_models")
        alert_models = import_module("net.alert_models")
        domain_models = import_module("net.domain_models")
        self.assertTrue(callable(task_models.validate_string_list))
        self.assertTrue(callable(task_models.validate_json_object))
        self.assertTrue(callable(alert_models.validate_finite_json))
        self.assertTrue(callable(domain_models.validate_parameter_summary))

    def test_public_url_contract(self):
        self.assertEqual(reverse("index"), "/")
        self.assertEqual(reverse("people_statistics"), "/people/statistics/")
        self.assertEqual(reverse("domain_account_list"), "/domain/accounts/")
        self.assertEqual(reverse("domain_computer_list"), "/domain/computers/")
        self.assertEqual(reverse("computer_analysis_list"), "/computers/analyses/")
        self.assertEqual(reverse("task_list"), "/tasks/")
        self.assertEqual(reverse("alert_list"), "/alerts/")
```

- [x] **Step 2: Run the contract test and record the baseline**

Run:

```powershell
python manage.py test tests.architecture.test_compatibility_contracts --verbosity 2
```

Expected: PASS against the current layout. This is a characterization test, so it is allowed to pass before the move.

- [x] **Step 3: Create the listed package marker files**

Each marker contains a one-line ownership docstring matching its directory, for example:

```python
"""PC inventory and log-analysis feature package."""
```

Do not create `net/models/` or `net/admin/` yet because `net/models.py` and `net/admin.py` still occupy those import names.

- [x] **Step 4: Verify only package imports and Django wiring**

Run:

```powershell
python manage.py test tests.architecture.test_compatibility_contracts --verbosity 2
python manage.py check
```

Expected: contract tests pass and Django reports no issues.

- [x] **Step 5: Commit and stop for inspection**

```powershell
git add tests/architecture tests/__init__.py net/people net/devices net/inspections net/data_exchange net/dashboard net/infrastructure index/common index/dashboard index/people index/domain index/devices index/inspections index/alerts index/integrations
git commit -m "refactor: establish feature package boundaries"
```

---

### Task 2: Organize Models and Django Admin by Domain

**Files:**
- Replace file with package: `net/models.py` -> `net/models/__init__.py`
- Create: `net/models/people.py`
- Create: `net/models/domain.py`
- Create: `net/models/devices.py`
- Create: `net/models/records.py`
- Create: `net/models/tasks.py`
- Create: `net/models/alerts.py`
- Create: `net/models/integrations.py`
- Modify as compatibility facades: `net/asset_models.py`
- Modify as compatibility facade: `net/domain_models.py`
- Modify as compatibility facade: `net/record_models.py`
- Modify as compatibility facade: `net/task_models.py`
- Modify as compatibility facade: `net/alert_models.py`
- Modify as compatibility facade: `net/integration_models.py`
- Replace file with package: `net/admin.py` -> `net/admin/__init__.py`
- Move implementation: `net/admin_assets.py` -> `net/admin/assets.py`
- Move implementation: `net/admin_operations.py` -> `net/admin/domain.py`
- Move implementation: `net/admin_records.py` -> `net/admin/records.py`
- Move implementation: `net/admin_tasks.py` -> `net/admin/tasks.py`
- Create compatibility facades: `net/admin_assets.py`, `net/admin_operations.py`, `net/admin_records.py`, `net/admin_tasks.py`
- Create: `tests/architecture/test_model_contracts.py`
- Modify: `tests/architecture/test_compatibility_contracts.py`

**Interfaces:**
- Consumes: Current classes and validators from the six top-level `*_models.py` modules.
- Produces: Canonical model definitions under `net.models.*`; unchanged exports from `net.models` and historical validator modules; domain-grouped admin registration.

- [x] **Step 1: Add model identity and schema-contract tests**

Create `tests/architecture/test_model_contracts.py`:

```python
from django.apps import apps
from django.test import SimpleTestCase

from net.models import (
    AlertChannel, Computer, DomainOperation, Domain_Account, Domain_Computer,
    Monitor, Network_Device, People, Server, TaskRun,
)


class ModelContractTests(SimpleTestCase):
    EXPECTED_TABLES = {
        People: "net_people",
        Domain_Account: "net_domain_account",
        Domain_Computer: "net_domain_computer",
        Computer: "net_computer",
        Network_Device: "net_network_device",
        Server: "net_server",
        Monitor: "net_monitor",
        DomainOperation: "net_domainoperation",
        TaskRun: "net_taskrun",
        AlertChannel: "net_alertchannel",
    }

    def test_model_labels_and_tables_are_stable(self):
        for model, table in self.EXPECTED_TABLES.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(model._meta.app_label, "net")
                self.assertEqual(model._meta.db_table, table)
                self.assertIs(apps.get_model("net", model.__name__), model)
```

- [x] **Step 2: Run the model test against the old layout**

```powershell
python manage.py test tests.architecture.test_model_contracts --verbosity 2
```

Expected: PASS, confirming the values that must survive the move.

- [x] **Step 3: Move model definitions without changing class bodies**

Place models and their local validators in these canonical modules:

```python
# net/models/people.py
# People

# net/models/domain.py
# Domain_Account, Domain_Computer, Domain_Controller_Config,
# DomainOperation, DomainOperationSecret, contains_sensitive_payload,
# validate_parameter_summary

# net/models/devices.py
# Computer, Network_Device, Server, Monitor

# net/models/records.py
# RecordStatus, DynamicRecord, ComputerLogFile, ComputerLogArchive,
# ComputerAnalysis, InfrastructureRecord, all inspection and error models

# net/models/tasks.py
# AlertPolicyMode, InspectionProfile, ComputerAnalysisProfile, Schedule,
# TaskRun, TaskTargetRun, validate_string_list, validate_json_object,
# contains_sensitive_snapshot_value

# net/models/alerts.py
# all alert model classes and validate_finite_json

# net/models/integrations.py
# PeopleSyncSource and its validators
```

`net/models/__init__.py` imports the same public names currently exported by
`net/models.py` and retains:

```python
Computer_Inspection = ComputerAnalysis
```

Do not add `Meta.app_label`; Django continues to infer `net` because every canonical module is inside the `net` application package.

- [x] **Step 4: Convert historical model modules into explicit facades**

Each facade imports from the canonical module and defines `__all__`. Migration-referenced validators must be direct module attributes, for example:

```python
# net/task_models.py
from net.models.tasks import (
    AlertPolicyMode, ComputerAnalysisProfile, InspectionProfile, Schedule,
    TaskRun, TaskTargetRun, contains_sensitive_snapshot_value,
    validate_json_object, validate_string_list,
)
```

Use equivalent explicit exports for `net.alert_models` and `net.domain_models`.

- [x] **Step 5: Move admin registration into the `net.admin` package**

`net/admin/__init__.py` imports each registration module exactly once:

```python
from . import assets, domain, records, tasks  # noqa: F401
```

The four old `net/admin_*.py` paths become import-only facades so existing test or operator imports remain valid.

- [x] **Step 6: Run only model/admin compatibility checks**

```powershell
python manage.py test tests.architecture.test_compatibility_contracts tests.architecture.test_model_contracts index.test_admin_registry --verbosity 2
python manage.py makemigrations --check --dry-run
python manage.py check
```

Expected: focused tests pass, Django reports no changes, and system checks pass.

- [x] **Step 7: Commit and stop for inspection**

```powershell
git add net/models net/models.py net/*_models.py net/admin net/admin.py net/admin_*.py tests/architecture
git commit -m "refactor: organize models and admin by domain"
```

---

### Task 3: Consolidate Personnel and Active Directory Features

**Files:**
- Create: `net/people/importing.py` from `net/services/personnel_import.py`
- Create: `net/people/directory/base.py` from `net/integrations/people/base.py`
- Create: `net/people/directory/feishu.py` from `net/integrations/people/feishu.py`
- Create: `net/people/directory/dingtalk.py` from `net/integrations/people/dingtalk.py`
- Create: `net/people/directory/sync.py` from `net/integrations/people/sync.py`
- Create: `net/people/tasks.py` from `net/tasks/people.py`
- Create: `net/people/executor.py` from `net/tasks/executors/people.py`
- Convert old personnel modules into compatibility facades.
- Move: `net/services/ad_sync.py` -> `net/domain/sync.py`
- Move: `net/tasks/domain.py` -> `net/domain/tasks.py`
- Move: `net/tasks/executors/domain.py` -> `net/domain/executor.py`
- Convert the three old domain workflow paths into compatibility facades.
- Move: `index/views/integrations.py` -> `index/people/integrations.py`
- Move: `index/forms/integrations.py` -> `index/people/forms.py`
- Move: `index/views/domain.py` -> `index/domain/views.py`
- Move: `index/views/domain_operations.py` -> `index/domain/operations.py`
- Move: `index/forms/domain.py` -> `index/domain/forms.py`
- Modify facades: `index/views/__init__.py`, `index/forms/__init__.py`
- Test: existing `index/test_people_statistics.py`, `index/test_phase4_people_*.py`, `index/test_domain_*.py`

**Interfaces:**
- Consumes: Canonical models from Task 2 and existing directory adapter/domain client interfaces.
- Produces: `net.people.*` and `net.domain.*` canonical workflows while preserving `net.services.ad_sync`, `net.tasks.domain`, `net.tasks.people`, `net.integrations.people.*`, and public view/form imports.

- [x] **Step 1: Extend import contracts for personnel and domain paths**

Add this assertion table to `tests/architecture/test_compatibility_contracts.py`:

```python
LEGACY_FEATURE_MODULES = (
    "net.services.personnel_import",
    "net.integrations.people.base",
    "net.integrations.people.feishu",
    "net.integrations.people.dingtalk",
    "net.integrations.people.sync",
    "net.tasks.people",
    "net.tasks.executors.people",
    "net.services.ad_sync",
    "net.tasks.domain",
    "net.tasks.executors.domain",
)
```

Loop through the tuple with `import_module()` in a new test method.

- [x] **Step 2: Move personnel modules and add facades**

Move implementation without changing public function signatures. Each old module imports public names from its new location. `net/people/directory/__init__.py` exports `DirectoryPerson`, adapter lookup, preview, and apply entry points used by the UI and tasks.

- [x] **Step 3: Move domain workflow modules and add facades**

Keep `net/domain/client.py`, `actions.py`, `validation.py`, and `secrets.py` in place. Move sync/task/executor implementation beside them and preserve the old imports with explicit re-exports.

- [x] **Step 4: Move personnel/domain UI implementation behind current exports**

`index/views/__init__.py` continues to export every callable referenced by `index/urls.py`. `index/forms/__init__.py` continues to export current form classes. Do not change `index/urls.py` in this task.

- [x] **Step 5: Run focused personnel and domain checks**

```powershell
python manage.py test tests.architecture.test_compatibility_contracts index.test_people_statistics index.test_phase4_people_contract index.test_phase4_people_sync index.test_phase4_people_ui index.test_domain_actions index.test_domain_operation_models index.test_domain_secrets index.test_domain_permissions_ui --verbosity 1
python manage.py check
```

Expected: selected tests and system checks pass. Do not run the large domain worker suite yet.

- [x] **Step 6: Commit and stop for inspection**

```powershell
git add net/people net/domain net/services net/integrations/people net/tasks index/people index/domain index/views index/forms tests/architecture
git commit -m "refactor: consolidate people and domain features"
```

---

### Task 4: Separate PC, Server, Network, and Security Device Features

**Files:**
- Move: `net/services/computer_analysis.py` -> `net/devices/pc/analysis.py`
- Move: `net/services/computer_logs.py` -> `net/devices/pc/logs.py`
- Move: `net/services/computer_snapshot.py` -> `net/devices/pc/snapshot.py`
- Move: `net/tasks/executors/computer_analysis.py` -> `net/devices/pc/executor.py`
- Move: `net/services/collectors/http.py` -> `net/devices/server/windows_http.py`
- Split implementation from `net/services/collectors/ssh.py` into `net/devices/server/linux_ssh.py` and `net/devices/network/ssh.py`
- Move: `net/services/collectors/security.py` -> `net/devices/security/api.py`
- Move: `net/exports/adapters/network.py` -> `net/devices/network/configuration.py`
- Move: `net/exports/adapters/security.py` -> `net/devices/security/configuration.py`
- Move: `net/services/collectors/base.py` -> `net/infrastructure/collection.py`
- Move: `net/services/collectors/native_http.py` -> `net/infrastructure/http.py`
- Move: `net/services/inventory_refresh.py` -> `net/devices/inventory.py`
- Move: `net/services/inventory_io.py` -> `net/data_exchange/inventory_csv.py`
- Move: `net/exports/csv_export.py` -> `net/data_exchange/table_csv.py`
- Move: `net/exports/configuration.py` -> `net/data_exchange/configuration.py`
- Convert every old path above into an explicit compatibility facade.
- Test: existing device, PC log, collector, inventory, and configuration-export tests.

**Interfaces:**
- Consumes: Device models from Task 2 and generic `CollectionResult`/HTTP primitives.
- Produces: Device-specific collector and parser modules plus unchanged collector functions `collect_linux_ssh`, `collect_network_ssh`, `collect_windows_http`, and `collect_security_api` at their legacy paths.

- [ ] **Step 1: Pin collector and export import contracts**

Add focused imports to the compatibility test:

```python
from net.services.collectors import (
    CollectionResult, collect_linux_ssh, collect_network_ssh,
    collect_security_api, collect_windows_http,
)
from net.services.inventory_io import export_csv, import_csv
from net.exports.configuration import build_configuration_zip, latest_configuration
```

Assert all imported functions are callable and `CollectionResult.__name__ == "CollectionResult"`.

- [ ] **Step 2: Move PC implementation and add facades**

Preserve `ANALYSIS_ITEMS`, log scanning/import behavior, upload processing, snapshot extraction/update functions, and task executor signatures. Update internal imports to canonical `net.devices.pc.*` paths.

- [ ] **Step 3: Split collectors by device ownership**

Move generic result/deadline helpers to infrastructure. Keep server and network SSH parsing separate even though they share transport primitives. `net/services/collectors/ssh.py` re-exports both canonical functions:

```python
from net.devices.network.ssh import collect_network_ssh
from net.devices.server.linux_ssh import collect_linux_ssh

__all__ = ["collect_linux_ssh", "collect_network_ssh"]
```

- [ ] **Step 4: Move inventory and export behavior**

Place CSV behavior under `net.data_exchange`, device configuration adapters beside their device features, and orchestration under `net.data_exchange.configuration`. Preserve current view imports through old export/service facades.

- [ ] **Step 5: Run focused device checks**

```powershell
python manage.py test tests.architecture.test_compatibility_contracts index.test_asset_inventory_extension index.test_phase2_computer_logs index.test_phase4_config_export index.test_phase4_native_deadline index.test_windows_network_evidence --verbosity 1
python manage.py check
```

Expected: selected tests and checks pass without contacting real devices.

- [ ] **Step 6: Commit and stop for inspection**

```powershell
git add net/devices net/infrastructure net/data_exchange net/services net/exports net/tasks tests/architecture
git commit -m "refactor: separate device collection features"
```

---

### Task 5: Consolidate Inspection, Scheduling, Task, and Alert Orchestration

**Files:**
- Move: `net/tasks/queue.py` -> `net/inspections/queue.py`
- Move: `net/tasks/state.py` -> `net/inspections/state.py`
- Move: `net/tasks/schedules.py` -> `net/inspections/schedules.py`
- Move: `net/tasks/worker.py` -> `net/inspections/worker.py`
- Move: `net/tasks/executors/inspection.py` -> `net/inspections/executor.py`
- Move: `net/services/record_summary.py` -> `net/inspections/record_summary.py`
- Move: `net/services/task_summary.py` -> `net/inspections/task_summary.py`
- Move: `net/services/collectors/selection.py` -> `net/inspections/selection.py`
- Move: `net/services/dashboard_summary.py` -> `net/dashboard/assets.py`
- Move: `net/services/sanitization.py` -> `net/infrastructure/sanitization.py`
- Keep `net/alerts/` as the canonical alert feature; update it to canonical inspection/infrastructure imports.
- Convert old task/service/collector paths into compatibility facades.
- Modify: `net/tasks/__init__.py`
- Modify: `net/management/commands/run_task_worker.py`
- Modify: `net/management/commands/run_network_checks.py`
- Test: focused task queue, schedule, worker, alert, taskbar, and dashboard tests.

**Interfaces:**
- Consumes: Device executors from Task 4, task/record/alert models from Task 2, and domain/personnel task adapters from Task 3.
- Produces: Canonical inspection queue and worker APIs while retaining all existing `net.tasks.*` paths and management command behavior.

- [ ] **Step 1: Pin task and worker imports**

Extend the contract test:

```python
from net.tasks.queue import claim_next_task, enqueue_task, finish_task
from net.tasks.schedules import enqueue_due_schedules
from net.tasks.worker import TaskWorker

self.assertTrue(callable(enqueue_task))
self.assertTrue(callable(claim_next_task))
self.assertTrue(callable(finish_task))
self.assertTrue(callable(enqueue_due_schedules))
self.assertTrue(callable(TaskWorker))
```

- [ ] **Step 2: Move queue, state, scheduling, and worker implementation**

Update canonical internal imports to `net.inspections.*`. Old modules re-export explicit public names. `net/tasks/__init__.py` remains the stable convenience API.

- [ ] **Step 3: Move inspection execution and summaries**

Route device kinds to the canonical Task 4 executors. Preserve task status transitions, leases, target selection, database guards, and result snapshots exactly.

- [ ] **Step 4: Update alerts and dashboard imports**

Keep channel implementations and alert behavior unchanged. Only replace imports of sanitization, inspection execution, and summaries with canonical modules; old service paths remain facades.

- [ ] **Step 5: Run focused orchestration checks**

```powershell
python manage.py test tests.architecture.test_compatibility_contracts index.test_phase2_queue index.test_phase2_schedules index.test_phase2_worker index.test_phase3_alert_service index.test_phase3_senders index.test_home_taskbar index.test_dashboard_summary --verbosity 1
python manage.py check
```

Expected: selected orchestration tests pass. Do not run real schedules or external alert deliveries.

- [ ] **Step 6: Commit and stop for inspection**

```powershell
git add net/inspections net/dashboard net/infrastructure net/tasks net/services net/alerts net/management tests/architecture
git commit -m "refactor: consolidate inspection orchestration"
```

---

### Task 6: Organize UI, Templates, and Static Sources by Feature

**Files:**
- Move shared helpers: `index/table_options.py`, `index/table_query.py`, `index/table_registry.py` -> `index/common/`
- Add compatibility facades at the three old helper paths.
- Move dashboard view: `index/views/dashboard.py` -> `index/dashboard/views.py`
- Move asset view: `index/views/assets.py` -> `index/devices/views.py`
- Move PC log view: `index/views/computer_logs.py` -> `index/devices/pc/logs.py`
- Move script view: `index/views/scripts.py` -> `index/devices/pc/scripts.py`
- Move config export view: `index/views/config_exports.py` -> `index/devices/configuration.py`
- Move record/task views: `index/views/records.py`, `index/views/tasks.py` -> `index/inspections/`
- Move alert view/form implementation into `index/alerts/`.
- Move import/export views into `index/common/imports.py` and `index/common/exports.py`.
- Modify compatibility exports: `index/views/__init__.py`, `index/forms/__init__.py`.
- Reorganize templates into `index/templates/common`, `dashboard`, `people`, `domain`, `devices`, `inspections`, `alerts`, and `integrations`.
- Move: `static/css/style.css` -> `static/app/css/style.css`
- Move application JavaScript into `static/app/js/common`, `domain`, and `inspections`.
- Move Bootstrap files into `static/vendor/bootstrap/`.
- Modify: all `{% include %}`, `{% extends %}`, and `{% static %}` references affected by these moves.
- Keep: `index/urls.py` paths and names unchanged.
- Test: navigation, public URL, page workspace, permission, and JavaScript tests.

**Interfaces:**
- Consumes: Canonical services from Tasks 3-5.
- Produces: Feature-owned views/forms/templates/static files while preserving every callable exported from `index.views`, every form export, and the complete URL contract.

- [ ] **Step 1: Add template/static compatibility assertions**

Extend `tests/architecture/test_compatibility_contracts.py` with Django static lookup and representative template rendering:

```python
from django.contrib.staticfiles import finders
from django.template.loader import get_template

self.assertIsNotNone(finders.find("app/css/style.css"))
self.assertIsNotNone(finders.find("app/js/common/table_workspace.js"))
self.assertIsNotNone(get_template("dashboard/index.html"))
self.assertIsNotNone(get_template("common/table_workspace.html"))
```

These assertions should initially fail until the files move.

- [ ] **Step 2: Move Python UI modules and preserve exports**

Keep `index/urls.py` unchanged. Update `index/views/__init__.py` so every current URL callable is imported from its canonical feature module. Keep old `index.views.*`, `index.forms.*`, and table-helper paths as explicit facades.

- [ ] **Step 3: Move templates and update all references atomically**

Use this ownership mapping:

```text
index.html                              -> dashboard/index.html
pagination/table_*                     -> common/
import_modal.html                      -> common/import_modal.html
computer_*                             -> devices/pc/
item_list/assets                       -> devices/
domain_controller/domain_object        -> domain/
inspection_*/error_records             -> inspections/
```

Update every render/include/extends reference in the same patch as its move. Do not add URL redirects because URLs do not change.

- [ ] **Step 4: Move static sources and update template paths**

Use stable new asset names:

```text
app/css/style.css
app/js/common/table_tools.js
app/js/common/table_workspace.js
app/js/domain/operation_modal.js
app/js/inspections/task_ui.js
vendor/bootstrap/css/bootstrap.min.css
vendor/bootstrap/js/bootstrap.bundle.min.js
```

Move JavaScript tests temporarily beside their source; Task 7 moves them into `tests/frontend` after all references are stable.

- [ ] **Step 5: Run focused UI and JavaScript checks**

```powershell
python manage.py test tests.architecture.test_compatibility_contracts index.test_public_urls index.test_navigation_dropdowns index.test_project_record_workspace index.test_domain_permissions_ui --verbosity 1
node --test static/app/js/common/table_workspace.test.js static/app/js/domain/operation_modal.test.js static/app/js/inspections/task_ui.test.js
python manage.py check
```

Expected: selected Django and JavaScript tests pass and URLs remain unchanged.

- [ ] **Step 6: Commit and stop for inspection**

```powershell
git add index static tests/architecture
git commit -m "refactor: organize UI and static assets by feature"
```

---

### Task 7: Organize Tests, Agents, Deployment Assets, and Documentation

**Files:**
- Move Django tests from `index/test_*.py` and `index/tests.py` into domain packages under `tests/`.
- Move JavaScript tests from `static/app/js/**` into `tests/frontend/`.
- Move: `index/test_windows_agent_selection.ps1` -> `tests/agents/test_windows_server_agent.ps1`
- Move: `api_test.py` -> `tests/api/test_computer_upload_api.py`
- Move: `ps/GetInfo_JSON.ps1` -> `agents/pc/windows/GetInfo_JSON.ps1`
- Move: `ps/OpenHardwareMonitorLib.dll` -> `agents/pc/windows/OpenHardwareMonitorLib.dll`
- Move: `windows_agent/InspectionHttpService.ps1` -> `agents/server/windows/InspectionHttpService.ps1`
- Move: `windows_agent/Install-InspectionHttpService.ps1` -> `agents/server/windows/Install-InspectionHttpService.ps1`
- Move: `windows_agent/README.md` -> `agents/server/windows/README.md`
- Move: `deploy/systemd/` -> `deploy/linux/systemd/`
- Keep: `deploy/demo.py`, `deploy/__init__.py`, `deploy/windows/`
- Modify: `README.md`
- Modify: `docs/deployment.md`
- Modify: `docs/computer-log-contract.md`
- Create: `docs/project-layout.md`
- Modify test-only cross-imports such as `index.test_phase4_people_sync.SnapshotAdapter` to their new path.

**Interfaces:**
- Consumes: Stable code and UI layout from Tasks 1-6.
- Produces: Domain-organized tests, managed-device agents, Linux/Windows deployment assets, and current navigation documentation.

- [ ] **Step 1: Move tests by ownership**

Use these packages:

```text
tests/architecture/    compatibility, deployment, admin registry
tests/people/          personnel statistics, directory adapters and sync
tests/domain/          domain actions, operations, secrets, permissions, worker
tests/devices/pc/      log import, analysis, scripts, inventory
tests/devices/server/  Windows HTTP and Linux SSH evidence
tests/devices/network/ network collection and configuration
tests/devices/security/security API and configuration
tests/inspections/     task models, queue, schedules, worker, records, task UI
tests/alerts/          alert models, service, senders, UI
tests/dashboard/       cards, taskbar, statistics
tests/api/             upload API behavior
tests/frontend/        Node tests
tests/agents/          PowerShell agent tests
```

Every directory gets `__init__.py`. Rename phase-numbered files to behavior names only when no test label or cross-import depends on the old name; otherwise update all repository references in the same commit.

- [ ] **Step 2: Move managed-device scripts**

Keep script content and public download endpoints unchanged. Update generator source paths, PowerShell test paths, and documentation. Preserve `GetInfo_JSON.ps1` and `OpenHardwareMonitorLib.dll` as adjacent files.

- [ ] **Step 3: Organize deployment assets**

Move systemd files under `deploy/linux/systemd/`, update their README commands and project documentation, and leave `deploy.demo` import behavior unchanged. Installed systemd units are unaffected because this changes repository source paths only.

- [ ] **Step 4: Document the final layout**

`docs/project-layout.md` must explain:

```text
- net versus index responsibilities
- each feature package owner
- canonical imports versus compatibility facades
- source static files versus generated staticfiles
- runtime data that must not be moved
- where to add a new device collector, page, task, test, or agent
```

Update README commands to point to new agent, static, test, and Linux deployment paths.

- [ ] **Step 5: Run one final verification pass**

Run each command once:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --verbosity 1
node --test tests/frontend/*.test.js
powershell -NoProfile -ExecutionPolicy Bypass -File tests/agents/test_windows_server_agent.ps1
python -m deploy.demo --prepare-only
```

Then perform a read-only demo database smoke check with its configured database path:

```powershell
$env:DB_ENGINE = "sqlite"
$env:DJANGO_SQLITE_PATH = (Resolve-Path "demo-runtime/demo.sqlite3").Path
$env:DJANGO_SECRET_KEY = "DEMO-ONLY-NOT-FOR-PRODUCTION-LOCAL-ISOLATED-DATABASE"
python manage.py shell -c "from net.models import People, Computer, TaskRun; print(People.objects.count(), Computer.objects.count(), TaskRun.objects.count())"
```

Expected: checks and suites pass, no migration is generated, agent tests pass, demo preparation succeeds, and existing rows can be counted without modifying them.

- [ ] **Step 6: Confirm generated and runtime files were not added**

```powershell
git status --short
git check-ignore db.sqlite3 demo-runtime staticfiles .venv .task6-artifacts .worktrees
```

Expected: runtime/generated paths are ignored or unchanged and only intended source changes remain.

- [ ] **Step 7: Commit and stop for final inspection**

```powershell
git add tests agents deploy README.md docs index net static
git commit -m "refactor: complete project source organization"
```

## Self-Review Record

- Spec coverage: all compatibility requirements, seven implementation phases,
  runtime exclusions, focused validation, full final validation, commits, and review
  pauses map to Tasks 1-7.
- Placeholder scan: no deferred implementation markers are present.
- Interface consistency: canonical model, feature, inspection, UI, and facade paths are
  introduced before downstream tasks consume them.
- Testing scope: Tasks 1-6 use focused checks only; Task 7 runs the complete suite once.

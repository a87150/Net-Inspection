# Assets, Dashboard, and Inspection Taskbar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add richer static inventory, rename computers to PC in the UI, and replace the home-page record/error panels with a paginated task-centric inspection bar.

**Architecture:** Extend the existing concrete asset models with nullable inventory fields, keep dynamic utilization in record models, and centralize home-page aggregation in a focused query service. Reuse `TaskRun` and `TaskTargetRun`; do not create a second execution model.

**Tech Stack:** Python 3.12+, Django 5.2+, Django ORM, Bootstrap templates, SQLite/MySQL-compatible migrations, Django TestCase.

**Spec:** `docs/superpowers/specs/2026-09-01-asset-dashboard-domain-operations-design.md`

## Global Constraints

- Keep internal model names and existing URLs compatible while displaying “PC” to users.
- Static capacity/model fields belong to assets; dynamic utilization belongs to inspection/analysis records.
- New fields are nullable and require no production-data migration.
- Do not modify the original `db.sqlite3`; seed only the configured demo database.
- Preserve the existing flat Django model modules to avoid duplicate model registration.

---

### Task 1: Extend static inventory models and import/export contracts

**Files:**
- Modify: `net/asset_models.py`
- Modify: `net/services/inventory_io.py`
- Modify: `index/table_registry.py`
- Modify: `net/services/computer_snapshot.py`
- Create: `net/services/inventory_refresh.py`
- Modify: `net/tasks/executors/inspection.py`
- Modify: `net/integrations/people/feishu.py`
- Modify: `net/integrations/people/dingtalk.py`
- Modify: `net/integrations/people/sync.py`
- Modify: `net/management/commands/seed_demo_data.py`
- Create: `net/migrations/0014_asset_inventory_and_people_dates.py`
- Test: `index/test_asset_inventory_extension.py`

**Interfaces:**
- Produces: nullable asset fields `hire_date`, `departure_date`, `cpu_model`, `memory_total_gb`, `disk_total_gb`, and network counts used by tables and dashboard details.
- Consumes: existing `ComputerSnapshot` payload normalization and inventory CSV adapters.

- [ ] **Step 1: Write failing model and table-contract tests**

```python
class AssetInventoryFieldTests(TestCase):
    def test_people_and_device_inventory_fields_are_nullable(self):
        person = People.objects.create(employee_id='P-100', hire_date=date(2024, 1, 2))
        pc = Computer.objects.create(computer_name='PC-100', cpu_model='Core i7', memory_total_gb=32)
        network = Network_Device.objects.create(ip='192.0.2.90', port_count=48, vlan_count=12)
        self.assertIsNone(person.departure_date)
        self.assertEqual(pc.memory_total_gb, Decimal('32.00'))
        self.assertEqual(network.port_count, 48)

    def test_pc_table_omits_enabled_and_exposes_static_configuration(self):
        labels = [field.label for field in get_table_definition('computers').fields]
        self.assertNotIn('是否启用', labels)
        self.assertIn('CPU 型号', labels)
        self.assertIn('内存总量', labels)
        self.assertIn('磁盘总量', labels)
```

- [ ] **Step 2: Run the focused tests and confirm missing-field failures**

Run: `.venv\Scripts\python.exe manage.py test index.test_asset_inventory_extension -v 2`

Expected: FAIL because the new model fields and table definitions do not exist.

- [ ] **Step 3: Add nullable fields and migration**

Use `DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)` for GiB capacities, positive integer fields for core/port/VLAN counts, and nullable text fields for inventory descriptions. Add people dates; PC manufacturer/model/serial/architecture/CPU/core/memory/disk; shared CPU/memory/disk fields to network/server/monitor; and `port_count`, `active_port_count`, `vlan_count` to network devices.

- [ ] **Step 4: Update table definitions and inventory CSV mappings**

Add the exact new fields to `people`, `computers`, `networks`, `servers`, and `monitors`. Keep credentials and `Computer.is_active` absent from PC display/export. Parse blank numeric/date cells as `None`, reject invalid negative counts and invalid dates as whole-file validation errors.

- [ ] **Step 5: Update PC snapshot persistence and demo data**

Map normalized payload keys to static inventory without overwriting an existing nonblank value with a blank incoming value. Seed realistic dates, CPU models, capacities, and network counts using the existing idempotent `_upsert_asset` path.

- [ ] **Step 6: Refresh trusted inventory after collection and people sync**

Implement `refresh_asset_inventory(asset, normalized_result) -> set[str]` with an explicit per-model source-key allowlist. Call it only after a successful/partial persisted collection, update nonblank static capacity/model/count values, and never copy utilization percentages. Map hire/departure dates from Feishu/DingTalk when present; when a provider omits a date, leave the stored value unchanged.

- [ ] **Step 7: Run focused tests and migration drift check**

Run: `.venv\Scripts\python.exe manage.py test index.test_asset_inventory_extension index.test_phase1_exports index.test_phase4_people_sync -v 2`

Run: `.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`

Expected: all tests PASS and “No changes detected”.

- [ ] **Step 8: Commit**

```powershell
git add py/net/net/asset_models.py py/net/net/services/inventory_io.py py/net/index/table_registry.py py/net/net/services/computer_snapshot.py py/net/net/services/inventory_refresh.py py/net/net/tasks/executors/inspection.py py/net/net/integrations/people py/net/net/management/commands/seed_demo_data.py py/net/net/migrations/0014_asset_inventory_and_people_dates.py py/net/index/test_asset_inventory_extension.py
git commit -m "feat: extend static asset inventory"
```

---

### Task 2: Build reusable latest-state dashboard summaries

**Files:**
- Create: `net/services/dashboard_summary.py`
- Modify: `index/views/dashboard.py`
- Test: `index/test_dashboard_summary.py`

**Interfaces:**
- Produces: `build_asset_card_summaries() -> list[dict]` with `total`, `normal`, `abnormal`, `unchecked`, and `last_run_at`.
- Consumes: latest `ComputerAnalysis` and infrastructure inspection records.

- [ ] **Step 1: Write failing summary tests**

```python
class DashboardSummaryTests(TestCase):
    def test_pc_status_uses_each_devices_latest_analysis_not_today(self):
        # Create one old normal latest result, one abnormal latest result, and one unchecked PC.
        summary = next(x for x in build_asset_card_summaries() if x['key'] == 'computers')
        self.assertEqual(summary['normal'], 1)
        self.assertEqual(summary['abnormal'], 1)
        self.assertEqual(summary['unchecked'], 1)
        self.assertEqual(summary['last_run_at'], self.abnormal.created_at)

    def test_infrastructure_partial_or_unreachable_is_abnormal(self):
        summary = next(x for x in build_asset_card_summaries() if x['key'] == 'networks')
        self.assertEqual(summary['normal'], 1)
        self.assertEqual(summary['abnormal'], 2)
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `.venv\Scripts\python.exe manage.py test index.test_dashboard_summary -v 2`

Expected: FAIL because `net.services.dashboard_summary` does not exist.

- [ ] **Step 3: Implement latest-result annotations**

Use `OuterRef`/`Subquery` to select the latest result per asset. For PC, normal means latest analysis status is success and no related errors; for infrastructure, normal additionally requires reachability. Return unchecked separately and calculate last run with `Max('created_at')`.

- [ ] **Step 4: Replace date-bound dashboard calculations**

Make `index.views.dashboard.index` consume `build_asset_card_summaries()`. Rename visible copy to 人员总数/离职人数, PC/正常设备/异常设备/上次分析日期, and 巡检正常/巡检异常/上次巡检日期. Build domain account and computer sub-summaries separately.

- [ ] **Step 5: Run summary and query-count tests**

Run: `.venv\Scripts\python.exe manage.py test index.test_dashboard_summary index.test_phase1_routes -v 2`

Expected: PASS with bounded queries independent of asset count.

- [ ] **Step 6: Commit**

```powershell
git add py/net/net/services/dashboard_summary.py py/net/index/views/dashboard.py py/net/index/test_dashboard_summary.py
git commit -m "feat: summarize latest asset health"
```

---

### Task 3: Replace home activity panels with a paginated inspection taskbar

**Files:**
- Create: `net/services/task_summary.py`
- Modify: `index/views/dashboard.py`
- Create: `index/templates/components/inspection_taskbar.html`
- Modify: `index/templates/index.html`
- Modify: `static/css/style.css`
- Test: `index/test_home_taskbar.py`

**Interfaces:**
- Produces: `inspection_task_queryset()` and `summarize_task(task) -> dict`.
- Consumes: execution `TaskRun` rows and their prefetched `TaskTargetRun` rows.

- [ ] **Step 1: Write failing taskbar tests**

```python
class HomeTaskbarTests(TestCase):
    def test_home_lists_ten_execution_tasks_and_paginates(self):
        self.make_tasks(12, task_type=TaskRun.TaskType.INSPECTION)
        response = self.client.get(reverse('index'))
        self.assertContains(response, '巡检任务栏')
        self.assertEqual(len(response.context['task_page'].object_list), 10)
        self.assertContains(response, '?task_page=2')

    def test_people_and_domain_tasks_are_excluded(self):
        self.make_people_task()
        response = self.client.get(reverse('index'))
        self.assertNotContains(response, '人员目录同步预览')
```

- [ ] **Step 2: Run tests and verify task-page failures**

Run: `.venv\Scripts\python.exe manage.py test index.test_home_taskbar -v 2`

Expected: FAIL because `task_page` and the merged taskbar are absent.

- [ ] **Step 3: Implement task aggregation**

Filter task types to inspection, computer scan, and computer analysis. Prefetch targets, classify each as normal/abnormal/pending/cancelled from target status and linked result status, and expose counts plus task/profile labels without per-row queries.

- [ ] **Step 4: Add independent `task_page` pagination**

Use `Paginator(queryset, 10).get_page(request.GET.get('task_page'))`. Preserve all non-`task_page` query parameters in pagination links. The home card grid remains visible on every page.

- [ ] **Step 5: Replace both old sections with the taskbar component**

Remove `recent_inspections` and `recent_errors` from the dashboard context and template. Render one row per task with type, profile, source, time, total, normal, abnormal, pending, status, and detail link.

- [ ] **Step 6: Run taskbar and task-detail tests**

Run: `.venv\Scripts\python.exe manage.py test index.test_home_taskbar index.test_phase2_task_ui index.test_final_fix_evidence -v 2`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add py/net/net/services/task_summary.py py/net/index/views/dashboard.py py/net/index/templates/components/inspection_taskbar.html py/net/index/templates/index.html py/net/static/css/style.css py/net/index/test_home_taskbar.py
git commit -m "feat: add home inspection taskbar"
```

---

### Task 4: Polish cards and asset detail pages

**Files:**
- Create: `index/templates/components/dashboard_card.html`
- Modify: `index/templates/index.html`
- Modify: `index/templates/assets/detail.html`
- Modify: `index/views/assets.py`
- Modify: `index/views/domain.py`
- Modify: `static/css/style.css`
- Test: `index/test_asset_dashboard_ui.py`

**Interfaces:**
- Consumes: card dictionaries from `build_asset_card_summaries()` and new table fields.
- Produces: consistent card footer layout and richer details without exposing PC enabled status.

- [ ] **Step 1: Write failing rendered-copy and detail tests**

```python
def test_home_uses_pc_copy_and_bottom_right_actions(self):
    response = self.client.get(reverse('index'))
    self.assertContains(response, '>PC<')
    self.assertContains(response, '人员总数')
    self.assertContains(response, '上次分析日期')
    self.assertContains(response, 'metric-card__actions--end')

def test_pc_detail_exposes_inventory_but_not_enabled(self):
    response = self.client.get(reverse('asset_detail', args=['computers', self.pc.pk]))
    self.assertContains(response, 'CPU 型号')
    self.assertNotContains(response, '是否启用')
```

- [ ] **Step 2: Run UI tests and confirm old-copy failures**

Run: `.venv\Scripts\python.exe manage.py test index.test_asset_dashboard_ui -v 2`

Expected: FAIL on old card labels/layout.

- [ ] **Step 3: Implement the reusable card component**

Render domain management as two internal columns and all other cards with three metrics plus last-run text. Apply flex layout so all actions align at the lower-right, and give PC analysis/manual buttons the same primary-action class as manual inspection.

- [ ] **Step 4: Update page titles and detail fields**

Change user-facing computer labels to PC in asset pages, records, errors, navigation, empty states, and ARIA labels. Keep existing route names. Ensure people details include hire/departure dates and network details include port/VLAN counts.

- [ ] **Step 5: Run UI, accessibility-copy, and export tests**

Run: `.venv\Scripts\python.exe manage.py test index.test_asset_dashboard_ui index.test_phase1_routes index.test_phase1_exports index.test_public_urls -v 2`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add py/net/index/templates/components/dashboard_card.html py/net/index/templates/index.html py/net/index/templates/assets/detail.html py/net/index/views/assets.py py/net/index/views/domain.py py/net/static/css/style.css py/net/index/test_asset_dashboard_ui.py
git commit -m "feat: polish dashboard and asset details"
```

---

### Task 5: Verify inventory/dashboard release slice

**Files:**
- Modify: `README.md`
- Test: all Django and JavaScript suites

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: documented, independently releasable asset/dashboard slice.

- [ ] **Step 1: Update operator-facing documentation**

Document PC naming, latest-result status semantics, static versus dynamic fields, taskbar pagination, and demo reseeding command.

- [ ] **Step 2: Reseed only the demo database and smoke-check pages**

Run with `NET_DATABASE_PATH=demo-runtime/demo.sqlite3`: migrations, `seed_demo_data`, and Django test client/browser checks for `/`, `/assets/computers/`, `/assets/networks/`, and task details.

- [ ] **Step 3: Run full verification**

Run: `.venv\Scripts\python.exe manage.py test -v 1`

Run: `node --test static/js/*.test.js`

Run: `.venv\Scripts\python.exe manage.py check`

Run: `.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`

Run: `.venv\Scripts\python.exe -m pip check`

Expected: all commands succeed.

- [ ] **Step 4: Commit documentation**

```powershell
git add py/net/README.md
git commit -m "docs: explain asset dashboard status"
```

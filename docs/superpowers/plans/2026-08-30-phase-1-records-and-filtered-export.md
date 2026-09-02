# Phase 1 Records and Filtered Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the project around separate static asset pages and dynamic inspection/analysis record pages, with detail views, compact data-aware filters, import-only modals, and exports that honor the complete active filter.

**Architecture:** Reset the `net` app schema for a fresh database, keep static assets separate from dynamic records, and retain the registry-driven table query contract. Split the oversized view module by responsibility and introduce reusable option and export services so HTML tables and CSV exports execute the same validated query.

**Tech Stack:** Django 6.1, Django ORM, MySQL production/SQLite tests, Bootstrap 5, vanilla JavaScript, Python CSV.

**Spec:** `docs/superpowers/specs/2026-08-30-automation-alerting-and-records-design.md`

## Global Constraints

- Existing business data is not migrated, backfilled, or preserved; only schema migrations are required.
- Computers come from imported PowerShell JSON logs, not manual asset CSV uploads.
- Static asset lists must not expose CPU, memory, temperature, disk, service, interface, or channel measurements.
- Every person, domain object, device, inspection, and computer analysis record has a detail route.
- Export uses the validated current filter and sort over all matching rows, never only the current page.
- Secrets and connection credentials never appear in tables, filters, CSV, demo output, or ordinary errors.
- Every behavior change follows RED → GREEN → focused tests → full tests → commit.

---

### Task 1: Fresh schema and record boundaries

**Files:**
- Replace: `net/models.py` with package `net/models/__init__.py`
- Create: `net/models/assets.py`
- Create: `net/models/records.py`
- Delete: `net/migrations/0001_initial.py` through `net/migrations/0012_alter_computer_snapshot_address_fields.py`
- Create: `net/migrations/0001_initial.py` via `makemigrations`
- Create: `index/test_phase1_models.py`

**Interfaces:**
- Produces: `People`, `Domain_Account`, `Domain_Computer`, `Computer`, `Network_Device`, `Server`, `Monitor` from `net.models`.
- Produces: `ComputerLogFile`, `ComputerAnalysis`, `Network_Device_Inspection`, `Server_Inspection`, `Monitor_Inspection` from `net.models`.
- Produces: each dynamic record has `target`, `status`, `started_at`, `finished_at`, `summary`, `details`, and `created_at`-equivalent fields needed by record adapters.

- [ ] **Step 1: Write failing model-boundary tests**

```python
class StaticAndDynamicModelBoundaryTests(TestCase):
    def test_computer_static_model_has_no_live_resource_fields(self):
        names = {field.name for field in Computer._meta.fields}
        self.assertFalse({'cpu_usage', 'memory_usage', 'cpu_temperature'} & names)

    def test_each_dynamic_record_has_a_target_and_timestamp(self):
        for model, target_name in (
            (ComputerAnalysis, 'computer'),
            (Network_Device_Inspection, 'device'),
            (Server_Inspection, 'server'),
            (Monitor_Inspection, 'monitor'),
        ):
            self.assertIsNotNone(model._meta.get_field(target_name))
            self.assertIsNotNone(model._meta.get_field('created_at'))
```

- [ ] **Step 2: Run the model test and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase1_models.StaticAndDynamicModelBoundaryTests -v 2`

Expected: FAIL because the new record models and field boundaries do not exist.

- [ ] **Step 3: Implement the fresh models**

Define static models with identity/basic fields only. Define record status choices once:

```python
class RecordStatus(models.TextChoices):
    SUCCESS = 'success', '成功'
    PARTIAL = 'partial', '部分成功'
    FAILED = 'failed', '失败'

class ComputerLogFile(models.Model):
    source_path = models.TextField()
    modified_at = models.DateTimeField()
    content_hash = models.CharField(max_length=64, unique=True)
    import_status = models.CharField(max_length=20)
    archived_path = models.TextField(blank=True)
    parse_error = models.TextField(blank=True)
    payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

`ComputerAnalysis` references both `Computer` and `ComputerLogFile`. Infrastructure record models reference their static asset and contain dynamic JSON/text fields only.

- [ ] **Step 4: Reset and generate the initial migration**

Keep only `net/migrations/__init__.py`, then run:

`.venv\Scripts\python.exe manage.py makemigrations net`

Expected: one new `0001_initial.py` containing the fresh schema and no `RunPython` data migration.

- [ ] **Step 5: Run schema verification**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase1_models -v 2`

Run: `.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`

Expected: model tests pass and migration check reports `No changes detected`.

- [ ] **Step 6: Commit**

```bash
git add py/net/net/models py/net/net/migrations py/net/index/test_phase1_models.py
git commit -m "refactor: separate assets from dynamic records"
```

### Task 2: Split views and establish stable detail routes

**Files:**
- Delete: `index/views.py`
- Create: `index/views/__init__.py`
- Create: `index/views/dashboard.py`
- Create: `index/views/assets.py`
- Create: `index/views/records.py`
- Create: `index/views/domain.py`
- Create: `index/views/imports.py`
- Modify: `index/urls.py`
- Create: `index/templates/assets/list.html`
- Create: `index/templates/assets/detail.html`
- Create: `index/templates/records/list.html`
- Create: `index/templates/records/detail.html`
- Create: `index/test_phase1_routes.py`

**Interfaces:**
- Produces route names: `asset_list`, `asset_detail`, `record_list`, `record_detail`, `computer_analysis_list`, `computer_analysis_detail`, `person_detail`, `domain_account_detail`, `domain_computer_detail`.
- Consumes table keys from `index.table_registry.get_table_definition()`.

- [ ] **Step 1: Write failing route/detail tests**

```python
class DetailRouteTests(TestCase):
    def test_every_asset_kind_has_list_and_detail(self):
        cases = (('people', self.person.pk), ('computers', self.computer.pk),
                 ('networks', self.network.pk), ('servers', self.server.pk),
                 ('monitors', self.monitor.pk))
        for kind, pk in cases:
            self.assertEqual(self.client.get(reverse('asset_list', args=[kind])).status_code, 200)
            self.assertEqual(self.client.get(reverse('asset_detail', args=[kind, pk])).status_code, 200)

    def test_computer_record_copy_uses_analysis_log_wording(self):
        response = self.client.get(reverse('computer_analysis_list'))
        self.assertContains(response, '分析日志')
        self.assertNotContains(response, '计算机巡检')
```

- [ ] **Step 2: Run route tests and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase1_routes.DetailRouteTests -v 2`

Expected: FAIL because the route contract and templates do not exist.

- [ ] **Step 3: Split views and implement explicit adapters**

Use mappings that contain only model/table/template metadata:

```python
ASSET_PAGES = {
    'people': AssetPage(People, 'people', '人员'),
    'computers': AssetPage(Computer, 'computers', '计算机'),
    'networks': AssetPage(Network_Device, 'networks', '网络设备'),
    'servers': AssetPage(Server, 'servers', '服务器'),
    'monitors': AssetPage(Monitor, 'monitors', '安防设备'),
}
```

Unknown kinds raise `Http404`. Detail views use `get_object_or_404`; templates link static assets to their record history without embedding dynamic tables in the static list.

- [ ] **Step 4: Implement domain detail routes**

Add UUID detail routes for domain accounts and domain computers while preserving domain sync/settings pages.

- [ ] **Step 5: Run route and full tests**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase1_routes -v 2`

Run: `.venv\Scripts\python.exe manage.py test`

Expected: all routes return 200/404 correctly and all existing tests are updated to the new vocabulary.

- [ ] **Step 6: Commit**

```bash
git add py/net/index/views py/net/index/urls.py py/net/index/templates py/net/index/test_phase1_routes.py
git commit -m "feat: split asset and record workspaces"
```

### Task 3: Compact data-aware filters

**Files:**
- Modify: `index/table_registry.py`
- Modify: `index/table_query.py`
- Create: `index/table_options.py`
- Modify: `index/templates/table_workspace.html`
- Modify: `index/templates/table_field_filter.html`
- Modify: `static/css/style.css`
- Modify: `static/js/table_workspace.js`
- Modify: `static/js/table_workspace.test.js`
- Create: `index/test_phase1_filters.py`

**Interfaces:**
- Adds `TableField.option_mode` with `fixed`, `distinct`, `suggest`, or `none`.
- Adds `TableField.option_limit`, default `100`.
- Produces `build_field_options(queryset, definition) -> dict[str, tuple[tuple[str, str], ...]]`, where each tuple is `(submitted_value, displayed_label)`.

- [ ] **Step 1: Write failing option-generation tests**

```python
def test_distinct_options_are_deduplicated_sorted_and_limited(self):
    options = build_field_options(People.objects.all(), get_table_definition('people'))
    self.assertEqual(options['department'], (('研发部', '研发部'), ('运维部', '运维部')))

def test_secret_fields_can_never_be_option_sources(self):
    for definition in TABLE_DEFINITIONS.values():
        self.assertFalse({'password', 'api_token', 'bind_password'} & {f.source for f in definition.fields})
```

- [ ] **Step 2: Run filter tests and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase1_filters -v 2`

- [ ] **Step 3: Implement option metadata and query service**

Use the exact query shape below. If the result exceeds the limit, render a suggestion input rather than an oversized select.

```python
lookup = field.source
values = (
    queryset.order_by()
    .exclude(**{f'{lookup}__isnull': True})
    .exclude(**{lookup: ''})
    .values_list(lookup, flat=True)
    .distinct()
    .order_by(lookup)[:field.option_limit + 1]
)
```

- [ ] **Step 4: Replace the large filter card with a compact toolbar**

Render keyword, default fields, submit/reset, and `导出筛选结果` in one responsive row. Render remaining enabled fields inside `<details><summary>更多筛选</summary>…</details>`. Keep `data-column-toggle`, `data-filter-toggle`, and versioned localStorage behavior.

- [ ] **Step 5: Add JavaScript regressions**

```javascript
test('keeps compact filter preferences and suggestion values after reload', () => {
  const storage = memoryStorage();
  initializeWorkspace(document, storage, location);
  document.querySelector('[data-filter-toggle][data-filter-key="department"]').click();
  assert.equal(JSON.parse(storage.getItem(storageKey('people'))).filterFields.includes('department'), true);
});
```

- [ ] **Step 6: Run focused tests**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase1_filters`

Run: `node --test static/js/table_workspace.test.js`

- [ ] **Step 7: Commit**

```bash
git add py/net/index/table_registry.py py/net/index/table_query.py py/net/index/table_options.py py/net/index/templates py/net/static
git commit -m "feat: add compact data-aware table filters"
```

### Task 4: Import-only modal and filtered CSV export

**Files:**
- Create: `net/exports/__init__.py`
- Create: `net/exports/csv_export.py`
- Modify: `index/views/imports.py`
- Create: `index/views/exports.py`
- Modify: `index/views/__init__.py`
- Modify: `index/urls.py`
- Rename: `index/templates/data_tools_modal.html` to `index/templates/import_modal.html`
- Modify: asset/domain/record list templates
- Create: `index/test_phase1_exports.py`

**Interfaces:**
- Produces `export_filtered_csv(request, definition, queryset, filename) -> HttpResponse`.
- Produces `apply_table_filters(request, source, definition) -> tuple[source, TableState]`; both HTML pagination and CSV export call this function, while only the HTML view constructs a paginator.
- Import modal contains template download and import controls only.

- [ ] **Step 1: Write failing filtered-export tests**

```python
def test_export_uses_filter_and_ignores_page_size(self):
    response = self.client.get(reverse('table_export', args=['people']), {
        'filter_department': '运维部', 'page_size': '20', 'page': '2',
    })
    rows = list(csv.DictReader(StringIO(response.content.decode('utf-8-sig'))))
    self.assertGreater(len(rows), 20)
    self.assertEqual({row['部门'] for row in rows}, {'运维部'})

def test_import_modal_does_not_contain_export_link(self):
    response = self.client.get(reverse('asset_list', args=['people']))
    modal = parse_response_html(response).find(id='importModal')
    self.assertNotIn('/export/', str(modal))
```

- [ ] **Step 2: Run export tests and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase1_exports -v 2`

- [ ] **Step 3: Implement one validated query path for HTML and CSV**

Separate filter/sort application from pagination. The export definition enumerates allowed output columns and labels; never iterate raw model fields.

- [ ] **Step 4: Rename data tools to import**

Replace button copy with `导入`, modal id with `importModal`, and auto-open behavior with `open_import_modal`. Computers and domain pages do not render this button.

- [ ] **Step 5: Run tests and commit**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase1_exports`

Run: `.venv\Scripts\python.exe manage.py test`

```bash
git add py/net/net/exports py/net/index/views py/net/index/templates py/net/index/urls.py py/net/index/test_phase1_exports.py
git commit -m "feat: export complete filtered table results"
```

### Task 5: Dashboard actions and deterministic demo data

**Files:**
- Modify: `index/views/dashboard.py`
- Modify: `index/templates/index.html`
- Rewrite: `net/management/commands/seed_demo_data.py`
- Create: `index/test_phase1_demo.py`
- Modify: `static/css/style.css`

**Interfaces:**
- Dashboard cards expose explicit `list_url`, `record_url`, and `manual_action_label`.
- `seed_demo_data --reset` deletes only known `net` app business rows in dependency order and then recreates deterministic records; it never calls external systems.

- [ ] **Step 1: Write failing dashboard/demo tests**

```python
def test_dashboard_separates_list_and_dynamic_actions(self):
    response = self.client.get(reverse('index'))
    self.assertContains(response, '计算机列表')
    self.assertContains(response, '分析日志')
    self.assertContains(response, '手动执行巡检', count=3)

@patch('requests.Session.request')
def test_demo_seed_is_repeatable_and_offline(self, request_mock):
    call_command('seed_demo_data', reset=True)
    first = (People.objects.count(), ComputerAnalysis.objects.count())
    call_command('seed_demo_data', reset=True)
    self.assertEqual(first, (People.objects.count(), ComputerAnalysis.objects.count()))
    request_mock.assert_not_called()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase1_demo -v 2`

- [ ] **Step 3: Implement dashboard links and phase-one demo fixtures**

Generate people from manual/CSV/Feishu/DingTalk sources, all static asset types, normal/abnormal dynamic records, and computer analyses. Use obvious `.invalid` URLs and placeholder credentials.

- [ ] **Step 4: Run full phase verification**

Run: `.venv\Scripts\python.exe manage.py check`

Run: `.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`

Run: `.venv\Scripts\python.exe manage.py test`

Run: `node --test static/js/table_workspace.test.js`

- [ ] **Step 5: Browser acceptance**

Start the dev server and verify desktop plus 390×844: dashboard links, static/dynamic separation, every detail route, compact filters, suggestions, filtered export, import modal, table scrolling, and zero console errors.

- [ ] **Step 6: Commit**

```bash
git add py/net/index py/net/net/management/commands/seed_demo_data.py py/net/static
git commit -m "feat: complete asset and record workspace rebuild"
```

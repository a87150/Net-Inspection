# 网络巡检中心表格工作区改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 美化现有巡检中心，把域控配置与 CSV 工具改为弹窗，把域账号和域计算机拆成独立页面，并让全部完整数据表支持按注册字段筛选、排序和浏览器端自定义显示。

**Architecture:** 使用服务端字段注册表统一描述表格列、筛选和排序规则，Django 负责完整查询集的筛选与分页，轻量 JavaScript 只负责将列、筛选项和每页数量偏好保存到 `localStorage`。计算机最新上报摘要缓存到 `Computer` 模型，保证扩展字段可以在数据库层全量筛选和排序。

**Tech Stack:** Python 3、Django、Django REST Framework、SQLite、Django Templates、Bootstrap 5、原生 JavaScript。

**Spec:** `docs/superpowers/specs/2026-08-30-table-workspace-redesign.md`

## Global Constraints

- 保留 Bootstrap 和 Django 模板技术栈，不新增前端框架。
- 浏览器偏好仅保存在当前浏览器，不引入用户登录或数据库偏好模型。
- 域账号和域计算机只从域控同步，不提供手动导入。
- 不修改 SSH、Windows HTTP、安防 API 和域控同步协议。
- 所有动态 ORM 字段必须来自服务端白名单注册表。
- 每项生产代码改动前先运行对应失败测试，再编写最小实现。
- 当前仓库未配置 Git 作者身份；若提交仍失败，不修改全局 Git 配置，保留清晰的任务边界并继续测试。

---

### Task 1: 计算机最新上报摘要

**Files:**
- Create: `net/services/computer_snapshot.py`
- Create: `net/migrations/0011_computer_snapshot_fields.py`
- Create: `net/management/commands/backfill_computer_snapshots.py`
- Modify: `net/models.py`
- Modify: `net/views.py`
- Modify: `index/tests.py`

**Interfaces:**
- Produces: `extract_computer_snapshot(system_info: object, network_info: object, computer_info: object) -> dict[str, str]`
- Produces: `update_computer_snapshot(computer: Computer, inspection: Computer_Inspection) -> Computer`
- Adds nullable/blank `CharField` fields to `Computer`: `login_account`, `ip_addresses`, `mac_addresses`, `os_version`, `os_build`, `system_installed_at`, `last_boot_at`, `cpu_temperature`, `cpu_usage`, `memory_total`, `memory_usage`.

- [ ] **Step 1: Write failing extraction and API persistence tests**

Add tests that pass real PowerShell-shaped dictionaries and assert normalized output:

```python
class ComputerSnapshotTests(TestCase):
    def test_extracts_latest_snapshot_from_powershell_sections(self):
        snapshot = extract_computer_snapshot(
            {
                '当前登录用户工号': 'DOMAIN\\u001',
                '系统主要版本名': '23H2',
                '系统详细版本': '10.0.22631.5189',
                '系统安装日期': '2025-01-02 03:04:05',
                '开机时间': '2026-08-30 08:00:00',
            },
            [
                {'IP地址': '192.0.2.10', 'MAC地址': 'AA-BB-CC-DD-EE-01'},
                {'IP地址': '192.0.2.11', 'MAC地址': 'AA-BB-CC-DD-EE-02'},
            ],
            {
                '当前CPU温度': '48°C', '当前CPU占用率': '23%',
                '当前内存容量': '16GB', '当前内存使用率': '61%',
            },
        )
        self.assertEqual(snapshot['ip_addresses'], '192.0.2.10, 192.0.2.11')
        self.assertEqual(snapshot['login_account'], 'DOMAIN\\u001')
        self.assertEqual(snapshot['memory_usage'], '61%')
```

Extend the existing computer upload API test to assert every extracted field on the updated `Computer` row.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.tests.ComputerSnapshotTests`

Expected: FAIL because `computer_snapshot` and the new `Computer` fields do not exist.

- [ ] **Step 3: Implement model fields, migration and extraction service**

Implement tolerant conversion helpers:

```python
def extract_computer_snapshot(system_info, network_info, computer_info):
    system = system_info if isinstance(system_info, dict) else {}
    hardware = computer_info if isinstance(computer_info, dict) else {}
    adapters = network_info if isinstance(network_info, list) else []
    return {
        'login_account': _text(system.get('当前登录用户工号')),
        'ip_addresses': _join_unique(adapter.get('IP地址') for adapter in adapters if isinstance(adapter, dict)),
        'mac_addresses': _join_unique(adapter.get('MAC地址') for adapter in adapters if isinstance(adapter, dict)),
        'os_version': _text(system.get('系统主要版本名')),
        'os_build': _text(system.get('系统详细版本')),
        'system_installed_at': _text(system.get('系统安装日期')),
        'last_boot_at': _text(system.get('开机时间')),
        'cpu_temperature': _text(hardware.get('当前CPU温度')),
        'cpu_usage': _text(hardware.get('当前CPU占用率')),
        'memory_total': _text(hardware.get('当前内存容量')),
        'memory_usage': _text(hardware.get('当前内存使用率')),
    }
```

Call the extractor from `ComputerInspectionView.post()` and merge it into `Computer.objects.update_or_create(... defaults=...)`.

- [ ] **Step 4: Implement idempotent history backfill command**

For each `Computer`, select `computer.inspections.order_by('-log_time', '-inspection_time').first()`, extract the snapshot and update only snapshot fields. Print scanned and updated counts. Repeated execution with unchanged data must perform no value changes.

- [ ] **Step 5: Run migration and focused tests**

Run:

```powershell
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py test index.tests.ComputerSnapshotTests
.venv\Scripts\python.exe manage.py backfill_computer_snapshots
```

Expected: migration succeeds, focused tests pass, command exits zero.

- [ ] **Step 6: Commit task boundary**

```powershell
git add py/net/net/models.py py/net/net/views.py py/net/net/services/computer_snapshot.py py/net/net/migrations/0011_computer_snapshot_fields.py py/net/net/management/commands/backfill_computer_snapshots.py py/net/index/tests.py
git commit -m "feat: cache computer inspection snapshots"
```

---

### Task 2: 统一字段注册表与动态查询

**Files:**
- Create: `index/table_registry.py`
- Modify: `index/table_query.py`
- Modify: `index/tests.py`

**Interfaces:**
- Produces: immutable `TableField` with `key`, `label`, `source`, `kind`, `default_visible`, `default_filter`, `sortable`, `choices`.
- Produces: immutable `TableDefinition` with `key`, `title`, `fields`, `default_sort`, `default_order`, `search_fields`, `default_page_size`.
- Produces: `get_table_definition(key: str) -> TableDefinition`.
- Produces: `apply_table_query(request, queryset, definition, *, prefix='') -> tuple[QuerySet, dict]`.

- [ ] **Step 1: Write failing registry and query tests**

Create tests for text, exact choice, boolean and date-range filters, safe invalid-field fallback, dynamic sorting and clamped page sizes:

```python
def test_dynamic_field_filters_apply_to_whole_queryset(self):
    response = self.client.get('/item/people/', {
        'filter_department': '运维',
        'filter_is_active': 'true',
        'sort': 'employee_id',
        'order': 'desc',
        'page_size': '50',
    })
    self.assertEqual(list(response.context['page_obj'].object_list), [self.ops_user])
    self.assertEqual(response.context['table_state']['page_size'], 50)

def test_unregistered_filter_and_sort_are_ignored(self):
    response = self.client.get('/item/people/', {
        'filter_password': 'secret', 'sort': 'password', 'page_size': '9999',
    })
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.context['table_state']['sort'], 'name')
    self.assertEqual(response.context['table_state']['page_size'], 100)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.tests.TableRegistryTests index.tests.DynamicTableQueryTests`

Expected: FAIL because the registry and dynamic filter parameters are absent.

- [ ] **Step 3: Implement registry dataclasses and definitions**

Register `people`, `computers`, `networks`, `servers`, `monitors`, `domain_accounts`, `domain_computers`, `inspection_records` and `error_records`. Include every safe display field. Exclude passwords, API tokens and other credentials from display, filter and sort metadata.

Computer fields must include all Task 1 snapshot columns. Define allowed page sizes as `(20, 50, 100)`.

- [ ] **Step 4: Implement dynamic queryset filtering**

Use the definition to parse parameters named `filter_<field>`, `filter_<field>_from`, and `filter_<field>_to`. Build ORM lookups only from `TableField.source`. Return state containing `filters`, `sort`, `order`, `page_size`, `field_definitions`, `default_visible_fields` and `default_filter_fields`.

Retain a separate list-record adapter for aggregated inspection/error dictionaries, but make it consume the corresponding registry definition and the same parameter names.

- [ ] **Step 5: Run focused and existing filtering tests**

Run: `.venv\Scripts\python.exe manage.py test index.tests.TableRegistryTests index.tests.DynamicTableQueryTests index.tests.TableFilteringAndSortingTests`

Expected: all selected tests pass.

- [ ] **Step 6: Commit task boundary**

```powershell
git add py/net/index/table_registry.py py/net/index/table_query.py py/net/index/tests.py
git commit -m "feat: add dynamic table registry and filters"
```

---

### Task 3: 可复用表格工作区与浏览器偏好

**Files:**
- Create: `index/templates/table_workspace.html`
- Create: `index/templates/table_field_filter.html`
- Create: `static/js/table_workspace.js`
- Modify: `index/templates/base.html`
- Modify: `index/templates/item_list.html`
- Modify: `index/templates/pagination.html`
- Modify: `index/views.py`
- Modify: `index/templatetags/extras.py`
- Modify: `index/tests.py`

**Interfaces:**
- Consumes: Task 2 `TableDefinition` and `apply_table_query` state.
- Produces HTML hooks: `[data-table-workspace]`, `[data-table-key]`, `[data-column-key]`, `[data-filter-key]`, `[data-page-size]`, `[data-table-reset]`.
- Produces localStorage key: `inspection-table:v1:<table-key>` with JSON `{visibleFields: string[], filterFields: string[], pageSize: number}`.

- [ ] **Step 1: Write failing response tests for workspace metadata**

Assert that a people page renders every registered field as a selectable column, only default columns are initially visible, filter controls have stable field keys, sortable headers preserve query parameters, and pagination contains `page_size`.

```python
def test_asset_page_renders_configurable_workspace(self):
    response = self.client.get('/item/people/')
    self.assertContains(response, 'data-table-key="people"')
    self.assertContains(response, 'data-column-key="leader"')
    self.assertContains(response, 'data-filter-key="department"')
    self.assertContains(response, '自定义表格')
```

- [ ] **Step 2: Run the workspace response test and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.tests.TableWorkspaceTemplateTests`

Expected: FAIL because the workspace template and data hooks do not exist.

- [ ] **Step 3: Implement reusable templates and view context**

Render one GET form containing keyword search, dynamic field filters, sort state and page size. Render the table from field definitions and objects, using a formatting filter for booleans and datetimes. Add a non-modal collapsible “自定义表格” section with column checkboxes, filter checkboxes, page-size selection and reset button.

Replace the hard-coded `fields`, `sort_options` and `status_options` context in `item_list` with the registry definition and dynamic state. Keep the existing per-row inspection action as an operation column.

- [ ] **Step 4: Implement localStorage preference controller**

On `DOMContentLoaded`, for every workspace:

1. read and parse the versioned storage key inside `try/catch`;
2. intersect stored keys with fields present in the DOM;
3. apply column visibility to matching header and body cells;
4. apply filter visibility without deleting current URL values;
5. save changes from checkboxes and page-size selection;
6. reset by removing the storage key and applying DOM defaults.

If storage access throws, keep server defaults and allow the page to operate.

- [ ] **Step 5: Run template tests and full Django tests**

Run:

```powershell
.venv\Scripts\python.exe manage.py test index.tests.TableWorkspaceTemplateTests
.venv\Scripts\python.exe manage.py test
```

Expected: all tests pass.

- [ ] **Step 6: Commit task boundary**

```powershell
git add py/net/index/templates/table_workspace.html py/net/index/templates/table_field_filter.html py/net/index/templates/base.html py/net/index/templates/item_list.html py/net/index/templates/pagination.html py/net/index/views.py py/net/index/templatetags/extras.py py/net/static/js/table_workspace.js py/net/index/tests.py
git commit -m "feat: add configurable table workspace"
```

---

### Task 4: 域控主页、设置弹窗和独立子页面

**Files:**
- Create: `index/templates/domain_object_list.html`
- Modify: `index/templates/domain_controller_settings.html`
- Modify: `index/views.py`
- Modify: `index/urls.py`
- Modify: `index/tests.py`

**Interfaces:**
- Produces routes `domain_account_list` at `/domain/accounts/` and `domain_computer_list` at `/domain/computers/`.
- Consumes Task 3 table workspace for both domain object tables.
- Domain settings POST remains on `domain_controller_settings`; context boolean `open_domain_modal` controls automatic reopening after invalid form submission.

- [ ] **Step 1: Write failing route and modal tests**

```python
def test_domain_overview_links_to_separate_tables(self):
    response = self.client.get('/settings/domain-controller/')
    self.assertNotContains(response, '<table', html=False)
    self.assertContains(response, reverse('domain_account_list'))
    self.assertContains(response, reverse('domain_computer_list'))
    self.assertContains(response, 'id="domainConfigModal"')

def test_domain_account_and_computer_routes_are_separate(self):
    account_response = self.client.get('/domain/accounts/')
    computer_response = self.client.get('/domain/computers/')
    self.assertContains(account_response, self.account.login_name)
    self.assertNotContains(account_response, self.domain_computer.computer_name)
    self.assertContains(computer_response, self.domain_computer.computer_name)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.tests.DomainWorkspaceTests`

Expected: FAIL because the child routes and modal do not exist and the overview still renders both tables.

- [ ] **Step 3: Split domain queryset rendering into child view**

Implement `domain_object_list(request, object_type)` with an explicit map for `accounts` and `computers`. Apply Task 2 dynamic filtering, paginate by the selected page size, and render `domain_object_list.html` using Task 3 workspace.

- [ ] **Step 4: Convert domain form to Bootstrap modal**

Keep summary cards and add separate cards linking to both child pages. Move the existing form unchanged into `#domainConfigModal`. Add a normal sync POST form outside the modal. If `form.is_valid()` is false, render instead of redirect and set `open_domain_modal=True`; add a short inline script that calls `bootstrap.Modal.getOrCreateInstance(...).show()` only for that flag.

- [ ] **Step 5: Run domain tests and full tests**

Run:

```powershell
.venv\Scripts\python.exe manage.py test index.tests.DomainWorkspaceTests
.venv\Scripts\python.exe manage.py test
```

Expected: all tests pass.

- [ ] **Step 6: Commit task boundary**

```powershell
git add py/net/index/templates/domain_object_list.html py/net/index/templates/domain_controller_settings.html py/net/index/views.py py/net/index/urls.py py/net/index/tests.py
git commit -m "feat: split domain objects into table pages"
```

---

### Task 5: CSV 数据工具弹窗

**Files:**
- Create: `index/templates/data_tools_modal.html`
- Modify: `index/templates/item_list.html`
- Modify: `index/views.py`
- Modify: `index/tests.py`

**Interfaces:**
- Consumes existing routes `download_inventory_template`, `import_inventory`, and `export_inventory`.
- Produces modal `#dataToolsModal` and context values `import_enabled`, `export_enabled`, `data_source_note`.

- [ ] **Step 1: Write failing modal behavior tests**

Assert importable assets render one data-tools trigger, template link, upload form and export link inside the modal. Assert computers render export and source explanation but no file input. Assert domain child pages render export but no import controls.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.tests.DataToolsModalTests`

Expected: FAIL because the current import form is inline and no modal exists.

- [ ] **Step 3: Implement reusable data tools modal**

Move the existing multipart form into `data_tools_modal.html`. Render template download only when `import_enabled`; render export only when `export_enabled`. Use precise source copy for automatic computer and domain data. Keep endpoints and CSV format unchanged.

- [ ] **Step 4: Run focused and inventory tests**

Run: `.venv\Scripts\python.exe manage.py test index.tests.DataToolsModalTests index.tests.InventoryImportExportTests`

Expected: all selected tests pass.

- [ ] **Step 5: Commit task boundary**

```powershell
git add py/net/index/templates/data_tools_modal.html py/net/index/templates/item_list.html py/net/index/views.py py/net/index/tests.py
git commit -m "feat: move inventory tools into modal"
```

---

### Task 6: 巡检与异常记录接入统一工作区

**Files:**
- Modify: `index/templates/inspection_records.html`
- Modify: `index/templates/error_records.html`
- Modify: `index/templates/inspection_list.html`
- Modify: `index/templates/computer_error_list.html`
- Modify: `index/views.py`
- Modify: `index/table_query.py`
- Modify: `index/tests.py`

**Interfaces:**
- Consumes Task 2 record definitions and list-record adapter.
- Consumes Task 3 workspace template and browser preferences.

- [ ] **Step 1: Write failing record workspace tests**

For unified inspection and error pages, assert category, asset, status/type and date filters; registered sorting; configurable columns; configurable page size. Preserve the dedicated computer record URLs but make their table controls use the same query conventions.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.tests.RecordWorkspaceTests`

Expected: FAIL because record templates still use the old fixed filter and fixed columns.

- [ ] **Step 3: Adapt dictionary record filtering and templates**

Implement registered text, exact, boolean and date comparisons for dictionary records. Convert each full record template to workspace metadata while retaining its existing detail URL and status badges. Ensure all four pages use unique table keys so browser preferences do not collide.

- [ ] **Step 4: Run focused and full tests**

Run:

```powershell
.venv\Scripts\python.exe manage.py test index.tests.RecordWorkspaceTests
.venv\Scripts\python.exe manage.py test
```

Expected: all tests pass.

- [ ] **Step 5: Commit task boundary**

```powershell
git add py/net/index/templates/inspection_records.html py/net/index/templates/error_records.html py/net/index/templates/inspection_list.html py/net/index/templates/computer_error_list.html py/net/index/views.py py/net/index/table_query.py py/net/index/tests.py
git commit -m "feat: unify record table workspaces"
```

---

### Task 7: 全局视觉系统和响应式整理

**Files:**
- Modify: `index/templates/base.html`
- Modify: `index/templates/index.html`
- Modify: `index/templates/detail.html`
- Modify: `index/templates/item_list.html`
- Modify: `index/templates/domain_controller_settings.html`
- Modify: `index/templates/domain_object_list.html`
- Modify: `index/templates/inspection_records.html`
- Modify: `index/templates/error_records.html`
- Modify: `static/css/style.css`
- Modify: `index/tests.py`

**Interfaces:**
- Produces reusable visual classes: `.app-shell`, `.app-navbar`, `.page-heading`, `.surface-card`, `.metric-card`, `.table-toolbar`, `.status-dot`, `.empty-state`.
- Does not change URL, POST action, field name or data semantics.

- [ ] **Step 1: Write structural smoke tests**

Add template response assertions for one global navigation landmark, one main landmark, page heading structure, modal labels and accessible sortable links. Assert action buttons retain their existing endpoint URLs.

- [ ] **Step 2: Run structural tests and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.tests.VisualStructureTests`

Expected: FAIL because the new semantic classes and labels do not exist.

- [ ] **Step 3: Implement design tokens and component styling**

Define CSS custom properties for canvas, surface, text, muted text, border, primary, success, warning, danger, radius and shadow. Restyle navigation, cards, headings, toolbars, tables, badges, forms, pagination, modals and empty states. Add responsive rules below 768px for stacked heading actions, scrollable tables and full-width modal action buttons.

- [ ] **Step 4: Update templates to use the visual components**

Apply the same page heading and card structure across dashboard, asset, domain and record pages. Keep the homepage compact tables read-only. Add visually hidden labels where icon-only actions exist and preserve keyboard focus outlines.

- [ ] **Step 5: Run structural and full tests**

Run:

```powershell
.venv\Scripts\python.exe manage.py test index.tests.VisualStructureTests
.venv\Scripts\python.exe manage.py test
```

Expected: all tests pass.

- [ ] **Step 6: Commit task boundary**

```powershell
git add py/net/index/templates py/net/static/css/style.css py/net/index/tests.py
git commit -m "style: refresh inspection center workspace"
```

---

### Task 8: 最终验证与浏览器验收

**Files:**
- Modify only if a failing verification produces a regression test and corresponding fix.

**Interfaces:**
- Verifies all interfaces produced by Tasks 1–7.

- [ ] **Step 1: Run complete backend verification**

Run:

```powershell
.venv\Scripts\python.exe manage.py check
.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.venv\Scripts\python.exe manage.py test
```

Expected: system check has zero issues, no migration changes are detected, and all tests pass.

- [ ] **Step 2: Start local server and run desktop browser acceptance**

Open the in-app browser and verify:

1. dashboard and global navigation render without horizontal overflow;
2. domain overview opens the settings modal and links to two child pages;
3. computer table shows enriched default fields;
4. every field can be enabled as a filter and a column where allowed;
5. filter, sort and pagination combine correctly;
6. data-tools modal exposes only allowed import/export actions;
7. changing visible columns and filter fields survives a reload;
8. browser console has no JavaScript errors.

- [ ] **Step 3: Run narrow-screen acceptance**

Use a viewport around 390×844 and verify navigation collapse, stacked toolbars, modal scrolling, table horizontal scrolling, accessible buttons and no content clipped outside the viewport.

- [ ] **Step 4: Stop the temporary server and record evidence**

Capture the exact test count, check output, migration output and browser pages inspected. Do not claim completion without these fresh results.

- [ ] **Step 5: Commit verification fixes if any**

If verification required code changes, stage only those files and commit with a message describing the concrete regression. If no fixes were needed, do not create an empty commit.

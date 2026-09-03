# 简化人员 API 导入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将人员 API 导入重构为固定的飞书/钉钉配置、后台连接测试、后台数据预览、确认导入及可选定时同步，并修复 PC 分析配置弹窗滚动。

**Architecture:** 保留 `PeopleSyncSource` 作为内部凭据存储，但通过固定 provider key 隐藏多来源概念；手工测试和预览继续进入现有 Worker 队列，由浏览器会话专属 JSON 状态接口驱动轻量通知。扩展现有 `Schedule` 和 `TaskRun` 支持 `people_sync`，定时任务在 Worker 内完成“全量读取 → 校验预览 → 单事务应用”。

**Tech Stack:** Python 3、Django ORM/模板/测试框架、Bootstrap 5、原生 JavaScript、Node.js test runner、SQLite/MySQL 兼容迁移。

**Spec:** `docs/superpowers/specs/2026-09-03-simple-people-api-import-design.md`

## Global Constraints

- 页面只提供“飞书”和“钉钉”两套固定 API 配置，不提供新建来源、来源名称、来源标识或来源选择器。
- 固定内部标识分别为 `people-provider-feishu` 和 `people-provider-dingtalk`，固定内部名称分别为“飞书”和“钉钉”。
- 人员唯一匹配键始终是工号；无工号远端记录只计入跳过，格式错误、重复工号和 API 来源冲突使预览整体失败。
- API 密钥只写不读：空值保留原密钥，新值替换原密钥，响应、模板、会话和错误信息均不得泄露密钥。
- 测试连接、预览和定时同步必须由独立 Worker 执行；HTTP 请求不得直接访问远端人员目录。
- 手工后台任务不得整页自动刷新；运行提示可按任务关闭，完成弹窗每个任务只显示一次。
- 定时同步必须先完整读取并校验，再在单一数据库事务中应用；失败不得产生部分人员写入。
- 自动同步只支持“每隔 N 分钟/小时”和“每天指定时间”，飞书与钉钉各自最多一个计划。
- 配置修改后保留计划，但在新配置再次测试成功前不得调度。
- 定时同步任务不属于浏览器会话，不显示页面完成弹窗，只在后台任务列表和详情页记录结果。
- PC 分析配置弹窗只保留底部保存按钮，正文独立纵向滚动。
- 每完成一个 Task，提交代码并停止，等待用户检查确认后再继续下一个 Task。
- 只运行当前 Task 明确列出的聚焦测试；最终不调用真实飞书或钉钉 API，也不执行真实人员导入。

---

## File Structure Map

- `net/people/providers.py`：固定 provider 元数据、固定来源查询/保存和公共状态序列化；业务代码不再按任意 source id 查找来源。
- `index/people/forms.py`：固定平台凭据表单和人员同步计划表单，仅接收允许用户修改的字段。
- `index/people/integrations.py`：人员导入 HTTP 编排、会话任务状态/确认接口、预览与应用页面入口。
- `net/people/tasks.py`：创建手工及定时人员任务、校验会话所有权、保存不可变配置快照。
- `net/people/executor.py`：测试、预览、定时全量同步的 Worker 执行及安全错误分类。
- `net/models/tasks.py` 与 `net/migrations/0021_people_sync_schedules.py`：人员计划关联、`people_sync` 类型及数据库约束。
- `net/inspections/schedules.py`：发现到期人员计划并入队，同时跳过未测试或配置已变化的平台。
- `static/app/js/people/import_tasks.js`：人员后台任务轮询、运行提示关闭、完成弹窗和确认。
- `index/templates/integrations/`：固定平台配置、后台状态提示、预览结果和导入确认。
- `index/templates/inspections/profile_modal.html`、`static/app/css/style.css`：PC 配置弹窗滚动与单一底部保存操作。

---

### Task 1: 固定飞书和钉钉配置

**Files:**
- Create: `net/people/providers.py`
- Modify: `index/people/forms.py`
- Modify: `index/people/integrations.py`
- Modify: `index/templates/integrations/source_modal.html`
- Modify: `index/templates/common/import_modal.html`
- Modify: `index/common/imports.py`
- Modify: `net/management/commands/seed_demo_data.py`
- Test: `tests/people/test_directory_ui.py`
- Test: `tests/common/test_table_exports.py`
- Test: `tests/architecture/test_demo_seed.py`

**Interfaces:**
- Consumes: `PeopleSyncSource.public_data() -> dict` and existing encrypted/write-only `credentials` JSON contract.
- Produces: `PROVIDERS: dict[str, ProviderDefinition]`, `get_provider_definition(provider: str) -> ProviderDefinition`, `get_provider_source(provider: str) -> PeopleSyncSource | None`, `save_provider_source(provider: str, cleaned_data: dict) -> PeopleSyncSource`, `PeopleProviderForm(data=None, *, provider: str, source: PeopleSyncSource | None)`.

- [x] **Step 1: Write failing fixed-provider UI and persistence tests**

  Replace the multi-source expectations in `tests/people/test_directory_ui.py` and `tests/common/test_table_exports.py` with tests asserting:

  ```python
  def test_people_import_shows_only_fixed_provider_settings(self):
      response = self.client.get('/assets/people/?import=people&provider=feishu')
      self.assertContains(response, '飞书 API 设置')
      self.assertContains(response, '钉钉 API 设置')
      self.assertNotContains(response, '新建来源')
      self.assertNotContains(response, '来源名称')
      self.assertNotContains(response, '稳定来源标识')
      self.assertNotContains(response, 'name="source_id"')

  def test_provider_save_uses_canonical_identity_and_preserves_blank_secret(self):
      self.client.post('/integrations/people/providers/feishu/save/', {
          'app_id': 'cli_a', 'app_secret': 'secret-a',
          'root_department_ids': '0', 'is_enabled': 'on',
      })
      source = PeopleSyncSource.objects.get(source_key='people-provider-feishu')
      self.assertEqual((source.name, source.source_type), ('飞书', 'feishu'))
      self.client.post('/integrations/people/providers/feishu/save/', {
          'app_id': '', 'app_secret': '',
          'root_department_ids': '0, 12', 'is_enabled': 'on',
      })
      source.refresh_from_db()
      self.assertEqual(source.credentials, {'app_id': 'cli_a', 'app_secret': 'secret-a'})
      self.assertEqual(source.root_department_ids, ['0', '12'])
  ```

  Add a seed test asserting that only the two canonical keys are created by demo seeding.

- [x] **Step 2: Run the new tests and verify the old multi-source implementation fails**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.people.test_directory_ui.PeopleImportUITests tests.common.test_table_exports.PersonnelApiImportContractTests tests.architecture.test_demo_seed.DeterministicDemoSeedTests --verbosity 2
  ```

  Expected: FAIL because the page still renders source names/keys/selectors and the save endpoint accepts arbitrary source identity.

- [x] **Step 3: Add the fixed provider registry and safe save service**

  Create `net/people/providers.py` with an immutable definition and canonical lookup:

  ```python
  from dataclasses import dataclass
  from django.db import transaction
  from net.models import PeopleSyncSource

  @dataclass(frozen=True, slots=True)
  class ProviderDefinition:
      key: str
      label: str
      source_key: str
      credential_fields: tuple[str, ...]

  PROVIDERS = {
      'feishu': ProviderDefinition('feishu', '飞书', 'people-provider-feishu', ('app_id', 'app_secret')),
      'dingtalk': ProviderDefinition('dingtalk', '钉钉', 'people-provider-dingtalk', ('app_key', 'app_secret')),
  }

  def get_provider_definition(provider):
      try:
          return PROVIDERS[provider]
      except KeyError as exc:
          raise ValueError('不支持的人员 API 平台。') from exc

  def get_provider_source(provider):
      definition = get_provider_definition(provider)
      return PeopleSyncSource.objects.filter(source_key=definition.source_key).first()

  @transaction.atomic
  def save_provider_source(provider, cleaned_data):
      definition = get_provider_definition(provider)
      source = PeopleSyncSource.objects.select_for_update().filter(
          source_key=definition.source_key,
      ).first() or PeopleSyncSource(source_key=definition.source_key)
      previous_credentials = dict(source.credentials or {})
      source.source_type = definition.key
      source.name = definition.label
      source.root_department_ids = list(cleaned_data['root_department_ids'])
      source.is_enabled = cleaned_data['is_enabled']
      source.credentials = {
          field: cleaned_data.get(field) or previous_credentials.get(field, '')
          for field in definition.credential_fields
      }
      source.last_tested_at = None
      source.full_clean()
      source.save()
      return source
  ```

  The implementation must invalidate `last_tested_at` only when credentials, root departments, provider identity, or enabled state actually changes; submitting identical public values with blank secrets must retain a current successful test.

- [x] **Step 4: Replace the form, modal context and template with fixed tabs**

  Rename `PeopleSourceForm` to `PeopleProviderForm`; remove `source_type`, `name`, and `source_key`; keep provider-specific credential fields, roots, and enabled. Make `public_form()` reconstruct only non-secret bound values.

  Change `people_modal_context(request, *, provider=None, forms=None)` to always produce exactly two tabs in `PROVIDERS` order, each containing `provider`, `label`, `source.public_data()` or `None`, and a `PeopleProviderForm`. Change save routing to carry the provider in the URL and redirect to:

  ```python
  reverse('asset_list', args=['people']) + f'?import=people&provider={provider}'
  ```

  Remove all source selector/new-source markup and display fixed headings, “保存设置”, “测试连接”, and “预览数据”. Update `import_people_api` to redirect to this fixed tab without creating or selecting a source.

- [x] **Step 5: Canonicalize demo fixtures**

  In `seed_demo_data.py`, update/create the two rows using canonical `source_key` values and labels. Ensure reset logic identifies these records by their deterministic demo primary keys and canonical keys, without deleting unrelated user rows.

- [x] **Step 6: Run Task 1 tests**

  Run the same command from Step 2.

  Expected: PASS; rendered response must not contain saved secrets or arbitrary source controls.

- [x] **Step 7: Commit and stop for user inspection**

  ```powershell
  git add net/people/providers.py index/people/forms.py index/people/integrations.py index/templates/integrations/source_modal.html index/templates/common/import_modal.html index/common/imports.py net/management/commands/seed_demo_data.py tests/people/test_directory_ui.py tests/common/test_table_exports.py tests/architecture/test_demo_seed.py
  git commit -m "refactor: simplify personnel provider settings"
  ```

---

### Task 2: 会话专属后台任务状态和确认接口

**Files:**
- Modify: `net/people/tasks.py`
- Modify: `net/models/tasks.py`
- Modify: `index/people/integrations.py`
- Modify: `index/urls.py`
- Modify: `index/views/__init__.py`
- Test: `tests/people/test_directory_ui.py`

**Interfaces:**
- Consumes: `get_provider_source(provider: str)`, existing `enqueue_people_task(source_id, task_type, session_key)` and `owns_people_task(task, session_key)`.
- Produces: `TaskRun.PEOPLE_INTERACTIVE_TASK_TYPES`, `remember_people_task(session, task_id: UUID) -> None`, `pending_people_tasks(session) -> QuerySet[TaskRun]`, `acknowledge_people_task(session, task_id: UUID, *, kind: str) -> None`; JSON routes `people_task_status` and `people_task_acknowledge`.

- [x] **Step 1: Write failing ownership, status and acknowledgement tests**

  Add tests with two Django clients:

  ```python
  def test_status_returns_only_tasks_created_by_current_session(self):
      own, _ = self.enqueue('test', client=self.client)
      other_client = Client()
      other, _ = self.enqueue('preview', client=other_client, source=self.other)
      response = self.client.get('/integrations/people/tasks/status/')
      ids = {item['id'] for item in response.json()['tasks']}
      self.assertIn(str(own.pk), ids)
      self.assertNotIn(str(other.pk), ids)

  def test_acknowledgement_hides_running_notice_and_terminal_popup_independently(self):
      task, _ = self.enqueue('test')
      self.client.post(f'/integrations/people/tasks/{task.pk}/ack/', {'kind': 'running'})
      running = self.client.get('/integrations/people/tasks/status/').json()['tasks'][0]
      self.assertFalse(running['show_running'])
      self.execute(task)
      terminal = self.client.get('/integrations/people/tasks/status/').json()['tasks'][0]
      self.assertTrue(terminal['show_terminal'])
      self.client.post(f'/integrations/people/tasks/{task.pk}/ack/', {'kind': 'terminal'})
      self.assertEqual(self.client.get('/integrations/people/tasks/status/').json()['tasks'], [])
  ```

  Assert POST-only acknowledgement, CSRF protection, UUID validation, bounded session task history, and safe JSON fields only.

- [x] **Step 2: Run the status tests and verify endpoints are missing**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.people.test_directory_ui.PeopleImportUITests --verbosity 2
  ```

  Expected: FAIL with route-not-found or missing session-task behavior.

- [x] **Step 3: Implement bounded session task tracking**

  First define `TaskRun.PEOPLE_INTERACTIVE_TASK_TYPES` as the existing test/preview pair and make `PEOPLE_TASK_TYPES` an alias of it until Task 4 adds scheduled sync. Store only string UUIDs and acknowledgement sets under namespaced keys:

  ```python
  PEOPLE_SESSION_TASKS = 'people_directory_task_ids'
  PEOPLE_RUNNING_ACKS = 'people_directory_running_acks'
  PEOPLE_TERMINAL_ACKS = 'people_directory_terminal_acks'
  MAX_SESSION_TASKS = 20
  ```

  `remember_people_task` appends/deduplicates and truncates to the newest 20. `pending_people_tasks` parses valid UUIDs, queries `TaskRun.PEOPLE_INTERACTIVE_TASK_TYPES`, and additionally applies the existing hashed-session ownership check before serialization. Invalid/deleted IDs are removed from the session.

- [x] **Step 4: Add safe JSON status and POST acknowledgement views**

  Return only:

  ```python
  {
      'id': str(task.pk),
      'provider': task.people_source.source_type,
      'provider_label': task.people_source.get_source_type_display(),
      'operation': task.task_type,
      'operation_label': task.get_task_type_display(),
      'status': task.status,
      'status_label': task.get_status_display(),
      'show_running': task.status in TaskRun.ACTIVE_STATUSES and not running_acknowledged,
      'show_terminal': task.status in TaskRun.TERMINAL_STATUSES and not terminal_acknowledged,
      'message': safe_task_message(task),
      'jump_url': reverse('people_operation', args=[task.pk]),
      'ack_url': reverse('people_task_acknowledge', args=[task.pk]),
  }
  ```

  Never serialize snapshots, request URLs, credentials, raw exception strings, employee rows, or another session's task. Accept only `kind=running|terminal`; return 404 for unowned tasks and 400 for unsupported acknowledgement kinds.

- [x] **Step 5: Redirect test and preview enqueue back to the provider tab**

  After `enqueue_people_task`, call `remember_people_task`, add a one-time Django message saying the task is running in the background, and redirect to the fixed provider tab. Remove the `Refresh` response header from `people_operation`; retain an explicit manual refresh link on direct operation pages.

- [x] **Step 6: Run Task 2 tests**

  Run the command from Step 2.

  Expected: PASS; no tested response contains a `Refresh` header.

- [x] **Step 7: Commit and stop for user inspection**

  ```powershell
  git add net/people/tasks.py net/models/tasks.py index/people/integrations.py index/urls.py index/views/__init__.py tests/people/test_directory_ui.py
  git commit -m "feat: expose personnel task status by browser session"
  ```

---

### Task 3: 无闪烁运行提示、完成弹窗和预览导入入口

**Files:**
- Create: `static/app/js/people/import_tasks.js`
- Create: `tests/frontend/people_import_tasks.test.js`
- Create: `index/templates/integrations/task_notifications.html`
- Modify: `index/templates/common/base.html`
- Modify: `index/templates/common/import_modal.html`
- Modify: `index/templates/integrations/preview_modal.html`
- Modify: `index/templates/devices/list.html`
- Modify: `index/people/integrations.py`
- Modify: `static/app/css/style.css`
- Test: `tests/people/test_directory_ui.py`

**Interfaces:**
- Consumes: Task 2 status payload and acknowledgement endpoint.
- Produces: `createPeopleTaskController({document, window, fetchImpl, pollIntervalMs})` with `start()`, `stop()`, `poll()`, `dismissRunning(taskId)`, and `acknowledgeTerminal(taskId)` methods.

- [x] **Step 1: Write failing JavaScript controller tests**

  In `tests/frontend/people_import_tasks.test.js`, use small DOM/fetch/bootstrap fakes and assert:

  ```javascript
  test('poll updates notices without assigning or reloading location', async () => {
    const controller = createPeopleTaskController({document, window, fetchImpl, pollIntervalMs: 5000});
    await controller.poll();
    assert.equal(window.location.assignCalls.length, 0);
    assert.match(runningRegion.textContent, /飞书预览正在后台运行/);
  });

  test('dismiss running posts once while terminal completion still opens once', async () => {
    await controller.dismissRunning(taskId);
    await controller.poll();
    assert.equal(completionModal.showCalls, 1);
    await controller.poll();
    assert.equal(completionModal.showCalls, 1);
  });
  ```

  Also test failed fetch stops only the current polling timer, terminal confirmation posts `kind=terminal`, and jump uses the returned same-origin `jump_url`.

- [x] **Step 2: Run JavaScript tests and verify the controller is missing**

  Run:

  ```powershell
  node --test tests/frontend/people_import_tasks.test.js
  ```

  Expected: FAIL because `static/app/js/people/import_tasks.js` does not exist.

- [x] **Step 3: Implement the browser controller without full-page refresh**

  Export the controller under CommonJS for Node tests, then auto-start it only when `[data-people-task-notifications]` exists. Use one `setTimeout` after each successful poll rather than overlapping `setInterval` requests. Render running notices with a per-task “知道了，本次不再显示” button. Cache terminal task IDs in the controller so repeated identical payloads cannot reopen a modal before acknowledgement finishes.

- [x] **Step 4: Add notification regions and completion modal**

  Render a lightweight status region containing `data-status-url` and CSRF token, plus one Bootstrap modal with message, “关闭” and “查看结果”. Include it on the people list only. Add the script to `base.html` with `defer`; the controller must remain inert on all other pages.

- [x] **Step 5: Make preview result the only place that offers import**

  Keep `people_operation` as the jump target. For a successful valid preview, render counts, bounded samples, signed preview token, confirmation checkbox, and “导入数据”. For failed/invalid preview, render the categorized validation reason and no import form. After successful `people_apply`, redirect to the people list with exact created/updated/deactivated/skipped counts; if apply fails, return the same result modal with status 400 and no partial writes.

- [x] **Step 6: Add focused Django rendering tests**

  Assert the people page contains the notification status URL and JS hook, direct queued operation responses have no `Refresh`, valid preview contains exactly one `people_apply` form, invalid preview contains none, and another browser session cannot view or apply it.

- [x] **Step 7: Run Task 3 tests**

  Run:

  ```powershell
  node --test tests/frontend/people_import_tasks.test.js
  .\.venv\Scripts\python.exe manage.py test tests.people.test_directory_ui --verbosity 2
  ```

  Expected: PASS.

- [x] **Step 8: Commit and stop for user inspection**

  ```powershell
  git add static/app/js/people/import_tasks.js tests/frontend/people_import_tasks.test.js index/templates/integrations/task_notifications.html index/templates/common/base.html index/templates/common/import_modal.html index/templates/integrations/preview_modal.html index/templates/devices/list.html index/people/integrations.py static/app/css/style.css tests/people/test_directory_ui.py
  git commit -m "feat: add nonblocking personnel import notifications"
  ```

---

### Task 4: 人员定时同步数据库契约

**Files:**
- Modify: `net/models/tasks.py`
- Create: `net/migrations/0021_people_sync_schedules.py`
- Modify: `net/admin/tasks.py`
- Modify: `index/common/table_registry.py`
- Test: `tests/people/test_directory_ui.py`
- Test: `tests/inspections/test_queue.py`
- Test: `tests/architecture/test_admin_registry.py`

**Interfaces:**
- Consumes: existing `Schedule.Kind`, `Schedule.IntervalUnit`, `TaskRun.Source.SCHEDULED`, and `PeopleSyncSource`.
- Produces: `Schedule.people_source: ForeignKey[PeopleSyncSource] | None`, `TaskRun.TaskType.PEOPLE_SYNC = 'people_sync'`, and expanded `TaskRun.PEOPLE_TASK_TYPES` while retaining Task 2's `PEOPLE_INTERACTIVE_TASK_TYPES`.

- [x] **Step 1: Write failing model constraint tests**

  Add tests proving:

  ```python
  schedule = Schedule(people_source=source, kind='interval', interval_value=30,
                      interval_unit='minutes', is_enabled=True)
  schedule.full_clean()
  schedule.save()
  with self.assertRaises(ValidationError):
      Schedule(people_source=source, inspection_profile=profile, kind='daily',
               daily_time=time(8, 0)).full_clean()
  self.assertIn('people_sync', TaskRun.TaskType.values)
  ```

  Also assert one schedule per source, interval/daily field shape, and task binding rules: manual test/preview have no schedule; scheduled `people_sync` requires both matching `people_source` and `schedule`; `people_sync` cannot use manual source.

- [x] **Step 2: Run model tests and verify the fields/type are absent**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.inspections.test_queue tests.people.test_directory_ui tests.architecture.test_admin_registry --verbosity 2
  ```

  Expected: FAIL on missing `Schedule.people_source` and `people_sync` choice.

- [x] **Step 3: Extend models and separate interactive from all people task types**

  Add:

  ```python
  PEOPLE_INTERACTIVE_TASK_TYPES = frozenset({TaskType.PEOPLE_TEST, TaskType.PEOPLE_PREVIEW})
  PEOPLE_TASK_TYPES = frozenset({*PEOPLE_INTERACTIVE_TASK_TYPES, TaskType.PEOPLE_SYNC})
  ```

  Update `Schedule.clean()` to require exactly one of `inspection_profile_id`, `analysis_profile_id`, and `people_source_id`. Update `Schedule.__str__()` to use the first non-null relation. Expand database constraints and uniqueness for `people_source`. Update `TaskRun.clean()` to verify a scheduled task's `schedule.people_source_id == people_source_id`; update database shape constraints so scheduled people sync has no inspection/analysis profile, has both schedule/source, and cannot carry `people_applied_at` before completion.

- [x] **Step 4: Create and inspect the explicit migration**

  Generate `0021_people_sync_schedules.py`, then ensure it performs only:

  - add nullable `Schedule.people_source` with `PROTECT`;
  - replace `net_schedule_one_profile_ck` with exactly-one-of-three;
  - add `net_schedule_people_source_uniq`;
  - alter `TaskRun.task_type` choices;
  - replace `net_task_binding_shape_ck` for the new scheduled shape.

  Run:

  ```powershell
  .\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
  ```

  Expected: `No changes detected` after the migration exists.

- [x] **Step 5: Expose the new relation safely in Django admin and task tables**

  Add `people_source` to `ScheduleAdmin.list_display`, `search_fields`, and `raw_id_fields`. Add “人员自动同步” to task table choices. Keep task run and target run histories read-only.

- [x] **Step 6: Run Task 4 tests**

  Run the command from Step 2 plus migration drift check from Step 4.

  Expected: PASS.

- [x] **Step 7: Commit and stop for user inspection**

  ```powershell
  git add net/models/tasks.py net/migrations/0021_people_sync_schedules.py net/admin/tasks.py index/common/table_registry.py tests/people/test_directory_ui.py tests/inspections/test_queue.py tests/architecture/test_admin_registry.py
  git commit -m "feat: add personnel synchronization schedules"
  ```

---

### Task 5: 飞书和钉钉自动同步计划设置

**Files:**
- Modify: `index/people/forms.py`
- Modify: `index/people/integrations.py`
- Modify: `index/templates/integrations/source_modal.html`
- Test: `tests/people/test_directory_ui.py`

**Interfaces:**
- Consumes: `Schedule.people_source`, `next_run_at(schedule, after)`, canonical provider service from Task 1.
- Produces: `PeopleScheduleForm(data=None, *, source: PeopleSyncSource, schedule: Schedule | None)` and `save_people_schedule(source: PeopleSyncSource, cleaned_data: dict) -> Schedule`.

- [x] **Step 1: Write failing schedule form/UI tests**

  Test both providers and both schedule modes:

  ```python
  def test_fixed_provider_can_save_interval_and_daily_schedule(self):
      response = self.client.post('/integrations/people/providers/feishu/schedule/', {
          'is_enabled': 'on', 'kind': 'interval',
          'interval_value': '2', 'interval_unit': 'hours', 'daily_time': '',
      })
      self.assertRedirects(response, '/assets/people/?import=people&provider=feishu')
      schedule = Schedule.objects.get(people_source=self.feishu)
      self.assertEqual((schedule.kind, schedule.interval_value, schedule.interval_unit),
                       ('interval', 2, 'hours'))
  ```

  Add daily-time, pause/resume, invalid mixed fields, untested source cannot enable, changed config preserves schedule but renders “等待重新测试”, and status text for last/next run.

- [x] **Step 2: Run the focused UI tests and verify schedule controls are absent**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.people.test_directory_ui.PeopleImportUITests --verbosity 2
  ```

  Expected: FAIL on the missing schedule endpoint/form.

- [x] **Step 3: Implement strict plan parsing and atomic save**

  `PeopleScheduleForm.clean()` must normalize the inactive mode to `None`/empty string and reject enablement unless `source.is_enabled` and `source.public_data()['connection_test_current']` are true. In an atomic save, lock the source and existing schedule, call `full_clean()`, calculate `next_run_at` from `timezone.now()` when enabled, and set `next_run_at=None` when disabled.

- [x] **Step 4: Render compact schedule controls under each fixed platform**

  Add enabled checkbox, mode, interval value/unit, daily time, save button, and read-only last enqueue/last result/next run summary. Reuse the existing `data-schedule-kind` behavior in `task_ui.js`. When configuration changed after test, keep the saved schedule visible but show that it is not eligible to execute.

- [x] **Step 5: Run Task 5 tests**

  Run the command from Step 2.

  Expected: PASS.

- [x] **Step 6: Commit and stop for user inspection**

  ```powershell
  git add index/people/forms.py index/people/integrations.py index/templates/integrations/source_modal.html tests/people/test_directory_ui.py
  git commit -m "feat: configure scheduled personnel synchronization"
  ```

---

### Task 6: Worker 执行定时全量人员同步

**Files:**
- Modify: `net/people/tasks.py`
- Modify: `net/people/executor.py`
- Modify: `net/inspections/schedules.py`
- Modify: `net/inspections/worker.py`
- Modify: `index/inspections/tasks.py`
- Modify: `index/common/exports.py`
- Modify: `net/alerts/service.py`
- Modify: `tests/people/test_directory_ui.py`
- Create: `tests/people/test_scheduled_sync.py`
- Modify: `tests/inspections/test_worker.py`
- Modify: `tests/dashboard/test_taskbar.py`

**Interfaces:**
- Consumes: canonical source configuration identity, `preview_people_sync(source, adapter)`, `apply_people_sync(source, preview)`, existing task lease fencing and schedule polling.
- Produces: `enqueue_people_sync_task(schedule: Schedule, *, available_at: datetime) -> TaskRun` and `execute_people_sync(source, adapter) -> PeopleSyncResult` through the existing `execute_people_target` dispatch.

- [ ] **Step 1: Write failing enqueue and scheduler eligibility tests**

  In `tests/people/test_scheduled_sync.py`, assert a due tested source produces one scheduled task with one `people_source` target and immutable snapshots:

  ```python
  tasks = enqueue_due_schedules(now=self.now)
  task = tasks[0]
  self.assertEqual((task.task_type, task.source), ('people_sync', 'scheduled'))
  self.assertEqual(task.people_source_id, self.source.pk)
  self.assertEqual(task.schedule_id, self.schedule.pk)
  self.assertNotIn('credentials', task.profile_snapshot)
  self.assertEqual(task.target_scope_snapshot['targets'][0]['target_type'], 'people_source')
  ```

  Add tests that disabled source, disabled plan, never-tested source, and source modified after test remain due but enqueue nothing; after a successful retest the same plan enqueues. Concurrent/double scheduler polls must create at most one active task.

- [ ] **Step 2: Run scheduled enqueue tests and verify they fail**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.people.test_scheduled_sync --verbosity 2
  ```

  Expected: FAIL because scheduler profile resolution cannot handle `people_source`.

- [ ] **Step 3: Implement immutable scheduled enqueue**

  `enqueue_people_sync_task` must lock/refetch source and schedule, verify canonical key, enabled state, current connection test, matching relation, and schedule eligibility. Build `profile_snapshot` only from `source.public_data()`, add a non-secret configuration digest and schedule timing snapshot to `parameters_snapshot`, build a unique scope from task type plus source ID, and create one target with `target_type='people_source'`.

  Update `_schedule_profile`/`_enqueue_due_schedules` to select `people_source`, dispatch to this function, and advance `last_enqueued_at`/`next_run_at` only after successful enqueue. An ineligible changed source remains due for retry after retest.

- [ ] **Step 4: Write failing transactional Worker tests**

  Add three tests using `SnapshotAdapter`:

  ```python
  def test_scheduled_worker_previews_and_applies_complete_snapshot(self):
      self.run_worker_once()
      self.task.refresh_from_db()
      self.assertEqual(self.task.status, TaskRun.Status.SUCCESS)
      self.assertEqual(self.task.target_runs.get().result_snapshot['counts']['created'], 1)

  def test_validation_failure_rolls_back_every_personnel_change(self):
      before = list(People.objects.values_list('employee_id', 'name', 'is_active'))
      self.run_worker_once(adapter=self.invalid_duplicate_employee_adapter)
      self.assertEqual(list(People.objects.values_list('employee_id', 'name', 'is_active')), before)

  def test_apply_exception_rolls_back_and_persists_safe_failure(self):
      with patch('net.people.executor.apply_people_sync', side_effect=RuntimeError('secret-value')):
          self.run_worker_once()
      self.assertNotIn('secret-value', self.task.target_runs.get().error_message)
  ```

  Also assert persisted result counts include created, updated, unchanged, deactivated, and skipped; scheduled tasks contain no browser session owner and are excluded from browser status JSON.

- [ ] **Step 5: Implement scheduled execution and categorized safe errors**

  In `execute_people_target`, branch `PEOPLE_SYNC` to:

  ```python
  adapter = build_directory_adapter(source)
  preview = preview_people_sync(source, adapter)
  result = apply_people_sync(source, preview)
  result_snapshot = {
      'counts': {
          'created': result.created,
          'updated': result.updated,
          'unchanged': result.unchanged,
          'deactivated': result.deactivated,
          'skipped': len(preview.skipped),
      },
      'validation_messages': [],
  }
  ```

  Re-check source configuration identity and task lease immediately before apply. Map known adapter/sync exceptions to fixed categories `authentication`, `permission`, `rate_limit`, `connection`, `payload`, or `validation`; map unexpected exceptions to a generic safe message containing only the exception class name. Do not persist raw remote URLs, response bodies, tokens, secrets, or personnel samples in error text.

- [ ] **Step 6: Integrate task detail, export and alert exclusions**

  Change `index/inspections/tasks.py` so only `PEOPLE_INTERACTIVE_TASK_TYPES` redirect to the private operation modal; scheduled `people_sync` uses the standard task detail page. Keep every `PEOPLE_TASK_TYPES` member excluded from device alert reconciliation. Allow normal task-list filtering/detail/export for scheduled sync counts while preserving browser ownership checks for interactive test/preview exports.

- [ ] **Step 7: Run Task 6 tests**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.people.test_scheduled_sync tests.people.test_directory_ui.PeopleWorkerIntegrationTests tests.inspections.test_worker tests.dashboard.test_taskbar --verbosity 2
  ```

  Expected: PASS; all remote calls are mocked adapters.

- [ ] **Step 8: Commit and stop for user inspection**

  ```powershell
  git add net/people/tasks.py net/people/executor.py net/inspections/schedules.py net/inspections/worker.py index/inspections/tasks.py index/common/exports.py net/alerts/service.py tests/people/test_directory_ui.py tests/people/test_scheduled_sync.py tests/inspections/test_worker.py tests/dashboard/test_taskbar.py
  git commit -m "feat: execute scheduled personnel synchronization"
  ```

---

### Task 7: PC 分析配置弹窗纵向滚动

**Files:**
- Modify: `index/templates/inspections/profile_modal.html`
- Modify: `static/app/css/style.css`
- Modify: `tests/inspections/test_ui.py`

**Interfaces:**
- Consumes: Bootstrap `.modal-dialog-scrollable` structure and existing bottom submit button.
- Produces: `#profileConfigModal .modal-content` viewport height constraint and independently scrolling `.modal-body`.

- [ ] **Step 1: Replace the old top-save assertion with failing scroll assertions**

  Update `tests/inspections/test_ui.py`:

  ```python
  def test_pc_profile_modal_has_one_bottom_save_and_scrollable_body(self):
      response = self.client.get('/records/computers/?task_modal=profile')
      html = response.content.decode()
      self.assertEqual(html.count('>保存配置</button>'), 1)
      self.assertNotIn('profile-config-save-top', html)
      self.assertContains(response, 'id="profileConfigModal"')
      css = Path(settings.BASE_DIR, 'static/app/css/style.css').read_text(encoding='utf-8')
      self.assertIn('#profileConfigModal .modal-body', css)
      self.assertIn('overflow-y: auto', css)
  ```

- [ ] **Step 2: Run the focused UI test and verify it fails**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.inspections.test_ui.TaskUiTestCase.test_computer_pages_offer_manual_analysis_and_scan_range_controls --verbosity 2
  ```

  Expected: FAIL because two save buttons exist and no modal-specific overflow rule exists.

- [ ] **Step 3: Remove the duplicate button and constrain the modal body**

  Remove `.profile-config-save-top`. Add CSS scoped to the profile modal:

  ```css
  #profileConfigModal .modal-content {
      max-height: calc(100vh - 2rem);
  }

  #profileConfigModal form {
      display: flex;
      min-height: 0;
      flex: 1 1 auto;
      flex-direction: column;
  }

  #profileConfigModal .modal-body {
      min-height: 0;
      overflow-y: auto;
      overscroll-behavior: contain;
  }
  ```

  Keep the header and footer as non-growing flex children and retain the existing small-screen stacked footer behavior.

- [ ] **Step 4: Run Task 7 test**

  Run the command from Step 2.

  Expected: PASS.

- [ ] **Step 5: Commit and stop for user inspection**

  ```powershell
  git add index/templates/inspections/profile_modal.html static/app/css/style.css tests/inspections/test_ui.py
  git commit -m "fix: make PC analysis settings modal scrollable"
  ```

---

### Task 8: 删除旧多来源入口并进行聚焦验收

**Files:**
- Modify: `index/urls.py`
- Modify: `index/views/__init__.py`
- Modify: `index/common/imports.py`
- Modify: `index/people/integrations.py`
- Modify: `tests/architecture/test_people_domain_boundaries.py`
- Modify: `tests/architecture/test_application_contracts.py`
- Modify: `tests/common/test_table_exports.py`
- Modify: `README.md`
- Test: `tests/people/test_directory_ui.py`
- Test: `tests/people/test_scheduled_sync.py`
- Test: `tests/frontend/people_import_tasks.test.js`

**Interfaces:**
- Consumes: all fixed-provider routes and services produced in Tasks 1–7.
- Produces: one documented fixed-provider personnel import workflow with no routable legacy multi-source save/new-source behavior.

- [ ] **Step 1: Write failing route and secret-regression tests**

  Assert old generic save and legacy API POST paths return 404/405 as appropriate, fixed provider paths reverse correctly, and response/session serialization never contains known test secrets:

  ```python
  self.assertEqual(reverse('people_provider_save', args=['feishu']),
                   '/integrations/people/providers/feishu/save/')
  self.assertEqual(self.client.post('/integrations/people/sources/save/').status_code, 404)
  self.assertNotIn(b'super-secret', response.content)
  self.assertNotIn('super-secret', json.dumps(dict(self.client.session)))
  ```

  Add an architecture assertion that `PeopleSourceForm` and `people_source_save` are no longer public entry points.

- [ ] **Step 2: Run route tests and verify legacy paths still exist**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.architecture.test_people_domain_boundaries tests.architecture.test_application_contracts tests.common.test_table_exports --verbosity 2
  ```

  Expected: FAIL until old routes/imports are removed.

- [ ] **Step 3: Remove obsolete routes, imports and dead multi-source branches**

  Delete the generic `people_source_save` route/view/export and the legacy `data/people/api/<provider>/` POST endpoint if no current template references it. Remove query parameters `source_id`, source selector handling, “new source” session state, and old auto-refresh-specific branches. Keep the database model and existing noncanonical rows untouched but invisible.

- [ ] **Step 4: Update operator documentation**

  In `README.md`, document the three manual stages, Worker requirement, per-provider schedule modes, current-test requirement after config changes, background notification behavior, and where scheduled sync results are viewed. State explicitly that verification uses mocked adapters and does not contact production APIs.

- [ ] **Step 5: Run the agreed focused final verification**

  Run only:

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.people tests.inspections.test_ui tests.inspections.test_worker tests.common.test_table_exports tests.architecture.test_people_domain_boundaries tests.architecture.test_application_contracts tests.architecture.test_admin_registry --verbosity 2
  node --test tests/frontend/people_import_tasks.test.js
  .\.venv\Scripts\python.exe manage.py check
  .\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
  ```

  Expected: all selected tests PASS, JavaScript tests PASS, Django reports no issues, and no migration drift exists. Do not run the full suite, demo deployment, real provider connection, or real personnel import in this task.

- [ ] **Step 6: Commit and stop for final user inspection**

  ```powershell
  git add index/urls.py index/views/__init__.py index/common/imports.py index/people/integrations.py tests/architecture/test_people_domain_boundaries.py tests/architecture/test_application_contracts.py tests/common/test_table_exports.py README.md tests/people/test_directory_ui.py tests/people/test_scheduled_sync.py tests/frontend/people_import_tasks.test.js
  git commit -m "chore: remove legacy personnel source workflow"
  ```

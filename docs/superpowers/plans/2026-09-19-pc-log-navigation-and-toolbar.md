# PC 日志导航与工具栏 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把“日志列表”放到 PC 导航和首页 PC 卡片的明确入口中，删除日志分析记录页的重复入口，并让“分析筛选结果”紧邻“导出筛选结果”，保持筛选条件和弹窗行为不变。

**Architecture:** 只调整 Django 展示层。公共表格工作区增加一个可选动作插槽；日志页通过独立局部模板注入动作，分析弹窗继续位于筛选表单之外，避免嵌套表单。首页仍由 dashboard view 组装动作，不改变 URL、查询或任务创建逻辑。

**Tech Stack:** Django templates, Bootstrap 5, existing CSS, Django TestCase, Node built-in test runner.

**Spec:** docs/superpowers/specs/2026-09-19-operations-overview-and-mobile-design.md

## Global Constraints

- 在当前 main 分支工作，不创建 worktree。
- 不修改 index/devices/pc/bulk_analysis.py 的筛选和任务创建逻辑。
- 不改任何现有路由名称或接口地址。
- 不暂存或提交用户已有的 docs/identity-management.md、net/people/directory/sync.py、tests/people/test_directory_sync.py 修改。
- 所有改动遵循 red-green-refactor；每次提交前运行对应聚焦测试。

## Review Focus

- PC 菜单顺序必须是“PC 列表、日志列表、日志分析记录”。
- 首页 PC 卡片必须保留手动分析、PC 列表和日志分析记录，新增日志列表且顺序清楚。
- 日志分析记录页不再显示“导入日志证据 / 重新分析”。
- 日志列表的分析动作必须与导出动作同一工具栏，且分析弹窗不能嵌套在 GET 筛选表单内。
- 普通读者只看到其已有权限允许的入口和动作。

---

## Task 1: Add the PC log list entry to navigation and the home card

**Files:**

- Modify: index/templates/common/base.html
- Modify: index/dashboard/views.py
- Test: tests/architecture/test_navigation.py
- Test: tests/dashboard/test_asset_cards.py
- Test: tests/architecture/test_demo_seed.py

- [ ] Write failing navigation and dashboard tests.

Update the expected PC menu to:

    [
        ('PC 列表', '/assets/computers/'),
        ('日志列表', '/computers/logs/'),
        ('日志分析记录', '/computers/analyses/'),
    ]

Add an assertion that the PC dashboard card exposes secondary actions in this order:

    self.assertEqual(
        [action['label'] for action in pc_item['secondary_actions']],
        ['PC 列表', '日志列表', '日志分析记录'],
    )

- [ ] Run the focused tests and verify they fail because “日志列表” is absent.

    .\.venv\Scripts\python.exe manage.py test tests.architecture.test_navigation tests.dashboard.test_asset_cards tests.architecture.test_demo_seed

Expected: assertion failures showing the missing menu/card action.

- [ ] Implement the smallest navigation and card-action change.

In index/templates/common/base.html, insert this item between PC 列表 and 日志分析记录:

    <li><a class="dropdown-item" href="{% url 'computer_log_list' %}">日志列表</a></li>

In the PC item in index/dashboard/views.py add:

    'log_url': reverse('computer_log_list'),
    'log_label': '日志列表',

In _with_card_actions(), append that action after the generated list action and before record_url:

    if item.get('log_url'):
        secondary.append({'label': item['log_label'], 'url': item['log_url']})

- [ ] Run the focused tests and verify they pass.

    .\.venv\Scripts\python.exe manage.py test tests.architecture.test_navigation tests.dashboard.test_asset_cards tests.architecture.test_demo_seed

- [ ] Commit only the Task 1 files.

    git add index/templates/common/base.html index/dashboard/views.py tests/architecture/test_navigation.py tests/dashboard/test_asset_cards.py tests/architecture/test_demo_seed.py
    git commit -m "feat: expose PC log list navigation"

## Task 2: Remove the duplicate log-list button from analysis records

**Files:**

- Modify: index/templates/inspections/record_list.html
- Test: tests/inspections/test_record_workspace.py

- [ ] Add a failing response assertion for the computer-analysis record page.

    self.assertNotContains(response, '导入日志证据 / 重新分析')

Also retain an assertion that the page title and existing filters remain present.

- [ ] Run the focused test and verify it fails on the old button.

    .\.venv\Scripts\python.exe manage.py test tests.inspections.test_record_workspace

- [ ] Delete only the obsolete anchor from index/templates/inspections/record_list.html. Do not remove the page heading, task action, exports, filters, table, or pagination.

- [ ] Re-run the focused test and verify it passes.

    .\.venv\Scripts\python.exe manage.py test tests.inspections.test_record_workspace

- [ ] Commit the Task 2 change.

    git add index/templates/inspections/record_list.html tests/inspections/test_record_workspace.py
    git commit -m "refactor: remove duplicate log import entry"

## Task 3: Put bulk analysis beside filtered export without nesting forms

**Files:**

- Create: index/templates/devices/pc/bulk_analysis_action.html
- Modify: index/templates/common/table_workspace.html
- Modify: index/templates/devices/pc/log_list.html
- Modify: index/templates/devices/pc/bulk_analysis_modal.html
- Test: tests/devices/pc/test_bulk_analysis.py
- Test: tests/frontend/test_ui_design_contracts.py

- [ ] Add failing server-rendered contract tests.

For an administrator response from computer_log_list, assert:

    html.index('data-filtered-export') < html.index('data-bulk-analysis-action')
    html.count('id="bulkAnalysisModal"') == 1
    html.index('</form>') < html.index('id="bulkAnalysisModal"')

Add a reader assertion that data-bulk-analysis-action is absent. In the frontend contract test, assert the common workspace contains a guarded include named table_filter_actions_template immediately after the filtered export block.

- [ ] Run the tests and verify the missing insertion point fails.

    .\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_bulk_analysis tests.frontend.test_ui_design_contracts

- [ ] Add the optional action slot inside .table-filter-actions in index/templates/common/table_workspace.html.

    {% if table_filter_actions_template %}
        {% include table_filter_actions_template %}
    {% endif %}

Place it after data-filtered-export and before configuration_selection_path.

- [ ] Create index/templates/devices/pc/bulk_analysis_action.html containing only the administrator trigger.

    {% load extras %}
    {% query_transform request bulk_mode='filtered' as filtered_url %}
    <a class="btn btn-outline-primary text-nowrap d-inline-flex align-items-center justify-content-center"
       href="?{{ filtered_url }}" data-bulk-analysis-action>分析筛选结果</a>

- [ ] Change index/templates/devices/pc/log_list.html to pass the action partial to the workspace and render the modal after the workspace form.

    {% include 'common/table_workspace.html' with table_filter_actions_template='devices/pc/bulk_analysis_action.html' %}
    {% if can_administer %}{% include 'devices/pc/bulk_analysis_modal.html' %}{% endif %}

- [ ] Remove the trigger wrapper from index/templates/devices/pc/bulk_analysis_modal.html so that file contains only the modal. Reformat the template into readable blocks without changing field names, actions, query parameters, or button labels inside the modal.

- [ ] Re-run Django and frontend tests.

    .\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_bulk_analysis tests.frontend.test_ui_design_contracts
    node --test tests/frontend/*.test.js

Expected: all pass; no nested forms; the trigger is adjacent to export.

- [ ] Commit Task 3.

    git add index/templates/common/table_workspace.html index/templates/devices/pc/log_list.html index/templates/devices/pc/bulk_analysis_action.html index/templates/devices/pc/bulk_analysis_modal.html tests/devices/pc/test_bulk_analysis.py tests/frontend/test_ui_design_contracts.py
    git commit -m "refactor: integrate bulk analysis into log toolbar"

## Task 4: Regression verification

- [ ] Run template, navigation, PC log, and architecture tests together.

    .\.venv\Scripts\python.exe manage.py test tests.architecture.test_navigation tests.architecture.test_routes tests.architecture.test_demo_seed tests.dashboard.test_asset_cards tests.devices.pc.test_bulk_analysis tests.inspections.test_record_workspace tests.frontend.test_ui_design_contracts
    node --test tests/frontend/*.test.js

- [ ] Run Django system checks.

    .\.venv\Scripts\python.exe manage.py check

- [ ] Inspect git status and confirm the three pre-existing user-modified files remain unstaged.

    git status --short

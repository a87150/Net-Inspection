# 网络巡检中心前端统一改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保持全部 Django 业务契约不变的情况下统一美化网络巡检中心的仪表盘、列表、详情、弹窗与响应式体验。

**Architecture:** 以现有 `common/base.html` 和 `style.css` 为单一设计系统入口，在共享模板上加入稳定语义类，再由页面模板补充最少量布局钩子。所有交互继续使用既有 Bootstrap、原生 JavaScript 和 `data-*` 选择器。

**Tech Stack:** Django templates, Bootstrap 5（本地资源）, CSS, 原生 JavaScript, Django TestCase, Node test runner

**Spec:** `docs/superpowers/specs/2026-09-04-operations-ui-refresh-design.md`

## Global Constraints

- 不改变后端业务逻辑、数据库模型、接口地址、URL 名称或数据处理流程。
- 不删除或改名既有按钮、表单字段、筛选参数、排序、分页、导入导出、巡检和后台任务入口。
- 不引入在线资源或大型前端框架；Windows 与 Linux 均使用系统字体和本地静态文件。
- 自动测试不得连接真实飞书、钉钉、域控或设备，不执行真实导入和巡检。
- 直接在当前 `main` 分支工作，不创建工作树。

---

### Task 1: UI 契约测试与设计系统

**Files:**
- Create: `tests/frontend/test_ui_design_contracts.py`
- Modify: `index/templates/common/base.html`
- Modify: `static/app/css/style.css`

**Interfaces:**
- Consumes: 现有 Django URL 名称、Bootstrap 类和 `data-*` 交互选择器。
- Produces: `app-header`、`app-content`、`status-badge`、`data-table`、`table-actions-*`、`modal-shell` 等共享视觉契约。

- [ ] **Step 1: 写失败的页面契约测试**

```python
def test_core_pages_use_shared_application_chrome(self):
    for url in self.core_urls:
        response = self.client.get(url)
        self.assertContains(response, 'class="app-header')
        self.assertContains(response, 'id="main-content"')
```

- [ ] **Step 2: 运行测试并确认因共享界面钩子尚不存在而失败**

Run: `.venv\Scripts\python.exe manage.py test tests.frontend.test_ui_design_contracts -v 2`
Expected: FAIL，缺少新应用外壳、数据表或弹窗契约。

- [ ] **Step 3: 扩展公共应用外壳和 CSS 令牌**

在 `base.html` 保留全部导航链接并加入应用品牌说明、内容容器与页脚结构；在 `style.css` 定义排版、颜色、层级、焦点、按钮、表单、状态、表格和空状态样式。

- [ ] **Step 4: 运行契约测试并确认通过**

Run: `.venv\Scripts\python.exe manage.py test tests.frontend.test_ui_design_contracts -v 2`
Expected: PASS。

### Task 2: 首页与人员统计

**Files:**
- Modify: `index/templates/dashboard/index.html`
- Modify: `index/templates/dashboard/card.html`
- Modify: `index/templates/inspections/taskbar.html`
- Modify: `index/templates/people/statistics.html`
- Test: `tests/frontend/test_ui_design_contracts.py`

**Interfaces:**
- Consumes: `items`、`task_page`、`metrics` 和 `departments` 现有模板上下文。
- Produces: 仪表盘总览网格、可扫描指标卡和任务状态表。

- [ ] **Step 1: 增加失败测试，要求首页六类卡片、任务区域和状态文字均存在**
- [ ] **Step 2: 运行首页与人员测试并确认新断言失败**
- [ ] **Step 3: 重排卡片指标、操作层级和人员统计卡，保留全部链接和文本契约**
- [ ] **Step 4: 运行 `tests.dashboard` 与 `tests.people.test_statistics` 并确认通过**

### Task 3: 列表、筛选、分页和详情

**Files:**
- Modify: `index/templates/common/table_workspace.html`
- Modify: `index/templates/common/pagination.html`
- Modify: `index/templates/devices/list.html`
- Modify: `index/templates/devices/item_list.html`
- Modify: `index/templates/devices/detail.html`
- Modify: `index/templates/domain/object_list.html`
- Modify: `index/templates/inspections/*.html`
- Modify: `index/templates/devices/pc/*.html`
- Modify: `index/templates/alerts/*.html`
- Test: `tests/frontend/test_ui_design_contracts.py`

**Interfaces:**
- Consumes: `table_definition`、`table_state`、`page_obj` 以及既有 URL/query 参数。
- Produces: 可横向滚动的固定表头数据表、固定操作列、统一状态和详情卡片。

- [ ] **Step 1: 写失败测试，覆盖共享表格类、操作列、结果摘要与语义分页**
- [ ] **Step 2: 运行测试并确认因缺少语义类失败**
- [ ] **Step 3: 为所有主要数据表和详情模板加入共享类并统一视觉层级，不改变字段和动作**
- [ ] **Step 4: 运行 `tests.common`、`tests.system.test_application`、`tests.domain`、`tests.inspections` 与设备 UI 测试**

### Task 4: 弹窗和响应式收尾

**Files:**
- Modify: `index/templates/common/import_modal.html`
- Modify: `index/templates/integrations/*.html`
- Modify: `index/templates/domain/operation_modal.html`
- Modify: `index/templates/inspections/profile_modal.html`
- Modify: `index/templates/inspections/run_modal.html`
- Modify: `index/templates/alerts/channel_modal.html`
- Modify: `index/templates/alerts/policy_modal.html`
- Modify: `static/app/css/style.css`
- Test: `tests/frontend/test_ui_design_contracts.py`

**Interfaces:**
- Consumes: 现有 modal ID、表单 action、字段名、CSRF 与 JavaScript `data-*` 钩子。
- Produces: 视口内滚动正文、固定页脚操作、移动端单列操作和清晰配置分区。

- [ ] **Step 1: 写失败测试，要求长弹窗使用滚动 dialog 与语义分区**
- [ ] **Step 2: 运行测试并确认失败原因是缺少弹窗契约**
- [ ] **Step 3: 应用通用 modal shell 和响应式规则，保留所有字段及提交动作**
- [ ] **Step 4: 运行 Django UI 测试及 `node --test tests/frontend/*.test.js`**

### Task 5: 最终验证与提交

**Files:**
- Verify: all changed templates, CSS, tests, spec, and plan

**Interfaces:**
- Consumes: 完整应用测试套件。
- Produces: 无模板错误、无路由变化、无功能回退的单个清晰提交。

- [ ] **Step 1: 运行 `manage.py check` 和 `makemigrations --check --dry-run`**
- [ ] **Step 2: 运行完整 `manage.py test` 与全部 Node 前端测试**
- [ ] **Step 3: 检查 `git diff --check`、模板 URL/表单动作差异和 git 状态**
- [ ] **Step 4: 使用验证与代码审查清单检查需求覆盖和可访问性**
- [ ] **Step 5: 提交 `feat: refresh operations management interface`**

# 现有界面移动端收口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 系统收口现有 Django 页面在手机和平板上的导航、筛选、表格、详情和弹窗体验，同时精简重复样式，不改变业务功能和数据流。

**Architecture:** 在既有 tokens.css、foundation.css、operations.css 和 modal-workflows.css 上补齐可复用的响应式规则；模板只增加必要的语义类和 ARIA，不复制页面专属布局。以模板/CSS 合同测试兜底，再用关键视口做浏览器验证。

**Tech Stack:** Django templates, Bootstrap 5, existing CSS and native JavaScript, Django TestCase, Node built-in test runner.

**Spec:** docs/superpowers/specs/2026-09-19-operations-overview-and-mobile-design.md

## Global Constraints

- 不引入第二个前端框架；Vue 仍只属于综合展示页。
- 不改变表格查询、服务端排序、服务端筛选、分页、导入导出或任务流程。
- 表格保持横向滚动，不通过隐藏关键列来伪造移动端适配。
- 所有操作至少有文字或可访问名称，触控目标优先达到 44px。
- 弹窗只保留一个主滚动区域，标题和底部操作保持可见。
- 保护当前 main 分支上用户的未提交文件。

## Review Focus

- 360px 宽度无整页横向滚动；宽表只在自己的滚动容器中横向滚动。
- 导航展开后无遮挡、无溢出，账号菜单和二级菜单可触控。
- 筛选区、结果工具栏和分页在窄屏自然换行，按钮不再上移或错位。
- 长文本、状态徽标、空状态和操作列仍可读。
- 大弹窗只有内容区滚动，底部按钮固定清晰；软键盘出现时仍可提交。
- 共享 CSS 合并重复规则后，桌面端视觉不回退。

---

## Task 1: Encode the responsive contracts before changing styles

**Files:**

- Modify: tests/frontend/test_ui_design_contracts.py
- Modify: tests/frontend/ui_contracts.test.js

- [ ] Add failing source/markup contracts for:

    - app navbar has a mobile collapse target and account menu remains inside it.
    - .table-responsive owns overflow-x while body/app-main do not.
    - .table-filter-actions, .page-actions and .pagination can wrap.
    - modal-dialog-scrollable and modal-footer--sticky are used by long configuration/import modals.
    - mobile media queries cover 767.98px and 575.98px.
    - reduced-motion disables nonessential transitions and animated decorations.
    - interactive controls have a shared minimum touch-size token/rule.

- [ ] Run the focused tests and record each expected failure before implementation.

    .\.venv\Scripts\python.exe manage.py test tests.frontend.test_ui_design_contracts
    node --test tests/frontend/ui_contracts.test.js

## Task 2: Normalize the mobile shell, header and page actions

**Files:**

- Modify: static/app/css/tokens.css
- Modify: static/app/css/foundation.css
- Modify: index/templates/common/base.html
- Test: tests/frontend/test_ui_design_contracts.py

- [ ] Add a --control-touch-size token of 2.75rem and use it for navbar toggles, dropdown items, buttons and form controls on coarse pointers without inflating compact desktop tables.

- [ ] At max-width 991.98px make the collapsed navbar a bounded glass panel with vertical scrolling, full-width nav items, stable dropdown positioning and a separated account section. Preserve Bootstrap collapse behavior and all links.

- [ ] At max-width 767.98px reduce app-main side padding, allow page-heading actions to wrap below the title, and keep headings/metadata from forcing horizontal overflow. Add overflow-wrap:anywhere only to user-generated long identifiers, not every label.

- [ ] Run the focused contract test until green.

    .\.venv\Scripts\python.exe manage.py test tests.frontend.test_ui_design_contracts

- [ ] Commit shell changes.

    git add static/app/css/tokens.css static/app/css/foundation.css index/templates/common/base.html tests/frontend/test_ui_design_contracts.py tests/frontend/ui_contracts.test.js
    git commit -m "style: normalize responsive application shell"

## Task 3: Make filters, tables and pagination robust on narrow screens

**Files:**

- Modify: static/app/css/operations.css
- Modify: index/templates/common/table_workspace.html
- Modify: index/templates/common/pagination.html
- Test: tests/common/test_table_filters.py
- Test: tests/frontend/test_ui_design_contracts.py

- [ ] Add failing response assertions that filter actions keep semantic order, table regions have role/label/tabindex, result summaries are live regions and pagination controls retain accessible labels.

- [ ] Implement shared responsive behavior:

    - filter fields use minmax(12rem, 1fr) on desktop and one column below 575.98px.
    - action groups use display:flex, align-items:stretch and flex-wrap:wrap.
    - narrow-screen action buttons grow to full row only below 575.98px.
    - table-scroll-shell contains sticky headers and overflow; it does not clip focus outlines.
    - operation columns remain readable with white-space:nowrap and a subtle edge divider.
    - pagination separates summary, page links and page-size selector into wrapping groups.

- [ ] Run table/filter tests.

    .\.venv\Scripts\python.exe manage.py test tests.common.test_table_filters tests.frontend.test_ui_design_contracts

- [ ] Commit shared table improvements.

    git add static/app/css/operations.css index/templates/common/table_workspace.html index/templates/common/pagination.html tests/common/test_table_filters.py tests/frontend/test_ui_design_contracts.py
    git commit -m "style: harden responsive data workspaces"

## Task 4: Standardize modal scrolling and mobile action bars

**Files:**

- Modify: static/app/css/modal-workflows.css
- Modify: tests/frontend/test_ui_design_contracts.py
- Modify: index/templates/inspections/profile_modal.html
- Modify: index/templates/inspections/run_modal.html
- Modify: index/templates/devices/pc/config_modal.html
- Modify: index/templates/alerts/channel_modal.html
- Modify: index/templates/alerts/policy_modal.html
- Modify: index/templates/alerts/template_modal.html
- Modify: index/templates/integrations/source_modal.html
- Modify: index/templates/integrations/preview_modal.html
- Modify: index/templates/common/import_modal.html
- Modify: index/templates/domain/group_members.html
- Modify: index/templates/domain/controller_settings.html

- [ ] Inventory all modal templates before editing and record which already satisfy the contract; do not create replacement templates.

    rg -l "modal-dialog|modal-content" index/templates

- [ ] Add failing contracts that every long workflow modal uses modal-dialog-scrollable, modal-body--scroll and modal-footer--sticky, and that only the modal body owns vertical overflow.

- [ ] Consolidate modal sizing and scrolling rules in modal-workflows.css. Use max-height based on 100dvh with a vh fallback; keep headers/footers non-shrinking; stack footer buttons full-width below 575.98px; preserve inline field errors next to their fields.

- [ ] Remove page-local declarations that duplicate the shared modal rules. Do not rename input fields, form ids, submit URLs, data attributes or Bootstrap modal ids.

- [ ] Run the modal contracts and existing workflow tests.

    .\.venv\Scripts\python.exe manage.py test tests.frontend.test_ui_design_contracts tests.devices.pc.test_config_layout tests.domain.test_groups tests.alerts.test_ui

- [ ] Commit modal changes.

    git add static/app/css/modal-workflows.css index/templates/inspections/profile_modal.html index/templates/inspections/run_modal.html index/templates/devices/pc/config_modal.html index/templates/alerts/channel_modal.html index/templates/alerts/policy_modal.html index/templates/alerts/template_modal.html index/templates/integrations/source_modal.html index/templates/integrations/preview_modal.html index/templates/common/import_modal.html index/templates/domain/group_members.html index/templates/domain/controller_settings.html tests/frontend/test_ui_design_contracts.py
    git diff --cached --check
    git commit -m "style: standardize responsive workflow modals"

Before committing, unstage any template not actually changed and confirm no unrelated file is staged.

## Task 5: Perform page-level responsive cleanup with exact acceptance checks

**Files:**

- Modify: static/app/css/operations.css
- Modify: static/app/css/foundation.css
- Modify: index/templates/dashboard/index.html
- Modify: index/templates/devices/list.html
- Modify: index/templates/devices/item_list.html
- Modify: index/templates/devices/pc/log_list.html
- Modify: index/templates/inspections/record_list.html
- Modify: index/templates/inspections/task_list.html
- Modify: index/templates/domain/object_list.html
- Modify: index/templates/alerts/list.html
- Modify: index/templates/people/statistics.html
- Test: tests/frontend/test_ui_design_contracts.py
- Test: tests/frontend/test_ui_design_contracts.py

- [ ] Start the demo server for read-only visual verification. Do not execute imports, tests of external channels, domain operations or inspections.

    .\.venv\Scripts\python.exe -m deploy.demo

- [ ] Audit these pages at 1440x900, 1024x768, 768x1024, 390x844 and 360x800:

    首页；人员列表/统计；PC 列表/日志列表/日志分析记录；网络设备/服务器/安防设备列表；巡检任务/异常/详情；域账号/域计算机/域分组；告警记录；飞书/钉钉导入；巡检配置、告警配置、域控连接、成员管理和导入弹窗。

For each viewport verify no body horizontal scroll, no clipped primary action, visible focus, readable status text, reachable pagination, single modal scroll container and sticky modal footer.

- [ ] For every observed defect, first add a focused contract assertion or a small reproducible browser check, then make the smallest shared CSS/template change. Prefer an existing component class over a page-specific selector.

- [ ] Re-run the contract suite after each cluster of fixes.

    .\.venv\Scripts\python.exe manage.py test tests.frontend.test_ui_design_contracts
    node --test tests/frontend/*.test.js

- [ ] Commit verified cleanup in one page-family-sized commit, listing the page family in the message.

## Task 6: Final regression and duplication review

- [ ] Search for duplicate responsive declarations and repeated magic values.

    rg -n "@media|max-height:.*vh|overflow-x|2\.75rem|44px" static/app/css

Move truly shared values into tokens.css or one owning component file. Keep deliberate page-specific exceptions documented with a one-line comment.

- [ ] Run the full frontend and relevant Django suites.

    node --test tests/frontend/*.test.js
    .\.venv\Scripts\python.exe manage.py test tests.frontend tests.architecture tests.dashboard tests.common.test_table_filters tests.devices.pc.test_bulk_analysis tests.inspections.test_record_workspace tests.domain.test_groups tests.system.test_reader_controls
    .\.venv\Scripts\python.exe manage.py check

- [ ] Inspect the final diff for route changes, missing buttons, renamed fields, external URLs, embedded credentials and accidental user-file staging.

    git diff --check
    git status --short
    rg -n "https?://|cdn|unpkg|jsdelivr" index/templates static/app static/vendor/vue/README.md

The only allowed remote URLs in runtime templates/assets are none; provenance URLs may appear only in static/vendor/vue/README.md.

- [ ] Create a final integration commit only if this task produced remaining verified changes.

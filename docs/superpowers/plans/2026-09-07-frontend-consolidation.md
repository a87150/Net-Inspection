# Frontend Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the operations UI design system, shared components, Admin chrome, accessibility, dense-table usability, and dashboard hierarchy without changing backend workflows.

**Architecture:** Preserve `style.css` as the public entry point while splitting its existing cascade into ordered local layers. Introduce small Django includes and native-JavaScript controllers at presentation boundaries, retain page-specific business rows, and verify every user-visible behavior with Django or Node tests before implementation.

**Tech Stack:** Django templates and test client, Bootstrap 5, local CSS, native JavaScript, Node test runner.

**Spec:** `docs/superpowers/specs/2026-09-07-frontend-consolidation-design.md`

## Global Constraints

- Keep all existing routes, models, interfaces, task processing, import/export, inspection, alert, and domain behavior unchanged.
- Use only local assets and preserve Windows/Linux deployment.
- Do not introduce a new production frontend framework.
- Do not call real external integrations or devices in tests.
- Preserve unrelated dirty-worktree changes and stage mixed files selectively.
- Execute in the current branch because the user explicitly prohibited a new worktree.

---

### Task 1: Ordered CSS design-system layers

**Files:**
- Create: `static/app/css/tokens.css`
- Create: `static/app/css/foundation.css`
- Create: `static/app/css/operations.css`
- Create: `static/app/css/modal-workflows.css`
- Modify: `static/app/css/style.css`
- Test: `tests/frontend/test_ui_design_contracts.py`

**Interfaces:**
- Consumes: existing `style.css` section order and `app/css/style.css` URL.
- Produces: the same entry-point URL importing four local layers in original cascade order.

- [ ] Add a Django contract test that resolves `style.css`, follows each local import, and asserts the shared tokens and representative application, table, dashboard, and modal selectors remain present.
- [ ] Run the focused contract test and confirm it fails because the layer files do not exist.
- [ ] Move the existing CSS sections mechanically into the four layer files, leave ordered imports in `style.css`, and introduce shared status/glass/spacing variables only where replacements are value-identical.
- [ ] Run frontend design contracts and `manage.py check`.
- [ ] Commit only the CSS layers, entry point, and focused tests with `refactor: split frontend design system layers`.

### Task 2: Shared presentation primitives

**Files:**
- Create: `index/templates/common/brand.html`
- Create: `index/templates/common/table_sort_headers.html`
- Create: `index/templates/common/status_badge.html`
- Create: `index/templates/common/empty_state.html`
- Create: `static/app/js/common/conditional_fields.js`
- Modify: `index/templates/common/base.html`
- Modify: `templates/admin/base_site.html`
- Modify: list templates under `index/templates/devices/`, `domain/`, `inspections/`, and `alerts/`
- Modify: `static/app/js/inspections/task_ui.js`
- Modify: `static/app/js/people/modal.js`
- Test: `tests/frontend/test_ui_design_contracts.py`
- Test: `tests/frontend/conditional_fields.test.js`

**Interfaces:**
- Consumes: `table_definition`, `table_state`, `query_transform`, standard status strings, and `data-schedule-*` attributes.
- Produces: shared includes plus `bindConditionalFields(root)` for interval/daily controls.

- [ ] Add rendering tests proving the application and Admin use the same brand partial, sortable list headers expose `scope="col"`, standard task statuses render through one shared badge contract, and empty lists retain their existing copy.
- [ ] Add Node tests showing interval and daily controls toggle visibility and disabled state consistently.
- [ ] Run the focused Django and Node tests and confirm expected failures from missing shared primitives.
- [ ] Implement the includes and controller, replace repeated presentation fragments without changing row data or links, and page-load the controller through the shared shell.
- [ ] Run all frontend Node tests and the affected Django list-page tests.
- [ ] Commit the shared primitives with `refactor: reuse frontend presentation components`.

### Task 3: Admin chrome and accessible form feedback

**Files:**
- Modify: `templates/admin/base_site.html`
- Modify: `templates/admin/base.html`
- Modify: `static/app/css/admin.css`
- Create: `static/app/js/common/form_accessibility.js`
- Modify: `index/templates/common/base.html`
- Modify: `index/templates/common/table_workspace.html`
- Modify: `index/templates/common/table_field_filter.html`
- Modify: form-heavy modal templates under `index/templates/`
- Test: `tests/frontend/form_accessibility.test.js`
- Test: `tests/frontend/test_ui_design_contracts.py`

**Interfaces:**
- Consumes: Django-rendered `.errorlist` and field-level `.text-danger` nodes.
- Produces: `enhanceFormErrors(root)` which adds stable error IDs, `aria-invalid`, and merged `aria-describedby` values without changing submission.

- [ ] Add Admin rendering tests proving only 返回运维总览、修改密码、注销 are present in user tools and no welcome/theme control remains.
- [ ] Add Node tests proving invalid controls reference their visible error text and preserve existing help-text descriptions.
- [ ] Add Django rendering tests proving common table-filter labels point to stable control IDs.
- [ ] Run focused tests and confirm failures against the existing Admin and form markup.
- [ ] Implement the Admin user-tools override, scoped CSS cleanup, form accessibility controller, stable filter IDs, and consistent field-error markup.
- [ ] Run Admin, modal, domain, people, and table-filter tests plus all frontend Node tests.
- [ ] Commit with `fix: align admin and form accessibility`.

### Task 4: Dense-table responsive usability

**Files:**
- Modify: `index/templates/common/table_workspace.html`
- Modify: `index/templates/common/pagination.html`
- Modify: `index/templatetags/extras.py`
- Modify: `static/app/js/common/table_workspace.js`
- Modify: `static/app/css/operations.css`
- Modify: task, alert, device, domain, and record list templates where action and long-text cells are rendered.
- Test: `tests/frontend/table_workspace.test.js`
- Test: `tests/common/test_table_filters.py`

**Interfaces:**
- Produces: `page_window(page_obj, radius=2) -> tuple[int, ...]`; active-filter chips using existing `data-filter-*` metadata; responsive `.table-row-actions` and `.table-text-disclosure` components.

- [ ] Add Node tests that render active-filter chips, clear exactly one filter, preserve unrelated query parameters, and move focus to the related field.
- [ ] Add Django tests for first/last/windowed pagination links and visually hidden table captions.
- [ ] Run focused tests and confirm failures because chips and page windows are absent.
- [ ] Implement filter-chip DOM behavior, the pagination template tag, captions, first/last/windowed pagination, responsive action wrapping, and accessible expandable long text.
- [ ] Run table workspace Node tests and Django table/list tests.
- [ ] Commit with `feat: improve dense table workflows`.

### Task 5: Dashboard abnormal-first hierarchy

**Files:**
- Modify: `index/dashboard/views.py`
- Modify: `index/templates/dashboard/index.html`
- Modify: `index/templates/dashboard/card.html`
- Modify: `static/app/css/operations.css`
- Test: `tests/dashboard/test_asset_cards.py`
- Test: `tests/frontend/test_ui_design_contracts.py`

**Interfaces:**
- Produces: `dashboard_attention_score(item) -> tuple[int, int]` used only to order presentation; card context exposes `primary_url`, `primary_label`, and `secondary_actions` without removing links.

- [ ] Add dashboard tests with mixed healthy/abnormal summaries proving actionable cards render first and every existing destination remains present exactly once.
- [ ] Add a rendering test proving each card exposes one primary action and a compact secondary action group.
- [ ] Run focused dashboard tests and confirm ordering/action-hierarchy failures.
- [ ] Implement presentation-only attention scoring, derive primary/secondary actions, show a rendered refresh timestamp, and collapse the task region only when there is no current task content.
- [ ] Run dashboard and frontend design contract tests.
- [ ] Commit with `feat: prioritize dashboard attention states`.

### Task 6: Final verification and selective integration

**Files:**
- Review: all files changed by Tasks 1–5

**Interfaces:**
- Consumes: all preceding presentation contracts.
- Produces: a clean sequence of scoped commits while leaving unrelated working-tree edits untouched.

- [ ] Run all `tests/frontend/*.test.js` with Node.
- [ ] Run Django frontend, dashboard, table, Admin, domain UI, people UI, inspection UI, and alert UI test modules without external calls.
- [ ] Run `manage.py check` and `git diff --check`.
- [ ] Inspect every staged diff, selectively stage mixed files from `HEAD` plus only the intended hunks, and verify unrelated modifications remain unstaged.
- [ ] Report the exact tests, commit hashes, remaining unrelated dirty files, and the browser-screenshot limitation.

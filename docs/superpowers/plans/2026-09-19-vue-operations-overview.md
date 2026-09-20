# Vue 运维态势与网络拓扑 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个可在“运维态势”和“网络拓扑”之间切换的综合展示页，以现有只读数据呈现真实运行状态；页面每 30 秒局部刷新，支持暂停、手动刷新、隐藏页暂停和失败保留旧数据。

**Architecture:** Django 提供首屏 JSON 和只读快照端点；Vue 3.5.43 仅挂载到综合展示页根节点，使用本地 production ESM 构建，不接管公共导航。纯 JavaScript 模块负责刷新状态机、拓扑规范化和效果生命周期。拓扑区域同时组合逻辑归属边与 net.topology.read_model 已白名单化的 LLDP/CDP 物理链路；无物理数据时自然降级为逻辑拓扑。

**Tech Stack:** Django, Vue 3.5.43 local ESM build, SVG, Canvas 2D, Bootstrap 5, CSS custom properties, Django TestCase, Node built-in test runner.

**Spec:** docs/superpowers/specs/2026-09-19-operations-overview-and-mobile-design.md

## Global Constraints

- Vue 只允许用于此页面；其他页面继续使用 Django 模板、Bootstrap 和原生 JavaScript。
- 不使用 CDN、在线图片、在线字体或运行时联网资源。
- 不创建拓扑数据库模型，不采集设备，不调用飞书、钉钉、域控或巡检接口。
- 不把子网、ARP、MAC、FDB 或 VLAN 相似性伪装成物理链路。
- 端点仅返回展示所需字段，不返回凭据、密文、令牌、原始配置或完整日志正文。
- 保留当前 main 分支和用户现有未提交修改。

## Review Focus

- 顶部导航链接位于“告警记录”左侧。
- 两个视图在同一 URL 内切换，刷新时保留视图、筛选、缩放和平移状态。
- 页面隐藏时停止定时器；重新可见后立即刷新；手动暂停不被可见性变化覆盖。
- 请求失败时保留上一份成功数据并显示可访问的陈旧状态提示。
- prefers-reduced-motion 下停止非必要动画；Canvas 正确销毁，不重复注册事件。
- 逻辑拓扑和 LLDP/CDP 物理拓扑在数据结构、标签和图例上明确区分。
- 未解析邻居保留为外部节点；单向、双向、陈旧和冲突证据均有文字与线型，不只依靠颜色。

---

## Task 1: Vendor the pinned Vue production build locally

**Files:**

- Create: static/vendor/vue/vue.esm-browser.prod.js
- Create: static/vendor/vue/LICENSE
- Create: static/vendor/vue/README.md
- Test: tests/frontend/test_ui_design_contracts.py

- [ ] Add a failing static dependency contract test that asserts the three files exist and README.md contains all of:

    Vue 3.5.43
    vue.esm-browser.prod.js
    MIT
    https://github.com/vuejs/core/releases/tag/v3.5.43

- [ ] Run the test and verify it fails because the vendor files are absent.

    .\.venv\Scripts\python.exe manage.py test tests.frontend.test_ui_design_contracts

- [ ] Download the exact official v3.5.43 distribution during development and store it in static/vendor/vue/. Record the upstream release URL, npm tarball URL, file SHA-256, and retrieval date in README.md. Copy the upstream MIT license unchanged into LICENSE.

Use a temporary directory for download/extraction and verify the version from package/package.json before copying package/dist/vue.esm-browser.prod.js. The deployed application must never contact npm or a CDN.

- [ ] Compute and compare SHA-256 against the recorded value.

    Get-FileHash static/vendor/vue/vue.esm-browser.prod.js -Algorithm SHA256

- [ ] Re-run the contract test and Django static finder.

    .\.venv\Scripts\python.exe manage.py test tests.frontend.test_ui_design_contracts
    .\.venv\Scripts\python.exe manage.py findstatic vendor/vue/vue.esm-browser.prod.js --verbosity 0

- [ ] Commit the vendored dependency separately.

    git add static/vendor/vue/vue.esm-browser.prod.js static/vendor/vue/LICENSE static/vendor/vue/README.md tests/frontend/test_ui_design_contracts.py
    git commit -m "build: vendor Vue for operations overview"

## Task 2: Build a deterministic, failure-isolated snapshot presenter

**Files:**

- Create: index/dashboard/operations.py
- Create: tests/dashboard/test_operations_overview.py

- [ ] Write failing tests for build_operations_snapshot(now=...). Cover:

    snapshot['schema_version'] == 1
    snapshot['generated_at'] is an ISO-8601 string
    snapshot['summary'] contains people, computers, networks, servers, monitors, domain, tasks
    snapshot['tasks'] contains only display-safe status/count/time fields
    snapshot['alerts'] contains only display-safe severity/status/summary/time/channel-result fields
    snapshot['topology']['mode'] is 'logical' without discoveries and 'hybrid' with discoveries
    snapshot['topology']['interfaces'] contains only read-model allowlisted fields
    snapshot['topology']['physical_edges'][0]['kind'] == 'physical_discovered'

Create a partial-failure test by mocking one region builder to raise DatabaseError. The overall snapshot must still return other regions and include a stable error code such as summary_unavailable, never the raw exception text.

- [ ] Run the new test module and verify import/contract failures.

    .\.venv\Scripts\python.exe manage.py test tests.dashboard.test_operations_overview

- [ ] Implement small private region builders and one public composition function:

    def build_operations_snapshot(*, now=None):
        generated_at = now or timezone.now()
        return {
            'schema_version': 1,
            'generated_at': generated_at.isoformat(),
            'summary': _safe_region('summary', _build_summary),
            'tasks': _safe_region('tasks', _build_tasks),
            'alerts': _safe_region('alerts', _build_alerts),
            'topology': _safe_region('topology', _build_logical_topology),
        }

Reuse build_asset_card_summaries() and inspection_task_queryset()/summarize_tasks() where their semantics already match. Limit task and alert lists to the newest 10 rows. Use model display labels, not internal enum values, for visible text.

Topology nodes must have stable ids, kind, label, status, url and optional parent_id. Logical edges must have id, source, target and relationship='logical_membership'. Build the physical portion by calling current_topology_payload(include_stale=True, limit=1000) directly; do not call the HTTP API from the server. Keep its allowlisted interface/link fields intact under interfaces and physical_edges. Map known device ids to logical device nodes in the Vue adapter; unresolved remote endpoints remain external nodes labelled from remote_system_name, chassis id, management address or “未解析邻居”. Never request or embed evidence.

- [ ] Re-run the tests and verify all assertions pass.

    .\.venv\Scripts\python.exe manage.py test tests.dashboard.test_operations_overview

- [ ] Commit the presenter and tests.

    git add index/dashboard/operations.py tests/dashboard/test_operations_overview.py
    git commit -m "feat: build operations overview snapshot"

## Task 3: Add read-only routes, access rules, template bootstrap and navigation

**Files:**

- Modify: index/urls.py
- Modify: index/views/__init__.py
- Modify: index/common/access.py
- Modify: index/templates/common/base.html
- Create: index/templates/dashboard/operations_overview.html
- Test: tests/dashboard/test_operations_overview.py
- Test: tests/architecture/test_navigation.py
- Test: tests/system/test_reader_controls.py

- [ ] Add failing route/access/rendering tests for:

    reverse('operations_overview') == '/operations/overview/'
    reverse('operations_overview_data') == '/operations/overview/data/'
    reader GET receives 200 for both routes
    anonymous GET is redirected to login
    POST to the data endpoint is rejected
    response JSON has Cache-Control: no-cache/no-store behavior from middleware
    template contains operations-overview-app, operations-overview-bootstrap and data-snapshot-url
    导航中“综合展示”位于“告警记录”之前

- [ ] Run the tests and verify route failures.

    .\.venv\Scripts\python.exe manage.py test tests.dashboard.test_operations_overview tests.architecture.test_navigation tests.system.test_reader_controls

- [ ] Add two named GET-only views in index/dashboard/operations.py:

    @require_GET
    def operations_overview(request):
        snapshot = build_operations_snapshot()
        return render(request, 'dashboard/operations_overview.html', {'snapshot': snapshot})

    @require_GET
    def operations_overview_data(request):
        return JsonResponse(build_operations_snapshot())

Export them through index/views/__init__.py, register /operations/overview/ and /operations/overview/data/, and add both names to READER_VIEWS. Do not modify the existing /operations/topology/ endpoints.

- [ ] Build a semantic template skeleton extending common/base.html. Use Django json_script:

    {{ snapshot|json_script:'operations-overview-bootstrap' }}

Set the API URL as a data attribute produced by {% url 'operations_overview_data' %}. Include real headings, switch buttons, pause/manual refresh buttons, aria-live status, noscript fallback, and an empty Vue mount root. Do not use v-html.

- [ ] Insert “综合展示” immediately before “告警记录” in base.html.

- [ ] Re-run route/access/rendering tests.

    .\.venv\Scripts\python.exe manage.py test tests.dashboard.test_operations_overview tests.architecture.test_navigation tests.system.test_reader_controls

- [ ] Commit route and template scaffolding.

    git add index/dashboard/operations.py index/urls.py index/views/__init__.py index/common/access.py index/templates/common/base.html index/templates/dashboard/operations_overview.html tests/dashboard/test_operations_overview.py tests/architecture/test_navigation.py tests/system/test_reader_controls.py
    git commit -m "feat: add operations overview routes"

## Task 4: Implement and test the refresh state machine

**Files:**

- Create: static/app/js/operations-overview/state.js
- Create: tests/frontend/operations_overview_state.test.js

- [ ] Write Node tests using injected clock, document visibility and fetch dependencies. Cover initial state, successful refresh, stale-data failure, overlapping request suppression, manual pause, hidden pause, immediate visible refresh, timer cleanup and preserved view/filter/viewport fields.

The public factory contract is:

    createRefreshController({
      initialSnapshot,
      intervalMs: 30000,
      fetchSnapshot,
      now,
      schedule,
      cancel,
      isVisible,
    })

It returns getState(), subscribe(listener), start(), stop(), refresh(), setManualPaused(value), and handleVisibilityChange().

- [ ] Run the Node test and verify the missing module fails.

    node --test tests/frontend/operations_overview_state.test.js

- [ ] Implement state.js without DOM dependencies. On failure keep lastSnapshot, set stale=true and expose a short user-safe error message. Never start two requests at once. stop() must cancel the pending timer.

- [ ] Run the test and verify it passes.

    node --test tests/frontend/operations_overview_state.test.js

- [ ] Commit the state module.

    git add static/app/js/operations-overview/state.js tests/frontend/operations_overview_state.test.js
    git commit -m "feat: add overview refresh controller"

## Task 5: Implement and test logical topology normalization and layout

**Files:**

- Create: static/app/js/operations-overview/topology.js
- Create: tests/frontend/operations_overview_topology.test.js

- [ ] Write failing tests for normalizeTopology(snapshot) and layoutTopology(graph, viewport). Cover deterministic ordering, stable coordinates, invalid logical endpoints dropped, unresolved physical endpoints retained as stable external nodes, self-edges dropped, reciprocal physical observations deduplicated, logical and physical edges kept separate, protocol/evidence/status labels retained, and mobile viewport bounds.

- [ ] Run the test and verify the missing module fails.

    node --test tests/frontend/operations_overview_topology.test.js

- [ ] Implement a deterministic layered layout: root column, category column, asset grid and an external-neighbor rail. Clamp positions within the supplied SVG viewport. Return nodes, logicalEdges, physicalEdges, legend and bounds. Consume only physical_discovered links supplied by the backend; do not infer any physical link.

- [ ] Run the topology test and verify it passes.

    node --test tests/frontend/operations_overview_topology.test.js

- [ ] Commit the topology module.

    git add static/app/js/operations-overview/topology.js tests/frontend/operations_overview_topology.test.js
    git commit -m "feat: add logical topology renderer model"

## Task 6: Build the isolated Vue page and hacker-style visual system

**Files:**

- Create: static/app/js/operations-overview/app.js
- Create: static/app/js/operations-overview/effects.js
- Create: static/app/css/operations-overview.css
- Modify: index/templates/dashboard/operations_overview.html
- Test: tests/frontend/test_ui_design_contracts.py

- [ ] Add failing markup/style contract tests for local Vue import, module script, page-only stylesheet, two accessible tabs, refresh controls, SVG title/description, status text accompanying colors, reduced-motion rules, Canvas aria-hidden, and no v-html/innerHTML.

- [ ] Run the contract test and verify it fails.

    .\.venv\Scripts\python.exe manage.py test tests.frontend.test_ui_design_contracts

- [ ] Implement app.js with this isolated entry shape:

    import { createApp } from '../../../vendor/vue/vue.esm-browser.prod.js';
    import { createRefreshController } from './state.js';
    import { normalizeTopology, layoutTopology } from './topology.js';
    import { createAmbientEffects } from './effects.js';

Read initial data only from #operations-overview-bootstrap.textContent using JSON.parse. Read the endpoint URL from the mount element dataset. Use Vue delimiters ['[[', ']]'] so Django template syntax remains unambiguous.

- [ ] Render the 运维态势 view with summary cards, task progress rows, alert feed, health labels, last refresh time, pause/resume button, manual refresh button and stale/error banner. Each status uses icon/text plus color.

- [ ] Render the 网络拓扑 view as accessible SVG. Provide zoom in/out/reset controls, drag-to-pan, keyboard-operable node links and a legend that distinguishes “逻辑归属” from “LLDP/CDP 物理发现”. Link details show local/remote interfaces, protocol, direction, resolution, current/stale state, speed, VLAN, confidence and last-seen time. Preserve view and viewport through data refresh.

- [ ] Implement effects.js as a disposable Canvas background controller. Cap devicePixelRatio, limit particle count on small screens, pause on hidden documents, and fully remove listeners/animation frames in destroy(). Return a no-op controller when prefers-reduced-motion is true.

- [ ] Implement page-scoped CSS using existing design tokens: dark glass panels, subtle cyan/green accents, scan-line texture, restrained glow, 44px controls, readable contrast, no horizontal page overflow. At max-width 767.98px collapse cards to one column, convert the tab/control rail into wrapping rows, reduce particles, and keep SVG controls reachable.

- [ ] Re-run Django and Node frontend tests.

    .\.venv\Scripts\python.exe manage.py test tests.frontend.test_ui_design_contracts tests.dashboard.test_operations_overview
    node --test tests/frontend/*.test.js

- [ ] Commit the Vue UI.

    git add static/app/js/operations-overview/app.js static/app/js/operations-overview/effects.js static/app/css/operations-overview.css index/templates/dashboard/operations_overview.html tests/frontend/test_ui_design_contracts.py
    git commit -m "feat: build Vue operations command center"

## Task 7: Verify behavior, accessibility and isolation

- [ ] Run the focused Django suite.

    .\.venv\Scripts\python.exe manage.py test tests.dashboard tests.architecture.test_navigation tests.architecture.test_routes tests.system.test_reader_controls tests.frontend.test_ui_design_contracts

- [ ] Run every frontend unit test.

    node --test tests/frontend/*.test.js

- [ ] Run Django checks and static collection validation without writing into the project tree.

    .\.venv\Scripts\python.exe manage.py check
    .\.venv\Scripts\python.exe manage.py findstatic app/js/operations-overview/app.js vendor/vue/vue.esm-browser.prod.js --verbosity 0

- [ ] Start the demo server only for local visual verification. Do not trigger imports, inspections, domain syncs or external delivery tests.

    .\.venv\Scripts\python.exe -m deploy.demo

At 1440x900, 1024x768, 768x1024, 390x844 and 360x800 verify: both tabs, focus order, keyboard switching, pause/resume, hidden-tab refresh behavior, stale banner via mocked failed fetch, topology zoom/pan, reduced motion, no console errors and no horizontal page scroll.

- [ ] Confirm Vue is referenced only by operations_overview.html/app.js.

    rg -n "vendor/vue|createApp" index static/app/js

- [ ] Inspect git status and commit only any final test/documentation adjustments related to this page.

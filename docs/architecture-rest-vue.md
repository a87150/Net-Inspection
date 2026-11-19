# REST + Vue 架构

本文说明页面如何从"服务端渲染整页 + 表单 POST"迁到"REST 资源 + Vue 页面"，以及还剩哪些页没迁。

迁移是**逐页**进行的，不是整体重写：URL、外观、功能都不变，同一时刻新旧两种页可以共存。

## 三层

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 外壳 | `index/templates/` | 布局、导航、标题；只渲染容器和注水 JSON |
| 资源 | `index/api/` | DRF 视图集，JSON 读写；权限、分页、错误格式都在这一层定死 |
| 页面 | `static/app/vue/` | Vue 组件，读注水数据渲染首屏，之后所有读写走 REST |

Django 模板不再渲染表格行。行数据由视图算成 payload，同时喂给注水 JSON 和 REST 列表，两边同源。

## 一次页面请求

1. 浏览器 `GET /maintenance/` → 视图查一次库，把行 payload 塞进 `bootstrap`。
2. 模板渲染标题、`{% csrf_token %}`、`<div data-vue-page="maintenance-list" data-vue-bootstrap="maintenance-bootstrap">`，外加 `{{ bootstrap|json_script:"maintenance-bootstrap" }}`。
3. `boot.mjs` 扫 `[data-vue-page]`，按登记表动态 import 组件，用注水数据当 props 挂载。
4. 之后的筛选、翻页、删除都是 `fetch /api/v1/...`，页面不整页刷新，地址栏用 `history.replaceState` 同步。

首屏不再额外请求，所以没有空白闪烁；关掉 JavaScript 会看到明确提示，而不是空白页。

## 约定

- **路由**：`/api/v1/<资源>/`，注册在 `index/api/urls.py`，不进 `index.urls`（那里只放页面）。
- **权限**：DRF 默认 `IsReader`（登录可读），写操作按需 `IsAdminOrReadOnly`。`AccessMiddleware` 对 `/api/` 前缀放行，理由写在中间件里——否则 JSON 客户端会收到 HTML 登录跳转。
- **错误**：统一 `{"message": "...", "errors": {...}}`，`EXCEPTION_HANDLER` 在 `index/api/exceptions.py`。前端 `ApiError.message` 直接可展示。
- **分页**：`?page=&page_size=`，`page_size` 只能取 `PAGE_SIZES` 里的值，和服务端表格同一套。
- **CSRF**：写请求带 `X-CSRFToken`，前端先找页面上渲染的 `csrfmiddlewaretoken`，回退到 cookie。
- **模块后缀**：前端 ESM 一律 `.mjs`。项目没有 `package.json` 的 `type` 字段，`.js` 在 Node 里按 CommonJS 解析，`node --test` 就没法直接 import。Django 静态服务对 `.mjs` 返回 `text/javascript`，浏览器照常加载。

## 目录

页面按业务分 app，一个功能一个顶层包，共享层留在 `index`：

```
people/       人员（含接入渠道模板）
domain/       域控管理
pc/           PC 采集、日志、软件策略、上传配置
server/       服务器采集脚本
weakcurrent/  弱电设备（原 index/access）
network/      网络设备参数示例
inspections/  后台任务与巡检结果
alerts/       告警维护 + 维护窗口
topology/     网络拓扑
ipam/         IP 管理
assets/       四类资产共享的列表/详情/创建/配置备份（原 index/devices）
index/        外壳与共享层：common、api、dashboard、templatetags、公共视图
```

每个 app 自带 `templates/<前缀>/`，模板路径（`{% include 'alerts/event_row.html' %}`）不因搬迁而变。

```
index/api/`
  permissions.py    IsReader / IsAdminOrReadOnly
  pagination.py     与表格同规则的 page_size
  exceptions.py     统一错误信封
  serializers.py    写侧校验 + 标签字段
  viewsets.py       每个聚合一个视图集
  urls.py           /api/v1/ 注册表
static/app/vue/
  api.mjs           fetch 封装：CSRF、查询串、ApiError
  boot.mjs          页面登记表 + 挂载器
  pages/*.mjs       每页一个组件
```

页面登记表在 `boot.mjs` 的 `pages` 对象里。没登记的 `data-vue-page` 会打一条 warn 并跳过，不会白屏报错。

## 新增一页的步骤

1. 把视图里的查询抽成函数，产出 payload（参照 `alerts/payloads.py` 的 `alert_event_payloads`）。
2. 在 `index/api/` 加视图集，复用同一个 payload 函数。
3. 写 `static/app/vue/pages/<page>.mjs`，在 `boot.mjs` 登记；模板改成容器 + 注水。

## 迁移状态

已迁（3）：维护记录 `/maintenance/`（整页 Vue + REST 读写）、IP 管理 `/network/ip/`（台账仍服务端渲染，新增网段走本页弹窗 + `POST /api/v1/ip-subnets/`，不再跳后台 admin）、B1 全部 9 个列表页（见下）。

待迁，按形状分四批。

| 批次 | 页面 | 说明 |
| --- | --- | --- |
| ~~B1 列表页~~ 已迁 | 告警记录、巡检记录、错误记录、任务列表、域账号/域计算机/域分组列表、门禁记录、PC 日志列表、PC 错误列表 | 完成：`index/common/table_payload.py` 把 `TableDefinition` 的列/单元格/徽章/行动作压成 JSON，`static/app/vue/pages/resource_table.mjs` 一份组件渲染 8 页（告警页保留专属组件），翻页走 REST，筛选与排序仍是 URL 参数 |
| ~~B2 详情页~~ 已迁 | 资产详情、人员详情、任务详情、告警详情、记录详情、PC 日志详情、PC 分析详情、域对象详情 | 8 类详情页全部由 `index/common/detail_payload.py` + `static/app/vue/pages/resource_detail.mjs` 渲染。payload 是 `{title, eyebrow, description, actions, sections, disclosure, csrf}`；分区只有 `fields` / `list` / `table` / `groups` / `text` / `alert` / `form` 七种，够覆盖现有全部详情页。`actions` 支持 `link` 与 `form`（带 `confirm`），表格单元格支持 `{text, badge, url, label, note}`，所以分级徽章、结果链接、脱敏摘要都不用另写模板。`GET /api/v1/alert-events/<pk>/detail/` 与页面共用同一个构造函数 |
| ~~B3 写操作~~ 已迁 | 资产新增/编辑、巡检配置、问题等级、告警渠道/策略/模板、导入、批量执行、维护窗口、PC 采集配置、人员平台与同步计划、门禁来源 | 模态框表单带 `data-form-ajax` 时用 fetch 提交，服务端按 `X-Form-Ajax` 决定回 JSON 还是重定向：失败 400 + `errors[]` 留在框里，成功 200 + `message/redirect` 就地提示。表单本身没改，禁用 JS 时照旧 POST + 重定向 |
| ~~B4 拓扑~~ 已改 | 网络拓扑 | 拓扑本来就是 Vue（`operations-overview/*.js`），这轮只改布局：后端按「摘掉出口之间的链路后剩下的连通分量」切场地，每块场地内部再按出口→核心→汇聚→接入分层，前端一个场地一列并排展开，场地之间的链路按 VPN 隧道单独上色。效果见 [topology-preview.png](topology-preview.png) |
| B4 其余 | 首页仪表盘、人员统计、IP 管理台账、登录页 | 均为只读汇总或单表单页，服务端渲染首屏更快、无 JS 依赖。首页卡片数据已经是 `items` 列表结构，需要自动刷新时再挂 Vue 岛即可 |

B1 已完成：一个通用组件 + `RecordTableViewSet` 子类吃掉了 9 个页面。行内的「查看详情 / 结束任务 / 成员管理 / BitLocker」由 payload 的 `actions` 描述（`link` / `form` / `modal` 三种），所以表单 POST 与模态框脚本照旧工作；域列表的分批选择列复用原 `data-domain-target-checkbox` 属性，`operation_modal.js` 无需改动。

写操作先落 REST、页面继续服务端渲染是可行的中间态：IP 管理页就是这么做的，弹窗提交成功后整页刷新，台账仍由 `plan_report()` 渲染，没有第二套数据拼装。

### 写操作的两条通道

`index/common/form_json.py` 只有一个判断：请求带 `X-Form-Ajax: 1` 就回 JSON，否则回重定向。视图里只有失败分支要显式调用，成功分支用 `form_saved()` 给出消息和跳转目标。

前端 `static/app/js/common/form_ajax.js` 挂在 `form[data-form-ajax]` 上：响应是 JSON 就按 `ok` 决定渲染错误还是跳转；不是 JSON 说明服务端已经重定向到带 flash 消息的页面，直接跟随。同一个表单在禁用 JS 时行为完全不变。

提交后本来就要离开当前页的操作（创建任务、域控操作）没有挂 `data-form-ajax`——就地提示对它们没有意义。

## 已知取舍

- 迁移期间同一个功能可能同时存在表单 POST 与 REST 两条写路径（例如维护窗口建删）。两条路径共用 `create_windows` 这类 service 函数，不会各写一份校验；全部迁完后删掉表单路径。
- 注水 JSON 让首屏和数据来源是两份表达（模板注水 / REST 列表），但都由同一个 payload 函数产生，字段不会漂移。
- 暂不做前端路由（`vue-router`）。页面之间的跳转仍是整页导航；只有页内交互走 REST。理由：95 条页面路由里多数依赖服务端权限与上下文，改成前端路由会把权限判断搬到客户端，得不偿失。

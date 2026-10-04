# 更新记录

[返回项目首页](../README.md)

本文件只记录**已经生效的变更**和**仍待处理的问题**。
系统当前是什么样，请看导航里的架构、设备巡检、人员与域控、数据库文档，本文件不重复描述。

## 当前待处理

| 事项 | 说明 |
| --- | --- |
| 门禁平台兼容性 | V6600 需部署实例核实端点与认证，当前按 V6000 2.11 兼容格式处理；海康/大华仅为占位，不承诺可用 |
| SSH 主机身份校验 | 尚未做 known_hosts 校验，见 [安全待办](security-followups.txt) |
| 部分凭据存储 | 仍有凭据依赖系统凭据库，跨主机迁移需重新配置 |
| PC 采集器 HTTPS 与双向认证 | 当前仅 Bearer Token，无客户端证书 |
| `index/access/adapters.py` 方向与结果字段 | 目前固定回填 `unknown`，已知问题 |
| H4 无障碍项 | 部分弹窗与表格控件缺少完整键盘与读屏语义 |
| `my_command.py`、`table_filter.html`、`style.css` | 死代码，待清理 |
| `sync_domain` / `policy_snapshot` | 仅被测试引用的函数 |
| 拓扑批量删除 | 关联 PROTECT 保护缺失（D2），删除前需确认级联范围 |
| PostgreSQL 排序规则 | 库必须按 `LC_COLLATE 'C'` 建，locale 排序会让中文名先后与 MariaDB 相反 |
| PC 采集宿主自测 | 3 个用例依赖预编译 `PCCollectorHost.exe`，它硬编码调用 Windows PowerShell 5.1；执行策略为 Restricted/AllSigned 的机器上会被拒 |

## 1.0.0（2026-10-04）

### MariaDB / PostgreSQL 双数据库

`DB_ENGINE` 取 `mysql` 或 `postgresql`，正式环境和模拟环境共用同一份 `.env`，改一个值即可整体换库。
两种后端跑同一套迁移、同一套测试。`psycopg[binary]==3.3.6` 进入 `requirements.txt` 与锁文件。

为了让同一份代码在两个后端上行为一致，修掉了这些后端差异：

- `JsonKeyType` 补齐 PostgreSQL（`JSONB_TYPEOF(JSONB_PATH_QUERY_FIRST)`）和 SQLite 分支。
  用 `_FIRST` 是因为 `JSONB_PATH_QUERY` 是集合返回函数，放进 `CASE` 会报类型不匹配。
- 去掉三处 `select_for_update()` + 可空外键 `select_related()` 的组合（`DomainOperation.task`、
  `Schedule.people_source`、`Schedule` 的四个档案字段）。PostgreSQL 拒绝在外连接可空侧加锁。
- 设备配置备份的文件名在落库前剔除 NUL：MariaDB 收得下，PostgreSQL 报 `DataError`，Windows 本身也不允许。
- 告警策略的 upsert 按后端能力决定是否传 `unique_fields`。

### 域控同步纳入告警

域控同步失败此前不产生任何告警：告警白名单只认巡检和日志分析两种任务类型，而域控同步按 DC 配置分作用域，
既没有档案也没有可映射的外键。现在 `result_type='domain_sync'` 会生成 `execution.failure` critical 告警，
并在同步结果落库后立即评估，不用等 worker 下一轮。

### 人员自动同步纳入告警

和域控同步同样的漏网：`ALERTED_TASK_TYPES` 里没有 `people_sync`，人员自动同步失败不产生任何告警。
现在按同步源分作用域（`people_source`），同步结果落库后立即评估，不用等 worker 下一轮。

### 界面

域控连接设置页的「启用定时同步」改用与右侧间隔输入框一致的开关卡片样式。

## 2026-10-03

### 巡检项目按设备动态备注采集方式

巡检项目标签会显示该项目在**当前所选设备**上实际使用的采集方式，例如 `实时接口流量（SNMP）`、
`CPU（SNMP/SSH）`。备注随勾选设备变化，一台没选时清空。

口径跟着采集器走：网络用 `_network_item_plan`，服务器按 `server_type`，
弱电取 `_collect_auto` 的 SNMP → HTTP API → Ping 回退链首选，避免界面与实际路径不符。

### 设备类型标签统一中文

台账里存的是代码（`switch`/`camera`）或历史自由文本，弱电设备在列表、筛选、巡检配置、导入四处
显示不一致。现统一由 `device_type_label` 翻译，来源为 `SUBTYPES`。

### 折叠区块嵌在卡片里的白块

`foundation.css` 的裸 `details { background: #fafbfc }` 会在「SNMP 高级参数」这类玻璃卡片内部
刷出不透明底色。加 `.config-section-card > details { background: transparent }` 精准覆盖，不影响
其余 14 处独立 `details`。

### 巡检项目按所选设备动态显示

一台设备都没选时不再回退显示全部项目的并集，直接不显示任何巡检项目。

### 巡检配置「测试回显解析」不再刷新页面

改为 AJAX 提交并就地回显/报错，两处入口（模板配置、单设备覆盖）都已处理。

### 服务器巡检配置工具栏与代码整理

- 服务器不渲染厂商筛选，五列工具栏会拉伸「全选」按钮，补 `.target-device-picker__toolbar--no-vendor`。
- 删除 `index/devices/network/`、`index/devices/security/` 两个空壳包（零引用）。
- 弱电设备默认模板与网络设备对齐，并统一列表、分析、巡检页面的操作按钮顺序。

### 文档整理

把 15 份现役文档合并为 7 份（架构 / 设备巡检 / PC 采集 / 人员与域控 / 数据库 / 部署 / 本文件），
删除 45 份历史实施计划与设计规格、30 份 SDD 任务记录、5 份评审报告。

## 2026-10-01

### PostgreSQL 支持

`DB_ENGINE` 增加取值校验，拼错直接启动失败。**这是刻意的**：早前拼错会静默连上根目录 SQLite 空库，
看起来像数据全没了。阶段 1 迁移评估见上表。

### 后台批量删除与设备停用

管理后台支持批量删除设备并带连带清理；前端改为停用而非删除，避免误删台账。

### 弱电设备扩充

「安防设备」更名为「弱电设备」，新增打印机与温湿度计类型，默认模板对齐网络设备。

## 2026-09

### 一键部署与双服务

Windows 计划任务 / Linux systemd 各起 Web 与 Worker 两个服务，支持重启。
完整步骤见 [一键部署](../deploy/README.md)。

### MariaDB / Redis

从 SQLite 切到 MariaDB，Redis 仅用于页面缓存（默认关闭），不承担任务队列。
回退边界与迁移校验见 [数据库与维护](database.md)。

### 网络设备 SNMP/SSH 混合采集

默认 auto（SNMP 优先、缺项 SSH 补充），项目可显式指定协议。深信服 AC 走独立 Open API。
详见 [设备巡检](device-inspection.md)。

### 企业网络拓扑与运维总览

自顶向下的骨干拓扑，物理链路只来自真实 LLDP/CDP 采集结果，端点按证据再按网段挂接；
演示数据为推断链路，生产不生成模拟设备。

### PC 日志采集与远程分析

Windows/macOS 采集器每两小时本地覆盖 `latest.json` 后直传 Web API，分析从数据库冻结最新日志。
详见 [PC 采集与分析](pc-collection.md)。

### 表格工作区与前端整合

统一筛选、排序、分页、导出与列偏好；Vue 仅用于运维总览页，其余为服务端模板 + 原生 JS。


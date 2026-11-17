# 1.2 实施计划：五项能力 + Vue 岛屿

基线 1.1.0。本文是实现前的施工图，不含代码。所有落点均已对照现有模型与函数核实。

## 0. 总览

| # | 项目 | 新表 | 迁移 | 新页面 | 预估 |
| --- | --- | --- | --- | --- | --- |
| 1 | 全局采集并发预算 | 否 | 无 | 无 | 2–3 人日 |
| 2 | 配置版本对比 + 漂移告警 | 否 | 无 | 无（详情页内） | 5–7 人日 |
| 3 | 告警维护窗口 | 是 | 0067 | 告警设置内 | 4–6 人日 |
| 4 | IP 定位查询 | 视规模 | 0 或 0068 | 是 | 5–8 人日 |
| 5 | 轻量 IP 台账 | 是 | 0068/0069 | 是 | 8–12 人日 |
| 6 | Vue 岛屿（四页） | 否 | 无 | 否（改造） | 8–14 人日 |

合计 **32–50 人日**。建议按 1 → 2 → 3 → 4 → 6 → 5 的顺序交付，前四项与 Vue 各自可独立上线，第 5 项放最后（唯一需要产品决策的一项）。

## 1. 全局采集并发预算

**现状**：`net/inspections/worker.py:92` 的 `_max_workers()` 只看两处——任务快照里的 `concurrent_workers` 与 worker 自身的 `self.threads`，取较小值。没有跨任务的资源预算。两个巡检配置同时跑，或单个配置设 32，就会把交换机 VTY 打满；现场表现是大面积「登录失败」，不是「并发过高」，排查方向会被误导。

**设计**：新增 `net/inspections/budget.py`，按资源类别各持一个进程内 `threading.BoundedSemaphore`：

```
ssh   默认 20      telnet 并入 ssh（同一份 VTY 配额）
snmp  默认 30      http 默认 20
```

限流位置选在**任务线程级**（`_run_target` 进入前 acquire、finally release），不在连接函数里——否则一次巡检里多次建连会重复计数。acquire 带超时，超时按现有 `lease_guard` 语义放弃该目标并记录原因，不阻塞队列。

配置走 `net/settings.py` 三个环境变量（`NET_CLI_CONCURRENCY`、`NET_SNMP_CONCURRENCY`、`NET_HTTP_CONCURRENCY`），默认值与对方平台的 20/30 同量级。

**天花板**：进程内信号量在多 worker 进程部署时只是单进程上限，实际总量随 worker 数放大。这条按项目惯例留 `ponytail:` 注释写明，升级路径是换 Redis 计数器，与对方「CMDB 采集专用容量池」同思路。

**测试**：三例——并发数不超过预算、超时后释放不泄漏、`concurrent_workers` 与全局预算取小。

## 2. 配置版本对比与漂移告警

**现状**：`net/models/configuration_backups.py` 的 `DeviceConfigurationBackup` 有 `ciphertext`（`BinaryField`，加密）、`sha256`、`captured_at`、唯一约束「每设备每天一版」，保留最近 10 个成功日版本。全项目 grep `diff` 无实现——只能下载两个文件到本地比。

**设计**：

`net/devices/configuration/diff.py`：解密两版 → 按行 `difflib.SequenceMatcher`（不用 `unified_diff`，需要行号区间做前端定位）→ 输出 `[{kind: 'equal'|'add'|'remove', left_no, right_no, text}]`。纯标准库，零依赖。

脱敏在**输出层**做，不在存储层：匹配 `(password|secret|community|key|pre-shared|snmp-server community)` 的行整行替换为 `<已隐藏>`，保留行号。已有 `net/infrastructure/` 的脱敏工具可复用。

界面：配置备份详情页加「与上一版对比」，可选任意两版；差异默认折叠相同行，只展开变更块。

漂移告警：日备份落库后比较与前一版的变更行数，超过阈值（默认 20 行，可配）则产生 `AlertEvent`。**新增 `event_type='drift'`**——注意 `net/models/alerts.py:482` 有 `net_alert_event_type_ck` 检查约束，扩枚举要同步改约束。

**风险**：解密要经 `DEVICE_BACKUP_ENCRYPTION_KEY`，密钥不对时报错要和「文件损坏」区分；配置文件可能几十 KB 到几百 KB，单次对比在毫秒级，不需要异步。

## 3. 告警维护窗口抑制

**现状**：`process_target_findings()` 生成事件，`deliver_event()` / `deliver_due_alerts()` 负责投递外发。有 `AlertState` 状态机与 `AlertPolicy` 继承，无维护窗口概念。

**设计**：新增 `MaintenanceWindow`：

```
scope_type('global','device_group','profile','device') + scope_id
starts_at / ends_at          单次窗口
repeat('none','daily','weekly','monthly') + weekdays / month_day + time
note                         变更单号或说明
is_enabled
```

判断点在 `deliver_event()` 内、真正发送前。命中窗口则**照常生成事件与投递记录，但状态标 `suppressed`**，不外发。这样事后仍能查「当时确实告警了、被维护窗口压掉」，与直接丢弃是两回事。

时间计算全部在 Python 侧做，用 `settings.TIME_ZONE`，窗口按半开区间 `[start, end)`。**不能用数据库 `Trunc*` 或时区转换**——`net.W001` 未解决，MariaDB 时区表为空时这类表达式返回 `NULL`。

**测试**：跨午夜、跨月（31 日不存在）、周重复、单次窗口过期、事件被抑制但仍可查。对方 v1.0.10 专门修过跨午夜/跨月，这四个用例必须有。

## 4. IP 定位查询

**现状**：`arp_table`、`mac_table`、`lldp_neighbors`、`routing_table` 已是交换机/路由器/AC/AP 的默认巡检项（`net/devices/network/default_profiles.py:10-14`），四厂商命令齐全。解析产物在 `ssh_collectors.py:367` 落成 `{'records': [...], 'count': N}`，经 `DynamicRecord.details`（JSONField）保存。**证据每天都在采，只缺查询入口。**

**设计**：新服务 `net/devices/network/observations.py`，提供三个查询：

- 按 IP → 哪台设备的哪个接口、哪次巡检、什么时候见到
- 按 MAC → 同上，外加所属 VLAN
- 按接口 → 该口下挂过哪些 IP/MAC

两种实现路径，取决于设备规模：

A. **扫 JSON**（无迁移）：取每台设备**最近一次成功巡检**的 `details`，在 Python 里建索引。设备数 500 以内、结果页面分页 50 条时响应在百毫秒级。

B. **物化索引表**（迁移 0068）：新增 `NetworkObservation`（device / item / ip / mac / interface / vlan / observed_at / target_run），巡检落库时同事务写入。查询走索引，但每天每设备新增几十到几百行，需要保留策略。

先 A，超过阈值再换 B。这也是为什么第 5 项排最后——如果真要做 IP 台账，B 顺带就有了。

**界面纪律**：ARP/MAC 是被动观察，不是在线证明。页面必须显示采集时间与来源任务，不给「在线/离线」结论。对方在 v1.0.11 里专门强调过这一点。

## 5. 轻量 IP 台账

**模型**：

```
IPSubnet      cidr / name / site / vlan / gateway / note
IPAssignment  subnet(FK) / ip / mac / hostname / status('assigned','reserved','free','conflict') / note
```

**不做的部分**：地址池、DHCP 租约、VIP、六类对账。这些需要「完整采集代次」概念（哪次巡检覆盖了哪个网段的全量）与观察历史累积，代码量约为本方案的 4 倍，收益集中在最后 10%。只做两类提示：ARP 里出现但台账没登记（未登记）、台账有但 MAC 不一致（冲突）。

与设备台账**不建外键**，按 IP 字符串弱关联——设备管理 IP 会变，强关联会产生孤儿行；这与现有 `DeviceConfigurationBackup.device_id` 用裸 UUID 的做法一致。

**迁移**：0068（若不建观察索引表）或 0069。

## 6. Vue 岛屿

**不引入任何新基础设施**，复制拓扑页已验证的模式：

```
static/vendor/vue/vue.esm-browser.prod.js   已有，v3.5.43，禁止 CDN
模板：{{ snapshot|json_script:'xxx-bootstrap' }} + 挂载点带 data-*-url
JS：  createApp({ delimiters: ['[[', ']]'] }) 避免与 Django {{ }} 冲突
入口：{% block page_scripts %}<script type="module" src="...">{% endblock %}
```

**新增一层薄共享**：`static/app/js/vue/mount.js`，提供 `mountIsland({ selector, bootstrapSelector, dataUrl, setup })`，统一处理「找不到元素就静默退出」「JSON 解析失败降级」「CSRF 头」。四页共用，避免各写一份。

轮询直接复用 `operations-overview/state.js` 的 `createRefreshController`（可见性暂停、失败保留上次结果、手动暂停）——它已经是可测试的纯函数。

**接管顺序**（按交互密度）：

1. 巡检配置弹窗 `inspections/profile_modal.html`（234 行，条件字段最多）
2. 设备采集设置 `devices/collection_settings_body.html`（217 行）
3. 告警策略弹窗 `alerts/policy_modal.html`（86 行 + 联动）
4. 域控组成员 `domain/group_members.html`（199 行）

**纪律**：每页保留 `<noscript>` 提示与表单的非 JS 提交路径（渐进增强），出问题删一行 module 引用即可回退。**不引入 SFC、Vite、npm 构建**——`package.json` 目前只用于 `node --test`，不能被 `"type": "module"` 之类改动破坏（前端测试是 CommonJS）。

## 7. 顺序与验收

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| 1 | 并发预算 | 并发数受控；`1687` 现有用例不回归 |
| 2 | 配置对比 | 两版对比正确；脱敏行打码；漂移事件可查 |
| 3 | 维护窗口 | 四个时间边界用例通过；抑制事件仍可查 |
| 4 | IP 定位 | 按 IP/MAC/接口三向可查，含时间与来源 |
| 5 | Vue 四页 | 每页行为与旧模板一致；关 JS 仍能提交 |
| 6 | IP 台账 | 未登记/冲突两类提示准确 |

每阶段独立提交、独立可发布。建议 1–4 合成 1.2.0，Vue 与 IP 台账合成 1.3.0——理由：前四项改数据与告警语义，后两项改界面与功能边界，混在一个版本里回归面太大。

## 8. 要你定的七件事

1. **设备规模**：现场网络设备是几十台、几百台还是上千台？这直接决定 IP 定位走「扫 JSON」还是「建索引表」。
2. **IP 台账边界**：只要「网段 + IP 登记 + 两类冲突提示」，还是需要地址池/DHCP 租约/VIP？
3. **配置对比的原文**：差异默认打码敏感行。要不要额外提供「查看原文」的二次确认入口？提供则审计压力上升，不提供则排障时要下载原文件。
4. **维护窗口粒度**：按全局、按设备分组、按巡检配置，还是按单台设备？支持得越多，页面越复杂。
5. **并发默认值**：现场交换机的 VTY 通常 5–16 条，SSH 全局 20 是否偏大？还是按「同时最多 5 台」更贴合实际？
6. **Vue 首批页面**：我列的四页是否都算痛点？还是先只做巡检配置弹窗这一页试水？
7. **版本拆分**：接受 1.2.0（后端四项）+ 1.3.0（Vue + IP 台账）两版，还是一次发 1.2.0？

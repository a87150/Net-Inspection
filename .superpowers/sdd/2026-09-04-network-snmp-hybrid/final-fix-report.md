# SNMP + SSH 混合巡检最终集中修复报告

## 基线与范围

- 工作树：`C:\Users\a8715\Desktop\code\py\net\.worktrees\network-snmp-hybrid`
- 起始 HEAD：`3c49001d365752489dd787e02833c3e754effd93`
- 仅阅读指定设计 `docs/superpowers/specs/2026-09-04-network-snmp-hybrid-design.md`、相关代码/测试及原 Task5 简报和报告；未读取其他实现计划。
- 未调用子代理，未连接真实 SNMP/SSH 设备。
- 修改范围：collector、SNMP 会话和解析器、网络设备模型、`0022` 迁移、对应测试及本报告；未改行为不变的文档。

## Findings 映射

1. collector 完成证据：新增 `_item_completed()`，带 `status` 的对象只有 `status=success` 才进入完成集合；失败/unsupported 对象仍保留在 `data`。纯 SSH 失败配置现在为 `failed`，hybrid 指标成功但配置失败为 `partial`。auto fallback 同样按成功证据判断。
2. PySnmpSession fail closed：在构造任何凭据前校验版本、安全级别、认证/隐私协议和对应必需字段；未知值统一抛出不含输入值的 `SnmpQueryError('invalid_configuration')`，不再把 `None` 交给 PySNMP 触发 MD5/DES/noAuth 默认。
3. ENTITY-SENSOR：scale 限制为整数 1–17，precision 为整数 -8–9，value 为有限 32 位整数，换算结果必须有限且处于 -273.15–1000°C；坏温度仅使 temperature 缺失，CPU 等已完成数据保持。
4. 异常分类/reachable：`ImportError`/`ModuleNotFoundError` 固定为 `dependency`；Timeout 固定为 `timeout`；socket/DNS/路由/OSError 固定为 `unreachable` 且 `reachable=False`；认证和普通设备响应错误保持 `reachable=True`。所有公开消息固定，不拼接异常原文或秘密。
5. HOST-RESOURCES 内存：遍历并聚合全部有效 physical-memory 行，只接纳有限正 allocation unit、非负 size/used 且 `used<=size` 的行；最终再次拒绝 `used>total`。
6. device_info：增加数字 OID `1.3.6.1.2.1.1.2.0`（sysObjectID）的 GET、raw 保留与 `object_id` 解析。
7. snmp_retries：模型与 `0022_network_device_snmp` 的最大值统一为 5，默认仍为 1；测试覆盖 0、5 合法和 6 非法，并直接核对迁移 operation/default/validators。
8. raw 合并：每个证据键都通过稳定分配器写入；首选键冲突时使用 `#2`、`#3` 递增后缀，即使输入已带 `snmp:`/`ssh:` 前缀也不丢证据。
9. Task1 收口：authPriv 模型测试直接断言缺少认证协议、认证密码、隐私协议、隐私密码四个错误键；`0022` 测试锁定 AddField operation、默认值和上下界 validator。
10. 队列/模型约束：队列测试直接断言公开 SNMP 字段被冻结且四类 SSH/SNMP 秘密不进入快照；队列汇总和数据库/`full_clean()` 测试固定父任务 `partial` 可合法为 0 success + 1 failed。现有生产约束已满足，无需修改约束迁移。
11. seed repeatability：网络资产快照纳入全部新增 SNMP 字段，包括演示秘密字段，以锁定 reset 后的完整重复性。
12. inventory CSV：普通网络设备表格导出直接逐项断言 `SSH密码`、`SNMP Community`、`认证密码`、`加密密码` 四个表头不存在，且对应四个秘密值不在任一导出行或原始响应中。

## TDD / RED 证据

测试先于生产代码写入并执行：

```powershell
C:\Users\a8715\Desktop\code\py\net\.venv\Scripts\python.exe manage.py test tests.devices.network.test_hybrid_collector tests.devices.network.test_snmp tests.devices.network.test_snmp_model tests.inspections.test_queue.LeaseQueueTests.test_finish_keeps_one_partial_target_as_parent_partial_with_zero_successes tests.inspections.test_models.TaskModelDatabaseTests.test_database_and_model_accept_parent_partial_with_no_successful_targets tests.inspections.test_queue.EnqueueTaskTests.test_enqueue_snapshots_public_snmp_settings_and_excludes_all_secrets tests.architecture.test_demo_seed.DeterministicDemoSeedTests.test_demo_seed_reset_is_repeatable_and_offline tests.common.test_table_exports.FilteredExportContractTests.test_network_lists_and_exports_expose_only_public_connection_settings --verbosity 1
```

修正测试收集方式后，原始摘要为：`Ran 55 tests`，`FAILED (failures=21)`。失败明确覆盖 collector 错判 success、内存只取首行/接受 used>total、温度巨大/非有限值导致错误状态或整体失败、sysObjectID 缺失、unreachable/dependency/config 分类缺失、未知配置未 fail closed、重试上限仍为 10。

raw 前缀碰撞另作最小复现：`Ran 1 test`，`FAILED (failures=1)`；实际只保留 2/4 条证据。

首次尝试 worktree 相对路径 `\.venv\Scripts\python.exe` 因该 worktree 无 `.venv` 未启动测试；随后全程使用主检出已有虚拟环境的绝对解释器，不创建联接。一次扩大到整个 `tests.inspections.test_models` 的 RED 运行还暴露其既有 `ScheduleContractTests` 对 `people_source` 键的旧断言，与本次范围无关；最终针对本次新增 `TaskModelDatabaseTests` 用例验证，并由原 Task5 命令覆盖批准的集成集合。

## GREEN 与最终验证

### 定向 GREEN

- 网络 collector/SNMP/模型：`Ran 50 tests in 1.705s`，`OK`。
- 所有新增定向用例（含队列、模型约束、seed、导出）：`Ran 55 tests in 2.188s`，`OK`。

### 所有受影响聚焦测试

```powershell
C:\Users\a8715\Desktop\code\py\net\.venv\Scripts\python.exe manage.py test tests.devices.network tests.inspections.test_queue tests.inspections.test_models.TaskModelDatabaseTests tests.architecture.test_demo_seed tests.common.test_table_exports --verbosity 1
```

原始摘要：`Ran 172 tests in 7.468s`，`OK`，退出码 0。

### 原 Task5 最终聚焦命令

使用相同测试目标，仅因 worktree 无 `.venv` 而采用绝对解释器：

```powershell
C:\Users\a8715\Desktop\code\py\net\.venv\Scripts\python.exe manage.py test tests.devices.network tests.devices.test_collection_evidence tests.inspections.test_worker tests.inspections.test_worker_review tests.devices.pc.test_inventory tests.common.test_table_exports tests.architecture.test_device_boundaries tests.architecture.test_admin_registry tests.architecture.test_demo_seed tests.system.test_dependencies --verbosity 1
```

原始摘要：`Ran 188 tests in 13.163s`，`OK`，退出码 0；`System check identified no issues (0 silenced)`。

### 静态/迁移检查

- `...python.exe manage.py check`：退出码 0；`System check identified no issues (0 silenced)`。
- `...python.exe manage.py makemigrations --check --dry-run`：退出码 0；`No changes detected`。
- `git diff --check`：退出码 0；无 whitespace error，仅有现有 Windows LF→CRLF 提示。

## 自审

- 逐项对照 12 个 findings 与设计：每项均有直接行为测试；没有用源码文本匹配代替运行行为。
- collector 仍保留失败配置对象供 UI 展示；成功证据可以替换同项目先到的失败占位，成功项目之间保持原有先到优先。
- 所有 SNMP 对外错误只取固定类别消息；异常原文、community、认证密码、隐私密码均未写入结果。
- PySNMP session 在配置或构造失败时关闭 dispatcher；GET/WALK 失败仍由 finally 关闭。
- 温度条目逐条丢弃，解析器不会因一个非有限/巨大值抛出并清空其他项目。
- 未新增迁移；模型状态与修改后的 `0022` 一致，`makemigrations --check` 已证明无漂移。
- git diff 范围未包含 README、部署文档、其他计划、并发 UI 文件或任何真实凭据。

## 顾虑

- 自动化全部使用内存会话和 mock，未验证真实厂商对 sysObjectID、ENTITY-SENSOR 或多 RAM 行的具体实现差异；生产前仍应按既有部署文档在隔离管理网做只读 smoke test。
- ENTITY-SENSOR 的物理合理温度范围固定为 -273.15–1000°C；若未来明确支持超过 1000°C 的工业传感器，需要带设备契约和测试调整此边界。
- 测试环境继续提示 worktree 缺少 `staticfiles/` 目录以及 Git LF→CRLF 预告；均未影响退出码或结果。

# Final Branch Independent Re-review

日期：2026-09-01

项目：`C:\Users\a8715\Desktop\code\py\net\.worktrees\asset-dashboard-domain-ops\py\net`

复审 HEAD：`cc169ab02094b46643fc897a705a2596e18417b5`

复审范围：原报告 `.superpowers/final-branch-review.md` 的 3 个 Important、1 个 Minor，以及修复提交 `77cfc29`、`0cd2959`、后续边界提交 `6d8f34e` 和最终报告提交 `cc169ab`。

## 结论

**批准进入最终验证。**

本次独立复审未发现新的 Critical、Important 或 Minor 级逻辑、权限、隐私、并发或数据一致性问题。原报告的 3 个 Important 和 1 个 Minor 均已关闭。

## 原发现复核

### 1. create_user 分阶段恢复：已关闭

- LDAP `add` 只有明确成功后才进入密码初始化；密码初始化失败会持久化非敏感阶段 `user_created_password_pending`，不保存密码。
- 重试必须提交新密码，且仅从失败目标受栅栏持久化的 `result_snapshot` 复制可信恢复阶段；阶段动作、失败状态和 DN 必须与原目标快照一致。
- Worker 恢复时使用 BASE 查询精确目标 DN，并核对 `sAMAccountName`（大小写不敏感）与 `displayName`；不匹配、查询失败或条目数量异常均转为 `manual_intervention_required`，不会执行密码修改。
- `add` 抛出 ldap3 或包装层异常、返回缺失/矛盾结果、或返回连接类结果码时，均标记为需要人工核查，页面和服务层同时禁止自动重试。
- `add=False/result=68` 的明确“对象已存在”边界只返回普通失败，不查询、不接管、不重置该对象；再次提交只会重新执行安全的 add，不会跳过 add 进入密码修改。`add=False/result=0` 则按矛盾结果进入人工核查。
- 连接建立失败发生在 add 之前，保持普通可重试失败是安全的；不存在“操作可能已执行但系统盲目重试”的窗口。

### 2. Worker 栅栏内本地镜像更新：已关闭

- LDAP 成功结果在 `TaskTargetRun` 与 `TaskRun` 行锁、有效 worker lease、lease guard、目标运行状态及 claim generation 全部通过后才持久化。
- `move_ou` 对账号和计算机写回新的 `distinguished_name` 与 `ou`；`enable` / `disable` 写回 `is_active`。
- 写回前锁定本地 `Domain_Account` / `Domain_Computer`，并比较不可变目标快照 DN；本地对象已漂移时不覆盖同步结果。
- 本地对象缺失、已漂移、结果缺少新 DN/OU 或本地保存失败时，目标仍保持 LDAP 成功，只记录非敏感 `mirror_status` 等待后续同步。
- `create_user` 使用虚拟目标 ID，代码明确跳过本地镜像查询。

### 3. 停用 PC 分析配置：已关闭

- 下载接口对已停用配置返回 HTTP 400，并给出“先启用分析配置”的明确提示。
- PC 资产列表和分析配置弹窗都要求配置存在、已启用且扫描目录已配置后才渲染 Windows/macOS 下载链接。
- 全局检索未发现第三处绕过上述条件的 PC 脚本下载入口；上传接口继续只接受启用配置，下载与上传约束一致。

### 4. destination_ou/group_dn 确认摘要：已关闭

- `destination_dn` 与 `group_dn` 均绑定 `input` 和 `change` 事件。
- 移动 OU、加入组、移出组会在提交前实时更新可见的范围标签和 DN 内容；动作和目标勾选变化也继续刷新摘要。

## 权限、隐私与并发检查

- 域操作创建和重试入口继续受 `net.manage_domain_operations` 权限保护；人工核查状态同时在 UI 隐藏重试和服务层拒绝重试。
- 一次性密码未进入 `parameters_snapshot`、`target_snapshot`、`result_snapshot` 或错误信息；持久化结果仅允许动作、阶段、DN/OU 和镜像状态等非密码字段。
- 镜像写回未扩大 LDAP 操作权限，也未绕过现有任务范围键和目标 DN 并发约束。
- 未发现由本次修复引入的跨目标覆盖、陈旧 Worker 覆盖、密码泄露或无关对象接管路径。

## 验证证据

- Django 聚焦测试：`82/82` 通过：
  - `index.test_domain_actions`
  - `index.test_domain_worker`
  - `index.test_domain_permissions_ui`
  - `index.test_pc_script_downloads`
- JavaScript 聚焦测试：`static/js/domain_operation_modal.test.js`，`2/2` 通过。
- 最新 HEAD 上针对 `6d8f34e` 边界追加运行 4 个精确用例：普通 object-exists、`add=False` 缺失结果、`add=False/result=0` 矛盾结果、包装层异常，`4/4` 通过。
- `git diff --check`：通过，仅出现既有 LF/CRLF 提示。
- 测试全部使用 Django 自动创建/销毁的测试数据库和 mock/fake LDAP；未连接真实 LDAP。
- 原数据库复审前后 SHA256 均为 `0625A3629821483ED0C2B9FBDA06C301B59C4D1D73A99445C2802F34AAE3B1E0`，未发生变化。
- 未修改任何项目代码。保留既有未跟踪文件 `.superpowers/final-branch-review.md` 与 `py/__pycache__/__init__.cpython-312.pyc`。

## 最终验证前的环境风险

以下不是本次确认的代码缺陷，不阻止进入最终验证：

- 仍需在隔离测试 AD/OU 上验证真实 LDAPS 证书链、权限、超时和对象生命周期竞争场景。
- 仍需在真实 Windows PowerShell 5.1 与 macOS 主机验证采集脚本、路径、Unicode、TLS 上传及失败留存。
- 仍需在目标生产数据库引擎上演练迁移和回滚预案。

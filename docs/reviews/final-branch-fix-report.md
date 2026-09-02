# Final Branch Review Fix Report

日期：2026-09-01

分支：`codex/asset-dashboard-domain-ops`
审查依据：`.superpowers/final-branch-review.md`

## 修复结果

1. **新增域用户分阶段恢复**
   - LDAP `add` 已确认成功但 `unicodePwd` 初始化失败时，仅持久化非敏感阶段 `user_created_password_pending`、目标 DN 和固定脱敏错误。
   - 重试必须提交新密码；队列仅复制受信阶段。Worker 在跳过 `add` 前通过 BASE 查询严格核对 DN、`sAMAccountName` 和 `displayName`。
   - LDAP add 超时、缺失/矛盾结果、连接类结果码以及 ldap3/包装层异常均记录 `manual_intervention_required`；任务详情隐藏自动重试并提示人工核查。
   - `add=False` 且 `result=0` 的矛盾组合按结果不确定处理。明确的普通“对象已存在”结果保持固定失败、无可信恢复阶段，绝不查询、接管或重置该对象。
   - 恢复查询不匹配的既有对象转人工核查，不会被重置密码。

2. **域对象本地镜像写回**
   - `move_ou` 成功结果返回新 DN/OU；`enable`/`disable` 成功后更新 `is_active`。
   - 写回位于现有 lease/generation fencing 的成功事务内，并锁定本地 `Domain_Account` / `Domain_Computer`。
   - 本地对象缺失、DN 漂移或本地保存冲突不会覆盖 LDAP 成功，只在非敏感结果中记录等待下次同步的状态。
   - `create_user` 的虚拟目标明确跳过本地对象查询。

3. **停用 PC 分析配置**
   - 下载接口对停用配置返回 HTTP 400，并提示先启用分析配置。
   - 资产页和分析配置弹窗不再为停用配置展示可执行脚本下载入口。

4. **域操作确认摘要**
   - 域操作弹窗逻辑提取到可测试的独立 JavaScript 模块。
   - `destination_dn` / `group_dn` 的 `input` 与 `change` 都会在提交前即时刷新影响范围。

## TDD 证据

RED（均在实现前观察到失败）：

- `python manage.py test index.test_domain_actions --verbosity 2`：初始新增用例出现 4 个错误，暴露缺少阶段结果和恢复参数；补强用例继续暴露非 ldap3 add 异常、密码写超时和无结果码 `add=False` 未被安全分流。
- `python manage.py test index.test_domain_worker --verbosity 2`：写后镜像、新 DN 复用、可信阶段复制、manual 阻断和本地保存冲突安全降级用例失败。
- `python manage.py test index.test_domain_permissions_ui --verbosity 2`：人工核查标记缺失且仍显示重试入口。
- `python manage.py test index.test_pc_script_downloads.PcScriptDownloadTests --verbosity 2`：停用 profile 仍返回 200。
- `node --test static/js/domain_operation_modal.test.js`：控制器文件尚不存在。
- 最终补强：`add=False` 且 LDAP `result=0` 的指定用例先得到 `success=True`，证明矛盾结果会被误判成功。

GREEN（最终树）：

- `python manage.py test index.test_domain_actions --verbosity 2`：28/28 通过。
- `python manage.py test index.test_domain_worker --verbosity 2`：31/31 通过。
- `python manage.py test index.test_domain_permissions_ui --verbosity 2`：10/10 通过。
- `python manage.py test index.test_pc_script_downloads.PcScriptDownloadTests --verbosity 2`：11/11 通过。
- 上述 Django 聚焦用例合计 80/80；`node --test static/js/*.test.js`：15/15 通过，其中 modal 2/2。

所有 LDAP 调用均由 fake/mock 替代，未连接真实 LDAP；Django 仅使用自动创建和销毁的测试数据库。

## 最终验证

- `python manage.py test --parallel 4 --verbosity 1`：755/755 通过（最终提交后的业务树，47 秒）。
- `node --test static/js/*.test.js`：15/15 通过。
- `python manage.py check`：通过。
- `python manage.py makemigrations --check --dry-run`：无模型漂移。
- `python -m pip check`：无损坏依赖。
- PowerShell 模板解析：通过。
- macOS shell 模板 `bash -n`：通过。
- 高置信私钥/访问密钥模式扫描：未发现。
- 生产变更文件的 `result_snapshot` / `target_snapshot` / `parameters_snapshot` 密码或 secret 字段扫描：未发现；全仓命中仅为既有拒绝敏感快照的测试夹具。
- `git diff --check`：通过（仅 Git 的 LF/CRLF 提示）。
- 原始数据库 `C:\Users\a8715\Desktop\code\py\net\db.sqlite3` SHA256：`0625A3629821483ED0C2B9FBDA06C301B59C4D1D73A99445C2802F34AAE3B1E0`，与基线一致。

已知非阻塞提示：测试环境没有 `staticfiles/` 目录，Django 发出既有 warning，不影响测试结果。

## 提交

主要实现提交：`77cfc29`。

LDAP add 矛盾返回补强：`0cd2959`；普通“对象已存在”最终边界：`6d8f34e`。

实施计划提交：`902e7e2`。

未纳入提交：预存的 `py/__pycache__/__init__.cpython-312.pyc` 与审查原文 `.superpowers/final-branch-review.md`。

# 网络巡检中心（net）代码质量 / 废弃代码 / 优化修复 完整审计报告

- **审计日期**：2026-10-03
- **审计对象**：工作区当前状态（**非 git HEAD**），处于一次大规模未提交重构中
- **项目根**：C:/Users/takanashikotori/Desktop/code/py/net
- **规模**：跟踪文件 701 个 / 13.09 MB；Python 469 文件；模板 86 个 / 3570 行；CSS 7 文件 / 747 规则；JS 23 文件 / 2907 行；测试 1666 用例

> ### 快照声明（必读）
> 本次审计进行期间，**工作区被另一个进程并发修改**：docs/ 从 60 个 md 收敛为 6 个（architecture / changelog / database / device-inspection / operations / pc-collection），docs/code-quality-audit.md 中途消失，net/settings.py(18:46)、net/access/adapters.py(18:46)、README.md(18:51)、tests/system/test_backend_maintenance.py(19:02) 等被改写；git status 条目数从 210 → 222 → 220 波动。
> 这不是本次审计所为（6 个子任务全部只读，只写 %TEMP%/net-audit/）。因此**下文所有「文件:行号」都是 2026-10-03 18:20–19:20 之间的快照**，关键项一律附带函数名/符号名，便于在当前文件中重新定位。文档类结论（E 组）可能已被那次改动覆盖，请以最新文件为准。

---

## 一、总体结论

**代码基本盘是好的，但有三处「不修就会出事」的硬伤，且存在一条反复被点名却三轮未清的废弃代码债。**

| 维度 | 评价 |
| --- | --- |
| Lint / 静态规范 | **优秀**。ruff check . 0 问题 / 414 文件；迁移无漂移；node --check 23 个 JS 全过 |
| 测试投入 | **优秀**。1666 个用例，无断言/恒真断言/被注释测试/纯 mock 自证 **全部为 0** |
| 攻击面 | **良好**。0 处 shell=True/os.system/eval/exec/pickle/yaml.load；0 处 \|safe/mark_safe/autoescape off；SQL 全参数化；CSV 公式注入、ZIP 路径、PC 令牌比较均已正确防护 |
| **运行正确性** | **有严重缺陷**。MariaDB 上日期筛选静默返回空（生产真实故障） |
| **质量门禁** | **失真**。CI 只跑 SQLite，而生产是 MariaDB；且 CI 的 JS 步骤必然失败并连带取消语法检查 |
| 可观测性 / 日志 | 已修（settings.py 有 LOGGING，net/index 两个 logger） |
| 性能 | 多处 N+1 与无 LIMIT 全表扫描，拓扑页单次请求 **463 条 SQL** |
| 可访问性 | 系统性欠账：17 处控件无可访问名称；拓扑 SVG role="img" 与可聚焦子元素冲突 |
| 废弃代码 | **未清理**。约 176 行死 Python + 43 行死 JS + 13 组逐字重复，其中 7 个死函数是**第三轮**被点名 |
| 仓库卫生 | 尚可，但 main 与 origin/main **无共同祖先**，一次 push --force 会抹掉远端 5 条提交 |

---

## 二、审计方法（6 个子任务 × 不同思路）

| 组 | 思路 | 产出 |
| --- | --- | --- |
| A | 自建 AST 定义图（1406 个模块级定义）× CODE/TESTS/DOCS 三区计数交叉验证 | 463 行，11 项确定死代码 |
| B | AST 测试用例审查 + get_runner().build_suite() 只构建不执行 + 门禁实测 | 744 行 |
| C | 威胁面清单逐项 grep + 关键路径人工审读 + 反证排除 | 416 行，15 条 |
| D | 静态资产清单 + CSS 选择器反向引用 + node --check + 活实例 HTTP 实测 | 455 行 |
| E | git 元数据考古（ls-files / merge-base / check-ignore）+ 文档与代码一致性核对 | 490 行，21 条 |
| F | ORM 查询模式静态扫描 + 复杂度度量 + 只读 demo 库实测 SQL 计数 | 928 行 |
| 主 | 全量测试实跑（MariaDB）+ git archive HEAD 隔离对照 + SQLite 对照 + MariaDB 时区探针 | 见下 |

---

## 三、P0：必须优先处理（3 条）

### P0-1 【高】MariaDB 未加载时区表 → 所有 datetime 日期筛选**静默返回空**（生产真实故障）

**这是本次审计最重要的发现：一个在生产数据库上真实存在、CI 永远测不到的功能性故障。**

**证据链（全部为本机实测）**

1. 生产/本机数据库是 MariaDB（.env 里 DB_ENGINE=mysql、DB_NAME=net；Get-Service 显示 MariaDB 正在运行）。
2. 时区表为空：SELECT COUNT(*) FROM mysql.time_zone_name → **0 行**。
3. SELECT CONVERT_TZ('2026-01-15 04:00:00','+00:00','Asia/Shanghai') → **NULL**
   SELECT CONVERT_TZ('2026-01-15 04:00:00','+00:00','+08:00') → 2026-01-15 12:00:00（偏移形式正常，命名形式失效）
4. Django 的 MySQL 后端无条件生成命名时区转换：.venv/Lib/site-packages/django/db/backends/mysql/operations.py 的 _convert_sql_to_tz()。has_zoneinfo_database 只在 mysql/base.py:396-418 被探测，**不参与任何兜底**。因此 NULL 被当作普通值参与比较 → 比较恒不成立 → 结果集为空，**不报任何错**。
5. 端到端复现：tests/system/test_application.py 的 DynamicTableQueryTests.test_datetime_date_range_includes_the_requested_end_date（在 MariaDB 上）
   AssertionError: Lists differ: [] != ['PC-IN-RANGE']；同一用例在 SQLite 上 OK。

**影响面**：index/common/table_query.py 的 _filter_queryset() 对 kind in {'date','datetime'} 的字段统一生成 field.source + '__date' 查找（该文件约 160 行处）。凡是 USE_TZ=True + MariaDB 命名时区表缺失，以下全部失效：
- 设备/PC/服务器/弱电列表按「最后上报时间」「创建时间」等日期区间筛选；
- 巡检/分析记录、异常记录、任务历史按日期筛选；
- 任何 __date / __year / __month / TruncDate 分组统计。

**二次伤害**：CONVERT_TZ(col, ...) 把列包进函数，该列上的索引全部失效，属于「既错又慢」。

**建议修法（两条都做最好）**
1. **部署侧**：安装脚本 + deploy/README.md 增加「导入 MariaDB 时区表」（Windows MariaDB 自带 mariadb-tzinfo-to-sql.exe，Linux 用 mariadb-tzinfo-to-sql /usr/share/zoneinfo | mysql mysql），并在 net/settings.py 的启动闸门（现有 _INSECURE_SECRET_KEYS 那一段，约 66-78 行）里比照增加一条：connection.mysql_server_data['has_zoneinfo_database'] 为假且 USE_TZ 时 raise ImproperlyConfigured。**治「静默」比治「空」更重要**——这个 bug 最恶劣的地方是完全没有报错。
2. **更彻底（推荐）**：把 _filter_queryset() 里 datetime 的 col__date >= X / <= Y 改成半开区间：col >= 当天 00:00 本地时间、col < 次日 00:00 本地时间。跨后端一致、不依赖时区表、且能走索引。这是根因修复。

---

### P0-2 【高】CI 的 JavaScript 步骤**必然失败**，并连带取消 23 个 JS 的语法检查

- 位置：.github/workflows/tests.yml（未跟踪文件）第 56-59 行
- 证据：

      $ node --test "tests/**/*.test.{js,cjs}"
      x tests/browser/modal_transport.test.cjs
      Error: Cannot find module 'playwright'
      i tests 109 / pass 108 / fail 1     -> exit 1

- 根因：该 glob 命中 tests/browser/modal_transport.test.cjs:3 的顶层 require('playwright')，但**全仓没有 package.json**，CI 里只有 actions/setup-node@v4，**没有 npm ci / playwright install**。
- 连带损失：GitHub Actions 默认 bash -e，第 58 行非 0 即终止本步骤，第 59 行的 node --check（23 个 static JS）**永远不会执行**。
- 这是「上一轮修 H7 时改 glob」引入的新回归：上一轮审计曾把 glob 从 tests/frontend/*.test.js 改成 tests/**/*.test.{js,cjs}。
- **生效时机**：tests.yml 目前是未跟踪文件，HEAD 上仍是能通过的 frontend.yml —— **这次重构一提交推送，CI 立刻变红**。
- 修法（最小）：把该步骤收窄为 node --test "tests/frontend/**/*.test.js"，tests/browser/ 单列一个 job 并先 npm i playwright && npx playwright install chromium；无论如何把 node --check 拆成独立步骤。若新增 package.json，**不要写 "type": "module"** —— tests/frontend/ 下 21 个文件全是 CommonJS，会集体挂。
- 附带：README.md 仍写 node --test tests/frontend/*.test.js，与 CI 不一致（按文档复现会「全绿」，与 CI 相反）。

---

### P0-3 【高】main 与 origin/main **无共同祖先**，一次 push --force 就抹掉远端 5 条提交

- 证据（已由主 Agent 独立复现）：

      $ git merge-base main origin/main                          -> 空输出，exit=1
      $ git rev-list --left-right --count main...origin/main      -> 168    5
      $ git remote -v     -> origin  git@github.com:a87150/Net-Inspection.git
      $ git rev-parse --abbrev-ref main@{upstream}                -> fatal: no upstream configured

- 含义：本地 main 是一条全新的孤儿历史（168 提交），远端是另一条（5 提交，含 748db91 Initial public release of Net Inspection）。git status 不会报警。
- 修法：先 git bundle create backup.bundle --all 备份 → 在仓库内写一份说明定性 → git push -u <内部 remote> main；若必须重写 origin，只允许 --force-with-lease。
- 是否「有意为之」仓库内无从判断，**标未验证**，请在动手前确认。

---

## 四、P1：安全（7 条）

### P1-1 【高】凭据明文落库，而同仓其它凭据是加密的 —— 保护策略自相矛盾

明文列：
- net/models/devices.py:82,89,99,103,109 —— 设备 SSH 密码、SNMP community / auth / priv 密码、api_shared_secret
- net/models/devices.py:187,189 —— Server api_token + password；:229,230 —— 弱电 api_password / api_token
- net/models/domain.py:140 —— 域控 bind_password
- net/models/alerts.py:160 —— 告警 webhook secret 与 SMTP 密码（明文 JSONField，:97/:139 还明确允许 password/secret 键）

已加密（同仓先例）：PC 上报令牌 net/models/pc_upload.py:17、域操作密码 net/models/domain.py:248、设备配置备份、SNMP 凭据 net/devices/collection_profiles.py:60。

**修法**：把这批字段改为 BinaryField/TextField，复用现成的 _cipher() / encrypt_credentials() 模式。这是一次一致性修复，不是新功能。

### P1-2 【高】Windows Agent 存在**无认证端点**

- agents/server/windows/InspectionHttpService.ps1:480 的认证分支被 if ($Token) 包裹；第 314 行的强制检查只在 -RunService 分支。
- 后果：-Console 前台模式（README:11 官方排障路径）不带 -Token 启动即得到监听 http://+:9180/ 的**无认证**端点，/inspection 返回已装软件、服务、事件日志、网络配置、硬件序列号；且不需要管理员。
- 次生：第 482 行用 -ne 做 token 比较（非常量时间，可被时序猜测）；明文 HTTP 传 Bearer。
- 修法：在 328 行前加 if (-not $Token) { throw }；比较改 CryptographicOperations::FixedTimeEquals。

### P1-3 【高】全库导出文件没有权限位

- deploy/transfer_database.py:48 用 output.open('x') 写 dumpdata 全量导出（含上面全部明文凭据 + auth_user 哈希 + 900+ 人员 PII），无 0o600；第 97 行的 .verification.json 同样。
- Linux umask 022 → 0644，任意本地用户可读。
- 同仓**正确写法已存在**：net/management/commands/archive_task_results.py:185 用 os.open(..., 0o600)。照抄即可。

### P1-4 【中】生产安全加固全缺

manage.py check --deploy 实测 6 条 warning，其中 4 条 security：**W004 HSTS / W008 SSL redirect / W012 SESSION_COOKIE_SECURE / W016 CSRF_COOKIE_SECURE**。
net/settings.py 全文没有 SECURE_HSTS_SECONDS / SECURE_SSL_REDIRECT / SESSION_COOKIE_SECURE / CSRF_COOKIE_SECURE，只有 SECURE_PROXY_SSL_HEADER 开关（约 30-38 行）。会话 Cookie 走明文 HTTP 可被内网嗅探。
另外 **CI 从不执行 check --deploy**（只跑 manage.py check）。

### P1-5 【中】登录无限流

index/urls.py:39 用标准 LoginView，全仓无失败计数 / 锁定 / 验证码，requirements 也没有 django-axes。AUTH_PASSWORD_VALIDATORS 只管设密码强度，不限制猜测。
修法：只针对 url_name == 'login' 加一个缓存计数器（5 次 / 5 分钟 → 429），零新依赖。

### P1-6 【中】XML 实体扩展 DoS（已实测）

347 字节 XML → 展开为 1,000,000 字符（0.02 s）。
受影响：net/data_exchange/xlsx.py:79,124、net/domain/user_import.py:110,121（行/列/单元格限额都在 fromstring **之后**才生效）、net/infrastructure/http_collectors.py:35（设备响应无长度上限）。
修法：fromstring 前拒绝 <!DOCTYPE / <!ENTITY（零新依赖），或换 defusedxml。

### P1-7 【中】告警 webhook 存在 SSRF

net/models/alerts.py:74-83 只校验 https，无主机白名单 → 管理员可把 webhook 指向内网任意 HTTPS / 云元数据地址；失败摘要（net/alerts/base.py:38-60）会写库并显示在 UI，构成盲 SSRF 回显。
修法：解析后拒绝私网 / 回环 / 链路本地，或加域名后缀白名单。

**另有两条需业务确认，不是技术缺陷**：
- 未登录访客可枚举全部人员姓名/部门与设备名/IP（index/common/access.py:10 的 PUBLIC_VIEWS 含 asset_list/item_list；index/common/public_views.py:19-40 的 people 投影含 name/department/is_active）。这是有 tests/system/test_public_privacy.py 背书的**设计行为**，但等于把全员通信录放在内网未认证页面。
- 本地 runtime/ 下有 6 份 304 MB 的 sqlite 快照（约 1.8 GB），含 926 人 / 919 个手机号 / 73 个部门 / hydsoft.com 邮箱 / 923 条钉钉+飞书同步来源 / 1 个 Server api_token。**非 demo seed 产物**（seed_demo_data.py:49 只造 7 个 DEMO-P00x），疑似真实数据（**是否为真实数据未验证**）。已被 .gitignore:34 覆盖不会提交，风险在本机磁盘。

---

## 五、P2：质量门禁失真（3 条）

### P2-1 【高】全量测试在 MariaDB 上稳定失败 25 例；HEAD 同样失败

主 Agent 实测（manage.py test --verbosity 1，第二次已加 Set-ExecutionPolicy Bypass + TZ=Asia/Shanghai + 独立 TEMP）：

     Ran 1666 tests in 313.943s
     FAILED (failures=19, errors=6, skipped=16)      <- 25 例失败

**用 git archive HEAD 导出到仓库外隔离对照**，同类模块：HEAD Ran 32 tests / FAILED (failures=8)，工作区 Ran 33 tests / FAILED (failures=7)，**失败集合几乎完全重合**。
→ **这些不是本次未提交改动引入的回归，而是长期存在的缺陷。**

**根因（已对照证明）**：把同一份代码切到 SQLite（DB_ENGINE=sqlite，CI 的配置）：

     Ran 33 tests in 8.587s
     OK                          <- 0 失败
     System check identified no issues (0 silenced).

即：**测试只在 SQLite 上通过**。而 CI（.github/workflows/tests.yml:15）写死 DB_ENGINE: sqlite，生产与开发机是 MariaDB。这形成**双向盲区**：
- CI 看不到 MariaDB 专属问题（如 P0-1 的时区表，以及 mysql.W003 / models.W036 两条只有连 MySQL 才出现的 warning）；
- 本地看不到 SQLite 专属回归（tests/domain/test_enqueue_locking.py:15、tests/inspections/test_cancel_sqlite_busy.py:16、tests/inspections/test_worker_sqlite_busy.py:15、tests/devices/pc/test_import_lock_retry.py:11,27 这 5 处本地被跳过、只有 CI 跑）。

**修法**：CI 加 MariaDB 矩阵 job（services: mariadb），把「本地跑 MariaDB、CI 跑 SQLite」这个错位消掉。这是从结构上解决 P0-1 被漏掉的过程性根因。
另：部分失败用例断言了 SQLite 特有的 SQL 文本（如双引号标识符、LIMIT 20 OFFSET 20 字面量），需区分「测试不可移植」与「生产真的错」—— test_all_pc_filters_and_sorts_are_sql_fields 返回**空结果集**（而非文本不匹配），属于后者，必须按 P0-1 修。

### P2-2 【中】本地可跑的测试与 CI 不一致

- requirements-dev.txt:4 用 -r requirements.txt，而 CI 装 requirements.lock.txt；tests/system/test_dependencies.py:60-73 断言 lock 里 56 个包全部按锁定版本安装 → 全新 venv 只装 dev 依赖时该测试会挂（本机 venv 与 lock 0 mismatch，所以现在绿）。
- README.md 与 CI 的 JS 测试 glob 不一致（见 P0-2）。

### P2-3 【中】门禁覆盖缺口

- manage.py check OK / makemigrations --check --dry-run OK / ruff check . OK / 全量测试 OK
- check --deploy **从不执行**；tests/agents/ 7 个 PowerShell 测试完全孤立（CI 无 PowerShell 步骤，README 未提，仅 3 个 docs 文件引用），其中 2 个还会把临时产物写进仓库内 .task6-artifacts/；
- mypy / bandit / coverage 无配置（是否有意省略未验证）。

---

## 六、P3：性能与数据库（10 条）

全部为子任务 F 在**只读 demo 库副本**上的实测 SQL 计数。

| # | 级别 | 问题 | 位置 | 实测 |
| --- | --- | --- | --- | --- |
| F1 | 高 | 拓扑总览页 N+1：.only() 字段不全 → 延迟字段逐条回查 | index/dashboard/operations.py:24-42（_asset_sources()）+ index/dashboard/topology_presentation.py:30-35,151-153,161-162,170-172 | 单次 /operations/overview/ = **463 条 SQL**；每网络设备边际 **3.01** 条、每 PC **2.00** 条；前端每 30 s 轮询一次 |
| F2 | 高 | 域控同步逐对象 2 SELECT + 1 写 | net/domain/sync.py:130-150（循环 251-341） | **2.00 SELECT/对象**；demo 6016 个域对象 → 单次同步约 **18,048** 条语句 |
| F3 | 高 | 筛选下拉触发**无 LIMIT** 的 SELECT DISTINCT | index/common/table_options.py:29,76-83、index/common/table_registry.py:390、index/inspections/records.py:291-306 | /tasks/<PC分析任务>/ = **8 条无 LIMIT DISTINCT**，含 6 个 JSON 抽取表达式 |
| F4 | 高 | 每次网络设备巡检把全表设备+接口载入内存线性匹配 | net/inspections/executor.py:487-492 + net/topology/discovery.py:121-155 | demo 载入 77 设备 + 124 接口；接口表按 设备×端口 增长 |
| F5 | 高 | 每页固定重跑 N 条全列 DISTINCT | index/common/table_options.py:12-29、index/common/table_query.py:180-182 | /assets/networks/ 20 条 SQL 中 **8** 条是 DISTINCT；/assets/computers/ 18 条中 **7** 条 |
| F6 | 中 | 任务摘要延迟字段 N+1 + 每任务子查询拼接 | net/inspections/task_summary.py:24-34,77,86,183-184,210 | _prepare_summaries(10) 6 条查询中 3 条是逐任务 WHERE id=... LIMIT 21 |
| F7 | 中 | 巡检记录回收每轮 12 次候选查询 | net/inspections/retention.py:53-77 | cleanup_inspection_records(200) = **12** 条查询，Worker 每 5 s 调一次 |
| F8 | 中 | PC 结果回收的 DELETE 未复检告警谓词 | net/devices/pc/retention.py:28-41 | 对比 net/inspections/retention.py:74-76 已在 DELETE 内复检 → **口径不一致** |
| F9 | 中 | 缺索引的高频 ORDER BY / WHERE 列 | net/models/*、迁移 0060 | net_schedule.created_at 单页出现 7 次无索引排序 |
| F10 | 中 | claim_next_task 缺 SQLite 写锁预取；恢复查询无 LIMIT；人员目录同步逐行 save() | net/inspections/queue.py:478-505,423-466；net/people/directory/sync.py:262-267 | 同文件 566-572 已为 cancel 做了该处理；bulk_update 先例见 net/domain/memberships.py:38 |

### 6.1 补充量化与可直接落地的细节（子任务 F 完整结论）

**F1 拓扑页**：77 设备 232 条、913 PC 1827 条；该 URL 被 static/app/js/operations-overview/app.js:211 每 30 s 轮询 → **1 个标签页约 5.5 万条 SQL/小时**。修复效益最大的是补全 .only() 字段清单（改 1 处即从 463 降到约 10），并加 assertNumQueries 护栏把回归钉住。

**F2 域控同步**：整个循环包在**同一个 transaction.atomic()**（net/domain/sync.py:258）里，18048 条语句全在一个事务内 —— 既慢又长事务持锁。已有 bulk_update 先例可直接复用（net/domain/memberships.py:31-38，预取 in_bulk + bulk_create/bulk_update）。

**F3/F5 选项查询**：project_record_definition 固定 complete_options=True（table_registry.py:390），导致高基数列也走无 LIMIT DISTINCT。建议只对低基数列开 complete_options，其余回落 option_limit，并对候选值加 60 s 缓存。

**F4**：913 台 PC 的规模下每轮巡检载入全表设备+接口；建议按本次邻居的 chassis MAC / 管理 IP 过滤，并把双层 for 换成一次性构建的 dict。

**F7 巡检回收**：3 表 × 4 个留存档位 = 12 条/轮，Worker 每 5 s 一轮 → **约 8640 条空转/小时**；且 net/inspections/retention.py:77 用 len(ids) 而不是 delete()[0] 计数（**计数不准**），worker.py:311-314 丢弃返回值、无日志、无 dry-run。

**F9 缺索引清单（可直接用的 AddIndex 候选）**：net_schedule.created_at（单页 7 次无索引排序）、network_device.device_name / server.name / weakcurrentdevice.device_name（列表默认排序）、computeranalysis.report_severity、alertevent.occurred_at、topologylink.last_seen_at。

**另一条值得单独看**：index/devices/views.py:233-246 把全表 hire_date 读进 Python 求平均，**而该视图在 PUBLIC_VIEWS 里、匿名可达** —— 是「性能问题 + 未认证暴露」的叠加。

**规模统计**：ORM 在循环内 63 处；实例级逐行写 35 处；结构性重复函数对 3 对；嵌套深度 >=5 共 158 处；最大表 net_alertstate 11478 行。最需要处理的 import 环是 net.devices.pc.severity <-> net.inspections.issues。

**唯一未验证的残留**：net/inspections/worker.py:287 的 executor.shutdown(wait=True) 没有墙钟上限。

**已核实无问题，不必再查**：所有出站 HTTP/SSH/SNMP/LDAP/SMTP 调用均带超时（requests 全仓仅 3 处且都带 timeout；paramiko/pysnmp/aiohttp/LDAP/SMTP/subprocess 均有超时）；模板自定义 filter/tag 不查库（0 处；group_delivery_outcomes 已在 index/alerts/views.py:165-166 prefetch_related）；未见 .objects.all() 无分页直渲染；.objects.all() 后 len() 仅 net/domain/memberships.py:136 一处且是刻意的完整性校验；回收分批（limit 200）与「保留最新一条」逻辑正确；崩溃任务不会永久卡住（租约恢复 + MAX_TASK_ATTEMPTS 有效，已验证）；SQLite WAL 默认关闭且需显式 --apply，超时参数 (0,60] 校验完整。

**架构分层**：存在 5 类低危反向依赖（业务层 import 页面层 net/inspections/schedules.py:45、net/data_exchange/table_csv.py:62；models 反向依赖业务层 net/models/records.py:19-21,182；net/infrastructure/* 依赖具体设备模块），以及 **10 组模块级 import 环**。嵌套深度 >=5 共 158 处（最深 net/devices/pc/analysis.py:130-139，深度 9）。最长函数 27 个 >=80 行（seed_demo_data.py 最大 1517 行，_seed_networks 161 行；net/models/tasks.py:690 的 clean() 156 行）。

---

## 七、P4：可访问性与前端（10 条）

| # | 级别 | 问题 | 位置 |
| --- | --- | --- | --- |
| D1 | ~~高~~ **已核销** | **误报**。子任务 D 报的「列表页 5 个过滤器控件无可访问名称」读的是**死模板** common/table_filter.html —— 它零 include（见 8.2），且它引用的 table_state.parameter_names.q / table_state.categories 早已无生产者。**现役列表页模板是正确的**：table_workspace.html:16 与 table_field_filter.html:7 都有 for=，:9/:21/:41 有对应 id=，:16/:18 的日期输入有 aria-label，:34 的候选下拉有 aria-label。**活页面无需修改**，本条只作为「删死模板」的理由（并入 8.2） |
| D2 | 高 **（已复核为真）** | 巡检配置 / 人员同步主表单 **12 个控件无可访问名称**；:213 用的 aria-description 支持度低，不算 aria-label | inspections/profile_modal.html:57-58,62-63,68-69,77-78,192-193,199-200,204-205,212-213 + integrations/source_modal.html:31-34 |
| D3 | 高 **（已复核为真）** | svg role="img" 内含 3 个可聚焦子元素 → nested-interactive 硬违规，读屏读不到设备 | dashboard/operations_overview.html:77-79（子元素 :85、:88 的 path tabindex=0 role=button，:111 的 a）。改 role="group" 最省（:128-136 已有等价文字列表） |
| D4 | 中 | collection_settings_body.html:122,124 手写 label 无 for，同文件其它行都用 label_tag | 同上文件 |
| D5 | 中 | **17 个零引用 CSS 类**（模板/JS/Python 三向反查 0 命中） | foundation.css:432,468,620-622,717,879-891,907,912-913,174-180,559,986-990；operations.css:180-195,236-244,446；admin.css:86,98-100,277。注意其中 :175/:987/:912-917/:931、operations.css:264 是组合选择器，**只能删段不能删整条规则** |
| D6 | 中 | static/app/js/common/table_tools.js（43 行）完全孤立死文件 | 无模板引用、无 JS import，它消费的 [data-local-filter]/table[data-local-sort] 全库 0 处；tests/frontend/test_ui_design_contracts.py:224 反向断言它不该进 base.html |
| D7 | 中 | admin.css 的 .theme-toggle 永不渲染 | templates/admin/base_site.html:10-16 覆盖了 usertools 且未 include admin/color_theme_toggle.html，:9 还关掉了 dark-mode-vars |
| D8 | 中 | static/app/css/style.css 只有 1 行注释、0 条规则，却被每页加载 | base.html:13 + templates/admin/base_site.html:5。删除需同步改 test_ui_design_contracts.py:194,208 的 finders.find('app/css/style.css') 锚点 |
| D9 | 中 | 6 个经典脚本未 IIFE 包裹，**38 个顶层声明泄漏到 window** | common/form_accessibility.js(5)、common/modal_feedback.js(13)、domain/operation_modal.js(3)、inspections/task_ui.js(14)、people/import_tasks.js(1)、people/modal.js(2)；同目录 table_selection.js:1-6 是正确写法 |
| D10 | 中 **（已复核为真）** | integrations/source_modal.html:38 用了 **Tailwind 类** justify-self-start（Bootstrap 5.3 无）→ 按钮不左对齐，**真实视觉缺陷**；另有 6 个类名在任何 CSS 中都不存在 | 同上；另 6 处：metric-summary-grid、metric-card__error、modal-kicker、alert-policy-modal__section、item-method-note、import-preview-modal |

其它前端度量：!important **47** 处；tokens.css 之外硬编码颜色 **372 处 / 232 个不同值**；内联 script 1 处、内联 style= 2 处；onclick= 0、tabindex 正值 0、img 0（图标全是内联 SVG，故「img 缺 alt」不适用）、button 内嵌 a 0、console.*/debugger 0、同一「选择器+声明」完全重复 **0** 组、>=20 行重复模板片段 **0** 组。

---

## 八、P5：废弃代码与死文件（可直接清理清单）

**这份清单是「第三轮」了**：上一轮审计早已点名，至今一行未动。

### 8.1 确定死代码（代码区 ∧ 测试区均零引用）

| 位置 | 内容 | 行数 |
| --- | --- | --- |
| net/management/commands/my_command.py | Django 脚手架残留，grep 仅命中自身 | 16 |
| net/devices/pc/checks.py:234,249 | evaluate_computer() / extract_network_info() | 24 |
| index/common/table_query.py:228-287 | _legacy_definition() + apply_queryset_table()（与活函数 apply_table_filters 并排在同一热模块） | 60 |
| net/infrastructure/ssh_collectors.py:185 | _read_channel()（SSH 采集已改走 net/devices/network/ssh.py） | 18 |
| net/inspections/issues.py:70 | threshold_snapshot()（只是 configuration_snapshot(...)[1] 的冗余包装） | 2 |
| index/common/exports.py:46 | _computer_analyses() 纯透传 | 2 |
| static/app/js/common/table_tools.js | 零引用死 JS | 43 |
| index/integrations/ | 只有 docstring 的死包 | 1 目录 |
| net/scripts/executable.py:12-13 | FOOTER_SIZE / PAYLOAD_HEADER_SIZE | 2 |
| net/topology/discovery.py:36-47 | NeighborCandidate | 12 |
| operations-overview.css:4 | 死 CSS 变量 | 1 |

**合计约 176 行 Python + 43 行 JS + 1 行 CSS + 1 个空包 + 1 个临时脚本 tmp-run.ps1（未跟踪）。**

### 8.2 需先改引用再删（裸删必挂）

- index/templates/common/table_filter.html（86 个模板中唯一孤儿；它依赖的 table_state.categories / sort_options 在 Python 侧**零生产者**）→ 须同步删 tests/frontend/test_ui_design_contracts.py:448
- static/app/css/tokens.css:16-17 → 须同步删 tests/frontend/test_frontend_consistency.py:16-17
- config/examples/software-policy.ini 与 software-policy-demo.ini 逐字节相同，但**两侧各有活引用**（index/devices/pc/software_policy.py:76,80 vs tests/devices/pc/test_software_policy_upload.py:43）
- net/alerts/service.py:418 deliver_event()（7 个测试引用，生产走 worker.py:328 的 deliver_due_alerts）

### 8.3 禁止删除 / 需先决策

- 9 个 0–2 字节的 __init__.py —— Django 包发现机制依赖。
- net/migrations/_record_report_0036.py —— **不是重复代码**：它是迁移 0036 的**冻结副本**（0036_record_report_fields.py:5 导入它），重复是设计意图。（上一轮报告把它误判为「可去重的重复代码」，本报告予以纠正。）
- net/devices/pc/analysis.py:448 analyze_log() —— **被 30 个测试测、被 tests/architecture/test_device_boundaries.py:8 声明为设备层契约入口，但 net/devices/pc/executor.py:15 只 import prepare_log, persist_analysis 绕过了它**。这是「重构时生产调用方被漏接」的信号，必须先决定「接回生产」还是「改契约 + 改测试」，不能当死代码删。
- 同类信号还有 3 处新增：net/alerts/service.py:418 deliver_event、index/common/table_options.py:93 build_field_options、net/devices/pc/analysis.py:54 analysis_items_for_platform。

### 8.4 重复代码（13 组逐字相同，可去重约 350 行）

net/people/directory/dingtalk.py 与 feishu.py（约 150 行，结构相似度 0.933 / 1.0）；_optional_text **三份**（base.py:39、dingtalk.py:292、feishu.py:298）；net/admin/domain.py 5 处 + net/admin/tasks.py 2 处的 has_change_permission **共 7 份**；_safe_next 两份（index/alerts/views.py:36 vs index/inspections/tasks.py:56）；validate_json_object 两份（net/models/tasks.py:43 vs net/topology.py:20）；net/models/alerts.py:395 vs :572 的 save 两份；_record_value、_column_index 各两份。

### 8.5 仓库卫生

- **127 个源文件已删除的陈旧 .pyc**（如 index/views/__pycache__/assets.cpython-312.pyc、index/forms/__pycache__/*）—— 重构遗留。
- runtime/build_real_topology.py（10823 字节手写 Python）**被 .gitignore:34 的 runtime/ 一起忽略**，untracked + ignored，git clean -xdf 或重新 clone 即永久丢失，历史里也没有。建议移入源码目录，或把规则收窄为 runtime/* + !runtime/build_real_topology.py。
- docs/superpowers/ 有 8 个文件**仍被跟踪**，而 .gitignore:27 声称忽略该目录（git check-ignore --no-index 能匹配，但已跟踪文件不会被自动移除）→ 二选一：git rm -r --cached docs/superpowers 或删掉该规则。
- .gitignore 缺 .ruff_cache/（现在靠 ruff 自生成忽略文件兜底）与 .pytest_cache/；runtime/ 规则完全覆盖了 :12/:31 两条，属冗余。
- agents/pc/windows/ 下 26 个 .exe/.dll（8.38 MB）被跟踪，其中 PawnIO_setup.exe 3.25 MB **超过仓库自己 .pre-commit-config.yaml 的 check-added-large-files --maxkb=2048**；.gitignore 只挡了 PCCollector.exe 与 PC-Windows-Collector*.zip。
- README.md 某行含字面字节 0x5c 0x6e（反斜杠+n），把「PC API 上报失败」与「PC 原始日志不可用」两行表格合并成一行，渲染错位。
- 2 个 prunable worktree（指向 C:/Users/a8715/...）；2 条已合并可删分支；无 tag；本地生成物约 2.99 GB（runtime 1746 MB / demo-runtime 1068 MB / .superpowers 144 MB）。
- 版本一致性：VERSION / net/__init__.py / README 标题三处一致（**0.9.1**）OK。但 net/settings.py:4、net/urls.py:5、net/wsgi.py 的脚手架注释仍写 Django 5.0.7（实际 **6.1.1**）；ruff.toml:8 target-version = "py312" 而本机 .venv 是 **Python 3.14.7**、requirements.lock.txt 注释写「Python 3.12 deployments」。Django 6.1.1 的 Requires-Python: >=3.12，故 CI 用 3.12 可行，但「文档/锁/解释器」三者不一致应统一。
- requirements.txt 缺直接 import 的 textfsm / ntc_templates（虽由 netmiko 传递带入）。
- **djangorestframework 是纯测试依赖**：全仓只有 tests/system/test_application.py:17,255 引用它，生产代码零使用，却列在 requirements.txt（生产锁）里。

### 8.6 死依赖

smbprotocol：AST 扫 478 个 .py **零 import**，反向依赖为空，且它是 pyspnego 的唯一上游、sspilib 只被 pyspnego 依赖 → 可连带清掉 3 行（requirements.txt:16、requirements.lock.txt:39、tests/system/test_dependencies.py:25）。
**注意不要误删**：pycryptodome 是活的（ldap3/utils/ntlm.py:500 有 from Crypto.Hash import MD4）；invoke 是 paramiko 4.0 的依赖。

---

## 九、误报澄清（这些**不要**改，避免误删）

- operations-overview.css 的 .is-abnormal / .is-backbone / .is-external / .is-inferred / .is-unknown **不是死代码** —— 由 dashboard/operations_overview.html:66 与 :91 的动态类名拼接（is- + kind / status）生成，候选值来自 operations-overview/topology.js:1,258-262，全是活代码。上一轮报告把它列为死代码是**误报**。
- .colMS / .deletelink / .helptext / .inline-group / .aligned / .current-app / .current-model 由 Django admin 模板自动生成；.modal-backdrop 由 Bootstrap JS 注入 —— 均为活。
- 上一轮说的「24 处未使用 import」「custom_filters.attr」「devices.py public_api_url 重复」「analysis/checks _issue 重复」「无 lint/pre-commit」「PowerShell 测试无平台守卫」**均已修复**，本轮复核确认清零（ruff check . 全绿；测试侧 11 处 subprocess 中 4 处 powershell 全部有 requires_powershell 守卫，生产侧 0 处 powershell/pwsh/cmd）。
- tests/powershell.py 是有效的平台守卫工具，不是重复文件。
- 仓库**没有**非 UTF-8 的跟踪文本文件（701 个全量解码验证 0 例）；pwsh 里看到的乱码是控制台代码页假象。
- 无「已删除但仍占历史」的巨型文件（历史路径最大仅 84 KB）。
- 常见误报源：**docs/ 会自污染引用计数** —— 用 grep 统计引用时若把 docs/*.md 计入语料，死函数会因为「被报告点名」而看起来有人用。必须按 CODE / TESTS / DOCS 分区计数。

### 9.1 统一分析阶段的交叉纠正（子任务之间结论冲突时以主 Agent 复核为准）

| 冲突 | 子任务 A 结论 | 子任务 D 结论 | 主 Agent 复核裁定 |
| --- | --- | --- | --- |
| common/table_filter.html 是死是活 | **孤儿模板**，唯一引用是 test_ui_design_contracts.py:448 | **被所有列表页 include**，并据此报了一条高危可访问性问题 | **A 正确，D 错误**。全仓 grep 无任何 include 'common/table_filter.html'；现役模板是 common/table_workspace.html（:23-27 遍历字段 include common/table_field_filter.html）。D 把 table_filter.html 与现役的 table_field_filter.html 看混了 |
| 该不该按 D 的高危条改代码 | —— | 要求给 5 个控件补 id/for | **不需要**。现役模板 table_workspace.html:16 与 table_field_filter.html:7 已正确关联 label 与控件。真正该做的是**删掉这个死模板**（连同 test_ui_design_contracts.py:448） |

**教训（供下次审计复用）**：静态可访问性扫描必须先用「该模板是否被 include / 是否被 render」过滤，否则会给死文件报出一堆无人受影响的缺陷，反而掩盖真正的问题（本报告 D2、D3 是复核后确认为真的活页面问题）。

---

## 十、建议修复顺序

**第一批（本周，都是小 diff 高收益）**
1. P0-1 时区表：加启动闸门 + 文档/脚本导入时区表；并把 _filter_queryset() 的 __date 改成半开区间（根因）。
2. P0-2 CI：JS glob 收窄 + node --check 独立步骤。
3. P0-3 先 git bundle 备份并定性 main/origin 关系。
4. P1-1 凭据加密（复用现成 _cipher()）。
5. P1-2 Agent 补 -Token 强制校验 + 常量时间比较。
6. P1-3 两处 os.open(..., 0o600)。

**第二批**
7. CI 加 MariaDB 矩阵 job（结构上解决 P2-1 的根因，也顺带覆盖 mysql.W003 / models.W036 / migrations/0040 的 MySQL 分支）。
8. check --deploy 进 CI + 补 SECURE_* / SESSION_COOKIE_SECURE / CSRF_COOKIE_SECURE。
9. 清理 8.1 的死代码 ⇒ 约 176 行 Python + 43 行 JS，零依赖变更。
10. D1/D2/D3 可访问性（纯机械改 id/for；SVG 改 role="group"）。

**第三批**
11. F1/F2/F3/F4/F5 五条高开销查询（补 .only() 字段、批量查域对象、给 DISTINCT 加 LIMIT、select_related/prefetch_related）。
12. 13 组重复代码合并；has_change_permission 7 份收敛。
13. 仓库卫生：.pyc 清理、runtime/ 忽略收窄、docs/superpowers 规则二选一、大文件策略、.gitignore 补项、版本号三处统一。
14. D5/D6/D7/D8/D9/D10 前端清理（含 Tailwind 类误用这个真实视觉缺陷）。

---

## 十一、明确未验证的事项（未臆测）

- net/models/domain.py:92 的 CharField(max_length=1024, unique=True)（mysql.W003）在 MariaDB 上唯一索引的**实际形态** —— 只读约束下未做 DDL 探测。
- net/models/tasks.py:956-962 条件唯一约束在 MariaDB 被忽略，net/migrations/0040_mariadb_active_target_scope.py 的生成列补偿**是否正确** —— 该 MySQL 分支零测试。
- tests/agents/ 7 个 PowerShell 测试当前是否通过（只读约束下未执行）。
- tests/inspections/test_worker.py:440 的 time.sleep(1.1) vs lease_seconds=1 是否真会在 CI 负载下抖动。
- runtime/ 下 sqlite 快照中的 926 人是否**真实**人员数据。
- main 与 origin/main 无共同祖先是否为有意为之。
- ldap3==2.9.1（2021 年）是否有已知 CVE（需联网确认）。
- 是否有意不引入 mypy / coverage / bandit。

---

## 附：本次审计对仓库的写入

除本报告文件外，**未修改任何仓库文件**。6 个子任务全部只读，分析脚本与分报告均写在 %TEMP%/net-audit/。
审计过程中我在仓库内临时创建过两个日志（.task6-audit-tests.log、runtime/audit-run.log），**已在收尾时删除**（二者本就匹配 .gitignore 的 *.log）。

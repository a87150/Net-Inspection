# 数据库、缓存与维护

## 双数据库支持

`DB_ENGINE` 决定使用哪个后端，取值 `mysql` / `postgresql` / `sqlite`。正式环境与模拟环境都用同一份 `.env`，
切换 `DB_ENGINE` 即可整体换库，两种后端跑同一套迁移、同一套测试。

| 关键项 | MariaDB / MySQL | PostgreSQL |
| --- | --- | --- |
| 驱动 | `mysqlclient` | `psycopg[binary]` |
| 默认端口 | 3306 | 5432 |
| 字符集 | `utf8mb4` / `utf8mb4_bin` | 库级 `ENCODING 'UTF8'` |
| 排序规则 | `utf8mb4_bin`（字节序） | `LC_COLLATE 'C'`（字节序） |
| 会话时区 | `init_command` 设 `time_zone` | 连接参数 `-c timezone=UTC` |

**排序规则必须对齐。** MariaDB 的 `utf8mb4_bin` 和 PostgreSQL 的 `LC_COLLATE 'C'` 都按 UTF-8 字节序比较，
中文名、DN 的大小写和先后在两个后端才一致。PostgreSQL 默认的 locale 排序（如 `en_US.UTF-8`）会按语言规则排，
「甲」「乙」的先后正好与字节序相反，`deploy.demo` 建库时已经固定为 `C`。

后端差异已在代码里逐个处理，不要在业务层判断 `vendor`：

- `index/inspections/result_query.py` 的 `JsonKeyType` 三种后端各一套 SQL（MySQL `JSON_TYPE`、SQLite JSON1 `json_type`、
  PostgreSQL `JSONB_TYPEOF(JSONB_PATH_QUERY_FIRST(...))`）。必须用 `_FIRST` 版本：`JSONB_PATH_QUERY` 是集合返回函数，
  放进 `CASE` 参数里 PostgreSQL 会报「CASE/WHEN 参数类型无法匹配」。
- `select_for_update()` 不能和指向可空外键的 `select_related()` 连用。MariaDB 不在意，PostgreSQL 会拒绝
  （外连接可空侧不允许 `FOR UPDATE`）。做法是只锁主表行、关联对象在同一事务里懒加载。
- 文本字段不能含 NUL。MariaDB 收得下，PostgreSQL 报 `DataError`；落库前统一剔除。
- `bulk_create(update_conflicts=True)` 的 `unique_fields` 两个后端要求相反：PostgreSQL 必填，
  MariaDB 收到反而不支持。按 `connection.features.supports_update_conflicts_with_target` 分支。
- PostgreSQL 拒绝 DROP 仍被会话占用的测试库，测试被中断后会留下孤儿连接，下次建库直接失败。
  跑测试前先清理 `pg_stat_activity` 里残留会话。

## 当前本机配置

根目录 `.env` 是 Web、Worker、manage.py 的共享配置，已被 Git 忽略。进程变量优先；`NET_ENV_FILE` 可选择其他文件。

- MariaDB：`127.0.0.1:3306/net_inspection`，utf8mb4_bin，专用 `net_app` 账号仅拥有本项目库权限。
- PostgreSQL：`127.0.0.1:5432`，正式库 `net`、模拟库 `net-test`，均按 `C` 排序规则建库。
- 密码：Windows 凭据管理器服务 `net-inspection-mariadb`、用户名 `net_app`。配置文件不保存数据库密码；root 仅用于建库授权。
- PC API 令牌由 `PC_LOG_SOURCE_ENCRYPTION_KEY`（或同名 `_FILE`）加密，不能丢失或随意替换；更换后需重置令牌并重新部署采集包。
- 设备口令、SNMP 密钥、域控绑定口令、告警口令、人员同步 app_secret 由 `DEVICE_BACKUP_ENCRYPTION_KEY` 加密。这把密钥同时保护配置备份，**丢失即永久无法恢复**，必须离线独立备份；Web 与 Worker 必须一致。字段清单与注意事项见 [device-inspection.md](device-inspection.md) 的「凭据加密」。
- `migrate` 会把存量明文凭据改写为密文（迁移 0064、0065）。**执行前先备份密钥**，并确认两个服务使用同一个值。
- Windows Web/Worker 应由同一 Windows 账号运行；换服务账号需在该账号凭据管理器设置密码。Linux 可通过服务环境注入 `DB_PASSWORD`，不必依赖桌面凭据管理器。
- Redis：`127.0.0.1:6379/1`，键前缀 `net-inspection`。不执行全库 flush。

继续使用 `.venv/Scripts/python.exe -m deploy.demo` 启动，自动启动一个 4 线程 Worker。启动器不再强制切回 SQLite，MySQL/MariaDB 模式不自动生成展示数据。单独管理服务时，Web 使用 `--no-worker`，另启 `manage.py run_task_worker`，不要重复运行。

## 页面缓存

`NET_PAGE_CACHE_ENABLED=true` 开启，`NET_PAGE_CACHE_SECONDS=15` 控制秒数。默认缓存登录后的首页、人员统计、人员详情、设备详情。模态列表、配置、任务轮询、导入导出、下载、异常响应及游客页面不缓存。

键包括 URL/参数、登录用户、权限标志、会话、Cookie、语言和时区。CSRF 密钥变更绕过缓存；不会把一个用户的页面给另一个用户。POST 等写操作更新当前会话缓存版本，其他会话最多延迟 TTL 秒看到变化。后台 Worker 写入也最多延迟 TTL 秒；实时任务接口始终直读。

Redis 连接/读取超时均为 0.2 秒，失败回退到真实页面。浏览器仍收到 private/no-store，缓存只在服务端。Redis 不是任务存储，也不是数据库备份。

## 数据迁移与校验

先暂停 Web/Worker并确认无活动任务；保存 SQLite 完整备份和密钥。新建空 MariaDB 库，运行 `manage.py migrate`，再运行：

```powershell
.venv/Scripts/python.exe -m deploy.transfer_database --source demo-runtime/backups/mariadb-20260908/source.sqlite3 --output demo-runtime/backups/mariadb-20260908/new-export.json
```

该工具拒绝覆盖已有应用数据和导出文件。源库只读；实体先入库、关联后入库；在单一数据事务内比较每个模型的数量和规范化 SHA-256，任何差异回滚。规范化保留微秒、统一 UUID 排序和无序多对多集合，JSON 业务列表顺序不变。Django 生成的 ContentType/Permission 按自然键关联，其余账号、会话和业务数据保留。成功生成 `.verification.json` 报告。

MariaDB 的 `0040` 迁移用生成列唯一索引补齐域控活动目标的条件唯一约束。PC API 改造包含 `0053_pc_api_logs` 和 `0054_pc_analysis_log_reference`；前者保留历史 payload 并回填可得到的时间字段，后者将分析来源改为普通 `log_id`，不建立日志外键。Django 仍可能提示原条件约束不受支持，但替代索引已在数据库生效；不能跳过该迁移。长域 DN 唯一字段也可能触发通用长度警告，应核对实际索引，不截断域路径。

## 回退

原 `demo-runtime/demo.sqlite3` 和 `demo-runtime/backups/mariadb-20260908/source.sqlite3` 均保留。回退前停止 Web/Worker，使用独立环境文件指定 `DB_ENGINE=sqlite`、明确旧库路径并关闭页面缓存，然后启动。密钥保持原值。

**旧 SQLite 只是切换时快照，不含切换后 MariaDB 新数据。** 若已有新数据，先备份 MariaDB 并制定反向迁移，不能直接切旧库冒充无损回退。备份和 JSON 导出含隐私及配置凭据，只能保存在受保护的本机目录，不要提交 Git 或公开分享。

---

## 归档与保留命令

以下操作沿用现有的 DB_ENGINE / DJANGO_SQLITE_PATH 选择规则与启动入口，不引入任何模型或迁移变更，命令都作用于已配置的默认数据库，不会自动执行。

`DB_ENGINE` 只接受 `mysql` 和 `sqlite`（不区分大小写）。其他取值——包括 `postgre` 这类拼写错误或空字符串——现在会直接以 `ImproperlyConfigured` 中止配置加载。它刻意不回退 SQLite：引擎名拼错过去会打开另一个空库，读起来就像所有记录凭空消失。`postgresql` 会按名字识别，但明确报告尚未实现，而不是当成拼写错误；参见 [PostgreSQL 支持方案](changelog.md#当前待处理)。

### SQLite 写入争用

`NET_SQLITE_TIMEOUT` 设置 SQLite 连接的忙等待超时秒数：默认 5，必须是大于 0 的有限值，最大 60。无效取值会让 SQLite 侧配置加载失败；MySQL 选项不变。改动后要重启 Web 和 Worker，新连接才会生效。更长的超时能容忍短时写争用，但不会让 SQLite 支持并发写入。

WAL 是需要显式发起的可选维护操作，绝不会挂在 AppConfig 启动钩子上。仅设置 `NET_SQLITE_WAL_ENABLED=true` 不会执行任何 PRAGMA。维护窗口内先停止 Web 和全部 Worker、备份数据库，然后：

```powershell
.venv/Scripts/python.exe manage.py sqlite_wal
$env:NET_SQLITE_WAL_ENABLED = 'true'
.venv/Scripts/python.exe manage.py sqlite_wal --apply
```

第一条只读取日志模式。第二条需要显式开启，且只对已存在的普通文件型 SQLite 数据库启用 WAL。内存库、SQLite URI 文件名、非 SQLite 后端，以及 test/testserver/pytest 进程一律跳过。文件不存在或库上有未完成事务时拒绝执行。命令会确认 SQLite 确实接受了 WAL，保持 synchronous 设置不变，后续启动也不会关闭 WAL。把环境变量改回 false 只是撤销本命令的授权，日志模式已经持久化在数据库里。WAL 只适合同一主机上多进程共享的本地文件系统，不要用在 SMB/NFS 上的数据库文件。给运行中的库做备份时要连同关联的 WAL 文件一起保留；优先使用 SQLite 官方的备份方式，或完全停服后再复制。维护后要重启服务。本实现没有改动任何在用数据库。

### 历史结果保留

历史大快照会一直保留，直到管理员主动运行维护命令。默认行为是只读预览。`--apply` 创建任务归档、清理过期的拓扑观测，只有同时带上 `--compact` 才会压缩任务快照。任务/巡检证据、审计历史、PROTECT 外键、日志文件和任务本身都不会删除。拓扑观测是下文描述的唯一有界例外；当前接口、链路和发现批次保留。没有安装任何自动清理、VACUUM 或归档过期机制。

使用带时区的过去 ISO 时间戳，并给一个有界上限（1..1000）。任务和目标都必须处于终态，且严格早于截止时间完成。只要有任一目标未终结的任务就被排除。缺少完成时间戳的任务和活动任务同样排除。结果按完成时间与 UUID 排序。

```powershell
# 预览候选 ID 及指向它们的 PROTECT/RESTRICT 引用数量。
.venv/Scripts/python.exe manage.py archive_task_results --before '2026-08-01T00:00:00+08:00' --limit 100

# 同时评估哪些重复详情可以安全压缩（仍然只读）。
.venv/Scripts/python.exe manage.py archive_task_results --before '2026-08-01T00:00:00+08:00' --limit 100 --compact

# 只创建归档。父目录必须已存在，目标文件必须不存在。
.venv/Scripts/python.exe manage.py archive_task_results --before '2026-08-01T00:00:00+08:00' --limit 100 --apply --output 'D:/Backups/net/results-001.jsonl'

# 可选：先生成持久归档，再压缩已核验的重复项。
.venv/Scripts/python.exe manage.py archive_task_results --before '2026-08-01T00:00:00+08:00' --limit 100 --apply --compact --output 'D:/Backups/net/results-compact-001.jsonl'
```

每份摘要都会给出 `next_cursor`，含 `after_id` 和 `after_finished_at`。把它们连同相同的截止时间作为 `--after-id` 和 `--after-finished-at` 传入，继续写入**新的**输出文件，直到 count 为零。预览不占用行；`--apply` 会重新评估当前资格。逐批保留游标和归档。

普通预览不包含 JSON 载荷。压缩预览会加载证据做校验。引用数量说明直接删除的阻塞项，它既不是删除授权，也不等于完整的递归删除分析。`compaction_blocked` 为 null 表示预览时重复详情符合压缩条件。

归档是 UTF-8 JSONL：一份带版本号的清单、目标和任务的完整具体字段（含原始快照与结果引用）、PC 日志 ID 及指向它们的受保护引用数量，最后是一个 `complete` 尾部，记录前序全部原始字节（含换行）的数量和 SHA-256。这是快照归档，不是完整数据库备份；被引用的记录和文件仍留在数据库和存储中。使用前请核对尾部数量、哈希和命令退出状态。文件以独占方式创建，拒绝覆盖。载荷和尾部分别 flush 并 fsync 之后才更新数据库。请用 Windows ACL 保护归档目录（POSIX 创建模式为 0600）：导出内容包含历史运维数据和可能的个人信息，存储必须保证 flush/fsync 的持久性。

`--apply --compact` 只在受支持的记录存在、引用了这个确切目标、且其详情与旧快照详情完全相等时，才使用 v2 结果存储助手。引用缺失或不匹配、格式未知、已存在 v2 快照，以及不会省下字节的改动都会被跳过。只移除重复的 `details`；原有的状态、健康度、摘要和其他标记全部保留，包括旧快照中本就没有 status/health 键的情况；助手自身的默认值也不会引入新的结论标记。引用和版本由助手提供。PC 的 `exceptions`、`analysis_items`、`log_id` 和 `source_collected_at` 原样保留，即使它们与规范记录重复；本命令只删除匹配上的 `details`。证据绝不会被改写。每次更新前，命令会在一个短事务里加锁并重新核对记录，比对当前快照、引用、任务/目标终态和截止时间。数据库更新采用 compare-and-swap，遇到并发改动就跳过而不是覆盖。这里刻意绕过常规的模型保存不可变守卫，且仅限显式请求的这项维护。

请在 Web/Worker 停止后的维护窗口内执行压缩。归档会在每个目标的事务开始前完整落盘；压缩被打断会留下一个部分压缩的批次，但完整原始归档仍然保留。尾部只证明归档完成，不代表压缩完成；请查看命令摘要中的 `compacted`。出错时保留归档并与数据库核对，改用新路径重试。绝不能用不完整的归档充当备份凭据。系统没有自动恢复/导入命令。恢复原始快照需要用归档和当前记录标识做一次单独评审的操作，不要覆盖并发写入的数据。压缩能减少 JSON 存储量，但不一定会让 SQLite 文件在磁盘上变小。

### 拓扑证据保留

`archive_task_results` 还会报告超过 90 天的拓扑观测。预览仍然是只读的。`--apply` 在成功创建请求的任务归档之后，按短批次有序删除过期的 `NetworkTopologyObservation` 行；发现批次、当前接口和链路保留。`--topology-evidence-days` 取值 1 到 3650，`--batch-size` 取值 1 到 10000。默认分别是 90 天和 500 行。

```powershell
# 预览归档候选与过期拓扑观测数量。
.venv/Scripts/python.exe manage.py archive_task_results --before '2026-08-01T00:00:00+08:00' --limit 100 --topology-evidence-days 90 --batch-size 500

# 先归档符合条件的任务结果，再清理过期拓扑观测。
.venv/Scripts/python.exe manage.py archive_task_results --before '2026-08-01T00:00:00+08:00' --limit 100 --apply --output 'D:/Backups/net/results-001.jsonl' --topology-evidence-days 90 --batch-size 500
```

摘要字段 `topology_observations` 是预览数量或实际删除数量。无效的保留取值会被拒绝。运行本命令时要用与 Web、Worker 相同的数据库配置；不要误把维护指向复制出来的或另一个数据库。

### 验证

专项测试使用独立的 Django 测试库和临时 SQLite 文件运行：

```powershell
$env:DB_ENGINE = 'sqlite'
$env:NET_SQLITE_WAL_ENABLED = 'false'
.venv/Scripts/python.exe manage.py test tests.system.test_backend_maintenance --noinput
```

WAL 只在一次性文件上测试，绝不会作用于已配置的服务库。

---

## 查询与执行优化现状

本次保留原有启动入口及数据库选择规则，不修改 API 凭据，不执行真实巡检。

### 查询与展示

- 任务历史先在数据库分页，再汇总当前页任务的正常、异常、取消和待处理数量。
- 总体统计由数据库聚合，保留原有计算口径：取消和等待目标不计入已完成故障率。
- 记录增加轻量报表字段，保存时生成指标摘要、问题类型、等级和人员关联摘要。
- 列表查询不再读取原始日志 payload、完整分析 details/exceptions 或设备 raw_output。
- 筛选和排序在分页前执行；下拉选项独立于当前页；导出仍包含全部筛选结果。
- PC 按人员匹配使用混合分页：人员快照和未匹配提示行保留在内存，真实分析记录由 SQL 筛选、排序和切片读取。筛掉已有日志不会伪造一条“未匹配日志”；同一人员的多份日志仍分别显示。
- 管理后台通过 save 更新记录时会更新报表字段。维护代码使用 QuerySet.update/bulk_update 修改证据时，必须同步更新报表投影，不能绕过这一约定。

### 分析、配置和采集

- PC 分析准备阶段移到事务和 SQLite 进程锁之外；保存前重新检查租约、取消状态和执行次数。
- PC 和设备巡检保存完成、事务提交前再次检查租约；失效则回滚记录及进度。相关执行路径统一先锁任务、再锁目标，减少与取消操作的锁顺序冲突。
- 问题等级和阈值一次读取；软件策略解析内容及 SHA256 固定在任务快照中，同一批使用相同策略。
- 配置弹窗支持版本冲突提示，保存成功后刷新版本，可继续保存。
- 新任务结果只保存版本、结果引用和摘要；完整证据保留在分析/巡检记录中。历史旧快照继续可读。
- SNMP 流量采样在同一次巡检中复用接口名称，优先使用 64 位计数器，对缺失项回退；带宽、重启与计数器重置仍重新检查。

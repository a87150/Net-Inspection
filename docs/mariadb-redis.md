# MariaDB 与 Redis

## 当前本机配置

根目录 `.env` 是 Web、Worker、manage.py 的共享配置，已被 Git 忽略。进程变量优先；`NET_ENV_FILE` 可选择其他文件。

- MariaDB：`127.0.0.1:3306/net_inspection`，utf8mb4_bin，专用 `net_app` 账号仅拥有本项目库权限。
- 密码：Windows 凭据管理器服务 `net-inspection-mariadb`、用户名 `net_app`。配置文件不保存数据库密码；root 仅用于建库授权。
- 原 PC 加密密钥通过 `PC_LOG_SOURCE_ENCRYPTION_KEY_FILE` 读取，不能丢失或随意替换。
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

MariaDB 的 `0040` 迁移用生成列唯一索引补齐域控活动目标的条件唯一约束。Django 仍可能提示原条件约束不受支持，但替代索引已在数据库生效；不能跳过该迁移。长域 DN 唯一字段也可能触发通用长度警告，应核对实际索引，不截断域路径。

## 回退

原 `demo-runtime/demo.sqlite3` 和 `demo-runtime/backups/mariadb-20260908/source.sqlite3` 均保留。回退前停止 Web/Worker，使用独立环境文件指定 `DB_ENGINE=sqlite`、明确旧库路径并关闭页面缓存，然后启动。密钥保持原值。

**旧 SQLite 只是切换时快照，不含切换后 MariaDB 新数据。** 若已有新数据，先备份 MariaDB 并制定反向迁移，不能直接切旧库冒充无损回退。备份和 JSON 导出含隐私及配置凭据，只能保存在受保护的本机目录，不要提交 Git 或公开分享。

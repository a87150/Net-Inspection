# 网络巡检中心

Django 网络与终端监控应用：PowerShell 上报计算机日志，后端做规则**分析**；网络设备、服务器和安防设备做协议**巡检**。包含资产、记录/异常详情、任务队列、定时计划、告警和人员目录预览确认。

源码按业务域组织，新增功能和测试的放置规则见 [项目目录说明](docs/project-layout.md)。

## 首次体验：独立持久的本地演示

要求 Python **3.12+**（启动时强制检查），本机已验证 `.venv` 的 **3.12.13**。现有环境也须重新安装 `requirements.lock.txt`（包括 Waitress、WhiteNoise 和新版原生 Dahua 依赖）。`requirements.txt` 记录直接依赖，锁文件固定直接及间接依赖，部署统一使用锁文件。

Windows，在本项目目录：

```powershell
# 没有虚拟环境时：py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m deploy.demo
```

Linux：

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.lock.txt
./.venv/bin/python -m deploy.demo
```

打开 `http://127.0.0.1:8000/`，Ctrl+C 停止。`--port 8766` 可换端口；`--prepare-only` 仅准备数据和静态文件。
启动器明确使用 `demo-runtime/demo.sqlite3`，自动迁移、首次离线 seed、collectstatic，然后用 **DEBUG=False + Waitress + WhiteNoise** 启动 Web。原始 `db.sqlite3` 不动，也不继承 MySQL/旧 SQLite 环境路径。再次启动保留演示编辑，不自动重置。`--runtime-dir` 可指定另一个空目录；非空且无演示所有权标记的目录会被拒绝。

持久演示：7 人（两种目录各有在职/停用人员）、3 计算机、每类基础设施各 3 台、域账号/域计算机/域分组各 3、2 个停用目录来源、2 个停用计划、10 个覆盖全部状态的任务（含 2 个离线、非敏感且终态的域操作审计：移动 OU/启用）、4 个分析、异常/恢复事件及成功/失败投递、3 份可下载脱敏配置。地址为 `.invalid`/文档保留 IP，凭据为 `DEMO-ONLY-NOT-A-SECRET`，目录来源及告警渠道停用。

等待/运行中任务是远期停放的离线展示，不是真实 Worker。启动器**不启动 Worker**；不要把真实 Worker 指向演示库。演示中的手动任务只会排队，验收使用 fixture/mock，真实采集需在配置好的生产环境执行。

需要重建 seed，先停演示 Web 并显式指定演示库：

```powershell
$env:NET_DATABASE_PATH=(Join-Path (Get-Location) 'demo-runtime/demo.sqlite3')
$env:DB_ENGINE='sqlite'                 # 强制使用 SQLite
$env:DJANGO_SQLITE_PATH=$env:NET_DATABASE_PATH
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo_data --reset
```

Linux：

```bash
export NET_DATABASE_PATH="$PWD/demo-runtime/demo.sqlite3"
export DB_ENGINE=sqlite
export DJANGO_SQLITE_PATH="$NET_DATABASE_PATH"
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py seed_demo_data --reset
```

`NET_DATABASE_PATH` 是操作员保存演示数据库位置的变量；Django 目前读取 `DJANGO_SQLITE_PATH`，因此上述命令明确把前者映射给后者。`--reset` 按依赖顺序只重置内置演示身份，保留无关行，所有权冲突/非演示引用会拒绝破坏性重置。普通 manage.py 默认仍使用旧 `db.sqlite3`；不要漏数据库环境变量，更不要对旧库误跑迁移。

## 正式部署：Web 与 Worker 两个服务

生产使用 MySQL 8 / utf8mb4、专用低权限账号；两个服务使用同一套 `DB_ENGINE=mysql`、`DB_NAME`、`DB_USER`、`DB_PASSWORD`、`DB_HOST`、`DB_PORT` 和 Django 密钥/主机设置。先 migrate，再 collectstatic，再启动两个进程。

- [Windows / NSSM 服务与环境示例](deploy/windows/README.md)
- [Linux / systemd 双服务与环境示例](deploy/linux/systemd/README.md)
- [PC 采集脚本部署、下载与端点要求](docs/deployment.md)

Web：`.venv` Python `-m waitress --listen=127.0.0.1:8000 --threads=4 net.wsgi:application`；WhiteNoise 从 `DJANGO_STATIC_ROOT` 服务 collectstatic 输出，DEBUG 必须 false，不用 runserver。
Worker：`.venv` Python `manage.py run_task_worker --threads 4 --poll-seconds 5 --lease-seconds 60`；页面“手动执行巡检”“手动执行分析”创建数据库任务，独立 Worker 做采集、扫描、分析和告警，受全局和任务并发上限约束。

域账号/计算机写操作也由此独立 Worker 执行；Web 与 Worker 必须共享生产数据库和 `DOMAIN_OPERATION_ENCRYPTION_KEY`。权限、Fernet 密钥、LDAPS、Windows/Linux Worker 及真实 AD 测试 OU 的上线 smoke test 请见 [域操作部署说明](docs/deployment.md#域控操作)。演示库只含离线的 OU 移动和启用审计示例，绝不含密码任务或真实 LDAP 连接。

服务示例包含可写日志/归档目录、停止窗口、重启及租约恢复。`--once` 会安排计划、执行任务/处理告警，**不是被动健康检查**。被动检查用 manage.py check、服务状态和本机 Web/CSS/JS GET。
本次验证本机 Windows Waitress/WhiteNoise 和 fixture Worker；Linux、MySQL、系统服务注册均仅提供示例，未在桌面安装或部署。
当前应用无全站访问授权；生产必须前置认证与 HTTPS、限制网络访问、保护凭据和日志。DEBUG=False 不等于可以直接公开暴露。

### PC 采集脚本

在 PC 的“分析配置”中选择已保存的配置后，可下载该 profile 绑定的 Windows PowerShell 或 macOS Shell 脚本。下载端点要求登录、仅允许 GET，并返回 `Cache-Control: no-store`；脚本含该 profile 的扫描路径和 UUID，不能作为可公开或可缓存的静态文件分发。生产优先设置 `NET_PUBLIC_BASE_URL=https://公开主机`，使终端不依赖反向代理看到的内部 scheme/host。`NET_TRUST_PROXY_HEADERS` 默认关闭；仅当受控反向代理会重写 forwarded host/proto、且 `DJANGO_ALLOWED_HOSTS` 正确限制这些公开主机时才开启。详见 [部署说明](docs/deployment.md)。

## 页面工作流

列表支持紧凑筛选、更多筛选、自定义列/筛选项、排序、每页数量和偏好持久化。“导出筛选结果”跨分页遵循筛选/排序，**不是全部数据导出**。CSV 为 UTF-8 BOM，密码/令牌不输出。服务器/安防公开 URL 只显示协议、主机、路径，省略 userinfo、所有查询参数及片段，不提供 URL 筛选建议；存储 URL 和采集快照不变。

人员、网络设备、服务器、安防设备支持 CSV 模板/导入，按主键新增更新，错误整批拒绝。普通计算机由 PowerShell 上报，无资产导入；域账号/计算机由域控同步。计算机页面用“分析日志”“分析配置”“手动执行分析”，基础设施用“巡检记录”“巡检配置”“手动执行巡检”。

### 资产看板与状态含义

面向操作员的“计算机”统一显示为 **PC**：首页卡片、列表、详情和操作按钮均使用 PC 文案；内部 URL、数据表键和 PowerShell/API 兼容名称仍是 `computers`，因此现有上报端无需改名。PC 的“分析”与网络设备、服务器、安防设备的“巡检”是不同采集流程，页面按钮会据此分别显示“手动执行分析”或“手动执行巡检”。

首页资产卡片只看**每台资产最新的一条结果**，不会把旧成功覆盖新失败。PC 最新分析为成功且没有关联异常时为“正常”；没有任何分析是“未分析”；其余结果为“异常”。基础设施最新巡检必须同时成功、可达且没有关联异常才是“正常”；从未巡检为“未巡检”，失败、部分成功、不可达或存在关联异常均为“异常”。只要仍有未分析/未巡检资产，卡片就显示对应数量。卡片的“上次分析/巡检日期”是该资产类别全部最新结果中的最晚时间，不代表每台资产都在该时间执行；单个类别查询失败时，该卡片显示错误状态，不会把错误伪装成零或影响其他卡片。

资产模型保存适合长期盘点的**静态**字段，例如厂商、型号、序列号、CPU 型号、物理核心数、逻辑处理器数、内存总量、磁盘总量与磁盘摘要，以及网络设备的端口/VLAN 数量；人员还保存入职/离职日期。采集结果中的 CPU、内存、磁盘利用率、连通性、服务状态和异常属于**动态**记录，不能从资产静态字段推断实时健康状态。PC 列表/导出不显示登录账号或启用状态，PC 仍只通过采集上传维护，不开放 CSV 导入。

首页“巡检任务栏”合并显示巡检、PC 扫描和 PC 分析任务，并单独按 `task_page` 每页显示 10 条；人员和域控任务不在其中。翻页链接保留 URL 中其他查询参数，只替换 `task_page`，因此资产卡片仍会在每个任务页显示。每行同时显示创建时间和完成时间，未完成任务的完成时间为“—”。“详情”进入对应任务；目标、正常、异常、待处理、已取消和扫描成功计数守恒。历史数据若存在任务目标总数与目标明细不一致，页面按不小于实际明细数的总数展示，并给出可见的完整性警告。

“分析配置”中的扫描、成功/失败归档目录是数据库配置项（非环境变量），须给 Worker 账号目录权限。使用扫描根 `incoming` 及其内部的 `incoming/processed`、`incoming/failed`；归档目录自动排除扫描，必须同文件系统以支持不覆盖的原子移动。不要放入 staticfiles。支持最近 N 天/指定起止日期、间隔/每日计划；种子计划停用，日期按 Asia/Shanghai。

“导入日志证据”可查看解析失败及归档状态，从已导入日志详情选择配置/项目重新分析；只入队，不重新读取或移动文件，历史保留。扫描在导入提交时记录本任务的分析意图，重领只恢复这些日志，不分析所有历史归档。任务重试仍受租约次数上限约束；耗尽后显示失败，操作员可从证据详情重新分析。

巡检配置可选择定时目标：全部、指定设备、多字段精确匹配（非空条件同时满足）。仅允许公开字段；删除/错误类型 ID 会拒绝。编辑目标不会修改已创建任务。更改计划时间、间隔、类型或重新启用时，按保存时刻计算严格晚于当前时间的下一次执行；不补发停用期间的过期时点，普通名称/项目编辑不重置时点。

CSV 新增及兼容的手工/CSV 更新标记 `source=csv`，清除不适用的平台同步元数据；拒绝改动任何供应商来源人员，整批验证成功才写入。目录预览仍使用全人员指纹/锁（M01 延后），无关人员编辑也可能使预览失效。

### 飞书 / 钉钉 API 导入

人员 → “导入”/“API 导入” → 对应平台页签，选择或保存独立来源，稳定 source_key 不含秘密，填写根部门和只写凭据。编辑时空白凭据保留原值，不回显。
“测试连接”“生成同步预览”都由 Worker 执行；预览展示新增/更新/停用差异，**明确确认后**才应用已保存差异。只停用同一来源中缺失的人，不停用其他来源。
预览绑定发起浏览器会话，签名有效期 **300 秒**；过期、会话丢失、凭据/范围或相关数据变化后重新预览，重复应用会拒绝。演示来源默认停用，真实环境的测试/预览按钮会联系供应商。

### 告警

飞书、钉钉、邮件渠道支持默认/项目覆盖策略、异常与恢复事件、投递结果和失败重试，凭据只写。演示渠道停用，投递结果是离线样例。测试发送会真实发送；本次浏览器只验证停用渠道的阻止行为。是否成功以投递详情为准。

入队时从权威 AlertPolicy 冻结继承/覆盖模式及非秘密渠道 ID；后续成员/模式编辑不会改道旧任务，扫描子任务保留父任务路由及有效并发。凭据发送时读取最新值，渠道停用/撤销是故意保留的安全否决。不会把凭据复制进任务快照。

## 协议和配置导出边界

Linux/网络设备使用 SSH，Windows 服务器用独立 HTTP JSON 服务，安防设备用 API；ICMP 仅辅助。区分成功、部分成功、失败/不可达。只保存本次选中项目及**脱敏后的原始输出**，不承诺完整原始回显。
配置下载仅使用最近成功的已保存配置项，**下载不连接设备**。ZIP 遵循资产筛选，manifest 列明成功、未采集、失败、不支持。

| 原生配置支持 | 精确范围 |
| --- | --- |
| Cisco / 思科 IOS | `show running-config` 运行配置 |
| H3C / 华三 | `display current-configuration` 当前配置 |
| Dahua / 大华 | 仅 `Network` 具名可读配置节 |

均为只读脱敏快照，**不是完整恢复备份**（不含全部文件/证书/密钥）。未知厂商/范围、不完整输出、二进制或加密配置拒绝导出；设备状态/版本不冒充备份。
应用最低 Python 3.12；原生 Dahua 传输 API 自身的下限为 3.11，不代表应用支持 3.11。依赖 `aiohttp>=3.12.15,<4`、`dnspython>=2.8,<3`；主机名使用配置的 DNS 服务器，不使用 hosts/NSS/mDNS；数字 IP 直接使用，`trust_env=False`，不继承代理环境。其他 HTTP 采集仍基于 Requests。须安装更新后的 requirements，不是所有采集均转为 aiohttp。

### 终端、Windows Agent、域控与 API

将 `agents/pc/windows/GetInfo_JSON.ps1` 与同目录的 `OpenHardwareMonitorLib.dll` 部署到 PC，以管理员运行脚本。`NET_INSPECTION_API_URL` 指定上传地址，默认 `http://127.0.0.1:8000/api/computer_inspection/`；失败仍保存 `C:\Software\计算机名.json` 并尝试原共享目录。`MIN_WINDOWS_RELEASE` 默认 23H2。
Windows 服务器独立 JSON 服务见 [Windows Agent](agents/server/windows/README.md)，令牌须与资产配置一致，地址留空默认 `http://服务器IP:9180/inspection`。
“域控管理”配置 LDAP/LDAPS、Base DN 和只读账号，连接测试/同步会访问实际目录，缺失对象停用不删除；本次只浏览界面，不执行真实目录调用。
`POST /api/computer_inspection/` 返回 **202**（已接收/分析排队），包含 `log_id`、`task_id`、`created`；不是分析结果。相同载荷重试返回 **200** 和原任务回执，不新增分析。任务详情 `/tasks/<task_id>/` 查看结果。上传地址可带 `?profile_id=<分析配置UUID>`；或 Web 设置 `COMPUTER_UPLOAD_PROFILE_ID`。未指定时选最早的启用配置；没有启用配置时自动建立可在页面修改的“PowerShell 上传默认分析”（激活、BitLocker、Defender、补丁），避免搁置上传。指定不存在/停用配置返回 400 且不写入。上传与目录导入使用相同静态提取、脱敏证据、Worker 分析及告警路径。

仅上传/既有证据重新分析的配置可留空扫描目录；开启定时扫描必须填写目录。并发上传冲突会回滚整次导入/入队事务后有限重试，不返回未提交任务的回执。

人员数据使用 CSV 或来源隔离的预览/确认同步；旧的 `/api/upload_people/` 接口已移除。

## 验证

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
node --test tests/frontend/*.test.js
powershell -NoProfile -ExecutionPolicy Bypass -File tests/agents/test_windows_server_agent.ps1
```

使用临时测试库与模拟外部依赖。静态回归实际 collectstatic 并在 DEBUG=False 检查 CSS/JS 内容；启动器回归验证新库、重启保留编辑、拒绝无所有权目录及原始库不变。

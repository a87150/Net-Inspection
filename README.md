# 网络巡检中心（net）

基于 Django 的内网资产管理、设备巡检与 PC 日志分析系统。集中管理人员、PC、网络设备、服务器、安防设备和 Active Directory 目录，通过网页配置、手动任务和定时任务完成采集、分析、记录查询、数据导入导出及异常/恢复通知。

本项目的重点是“资产台账 + 周期性巡检 + 有历史证据的规则分析”，不是持续秒级采样的时序监控平台，也不是自动修复或 AI 分析系统。

> 当前 PC 流程：终端生成 JSON → 写入统一共享汇总目录 → Worker 通过 SMB 或 FTP/FTPS 获取 → 入库归档 → 分析。PC 不再直接上传到 Web API；Windows 服务器的 HTTP 巡检服务是另一套独立功能。

## 目录

- [1. 功能概览](#1-功能概览)
- [2. 技术栈与第三方库](#2-技术栈与第三方库)
- [3. 项目自己实现了什么](#3-项目自己实现了什么)
- [4. 快速启动与管理员账号](#4-快速启动与管理员账号)
- [5. 正式部署与环境配置](#5-正式部署与环境配置)
- [6. 页面和数据操作](#6-页面和数据操作)
- [7. PC 共享日志获取与分析](#7-pc-共享日志获取与分析)
- [8. 网络设备、服务器和安防巡检](#8-网络设备服务器和安防巡检)
- [9. 人员 API 同步与域控管理](#9-人员-api-同步与域控管理)
- [10. 后台任务、定时和告警逻辑](#10-后台任务定时和告警逻辑)
- [11. 代码目录与扩展方式](#11-代码目录与扩展方式)
- [12. 日常维护、验证与排障](#12-日常维护验证与排障)
- [13. 已知边界](#13-已知边界)

## 1. 功能概览

| 模块 | 主要能力 | 数据来源 |
| --- | --- | --- |
| 首页 | 分类资产统计、正常/异常状态、上次执行时间、最近任务及详情入口 | 本地资产及任务结果 |
| 人员 | 列表、个人详情、在职/离职统计、平均在职年限、入离职日期、导入导出 | CSV/XLSX、飞书、钉钉 |
| PC | 配置台账、日志证据、规则分析、每日去重、Windows/macOS 采集脚本下载 | 统一共享目录中的 JSON |
| 网络设备 | SSH/SNMP 采集、指标和接口/VLAN 信息、日志、有限范围配置快照 | 设备管理接口 |
| 服务器 | Linux SSH 巡检、Windows HTTP JSON 巡检、硬件与系统信息 | SSH 或项目提供的 Windows 服务 |
| 安防设备 | 摄像头、录像机、门禁闸机等台账；设备/通道/存储状态；支持范围内的配置导出 | 厂商自带 HTTP API |
| 域控管理 | 域账号、域计算机、域分组同步及详情；账号/计算机批量操作 | LDAP/LDAPS |
| 后台任务 | 入队、目标明细、进度、租约恢复、手动结束、多线程执行 | 数据库任务队列 |
| 定时计划 | 每隔 N 分钟/小时、每天指定时间；项目与目标选择 | 页面保存的配置 |
| 告警 | 飞书、钉钉、邮件；默认/项目策略；异常与恢复通知、投递记录和重试 | 已持久化的巡检/分析结果 |
| 表格工具 | 筛选、可输入下拉选项、排序、自定义列、每页数量、筛选结果导出 | 统一表格定义 |
| 管理后台 | 基础数据维护、配置管理、任务和历史记录查询 | Django Admin |

人员、域账号和 Django 登录账号是三类不同对象：

- **人员**是业务台账，以工号识别。
- **域账号**是从 Active Directory 同步的目录对象。
- **Django 用户**用于登录管理后台和执行获授权操作。人员导入、域同步不会自动创建 Django 管理员。

## 2. 技术栈与第三方库

以下是仓库当前声明的直接依赖版本，不代表对上游“最新版本”的实时查询。完整依赖见 [requirements.lock.txt](requirements.lock.txt)，直接依赖见 [requirements.txt](requirements.txt)。

| 库 / 组件 | 当前版本 | 在本项目中的作用 |
| --- | --- | --- |
| Python | 最低 3.12 | Web、业务逻辑、Worker；启动时检查最低版本 |
| Django | 6.1.1 | 路由、模板、表单校验、ORM、事务、迁移、认证、权限、Admin |
| Django REST framework | 3.18.0 | 依赖仍保留，现有测试使用 APIClient；不是新版 PC 日志入口 |
| ldap3 | 2.9.1 | 域控连接、查询、目录同步和获授权的 AD 写操作 |
| cryptography | 50.0.1 | PC 来源凭据与域密码任务的 Fernet 加密等基础能力 |
| mysqlclient | 2.2.8 | Django 的 MySQL 数据库驱动 |
| requests | 2.34.2 | 人员平台、通用设备 HTTP 采集及部分消息投递 |
| aiohttp | 3.14.3 | 原生安防配置传输中的异步 HTTP，并非所有请求都使用它 |
| dnspython | 2.8.0 | 原生 HTTP 传输的 DNS 解析 |
| paramiko | 5.0.0 | Linux 和网络设备 SSH 连接、命令执行及回显读取 |
| pysnmp | 7.1.29 | SNMPv2c/v3 GET、BULK WALK 等只读采集 |
| smbprotocol | 1.17.0 | 通过其 smbclient 接口访问 SMB 共享目录 |
| waitress | 3.0.2 | Windows/Linux 通用 WSGI Web 服务 |
| whitenoise | 6.12.0 | DEBUG=False 时提供 collectstatic 生成的静态资源 |
| Bootstrap | 仓库内置静态文件 | 响应式布局、弹窗、表单和基础组件 |

另外使用 Python 标准库：

- concurrent.futures、threading：有上限的线程池、心跳与停止信号。
- ftplib、ssl：FTP/显式 FTPS 连接与传输。
- csv、zipfile、xml.etree：CSV、轻量 XLSX 和配置 ZIP。
- json、hashlib、configparser：日志解析、哈希去重和软件规则 INI。
- smtplib、email 等：邮件发送与消息构造。

前端是 **Django 服务端模板 + Bootstrap + 原生 JavaScript/CSS**，不是 React/Vue 单页应用；正常启动不需要 npm 构建。Node.js 用于运行前端 JavaScript 测试，不是 Web/Worker 的运行依赖。

Windows PC 采集使用 PowerShell，macOS 使用 shell、Python 3 和系统工具。仓库保留了 agents/pc/windows/OpenHardwareMonitorLib.dll，但能否取得硬件数据取决于实际脚本、传感器和权限，不能仅凭存在 DLL 就保证温度可用。

## 3. 项目自己实现了什么

第三方库负责框架、通信和底层协议；以下业务能力由项目代码实现：

| 自实现能力 | 说明 | 核心代码 |
| --- | --- | --- |
| 统一资产与历史模型 | 静态台账、动态记录、异常、任务、来源与配置分离 | [net/models](net/models) |
| 数据库任务队列 | 任务/目标快照、领取互斥、续租、超时恢复、取消和状态汇总 | [net/inspections](net/inspections) |
| 周期调度 | 间隔/每日计划、目标筛选、到期入队；不依赖 Celery/Redis/APScheduler | [schedules.py](net/inspections/schedules.py) |
| 协议适配与解析 | 厂商命令表、SSH 回显、SNMP OID 映射、HTTP 字段校验及结果归一化 | [net/devices](net/devices)、[net/infrastructure](net/infrastructure) |
| PC 日志流水线 | 远程发现、稳定下载、JSON 校验、每日去重、归档记录、恢复和分析交接 | [remote_ingestion.py](net/devices/pc/remote_ingestion.py) |
| PC 规则分析 | 利用并升级原有规则思路；按所选项目与阈值生成详情、异常及上下文快照 | [analysis.py](net/devices/pc/analysis.py)、[checks.py](net/devices/pc/checks.py) |
| 人员同步 | 平台分页/部门遍历、工号映射、预览差异、来源隔离、事务应用 | [net/people](net/people) |
| 域控业务操作 | DN 范围校验、操作白名单、批量目标、一次性密码任务及审计 | [net/domain](net/domain) |
| 通用表格 | 字段注册、筛选/排序、浏览器偏好、分页、同规则 CSV 导出 | [index/common](index/common) |
| 数据导入 | 中文模板、示例、类型转换、行号/字段错误、整批校验及新增更新 | [net/data_exchange](net/data_exchange) |
| 轻量 XLSX 读写 | 基于 ZIP/XML 读取表格、生成模板；不依赖 pandas/openpyxl，不是完整 Excel 引擎 | [xlsx.py](net/data_exchange/xlsx.py) |
| 告警业务 | 发现项归一化、异常/恢复状态、路由快照、投递队列与重试 | [net/alerts](net/alerts) |
| 演示启动器 | 隔离持久库、迁移、演示数据、静态收集、Web/Worker 生命周期 | [deploy/demo.py](deploy/demo.py) |

## 4. 快速启动与管理员账号

以下命令均在项目根目录执行。已有 .venv 时跳过创建步骤，不要重新覆盖环境。

### 4.1 Windows

推荐使用仓库锁文件对应的 Python 3.12 环境：

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m deploy.demo
~~~

### 4.2 Linux

~~~bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.lock.txt
./.venv/bin/python -m deploy.demo
~~~

如果 mysqlclient 等需要本机编译，先安装对应系统的编译工具、Python 开发头文件及 MySQL/MariaDB 客户端开发库。锁文件包含 MySQL 驱动，即使演示使用 SQLite，也会安装它。

打开 http://127.0.0.1:8000/ 。Ctrl+C 停止演示 Web，并由启动器结束其 Worker 子进程。

### 4.3 演示启动器会做什么

1. 检查 Python 版本和运行目录所有权。
2. 强制使用 demo-runtime/demo.sqlite3，不使用根目录 db.sqlite3 或继承的 MySQL 配置。
3. 执行迁移；首次初始化离线演示数据，之后保留页面编辑。
4. 执行 collectstatic，输出到 demo-runtime/staticfiles。
5. 使用 DEBUG=False + Waitress + WhiteNoise 提供网页。
6. 默认启动连接同一演示库的独立 Worker，默认 4 线程。
7. 未通过环境注入 PC 密钥时，在运行目录生成并复用 .pc-log-source.key。

| 参数 | 用途 |
| --- | --- |
| --port 8766 | 更换本地 Web 端口 |
| --prepare-only | 仅准备数据库、演示数据和静态文件 |
| --no-worker | 只看页面，不执行新提交的后台任务 |
| --runtime-dir <目录> | 使用另一套演示数据；非空且无所有权标记的目录会被拒绝 |

演示包含人员、各类设备、域对象、历史任务、异常/恢复、投递及脱敏配置样例。演示集成与计划默认停用，预置等待任务不会正常到期执行；**自己在页面提交的新任务会执行真实连接**。仅需离线查看时加 --no-worker。

不要删除 .seeded、所有权标记或密钥文件来尝试“修复启动”。演示不是正式部署方式，也不会因重复启动自动重置数据。

### 4.4 在正确的数据库创建管理员

启动器不保证创建固定的 admin/admin 账号。管理员存在于数据库，不存在于代码文件里；请为实际使用的库创建账号。

在另一个 PowerShell 窗口中，为默认演示库创建管理员：

~~~powershell
$env:DB_ENGINE = 'sqlite'
$env:DJANGO_SQLITE_PATH = Join-Path (Get-Location) 'demo-runtime/demo.sqlite3'
.\.venv\Scripts\python.exe manage.py createsuperuser
~~~

Linux：

~~~bash
export DB_ENGINE=sqlite
export DJANGO_SQLITE_PATH="$PWD/demo-runtime/demo.sqlite3"
./.venv/bin/python manage.py createsuperuser
~~~

访问 /admin/ 登录。忘记密码时，在同样的数据库环境下执行：

~~~powershell
.\.venv\Scripts\python.exe manage.py changepassword admin
~~~

使用自定义 --runtime-dir 时，将数据库路径改为该目录下的 demo.sqlite3。普通 manage.py 不会自动切换演示库；未配置时使用根目录 db.sqlite3。上述变量仅作用于当前 shell，之后切换正式库时应重新加载正式配置。

## 5. 正式部署与环境配置

部署形态是 **同一数据库 + Web 服务 + 独立 Worker 服务**。生产可使用 MySQL/utf8mb4；SQLite 适合演示和小规模开发，不应据此推断高并发生产表现。

~~~text
浏览器 ──HTTP──> Django / Waitress ──读写──> 数据库
                                            ↑
                                 Worker 领取任务并保存结果
                                            │
                         SSH / SNMP / HTTP / LDAP / SMB / FTP
                                            │
                             设备、目录平台、汇总共享文件夹
~~~

### 5.1 环境变量

以 [net/settings.py](net/settings.py) 为准：

| 变量 | 用途 / 默认值 |
| --- | --- |
| DJANGO_SETTINGS_MODULE | 管理命令和 WSGI 使用 net.settings |
| DJANGO_DEBUG | 默认 true；正式部署设 false |
| DJANGO_SECRET_KEY | Django 签名密钥；正式部署替换开发默认值 |
| DJANGO_ALLOWED_HOSTS | 逗号分隔主机名；默认 127.0.0.1,localhost |
| DB_ENGINE | 默认 sqlite；使用 MySQL 时填 mysql |
| DJANGO_SQLITE_PATH | SQLite 路径；未指定时为根目录 db.sqlite3 |
| DB_NAME、DB_USER、DB_PASSWORD、DB_HOST、DB_PORT | MySQL 数据库、账号及连接参数 |
| DJANGO_STATIC_ROOT | collectstatic 输出目录；默认根目录 staticfiles |
| PC_LOG_SOURCE_ENCRYPTION_KEY | PC 来源密码的稳定 Fernet 密钥；Web/Worker 必须相同 |
| DOMAIN_OPERATION_ENCRYPTION_KEY | 域密码操作的稳定 Fernet 密钥；Web/Worker 必须相同 |
| NET_TRUST_PROXY_HEADERS | 默认 false；仅在受控代理会重写转发头时启用 |

项目时区目前为 Asia/Shanghai，影响每日计划和文件日期筛选。Web/Worker 必须使用相同设置。项目没有自动读取 .env 的加载器；需由 shell、服务管理器或部署流程注入环境变量。

生成 Fernet 密钥的命令如下；为两种用途分别生成、妥善保存，不要每次启动重新生成：

~~~powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
~~~

Linux 将解释器换成 ./.venv/bin/python。密钥与数据库分别备份，不提交 Git。演示启动器只自动处理 PC 来源密钥，不自动提供域密码任务密钥。

### 5.2 初始化和启动

先创建数据库、设置上述正式环境，确认当前指向的库，再执行：

~~~powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe manage.py check
~~~

Web 服务：

~~~powershell
.\.venv\Scripts\python.exe -m waitress --listen=127.0.0.1:8000 --threads=4 net.wsgi:application
~~~

另一个进程运行 Worker，加载相同数据库、密钥和环境：

~~~powershell
.\.venv\Scripts\python.exe manage.py run_task_worker --threads 4 --poll-seconds 5 --lease-seconds 60
~~~

Linux 支持相同参数，将解释器替换为 ./.venv/bin/python。不要将 runserver 用作正式服务。

长期运行服务示例：

- [Windows / NSSM 双服务](deploy/windows/README.md)
- [Linux / systemd 双服务](deploy/linux/systemd/README.md)
- [协议、PC 脚本及域操作部署补充](docs/deployment.md)

示例路径和账号需按实际环境替换，并补充两个加密密钥。WEB_THREADS、WORKER_THREADS 等是服务示例使用的参数变量，并非 Django 自动读取的通用设置；直接执行时以命令行参数为准。

正式服务应前置 HTTPS、访问认证或内网访问控制，并限制设备管理网入口。当前没有完整的全站访问授权；DEBUG=False 不等于可以直接暴露公网。某些凭据仅做界面隐藏/输出脱敏，不是所有数据库秘密字段都已加密。

## 6. 页面和数据操作

### 6.1 资产列表与记录分开

- 首页分别进入人员统计/列表、PC 列表/日志分析记录，以及各设备列表/巡检记录。
- 静态台账保存名称、IP、MAC、系统版本、厂商、型号、CPU 型号、内存/磁盘容量等适用字段；网络设备还有端口与 VLAN 数量。
- 动态 CPU/内存占用、接口状态、服务和异常放在每次记录中，不作为实时资产属性。
- PC 列表不显示磁盘摘要列；服务器台账有较完整的硬件和系统字段。
- PC 与域计算机独立：前者来自日志，后者来自 AD 同步。

首页状态以**每台设备最新一次结果**为准。PC 无异常的成功分析为正常；基础设施还要求可达。未执行过显示未分析/未巡检，不能算正常。上次日期是该类别结果的最新时间，并不代表所有设备同时执行。

### 6.2 统计、任务与记录

- 首页巡检任务栏展示基础设施巡检、PC 获取和分析任务，每页 10 条。
- 分类记录页上部展示任务统计和最近 7 次任务，可翻页查看历史。
- 下部默认展示**最新那一次任务关联的记录**，不是每台设备各取最新结果后混合。
- 任务详情显示目标数量、正常/异常/待处理/已取消等，再进入逐目标记录。
- 整体故障率为：异常目标数 /（正常目标数 + 异常目标数）× 100%；未完成和取消目标不计入分母。它不是“失败任务次数 / 总任务次数”。

### 6.3 筛选、排序与导出

表格按已注册字段筛选，提供可选值与手动输入；可以选择显示列和启用的筛选项。偏好保存在**当前浏览器**，不做跨浏览器同步。

每页可选 20 / 50 / 100 / 200 / 500，选择框位于总条数和当前显示范围附近。排序、筛选和分页共用查询规则。

“导出筛选结果”位于筛选工具中，导出符合条件的跨页数据，遵循筛选和排序，不只是当前页。通用导出为 **CSV（UTF-8 BOM）**；不要把“支持 XLSX 导入/模板”误认为所有导出也支持 XLSX。秘密字段不列入普通表格导出。

### 6.4 文件导入

人员使用“导入人员”，网络设备、服务器、安防设备使用“导入设备”。下载当前页面的 **CSV 或 XLSX 模板**，模板有示例数据，替换示例后导入。

| 对象 | 新增/更新识别键 | 说明 |
| --- | --- | --- |
| 人员 | 工号 | 不允许为空；保存姓名、邮箱、部门、上级、在职状态和入离职日期 |
| 网络设备 | IP | SSH/SNMP 设置及硬件信息 |
| 服务器 | IP | 区分 Linux/Windows，填写对应连接方式 |
| 安防设备 | IP | 厂商、类型及 API 设置，可登记门禁闸机 |
| PC | 不提供手动台账导入 | 有效日志自动维护 |
| 域账号/计算机 | 不提供普通台账导入 | 域同步；“表格创建域用户”是独立的获授权写操作 |

人员表头示例：

~~~csv
姓名,工号,邮箱,部门,上级,是否在职,入职日期,离职日期
张三,H10001,zhangsan@example.invalid,信息技术部,李经理,是,2026-01-15,
~~~

日期建议 YYYY-MM-DD，工号按文本保存以保留前导零。XLSX 用于简单表格，不支持宏或公式计算，不要依赖合并单元格、复杂格式或多表联动；将导入表放在第一个工作表并使用日期文本。

普通导入先校验整批再写库。工号缺失、类型错误、重复键等提示行号/字段原因，整批不写入。手工导入不能覆盖飞书/钉钉来源人员，避免破坏平台同步关系。

<a id="pc-共享日志获取与分析"></a>

## 7. PC 共享日志获取与分析

### 7.1 完整流程

~~~text
Windows / macOS 采集脚本
  → 本地生成 JSON
  → 共享目录写 .uploading 临时文件，再改名 .json
  → Worker 按日期范围列举汇总目录
  → 有界并发下载、核对文件稳定性
  → 校验 JSON、更新 PC 静态台账、保存日志证据
  → 提交数据库后移入已处理/失败目录
  → 创建 PC 分析子任务
  → 按配置规则分析、保存详情和异常
  → 生成异常/恢复事件，交给告警投递
~~~

Worker 不会远程启动 PC 上的 PowerShell，也不会 SSH 登录每台 PC。“手动执行分析”触发服务器端获取与分析，**不会催促终端立即生成新日志**。终端采集周期和服务器分析周期需分别安排。

旧 /api/computer_inspection/ 已删除；不再使用旧“整理后上传 API”客户端。Windows 服务器的 /inspection HTTP 服务不受影响。

### 7.2 来源与目录配置

日常使用不需要填写下面的所有技术字段。选择“共享文件夹”，直接粘贴类似
**\\192.168.1.10\PCLogs\incoming** 的完整路径即可。默认使用运行项目的 Windows 账号权限，
不调用独立 SMB 登录、不读取保存的连接密码；Web 和 Worker 必须在有共享权限的账号下运行。
权限不足时切换“手动指定账号密码”，填写账号、域和密码后保存，再测试一次。
Linux 部署请选择手动账号模式；系统身份模式仅支持 Windows 的标准 445 端口。
路径也可以直接指向共享根目录。选择 FTP 时填服务器地址、FTP 日志目录、账号和密码。
端口、本地暂存、归档目录自动补全，文件日期范围仍直接显示；其它内容收进
默认折叠的“高级设置”。首次未指定归档位置时使用日志目录内的
_processed / _failed；已有自定义目录和密码不会因简化而重置。

在 PC“日志分析记录”的配置弹窗中，管理员保存日志来源。全系统共用一个来源；规则可使用不同分析配置。

| 字段 | 含义 |
| --- | --- |
| 协议、主机、端口、账号密码 | Worker 访问汇总服务器的方式；SMB 或 FTP/FTPS |
| 域、共享名称 | SMB 专用；共享名只填名称，不填完整 UNC |
| 远程根目录 | 相对于 SMB 共享或 FTP 服务的根位置 |
| 汇总目录 | 根目录下待获取 JSON 的相对目录 |
| 已处理目录 | 有效日志导入后的远程归档目录 |
| 失败目录 | 格式/必需字段无效日志的远程归档目录 |
| 本地暂存目录 | Worker 服务器上的绝对路径，不是终端目录 |
| Windows 终端路径 | 脚本实际写入的共享路径，例如 UNC |
| macOS 终端路径 | 已挂载共享目录下的 /Volumes/... 路径 |
| 递归读取 | 是否发现汇总目录的子目录文件 |
| 文件时间范围 | 最近 N 天或起止日期，按远程文件修改日期筛选 |

SMB 示例：

~~~text
主机：files.example.invalid
端口：445
共享名称：PCLogs
远程根目录：（留空）
汇总目录：incoming
已处理目录：processed
失败目录：failed
Worker 本地暂存目录：C:\NetInspectionData\pc-staging
Windows 终端路径：\\files.example.invalid\PCLogs\incoming
macOS 终端路径：/Volumes/PCLogs/incoming
~~~

Linux Worker 暂存目录可用 /var/lib/net-inspection/pc-staging。示例主机是假地址，使用前替换并创建目录/权限。

**“已处理”不是“分析正常”，“失败”也不是“设备异常”。** 合法 JSON 即使发现高 CPU、软件违规等异常，仍进入已处理目录；格式不合法才进入失败目录。网络/传输错误保留源文件等待重试。

归档使用移动/重命名，目录应位于同一共享或 FTP 文件系统。Worker 需要列目录、读取、建目录和重命名等权限；终端需要写临时文件及重命名权限。归档目录自动排除，不能与汇总目录形成冲突。

选择 FTP 时，其根目录和终端共享路径必须指向**同一批文件**；系统不会将 FTP 密码下发 PC，也不会替你搭建 FTP 服务。当前 FTPS 使用显式 TLS，FTP 服务需支持代码所用的目录元数据查询；普通 FTP 不加密。

### 7.3 保存、测试与预览

弹窗分为来源/目录/日期范围、规则/项目和定时设置，底部两个独立保存按钮：

1. **保存日志来源**：保存全局连接、目录和文件日期范围。
2. **测试连接**：检查访问及归档权限，会创建、移动、清理专用测试文件，不是纯只读 ping。
3. **预览日志**：列举符合条件的远程文件，不下载、不移动。
4. **保存分析与定时配置**：保存规则、阈值、并发数与执行时间。
5. **手动执行分析**：提交任务，查看获取摘要及后续分析子任务。

来源与分析分属不同表单，改两部分要分别保存。存在活动获取任务时限制来源修改，防止任务串到另一个来源。

日期范围按项目时区的**自然日**计算：最近 N 天含今天和前 N−1 天，指定范围包含起止两天；不是按文件名，也不是精确滚动 N×24 小时。每日去重使用日志日期，与文件修改日期是不同概念。

### 7.4 终端脚本部署与每日一次

先保存并启用分析配置、保存来源和终端路径，再以管理员身份下载脚本。

- Windows：通过运行账号自身身份写共享，可用域策略或任务计划程序安排每天执行。
- macOS：先挂载共享到 /Volumes/...，安装 Python 3，使用下载的 shell 脚本，可通过系统任务机制定时。
- 固定运行账号：采集范围、共享权限和本地每日标记可能受身份影响。
- 成功发布后记录本地每日标记；当天重复执行通常不再发布，失败可重试。
- 先写唯一 .uploading 文件，写完改名 JSON，避免 Worker 读到半个文件。
- 服务器按 **PC + 日志日期** 保留每天第一份有效日志。清除标记或更换账号可能重发，但不应重复台账/分析。
- 改终端路径后重新下载部署；Windows 脚本还嵌入非秘密 KMS 目标，改变此采集设置也需重新分发。仅修改后台判断阈值无需重发脚本。
- 传感器或工具不可用时保留缺失/未知，不伪造正常数据。

模板位于 [net/scripts/templates](net/scripts/templates)，生成逻辑在 [generator.py](net/scripts/generator.py)。使用页面生成的脚本，不直接运行包含占位符的模板。

### 7.5 分析内容和判断边界

| 项目 | 当前处理 |
| --- | --- |
| 系统激活 | 授权状态，配置允许列表时检查 KMS |
| 安装软件 | 清单；配置 INI 后执行白名单、按工号特例、黑名单 |
| 进程 | 展示采集清单，不是恶意进程识别引擎 |
| BitLocker | 根据已采集磁盘卷状态检查加密 |
| Defender | 病毒库与扫描日期是否超过配置天数 |
| 系统补丁 | 补丁日期及最大间隔 |
| 系统版本 | Windows 最低发行版本规则 |
| 运行时长 | 距开机是否超过最大小时数 |
| CPU/内存 | 使用率是否超过阈值 |
| 事件发现 | 已采集事件中的错误/严重级别等规则，不是实时抓取全部事件日志 |
| 域通讯 | 已采集的通讯/策略状态 |
| 浏览器扩展 | 清单及完整性，不代表完成恶意插件检测 |
| 账号与电脑匹配 | 规范化登录标识后与计算机名比较，适用于相应命名约定 |
| CPU 健康 | 温度/频率证据及温度阈值；频率展示不是性能基准测试 |
| 域信任、组策略 | 判断明确失败或缺失，保存已应用策略，不是完整 GPO 合规比对 |

软件规则结构见 [software-policy.ini](config/examples/software-policy.ini)，内容需按组织审核替换。配置路径指向 Worker 可读文件，相对路径按项目根目录解析。

只分析选中项目。缺失字段、未知值、错误类型会产生缺失/未知结果，不会自动当作“空列表且正常”。macOS 的 Windows 专用项目标记“不适用”。

人员、部门、OU 补充信息查询**已同步的本地数据库**，不在每次分析时实时查询平台或域控。站点网段映射示例：

~~~json
{"192.168.10.0/24": "长沙", "192.168.20.0/24": "北京"}
~~~

记录保存规则和关联信息历史快照；之后修改人员或配置不改写历史结论。从“导入日志证据”详情可重新分析已入库日志，不重新下载、不再移动源文件。

### 7.6 失败恢复

入库提交与远程移动不是一个事务。ComputerLogTransfer 记录下载、导入、重复、失败及归档状态：

- 下载期间文件变化：拒绝不稳定内容，后续重试。
- 已入库但归档失败：保留恢复信息，后续重试移动，不重复导入分析。
- 获取重试耗尽：任务失败；已入库但尚未交接分析的日志，由 Worker 恢复逻辑创建分析任务。
- 手动取消：不自动恢复该任务分析交接，也不撤销已完成的导入/移动。
- 历史 ComputerLogArchive 表保留，不表示旧本地扫描入口仍在使用。

## 8. 网络设备、服务器和安防巡检

通用操作：导入/维护设备 → 配置协议 → 选择巡检项目、目标、超时和并发 → 保存 → “手动执行巡检” → 查看任务与逐设备记录。首页类别手动按钮保留；定时任务走相同后台链路。

### 8.1 网络设备：SSH 与 SNMP 共存

| 模式 | SNMP | SSH |
| --- | --- | --- |
| ssh | 不使用 | 采集所选支持项目 |
| snmp | 采集支持指标 | 不暗中回退，日志/配置等不支持项明确缺项 |
| hybrid | 负责支持指标 | 负责日志 logs 和配置 config_info |
| auto | 优先采集指标 | 同 hybrid，并尝试对 SNMP 缺失指标回退 SSH |

SNMP 支持设备信息、CPU、内存、温度、接口状态和 VLAN；值取决于型号、系统、标准/私有 MIB 和读取视图。不读取日志、不导出配置，不执行 SNMP SET。

SSH 命令表包含 Huawei、H3C、Cisco、Ruijie 和通用分支，发送只读查询、处理分页、解析回显。通用分支不保证所有厂商；不匹配时可能只有原始证据或缺项。

部分协议/项目成功会保留有效数据，另一部分失败可形成 partial，不能因 ping 通或一个 OID 正常就宣称整体正常。详见 [网络设备部署说明](docs/deployment.md#网络设备-snmp--ssh-巡检)。

### 8.2 Linux 服务器：SSH

通过资产地址、端口、用户名和密码执行：

- hostname、uname、/etc/os-release：主机/系统。
- lscpu、/proc/loadavg：CPU 信息与负载证据。
- free -b、df -P -B1：内存和文件系统容量。
- ip -j address、ip -j route：接口/路由。
- systemctl --failed：失败服务。
- journalctl：限定时间与条数的错误日志。

目标需要相应命令和权限。CPU 负载回显不应冒充精确 CPU 使用率。依赖 systemd 的服务/日志采集不保证适用所有发行版或容器。

### 8.3 Windows 服务器：独立 HTTP 服务

在被巡检服务器上，进入 [agents/server/windows](agents/server/windows)，以管理员执行：

~~~powershell
.\Install-InspectionHttpService.ps1 -Port 9180 -Token "替换为随机令牌"
~~~

安装开机启动计划任务和防火墙规则。项目里服务器类型选 Windows，配置：

~~~text
API 地址：http://服务器IP:9180/inspection
API 令牌：与安装时一致
~~~

Worker 用 Bearer Token 请求 JSON，按所选字段校验系统、CPU、内存、磁盘、网络、服务和日志。限制可访问此端口的主机，不直接公开 HTTP 服务。详见 [Windows Agent](agents/server/windows/README.md)。

### 8.4 安防设备：厂商 API

通过 API URL、账号密码或 Token 获取 JSON/XML，归一化设备、状态、通道、存储等字段。包含海康/大华认证及返回数据适配；仍需填写实际可用的接口。

“能登记门禁闸机”不表示支持所有品牌接口。缺失字段明确记录，不把登录页面 HTML、版本字符串或任意响应冒充完整巡检。新厂商需增加 payload/配置适配并核对真实响应。

### 8.5 配置导出

先选中 config_info 并成功采集，再下载单台配置或按筛选导出 ZIP。**下载使用已保存快照，不在点击时连接设备。**

| 厂商 | 原生配置范围 |
| --- | --- |
| Cisco IOS | show running-config |
| H3C | display current-configuration |
| Dahua | 只读 Network 具名配置节 |

ZIP manifest 标记成功、未采集、失败或不支持。脱敏快照**不是完整可恢复备份**，不含全部密码、证书、文件和密钥；未知范围、截断、不完整或二进制配置不会冒充成功。

## 9. 人员 API 同步与域控管理

域控管理中的“从域控同步”会创建统一后台任务，不再在网页请求中执行 LDAP 同步。
管理员可在“域控连接设置”窗口保存定时同步：每隔 N 分钟/小时，或每天指定时间。
计划由现有 Worker 调度；同一时间只允许一个域控同步任务，停机后不会补发积压任务。
域控页面每页展示 10 次同步任务，详情显示账号、计算机和分组数量及失败原因，支持结束运行中的任务。
LDAP 读取后会在事务中更新本地目录；任务取消、租约失效或连接配置变更时不再应用旧结果。
此任务不参与设备告警，保存计划与手动同步沿用域控运维权限。

本次新增数据库迁移。演示部署等待现有任务结束后重新启动 `python -m deploy.demo`，
会自动迁移并收集静态文件；其他部署执行 `python manage.py migrate`、
`python manage.py collectstatic --noinput`，再重启 Web 和 Worker。

### 9.1 飞书/钉钉人员导入

“导入人员”使用固定平台页签，不需要新建来源名称：

1. 保存飞书 App ID / App Secret 或钉钉 App Key / App Secret，配置根部门和启用状态。
2. “测试连接”验证凭据可访问平台。
3. “预览数据”读取允许范围的完整部门/人员，校验工号等字段，展示新增/更新/停用差异。

   部门 ID 和上级用户 ID 会继续查询为名称（复用本次采集缓存）；无法读取详情或名称为空时预览失败，不把 ID 当名称写入。需要平台授予部门详情、用户详情读取权限和相应通讯录可见范围。

   人员导入的保存、测试、预览和确认导入在当前弹窗内更新；后台任务结束时，窗口内显示“查看结果 / 继续下一步”。校验失败返回可正常访问的列表地址，不会停留在仅支持 POST 的操作地址。
4. 预览通过后点击“导入数据”。
5. 查看完成结果和人员列表。

连接成功不等于有权限读取完整通讯录。预览需要接口权限和部门可见范围；接口错误、分页异常、空/冲突工号会导致失败，完整校验未通过不会悄悄写入部分人员。

按工号匹配，仅将**同一来源、完整结果中已不存在的人员**标记停用，不删除，也不将其他来源人员停用。平台停用不意味着一定返回实际离职日期。

连接测试/预览由 Worker 执行。运行提示可关闭，完成后显示通知；关闭提示不是取消。手工预览绑定浏览器会话，有效期 300 秒；过期、相关数据或配置变化后重新预览，不能重复应用旧结果。

各平台可设置间隔/每日自动同步。配置需先测试成功；修改凭据、范围或启用状态后需重新测试。自动同步无需浏览器常驻，后台完整校验和事务应用。旧 /api/upload_people/ 已删除。

### 9.2 域控同步与展示

分别展示域账号、域计算机和域分组。账号/计算机按启用与停用统计，各有列表/详情。配置服务器、端口、SSL、绑定身份和 Base DN，使用 ldap3 同步本地数据。

域分组来自 AD Group，不是本地人员分类。PC 分析关联 OU 前应先同步目录。

### 9.3 管理员批量操作

连接设置、测试/同步、目录写操作受 net.manage_domain_operations 权限控制，超级管理员自动拥有，也可为专用 Django 用户组授权。

| 对象 | 支持操作 |
| --- | --- |
| 域账号 | 添加用户、CSV/XLSX 批量创建、移动 OU、加入安全组、重置密码、下次登录改密、密码永不过期、解锁、启用/停用 |
| 域计算机 | 移动 OU、加入安全组、解锁、启用/停用 |
| 域分组 | 同步、列表和详情查询 |

不提供“移出分组”。加入组不等于替换全部成员关系。域用户表格导入会真实创建 AD 用户，不是普通本地导入。

操作校验 DN、Base DN 范围和目标，由 Worker 执行并记录逐目标审计。部分失败保留成功项，可对支持的操作仅重试失败目标。

添加用户/重置密码必须使用受信任证书的 LDAPS。一次性密码材料领取后删除；领取后中断不能盲目自动重放，应重新提交并输入新密码。见 [域操作部署说明](docs/deployment.md#域控操作)。

## 10. 后台任务、定时和告警逻辑

### 10.1 任务生命周期

~~~text
手动提交 / 到期计划
  → 校验、选择项目与目标
  → TaskRun + TaskTargetRun + 非秘密快照
  → queued 等待
  → Worker 原子领取，写租约
  → running，续租并有界并发执行
  → 保存记录、异常和目标结果
  → success / partial / failed / cancelled
  → 告警归一化和独立投递
~~~

任务冻结创建时的目标、项目、规则和告警路由；后续编辑不改变已入队任务的业务范围。秘密通常执行时读取，不复制到公开快照。

TaskWorker **一次领取一个任务**，在任务内并发执行目标。有效并发不超过 Worker --threads 和配置 concurrent_workers 的较小值。PC 下载线程各自持有连接，入库/归档协调按串行提交处理；并非所有步骤都同时执行。单一 PC 来源获取有互斥限制。

心跳维持租约，中断后到期任务按上限恢复领取；当前普通任务最多 3 次尝试。密码任务、取消任务和专门恢复流程不能套用普通重试逻辑。

### 10.2 定时设置

只提供两类周期：

- 每隔 N 分钟或 N 小时。
- 每天指定时刻。

设备目标支持全部、指定设备或支持字段的精确匹配；多个非空条件同时满足。PC 配置选择项目/周期，日志范围由全局来源决定；人员平台有自己的同步计划。

Worker 检查到期计划并入队，只有网页没有 Worker 不会执行。修改周期、时刻或重新启用后计算未来执行，不补跑停用期间每个时点；普通名称/项目编辑不应重置原时点。

### 10.3 手动结束任务

详情页可结束等待/运行任务，将未完成目标标记取消、撤销租约并阻止旧 Worker 继续提交结果；已完成结果保留。

这是**协作式取消**：已发出的 SSH/HTTP/LDAP 调用可能等返回或超时才退出，不是立即杀死调用，不会撤销域修改、导入或文件移动。外部调用卡住时，先结束任务，再按服务流程检查 Worker。

### 10.4 告警与恢复

1. 保存飞书机器人、钉钉机器人或 SMTP 邮件渠道。
2. 设置默认策略，项目可继承或覆盖。
3. 结果保存后，将明确发现转换为异常/正常/未知状态。
4. 生成异常事件；之前异常的同一项明确正常后生成恢复事件。
5. 保存投递记录，再发送消息；失败按机制重试。

未设置冷却时间，持续异常可在后续巡检继续通知，通过周期控制频率。恢复需要明确正常证据，缺失不充当恢复。人员同步和目录操作不作为设备告警处理。

入队冻结渠道 ID/路由，改策略不改道旧任务；发送时读当前凭据，停用渠道仍可阻止投递。“测试发送”会真实发消息，以投递详情判断成功。

## 11. 代码目录与扩展方式

~~~text
net/
├── manage.py
├── requirements.txt / requirements.lock.txt
├── net/                       # Django 设置和业务后端
│   ├── settings.py / urls.py / wsgi.py
│   ├── models/                # 人员、设备、目录、任务、记录、告警、来源
│   ├── migrations/            # 数据库迁移
│   ├── admin/                 # 分类管理后台
│   ├── dashboard/             # 资产统计
│   ├── people/                # 飞书/钉钉适配、预览、同步
│   ├── domain/                # LDAP、DN、同步、批量操作、密钥
│   ├── devices/
│   │   ├── pc/                # SMB/FTP、日志、分析、规则、上下文
│   │   ├── network/           # SSH/SNMP 与厂商配置
│   │   ├── server/            # Linux SSH / Windows HTTP
│   │   └── security/          # 安防 API 和配置
│   ├── inspections/           # 队列、Worker、计划、目标和统计
│   ├── alerts/                # 策略、状态、事件、投递
│   ├── data_exchange/         # CSV/XLSX、模板、配置 ZIP
│   ├── infrastructure/        # 通用 SSH/HTTP、结果和脱敏
│   ├── scripts/               # PC 模板和下载生成器
│   └── management/commands/   # Worker、演示 seed 等
├── index/                     # 页面层
│   ├── dashboard/ people/ domain/ devices/
│   ├── inspections/ alerts/ common/
│   ├── templates/             # 业务模板、弹窗、Admin 页面
│   └── urls.py
├── static/
│   ├── app/                   # 项目 CSS/JS
│   └── vendor/bootstrap/      # 第三方静态资源
├── agents/
│   ├── pc/windows/            # 保留的终端辅助库
│   └── server/windows/        # HTTP 巡检服务及安装器
├── config/examples/           # 软件策略示例
├── deploy/                    # 演示、Windows/Linux 服务
├── docs/                      # 专题文档、历史设计和审查
├── tests/                     # 按业务分类
└── demo-runtime/              # 演示运行数据，非源码
~~~

net 与 index 是两个 Django 应用，不是每个业务文件夹一个 App。业务放 net/，页面、表单、模板放 index/，减少 UI 与协议执行耦合。

静态目录区别：

- static/：人工维护的 CSS/JS 和第三方资源。
- staticfiles/：默认 collectstatic 产物，不手工编辑。
- demo-runtime/staticfiles/：演示独立的收集产物。
- 改源码后重新 collectstatic，必要时重启/刷新缓存，不合并生成目录到源码。

扩展规则：

- 新协议/厂商：业务适配放对应 net/devices 子目录，传输放 infrastructure，增加项目映射、校验和 fixture 测试。
- 新 PC 规则：同步项目声明、证据校验、规则、配置/快照与终端证据；明确缺失数据处理。
- 新表格字段：更新注册和允许筛选/排序/导出字段，秘密不得暴露。
- 新模型字段：生成并审核迁移，完善 Admin、导入映射和测试。
- 新任务：接入队列、目标执行、租约/取消及结果边界，不放进长时间 HTTP 请求。

Admin 对基础对象提供维护入口；任务、历史、密码材料等受只读或专门逻辑约束，不应为改历史而绕过业务直接改库。

## 12. 日常维护、验证与排障

### 12.1 维护与备份

- 备份数据库、稳定密钥、软件策略、远程归档和环境配置；数据库与密钥分别保存。
- 不把真实人员日志、密码、数据库、运行目录提交 Git。
- 迁移前确认库路径并备份；只看页面时不要运行重置 seed。
- 改 Python/模板重启 Web，改 Worker 代码重启 Worker，改静态源码重新 collectstatic。
- seed_demo_data --reset 会重置内置演示身份，不是排障命令。需要时先停服务、备份、显式指向演示库。
- seed_pc_remote_demo 可补充离线 PC 获取/传输样例，检测实际来源配置时拒绝；不用作生产初始化。
- 历史计划/审查描述当时实现，与当前冲突时以代码和本 README 当前流程为准。

### 12.2 验证命令

被动检查（不领取任务）：

~~~powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
~~~

按需运行回归。Django 使用测试库；MySQL 环境需允许创建测试库，绝不能手动把测试库配置成生产库：

~~~powershell
.\.venv\Scripts\python.exe manage.py test
Get-ChildItem tests/frontend -Filter *.test.js | ForEach-Object { node --test $_.FullName }
powershell -NoProfile -ExecutionPolicy Bypass -File tests/agents/test_windows_server_agent.ps1
~~~

Linux 前端可用 node --test tests/frontend/*.test.js；Agent PowerShell 测试需要 Windows 环境。可只运行受影响的 tests.<业务模块>，不必每次文档/样式改动跑全量。

run_task_worker --once 会安排到期计划、领取执行和处理相关投递，**不是只读健康检查**。

### 12.3 常见问题

| 现象 | 优先检查 |
| --- | --- |
| No module named 'whitenoise' 等依赖缺失 | 是否误用系统 Python；用 .venv 安装锁文件并启动 |
| 页面正常，任务一直等待 | Worker 是否运行；Web/Worker 是否同库；是否 --no-worker |
| 管理员登录不上/查不到数据 | 是否建到了根目录库，而 Web 使用演示库/MySQL |
| PC 脚本无法下载 | 管理员登录、配置保存并启用、来源和对应终端路径 |
| PC 配置未生效 | 来源与分析分别保存；旧任务使用快照；路径/KMS 采集设置需重发脚本 |
| 预览日志为空 | 实际 Worker 的权限、目录、扩展名、递归和修改日期范围 |
| 来源测试失败 | SMB 共享/文件权限、端口和域身份；FTP 元数据、被动端口/TLS；归档重命名权限 |
| 获取成功却有异常 | 有效 JSON 不等于分析正常，检查选中项目、阈值和缺失字段 |
| 重复日志没有新结果 | 同 PC 同日期保留首份；必要时从证据详情重新分析 |
| 入库后仍在汇总目录 | 传输记录的归档失败、同文件系统移动、权限；不要删除恢复记录 |
| 人员连接成功预览失败 | 通讯录权限、可见范围、分页、空/冲突工号及任务错误 |
| 人员预览失效 | 300 秒超时、会话改变、配置/数据变化；重新预览 |
| CSV/XLSX 导入失败 | 当前模板和行号/字段错误，工号、日期/IP/数字、来源冲突 |
| 域控 SSL 10054 | LDAP/LDAPS 端口、服务端 TLS 和证书链；不关闭验证来代替修复 |
| 网络巡检 partial | 分别查 SSH/SNMP 缺项、认证、命令、OID |
| 配置 ZIP 未采集/不支持 | 先执行支持厂商的 config_info；下载不连接设备 |
| 告警未收到 | 渠道、默认/项目策略、任务路由、网络和投递；目录任务不发设备告警 |
| 手动结束后仍有连接 | 外部调用尚未返回/超时，协作式取消不立即中断所有 socket |
| 改样式后未更新 | 当前运行源码、collectstatic 输出、重启 Web 和浏览器缓存 |

## 13. 已知边界

### PC 分析分级与人员匹配

等级设置现已按 PC、网络设备、服务器、安防设备四个项目分别保存。网络设备设置 CPU、内存、温度、实时流量、接口、VLAN 等；服务器设置 CPU、内存、磁盘、网络、服务等；安防设备设置运行状态、通道、存储、配置等。各项目互不影响。数值指标可配置阈值，只有实际取得的结构化数值参与判断。

网络“实时接口流量（SNMP）”需要在巡检配置中勾选并保存，同时在设备连接设置中配置 SNMP 凭据（即使其他项目使用 SSH，实时流量也通过 SNMP 读取）。每次巡检进行两次采样，中间间隔约 2 秒，按实际采样间隔计算每个接口收发 Mbps；有端口带宽时计算使用率。详情有独立接口速率表，列表关键指标显示最高收发速率。不是累计字节数，也不是浏览器持续刷新曲线。

流量优先使用 64 位计数器；设备重启、计数器重置、回绕不确定或采样失败时保留未知状态，不产生虚假的速率尖峰。缺少端口带宽时可显示速率，但不臆测带宽使用率。采样有超时限制，不会无限等待。

- 分析/巡检记录右上角“问题等级设置”仅管理员可见和操作。按检查项目分别设置实际问题与数据不足的提示/警告/严重等级，选择系统默认可恢复默认规则；保存不会关闭弹窗。配置冻结到新任务，历史结果及已排队任务不变。
- 任务详情和最新任务记录复用结果表，支持相同的列设置、问题类型/等级筛选及导出；任务详情的导出固定为该任务，未生成结果的目标另行展示。问题详情按软件、硬件、操作系统、系统更新、杀毒、磁盘加密、人员身份、域与组策略、系统事件、采集连接等分类。
- 设备巡检当前分级针对已有的采集连接/缺项问题，不会仅凭原始回显虚构硬件或软件故障。降低问题等级不会把真实采集执行失败改为成功。

- 分析执行结果与设备健康分开：有效日志完成规则检查后，任务显示成功；解析、数据库等执行错误仍为失败。
- 提示（info）：缺少字段、未知值或可选传感器数据不足。保留原始证据，不生成设备异常或告警，也不把未知当成已恢复。
- 警告（warning）：已确认的阈值或规则不符合，例如资源占用率、系统版本、软件策略、身份不一致和普通错误事件。
- 严重（critical）：域信任明确失败、CPU 温度超限、严重事件。警告和严重计入异常统计；列表、筛选、导出、详情显示等级。
- 在“PC 日志来源与分析配置 → 人员匹配方式”选择人员为主或日志为主，默认日志为主。人员为主相当于人员 LEFT JOIN 当前任务日志：保留无日志人员，仅提示，不伪造分析记录。日志为主相当于人员 RIGHT JOIN 日志：保留找不到人员的日志。
- 工号优先（支持域前缀/UPN），没有工号时使用唯一姓名或工号命名的计算机匹配；同名不猜测，有明确工号但未匹配时不以姓名覆盖。人员名册在分析任务创建时冻结，列表与导出共用同一匹配结果。
- 匹配模式控制结果展示，原始日志和实际分析目标仍保留；任务统计按实际分析目标计算，无日志人员不计设备故障。配置只影响后续任务，旧分析需重新分析才能获得新分级结果。
- 更新后执行数据库迁移并重启 Web 和 Worker；`deploy.demo` 使用 `demo-runtime/demo.sqlite3`，勿误迁移到另一数据库。

- 提供 Windows/Linux Web/Worker 路径，但系统服务、MySQL 并发和真实网络仍需部署验收。
- 自动化大量使用模拟 LDAP、SSH、SNMP、SMB/FTP 和平台响应；通过不等于验证了所有真实租户、AD、设备型号或 macOS。
- 周期快照“正常”仅表示最近一次所选项目通过当前规则，不代表实时在线、完整安全合规或绝对无故障。
- Linux/网络部分值为命令证据，安防因型号而异；未实现任意设备自动适配和所有指标的统一阈值引擎。
- 取消不是外部操作回滚；恢复不是所有接口的“严格仅执行一次”保证。
- 当前 SSH 自动接受未知主机密钥，正式使用需受控管理网络并进一步完善主机身份校验。
- 全站授权、全部凭据静态加密、审计保留周期等需按正式要求完善，不能视为已全面加固。
- 旧上传 API 已删除，不提供兼容入口。PC 日志链路使用配置的共享汇总来源。

进一步阅读：[部署说明](docs/deployment.md)、[Windows 服务](deploy/windows/README.md)、[Linux 服务](deploy/linux/systemd/README.md)、[PC 远程日志验证记录](docs/pc-remote-log-validation.md)。历史 docs/superpowers 文件用于追踪设计过程，不等于尚未实现的功能承诺。

后端性能更新：[分页、聚合和任务存储优化](docs/backend-optimization.md)、[SQLite WAL 与历史快照归档维护](docs/backend-maintenance.md)。更新包含数据库迁移，请在原有运行环境备份、迁移并重启 Web/Worker；维护命令默认只预览，不会自动删除历史数据。

# Windows Server / Linux 一键部署

适用于单台主机上的正式 Web + Worker。脚本安装项目依赖、配置环境、迁移数据库、收集静态文件、创建首个管理员，并设置开机启动。**不执行 `seed_demo_data`，不重置数据库，不自动创建数据库实例，不修改防火墙。**

## 先准备

1. 把代码放到固定目录，例如 Windows 的 `C:\NetInspection`、Linux 的 `/opt/net-inspection`。不要从临时解压目录运行，也不要把其他机器的 `.venv` 复制过来。
2. 准备可连接的 MariaDB/MySQL、UTF-8（utf8mb4）数据库和该库的专用账号。迁移需要这个库的建表/改表权限。账号密码在首次引导中填写，不要写到命令行。
3. Windows 安装 **64 位 Python 3.12+（为所有用户安装）**，需要“计划任务”服务；Linux 需要 systemd、root/sudo。Ubuntu 24.04+/Debian 13+ 可使用发行版 Python；支持提供 Python 3.12 软件包的 dnf 系统，其他环境可预装依赖后加 `--skip-system-packages --python /path/to/python3.12`。
4. 安装依赖需要访问 Python 包源和 Linux 软件源。使用内部软件源时先配置 pip/系统包管理器。Windows 若 mysqlclient 没有当前 Python 版本的轮子，请安装 MariaDB Connector/C 与 C++ 编译工具，或选用锁文件支持的 Python 3.12 环境。

**迁移现有项目时**：先备份并迁移数据库，连同原 `.env`、所有 `*_KEY_FILE` 指向的密钥文件、设备备份密钥和软件策略一起保存。检查 Windows/Linux 路径差异。不要给已有密文生成新密钥，也不要仅迁移代码后误连空库。Windows 凭据管理器的 keyring 数据不会随 `.env` 自动迁移，需要在目标运行账号下恢复。新建空库不会复制原机器的设备、人员、配置模板或历史记录。

## Windows Server

在**管理员 Windows PowerShell** 中执行：

```powershell
Set-Location C:\NetInspection
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Install-NetInspection.ps1
```

默认使用项目 `.venv` 或已安装的 Python；可指定：

```powershell
.\deploy\windows\Install-NetInspection.ps1 -PythonExe C:\Python312\python.exe
```

首次没有 `.env` 时按提示填写：

- 访问域名/IP：如 `inspection.example.invalid,192.0.2.10,localhost,127.0.0.1`，不要写协议或端口；替换为真实地址。
- 数据库主机、端口、库名、账号、密码。
- Web 监听地址：默认 `0.0.0.0:8000`；已有反向代理时可填 `127.0.0.1:8000`。
- 迁移完成后创建网站管理员；已有活跃超级管理员时不会改密码。

Django 密钥和三个独立 Fernet 密钥只在首次新建配置时生成。密码隐藏输入；`.env` 限制管理员、SYSTEM 和指定运行账号访问。现有 `.env` 原样复用，不自动修改数据库、密钥、静态目录或缓存配置；不合格配置会报错停止。已有配置需要明确写 `DJANGO_DEBUG=false`、有效稳定密钥和 `DB_ENGINE`；已有 SQLite 环境还必须明确填写原库的 `DJANGO_SQLITE_PATH`，不会自动改用演示库或根目录库。

使用系统自带计划任务，无需 NSSM/WinSW：

- `NetInspectionWeb`：Waitress Web。
- `NetInspectionWorker`：后台任务与定时调度。
- 开机启动，不需要保持终端打开；进程失败每分钟重试，最多 999 次；同一任务不重复启动。
- 默认 SYSTEM 身份。若数据库密码保存在特定用户的 keyring，或其他已部署的本机资源要求该身份，可用该账号运行两个任务：

```powershell
.\deploy\windows\Install-NetInspection.ps1 -ServiceCredential (Get-Credential)
```

该账号需要批处理登录权限、读取项目/Python/密钥文件以及写入实际静态目录和部署日志目录的权限。脚本只自动授权自身 `runtime\deployment` 目录，已有自定义目录须按实际路径授权。PC 日志由终端直传 API，不需要给 Web/Worker 配置共享盘、SMB 或 FTP 收集权限。

查看/停用：

```powershell
Get-ScheduledTask -TaskName NetInspectionWeb,NetInspectionWorker
Get-Content .\runtime\deployment\logs\web.log -Tail 50
Get-Content .\runtime\deployment\logs\worker.log -Tail 50
# 只停止本次运行：
Stop-ScheduledTask -TaskName NetInspectionWorker
Stop-ScheduledTask -TaskName NetInspectionWeb
# 若以后开机也不需要运行：
Disable-ScheduledTask -TaskName NetInspectionWorker
Disable-ScheduledTask -TaskName NetInspectionWeb
```

升级时先备份，更新代码后重跑同一安装命令。它只停止并替换自己创建的任务；同名旧服务、其他目录的任务或未退出的项目进程会阻止安装。Windows 停止计划任务会中断当前调用，未完成任务由数据库租约恢复，不删除任务数据。手动选择服务账号时，重跑也要带 `-ServiceCredential`；缺少该参数会停止，不会自动换成 SYSTEM。

## Linux / systemd

在目标 Linux 主机执行：

```bash
cd /opt/net-inspection
sudo bash deploy/linux/install.sh
```

脚本通过 apt/dnf 安装 Python、venv/编译依赖、mysqlclient 开发头文件和 ping；创建低权限 `net-inspection` 账号。项目放在 `/opt` 等该账号能遍历和读取的位置；不会把整份代码目录递归改成服务账号所有。

首次引导与 Windows 相同。两个服务共用同一 `.env`，以真实服务账号执行准备检查，避免 root 能连接而服务不能连接：

```bash
sudo systemctl status network-inspection-web network-inspection-worker
sudo journalctl -u network-inspection-web -n 50 --no-pager
sudo tail -n 50 runtime/deployment/logs/worker.log
# 暂停运行：
sudo systemctl stop network-inspection-worker network-inspection-web
# 同时取消开机启动：
sudo systemctl disable --now network-inspection-worker network-inspection-web
```

升级：备份、更新代码、重新执行安装脚本。仅替换带本脚本所有权标记的 unit；旧的手工 unit 应先由管理员明确迁移处理。停止预算 120 秒，超时后 systemd 结束进程组，未完成任务按租约恢复。

## 共用说明与排障

- 完成后在浏览器访问 `http://服务器IP:8000/`（以 `WEB_LISTEN` 为准）。脚本先检查登录页及静态资源 HTTP 200，成功后启动 Worker，再检查进程状态。**不会用 Worker `--once` 探活**，但正式启用 Worker 后会开始执行现有队列、到期计划与告警。
- `.env` 是这些新部署入口的配置来源，会覆盖维护终端残留的数据库变量；`python manage.py` 原有“进程变量优先”的规则不变。不要在设置过旧 `DB_*` 变量的终端直接操作其他数据库。
- 运行日志位于 `runtime/deployment/logs`，每角色 10 MiB × 最多 6 个文件；Linux 在日志文件初始化失败时可查 journal，Windows 可查任务历史和 `Get-ScheduledTaskInfo`。静态文件默认在 `runtime/deployment/staticfiles`。
- 监听端口占用：先停原启动器，别再起一套。浏览器 400：检查 `.env` 的 `DJANGO_ALLOWED_HOSTS` 包含实际访问 IP/主机名。本机通过、其他电脑不通：核对监听地址、防火墙及网络访问范围。HTTPS 通过现有反向代理提供，脚本不自动申请证书或开启代理信任。
- 密钥/权限错误：恢复原密钥，并给实际运行账号读取权限；不要生成新密钥“修复”已有备份。数据库不存在/认证失败：先创建或恢复目标库并授权。默认不会自动安装 Redis，新配置关闭可选缓存。
- 部署失败时两个角色保持停止/禁用，修复后重跑；不会为了恢复页面而留下 Worker 偷跑。依赖或迁移发生失败时不自动回滚 Python 包或数据库；使用备份恢复，不能擅自重置库。
- `-EnvFile C:\path\.env` / `--env-file /path/.env` 使用其他受限配置文件。
- `-NonInteractive` / `--non-interactive` 要求配置和活跃管理员已存在。
- `-PrepareOnly` / `--prepare-only` **不是只读演练**：仍停止已有受管进程、安装依赖、迁移和收集静态文件，只是不注册/启动新任务或服务。
- 空库需要默认网络模板时，使用同一配置执行 `python manage.py create_network_templates`；它不覆盖已有模板。服务器及安防模板可从设备列表创建。PC API 升级后还需重新下载并部署终端采集包，旧共享盘/FTP 脚本应停止分发。迁移已有项目以数据库备份中的模板为准。

验证范围：脚本语法、配置/密钥保留、重复进程锁和临时 SQLite 的迁移/静态文件流程可在开发机隔离测试；Windows 计划任务注册、Linux 包安装/systemd、目标 MariaDB 与真实外部设备仍须在目标机验证。

接口依据：[Microsoft ScheduledTasks](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskprincipal?view=windowsserver2025-ps)、[systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)、[mysqlclient 安装依赖](https://github.com/PyMySQL/mysqlclient/blob/main/README.md)。

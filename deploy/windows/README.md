# Windows Server：独立 Web / Worker 服务示例

本说明只提供命令，没有在开发桌面注册服务或更改系统。生产用 MySQL，不要把演示 SQLite 当生产数据库。
目标示例路径：应用 `C:\NetInspection`、运行数据 `C:\NetInspectionData`。
准备 Python 3.12+（应用强制最低 3.12）、MySQL 8 utf8mb4 数据库、专用数据库账号、已批准的 NSSM 服务包装器。

```powershell
Set-Location C:\NetInspection
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
New-Item -ItemType Directory -Force -Path C:\NetInspectionData\logs,C:\NetInspectionData\staticfiles,C:\NetInspectionData\incoming,C:\NetInspectionData\incoming\processed,C:\NetInspectionData\incoming\failed
```

复制 `environment.example.ps1` 到访问受限、**不纳入 Git** 的 `C:\NetInspectionData\environment.ps1`，填写真实密钥/数据库参数/允许的域名。仅指定低权限服务账号和管理员可读；不要将密码复制进截图。两个服务必须使用同一份环境。以同一服务账号验证目录写权限，不能假定 LocalSystem 有网络共享权限。

```powershell
. C:\NetInspectionData\environment.ps1
.\.venv\Scripts\python.exe manage.py migrate --noinput
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe -m waitress --listen=$env:WEB_LISTEN --threads=$env:WEB_THREADS net.wsgi:application
# 第二个 PowerShell 加载同一 environment.ps1，运行独立 Worker：
.\.venv\Scripts\python.exe manage.py run_task_worker --threads $env:WORKER_THREADS --poll-seconds $env:WORKER_POLL_SECONDS --lease-seconds $env:WORKER_LEASE_SECONDS
```

Web 使用 Waitress + WhiteNoise，不用 runserver。仅监听 loopback，前置 HTTPS + 认证反向代理；当前应用没有全站访问授权，不能直接暴露在公网。检查 `/`、`/static/app/css/style.css` 和 `/static/app/js/common/table_workspace.js` 的 200 状态和 Content-Type。正式服务安装仅在目标机由管理员执行：

```powershell
$nssm = 'C:\Tools\nssm\win64\nssm.exe'
$python = 'C:\NetInspection\.venv\Scripts\python.exe'
& $nssm install NetInspectionWeb $python '-m waitress --listen=127.0.0.1:8000 --threads=4 net.wsgi:application'
& $nssm install NetInspectionWorker $python 'manage.py run_task_worker --threads 4 --poll-seconds 5 --lease-seconds 60'
# 如更改环境中的线程/轮询/租约/监听值，也要更新以上 AppParameters。
$names = @('DJANGO_SETTINGS_MODULE','DJANGO_DEBUG','DJANGO_SECRET_KEY','DJANGO_ALLOWED_HOSTS','DJANGO_STATIC_ROOT','DB_ENGINE','DB_NAME','DB_USER','DB_PASSWORD','DB_HOST','DB_PORT','WEB_LISTEN','WEB_THREADS','WORKER_THREADS','WORKER_POLL_SECONDS','WORKER_LEASE_SECONDS','PYTHONUNBUFFERED','PYTHONUTF8')
$sharedEnvironment = @($names | ForEach-Object { "$_=$([Environment]::GetEnvironmentVariable($_, 'Process'))" })
foreach ($service in @('NetInspectionWeb','NetInspectionWorker')) {
    & $nssm set $service AppDirectory 'C:\NetInspection'
    & $nssm set $service AppEnvironmentExtra $sharedEnvironment
    & $nssm set $service AppStdout "C:\NetInspectionData\logs\$service.log"
    & $nssm set $service AppStderr "C:\NetInspectionData\logs\$service-error.log"
    & $nssm set $service AppRotateFiles 1
    & $nssm set $service AppRotateOnline 1
    & $nssm set $service AppRotateBytes 10485760
    & $nssm set $service AppExit Default Restart
    & $nssm set $service AppRestartDelay 5000
    & $nssm set $service AppStopMethodConsole 120000
}
# 用 NSSM GUI 的 Log on 页面设置专用服务账号并授权目录访问，勿在命令历史中输入密码。
& $nssm edit NetInspectionWeb
& $nssm edit NetInspectionWorker
& $nssm start NetInspectionWeb
& $nssm start NetInspectionWorker
```

每次配置变更重新加载受限环境脚本并更新**两个**服务的 AppEnvironmentExtra。NSSM 将环境保存在服务注册配置里，需保护服务配置权限。日志按 10 MiB 轮转，另设保留期。

在页面“分析配置”设置扫描目录 `C:\NetInspectionData\incoming`、成功归档 `C:\NetInspectionData\incoming\processed`、失败归档 `C:\NetInspectionData\incoming\failed`。这些目录配置保存在 MySQL，不是环境变量；归档必须位于扫描根内且同文件系统，自动排除扫描，移动不覆盖已有文件。静态文件目录必须分开。不要使用依赖交互用户的映射盘符。

重启：先摘除代理流量并等待 Web 请求结束，再 `nssm restart NetInspectionWeb`。NSSM 的 console stop 会发送 Ctrl+C；Waitress 会清理，但内置线程收尾仅约 5 秒，不能承诺强制停止时在途请求完成。Worker 收到 Ctrl+C 后停止领取任务并等有界采集结束；`nssm restart NetInspectionWorker` 留 120 秒窗口，超时强杀后由 60 秒数据库租约恢复未完成目标。若采集配置超过 60 秒，应相应增大停止预算。

被动健康检查使用 `manage.py check`、`nssm status`、本机 Web/静态 HTTP GET。
一次性 Worker 执行检查：`python manage.py run_task_worker --threads 4 --poll-seconds 5 --lease-seconds 60 --once`（务必使用上述 `.venv` Python）。**它会真实执行任务、安排计划和投递告警，不是无副作用的探活。** 本项目验收只在 fixture/mock 下执行它。

参考：[NSSM usage](https://nssm.cc/usage)、[NSSM CLI](https://nssm.cc/commands)、[Waitress CLI](https://docs.pylonsproject.org/projects/waitress/en/latest/runner.html)、[WhiteNoise](https://whitenoise.readthedocs.io/en/latest/django.html)。Linux/MySQL/服务安装均未在此桌面实际执行。

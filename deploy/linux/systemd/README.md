# Linux / systemd 部署示例（未在此 Windows 桌面安装或验证）

这是生产 MySQL 的安装说明，不是演示 SQLite 的启动命令。仅在目标 Linux 主机由管理员执行。
准备 Python 3.12+（应用强制最低 3.12）、MySQL 8 的 utf8mb4 数据库和专用数据库账号；安装 mysqlclient 所需的系统编译依赖。
将应用放到 `/opt/net-inspection`，创建 `.venv` 并 `./.venv/bin/python -m pip install -r requirements.lock.txt`。
创建低权限 `net-inspection` 服务账号，让它读取应用、写入以下目录（不要把日志/归档放进 staticfiles）：

```bash
sudo install -d -m 0750 -o net-inspection -g net-inspection /var/log/net-inspection /var/lib/net-inspection/staticfiles /var/lib/net-inspection/incoming /var/lib/net-inspection/incoming/processed /var/lib/net-inspection/incoming/failed
sudo install -d -m 0750 -o root -g net-inspection /etc/net-inspection
sudo install -m 0640 -o root -g net-inspection deploy/linux/systemd/environment.example /etc/net-inspection/environment
```

编辑环境文件，替换密钥、主机名和数据库密码；两服务使用同一文件。不要提交含真实凭据的副本。
管理命令也需加载此文件（文件使用 shell 与 systemd 均支持的简单 `KEY=value`，特殊字符值需正确引用）：

```bash
cd /opt/net-inspection
# 以 net-inspection 身份，在受限的维护 shell 中运行：
set -a
. /etc/net-inspection/environment
set +a
./.venv/bin/python manage.py migrate --noinput
./.venv/bin/python manage.py collectstatic --noinput
./.venv/bin/python manage.py check
```

先手动验证两个独立进程，再在目标机安装单元：

```bash
./.venv/bin/python -m waitress --listen="$WEB_LISTEN" --threads="$WEB_THREADS" net.wsgi:application
# 第二个加载相同 environment 的 shell：
./.venv/bin/python manage.py run_task_worker --threads "$WORKER_THREADS" --poll-seconds "$WORKER_POLL_SECONDS" --lease-seconds "$WORKER_LEASE_SECONDS"
# 验证后，仅在目标主机：
sudo cp deploy/linux/systemd/network-inspection-web.service deploy/linux/systemd/network-inspection-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now network-inspection-web network-inspection-worker
```

在“分析配置”中设置扫描目录 `/var/lib/net-inspection/incoming`、成功目录 `/var/lib/net-inspection/incoming/processed`、失败目录 `/var/lib/net-inspection/incoming/failed`；这些是数据库配置项。归档必须位于扫描根内且同文件系统，自动排除扫描，移动不覆盖已有文件。静态文件目录必须分开。日志见 `/var/log/net-inspection/*`，由运维配置轮转和保留期。

Web 仅监听 loopback，由经过身份验证的 HTTPS 反向代理访问。当前应用没有全站访问授权，不能直接公开暴露；白噪声静态服务并不等于认证、TLS 或完整生产安全加固。反向代理头只能信任确切的代理地址，按实际 HTTPS 配置 Django 安全 cookie/CSRF 设置。

滚动维护：先从代理摘除 Web 流量并等现有请求完成，再 `systemctl restart network-inspection-web`（SIGINT 触发 Waitress 清理，其内部线程收尾窗口默认仅 5 秒；不能保证强制停止时未完成请求成功）。Worker 用 SIGTERM 停止接收新目标，等待有界调用结束，再重启；未完成任务由 60 秒租约恢复。示例 120 秒停止窗口适用于 60 秒采集超时，若允许更长任务，必须同步提高停止窗口。不要删除活动任务或用 `--once` 探活。

被动检查用 `manage.py check`、`systemctl status` 和本机 HTTP GET `/`、`/static/app/css/style.css`、`/static/app/js/common/table_workspace.js`。
一次性执行检查为 `./.venv/bin/python manage.py run_task_worker --threads 4 --poll-seconds 5 --lease-seconds 60 --once`：它会执行实际排队任务、安排到期计划、处理告警，**不是被动健康检查**。仅在隔离 fixture 数据库或明确批准执行真实任务后使用。

参考：[Waitress CLI](https://docs.pylonsproject.org/projects/waitress/en/latest/runner.html)、[WhiteNoise Django](https://whitenoise.readthedocs.io/en/latest/django.html)、[systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)。

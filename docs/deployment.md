# 部署与 PC 采集脚本

本页补充生产 Web/Worker 部署的 PC 采集脚本部分。基础服务注册和环境变量示例见 [Windows / NSSM](../deploy/windows/README.md) 与 [Linux / systemd](../deploy/linux/systemd/README.md)。

## 公共地址与反向代理

生产环境首选显式设置 `NET_PUBLIC_BASE_URL` 为终端可访问的单一 HTTP(S) origin，例如：

```powershell
$env:NET_PUBLIC_BASE_URL = 'https://inspection.example.com'
```

下载脚本把它转换为 `https://inspection.example.com/api/computer_inspection/?profile_id=<UUID>`；它不能包含路径、查询参数、片段或 userinfo。显式地址优先于请求推导的地址，能避免 TLS 终止代理向 Django 转发内部 HTTP/内部 host 时生成错误上传地址。

`NET_TRUST_PROXY_HEADERS` 默认关闭。只有在下列条件同时满足时才设为 `true`、`yes` 或 `1`：反向代理由本部署团队信任并会重写 `X-Forwarded-Proto` 和 `X-Forwarded-Host`，该代理是 Django 的唯一/受控入口，而且 `DJANGO_ALLOWED_HOSTS` 只包含正确的公开 host（及必要端口）。开启后 Django 才信任这些 forwarded headers；未满足条件时保持关闭并使用 `NET_PUBLIC_BASE_URL`。不要让客户端可直连 Django 后仍开启该开关。

## 配置、下载和权限

1. 在 PC 的“分析配置”中保存 profile，并设置至少一个扫描目录、扫描递归选项和文件时间范围。每次下载都绑定**所选且已保存** profile 的 UUID；配置以后修改，必须重新下载脚本才会得到新配置。
2. 使用 Windows 本机路径（例如 `C:\InspectionLogs`）建立 Windows profile，使用 macOS POSIX 路径（例如 `/Library/Logs/Inspection`）建立 macOS profile。同一 profile 的路径原样嵌入两个模板；因此跨平台路径不同的环境必须分别保存 profile，不能把 Windows 路径用于 macOS 或反之。
3. 登录用户才能从 `GET /tasks/profiles/computer/<profile UUID>/scripts/windows/` 或 `.../macos/` 下载。成功响应是附件，带 `X-Content-Type-Options: nosniff` 和 `Cache-Control: no-store`。请在上层认证、HTTPS 和网络访问控制下使用；不要把下载 URL 或已下载文件放入共享缓存。

脚本只序列化扫描目录、递归和时间范围、分析项目、上传 URL 与 profile UUID；不会带入域控绑定密码、设备密码/API token、Django `SECRET_KEY` 或数据库凭据。

## 运行前置条件

Windows 下载文件名为 `GetInfo_Upload.ps1`，采用 UTF-8 **BOM**，以兼容 Windows PowerShell 5.1。以可读取扫描目录、可在首个扫描目录创建失败 JSON 的 Windows 帐号运行；主机必须能访问上传 URL，并提供 PowerShell/WMI（CIM）与网络查询所需系统组件。执行策略限制时由管理员按本组织策略放行，不要绕过策略或把脚本改为携带凭据。

macOS 下载文件名为 `getinfo_upload_macos.sh`，是无 BOM 的 UTF-8 POSIX shell 文件。使用有读取目录和写入首个扫描目录权限的帐号运行；需要 `sh`、`python3`、`curl`，以及系统自带的 `system_profiler`、`sysctl`、`diskutil`、`df`、`ifconfig`、`find`。下载后赋予执行权限，例如 `chmod 700 getinfo_upload_macos.sh`，再执行 `./getinfo_upload_macos.sh`。

两种脚本都会收集主机、系统、CPU/内存/磁盘、网络与时间范围内 JSON 日志元数据，并以 UTF-8 JSON POST 到 `/api/computer_inspection/`。上传失败时，脚本会把完整 JSON 保留到第一个配置的扫描目录，并返回非零退出状态；修复网络、DNS、TLS、认证或服务问题后，可审阅该 JSON 再重新运行/重传。保留目录应由 Worker 帐号和端点帐号按实际职责分别授权，且不得放在静态文件目录。

## Worker 消费与上线检查

上传 API 成功返回 `202` 表示已接收并入队，不表示分析已完成。独立 Worker（`manage.py run_task_worker`）消费任务、提取并脱敏证据，按上传 URL 中的 profile UUID 使用分析项目，创建分析结果及必要告警；在任务详情查看最终状态。Web 与 Worker 必须指向同一生产数据库和同一 profile 数据。

自动化测试覆盖生成、profile 绑定、下载登录保护、BOM/no-store 与静态秘密扫描，但不等同于真实端点环境验证。部署前仍必须在真实 Windows PowerShell 5.1 主机和真实 macOS 主机做 smoke test：从登录后的页面下载对应脚本，确认路径权限与 UTF-8/BOM、上传 URL 和 TLS，制造一次可控上传失败确认 JSON 留存，再确认 Worker 将一次成功上传消费为预期分析记录。不要在演示数据库或未受控的生产资产上执行该测试。

## 域控操作

域账号和域计算机的写操作由权限 `net.manage_domain_operations` 保护。超级管理员自动拥有该权限；建议创建一个专用组并授予权限，而不是向日常账号逐一赋权：

```powershell
.\.venv\Scripts\python.exe manage.py shell -c "from django.contrib.auth.models import Group, Permission; group, _ = Group.objects.get_or_create(name='Domain Operators'); group.permissions.add(Permission.objects.get(content_type__app_label='net', codename='manage_domain_operations'))"
```

将获授权的后台账号加入 `Domain Operators`。无此权限的用户只能查看目录列表和脱敏连接摘要，不能更改连接设置、测试/同步目录或提交/重试写操作。

### Fernet 密钥和服务环境

为每个部署生成一个稳定的 Fernet 密钥，并将**同一值**注入 Web 和 Worker 的受保护服务环境；不得把密钥提交到仓库、任务参数、日志或截图：

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```bash
./.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

将输出保存为 `DOMAIN_OPERATION_ENCRYPTION_KEY`。缺失或无效的值只会拒绝需要密码的域操作；仍应在上线前完成配置，且更换密钥前必须清空或按变更流程处理尚未领取的密码任务。

Web 与 Worker 都必须使用同一数据库和环境变量。Windows（NSSM 服务的程序参数）示例：

```powershell
.\.venv\Scripts\python.exe manage.py run_task_worker --threads 4 --poll-seconds 5 --lease-seconds 60
```

Linux（systemd `ExecStart`）示例：

```bash
./.venv/bin/python manage.py run_task_worker --threads 4 --poll-seconds 5 --lease-seconds 60
```

Worker 不是被动健康检查；它会领取并执行已排队任务。先用 `manage.py check`、服务状态和本机页面检查确认环境，再按变更窗口启动 Worker。

### 结果、密码和 LDAPS

一个批量任务的成功项会保留，失败项会使总体状态显示为“部分成功”；在任务详情中检查逐目标的脱敏错误，然后使用“仅重试失败项”。密码任务的一次性密文会在 Worker 领取后删除；若 Worker 在领取后中断，任务不能自动重试或复用密码，管理员必须重新提交任务并输入新密码。

新增用户和重置密码必须使用 LDAPS：域控配置应启用 SSL、使用受系统信任链验证的服务器证书，并使用端口 636（或组织明确配置的 LDAPS 端口）。禁止为了测试关闭证书验证，也不要把密码任务发往普通 LDAP/389。

### 真实 AD 测试 OU smoke test

自动化测试仅使用模拟 `ldap3`，不会连接真实 LDAP。首次上线时，使用隔离、可删除的测试 OU 与专用测试账号/计算机进行 smoke test：

1. 确认服务账号只拥有测试 OU 所需的最小权限，且 Web/Worker 均使用相同生产配置与密钥。
2. 从获授权账号测试连接与同步，验证 LDAPS 证书链、base DN 边界和脱敏错误展示。
3. 对测试对象依次执行一次非密码移动 OU、启用/停用和组成员变更，核对任务目标结果与 AD 实际状态。
4. 单独提交一次密码操作，确认任务详情、日志、导出和审计均无明文或密文；不要中断生产 Worker。若在受控演练中中断，按“重新提交并输入新密码”恢复。
5. 验证失败项的“仅重试失败项”不会重做已成功对象；完成后清理测试 OU 中的对象和组成员。

不要将 smoke test 指向演示数据库、生产人员 OU 或未隔离的生产对象。

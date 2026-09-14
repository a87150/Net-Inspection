# 部署与 PC 采集脚本

本页补充生产 Web/Worker 部署的 PC 采集脚本部分。基础服务注册和环境变量示例见 [Windows / NSSM](../deploy/windows/README.md) 与 [Linux / systemd](../deploy/linux/systemd/README.md)。

## PC 来源与采集脚本

新版 PC 仅从统一汇总共享目录获取 JSON，旧上传 API 不再提供。详细配置步骤见 [README 的 PC 日志章节](../README.md#pc-共享日志获取与分析)。

管理员在 PC 配置窗口保存 SMB 或 FTP/FTPS 来源、读取/归档目录、日期范围与终端写入路径。Windows 使用 UNC 路径；macOS 使用已挂载的 /Volumes 路径。后台密码仅加密保存在数据库，脚本不含该密码。若 Worker 使用 FTP，必须保证 FTP 目录和终端写入路径对应同一批文件。

Web 和 Worker 使用相同数据库及稳定的 PC_LOG_SOURCE_ENCRYPTION_KEY（有效 Fernet 密钥）；另需单独配置域控操作密钥。加密密钥与数据库应分别备份。更换密钥必须先重新配置凭据，不可直接丢弃旧值。

终端脚本从已保存的配置下载，仅管理员可下载，响应禁止缓存。修改终端写入路径后须重新下载部署；分析规则由 Worker 配置决定，不需要重新分发脚本。Windows 脚本兼容 PowerShell 5.1，UTF-8 BOM；macOS 使用 sh、Python 3 与系统自带工具。两者以固定本地账号按日运行，不内嵌后台登录密码。

采集先写 .uploading 再重命名 JSON；成功发布后保存每日标记。共享不可写时不会标记成功，可重试。服务器按 PC 与日志日期去重，每天保留第一份有效日志。已选检查的数据未知会明确显示缺失，不自动视为正常。

Worker 获取文件后先提交数据库，再归档远程文件；已处理/失败目录必须与汇总目录位于同一共享或 FTP 文件系统，并允许重命名。暂存目录必须位于 Worker 本机，不能放在 static/staticfiles。定时执行和手动执行使用同一获取任务链，分析可多线程。

## 上线检查

先在页面测试连接和预览：连接测试会创建、移动、删除专用测试文件；预览只读列目录。确认服务器、目录和日期范围无误，再人工执行获取。查看获取任务的导入/重复/失败/归档失败数量，以及后续分析子任务。

本地自动化使用模拟 SMB/FTP，不能代替真实网络权限、FTPS 证书及 Windows/macOS 终端验证。部署时分别检查终端写入权限、Worker 读取和归档权限，并用测试设备的日志验证整条链路。不要把生产账号或日志内容放入公开截图。

NET_TRUST_PROXY_HEADERS 仍只应在受控反向代理会重写相关头时启用；PC 脚本已不依赖 Web 公共地址。

## 网络设备 SNMP / SSH 巡检

网络设备主动巡检由独立 Worker 发起，而不是由浏览器或 Web 进程直连设备。所有 Worker 节点都必须使用更新后的 `requirements.lock.txt` 安装依赖，确认 PySNMP 可用，并能解析、路由到目标管理地址。生产防火墙和设备 ACL 必须允许 **Worker 到设备的 UDP/161** 及返回流量；资产使用自定义 SNMP 端口时放行对应 UDP 端口。无需向 Web 前端开放 UDP/161，也不要把设备管理网直接暴露给用户网段。

连接模式含义如下，生产常规选择推荐 `hybrid`：

| 模式 | 行为与适用场景 |
| --- | --- |
| `ssh` | 全部请求项目走现有 SSH 采集；用于未启用 SNMP 或仍依赖 SSH 指标命令的设备。 |
| `snmp` | 只采集 SNMP 支持项目；请求 `logs`、`config_info` 或其他不支持项目时会明确缺项/失败，不会暗中改走 SSH。 |
| `hybrid` | 推荐模式。SNMP 负责支持的指标，SSH 负责 `logs` 和 `config_info`；任一协议成功的数据会保留，另一协议失败可形成 `partial`。 |
| `auto` | 初始分工与 `hybrid` 相同；SNMP 没有返回的指标再交给 SSH 回退采集。适合确需指标回退的环境，但应监控额外 SSH 连接和命令负载。 |

SNMP 支持的项目为 `device_info`、`cpu`、`memory`、`temperature`、`interface_status`、`vlan_status`。其中厂商私有 OID 或标准 MIB 值可能因型号、系统版本和代理配置而缺失；单个 OID 不支持只影响相应项目，不应中断其他已完成项目。`logs` 和 `config_info` **必须使用 SSH**，SNMP 不读取日志，也不导出运行配置；需要这两项时应选择 `hybrid`、`auto` 或 `ssh`，并配置有效 SSH 凭据。

新设备推荐 SNMPv3 `authPriv`，同时提供身份认证和报文加密，并按设备支持选择认证、加密算法。SNMPv2c Community 以无加密方式传输，仅为无法使用 v3 的旧设备保留；应限制到专用管理 VLAN、仅授予只读权限、用 ACL 限制 Worker 来源并定期轮换。系统只执行 SNMP GET/BULK WALK，不执行 SET；不要授予写权限。SNMP 和 SSH 秘密字段只写，不能出现在列表、导出、任务快照、结果或错误信息中。

| 现象 | 检查与处理 |
| --- | --- |
| 超时 / 不可达 | 从实际 Worker 节点检查到设备管理地址的路由、UDP/161（或自定义端口）、双向防火墙/ACL 和 SNMP 服务状态；核对地址、端口、超时及重试次数。不要用 Web 节点连通性代替 Worker 验证。 |
| 认证失败 | 核对 v2c Community，或 v3 用户名、安全级别、认证/加密协议及密码；确认设备侧用户绑定了同一算法和只读视图。错误详情不得粘贴或回显秘密。 |
| 不支持 OID / 项目缺失 | 确认设备已启用对应标准 MIB 或支持已登记的厂商 OID，并检查 SNMP view 是否允许读取。单项缺失可保留其他项目；不要把缺值误判为设备整体离线。 |
| `partial` | 查看结果中已完成与缺失的请求项目，并分别检查 SNMP 和 SSH 错误。`hybrid` 下任一协议的有效数据都会保留；修复失败协议后重新巡检。 |
| SSH fallback 未发生或失败 | 只有 `auto` 会把 SNMP 未返回的指标回退给 SSH；`hybrid` 仅将 `logs`/`config_info` 固定交给 SSH。核对模式、SSH 地址/端口/用户名/密码、设备命令权限和 Worker 到 TCP/22 的策略。 |

本功能的自动化验收仅使用内存 SNMP 会话和 mock SSH，**尚未在真实网络设备上测试**，且演示数据不会发起真实采集。生产启用前应在隔离管理网选择受控设备做只读 smoke test：验证 Worker 网络路径、v3 `authPriv`、所选项目、`partial` 展示及必要的 SSH 回退；不得执行 SNMP SET 或远程配置变更。

## 域控操作

域账号、域计算机写操作以及域账号表格导入由权限 `net.manage_domain_operations` 保护。域分组仅从域控同步展示。超级管理员自动拥有该权限；建议创建一个专用组并授予权限，而不是向日常账号逐一赋权：

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

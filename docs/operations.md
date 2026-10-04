# 人员同步、域控管理与 LDAPS

[返回项目首页](../README.md)

域控管理中的“从域控同步”会创建统一后台任务，不再在网页请求中执行 LDAP 同步。
管理员可在“域控连接设置”窗口保存定时同步：每隔 N 分钟/小时，或每天指定时间。
计划由现有 Worker 调度；同一时间只允许一个域控同步任务，恢复运行时只处理一次到期计划，不逐次补发停机期间的全部时点。
域控页面每页展示 10 次同步任务，详情显示账号、计算机和分组数量及失败原因，支持结束运行中的任务。
LDAP 读取后会在事务中更新本地目录；任务取消、租约失效或连接配置变更时不再应用旧结果。
此任务不参与设备告警，保存计划与手动同步沿用域控运维权限。

域控后台任务及计划使用统一任务表；升级和服务管理见[部署指南](../deploy/README.md)。

## 飞书/钉钉人员导入

“导入人员”使用固定平台页签，不需要新建来源名称：

1. 保存飞书 App ID / App Secret 或钉钉 App Key / App Secret，配置根部门和启用状态。
2. “测试连接”验证凭据可访问平台。
3. “预览数据”读取允许范围的完整部门/人员，校验工号等字段，展示新增/更新/停用差异。

   部门 ID 和上级用户 ID 会继续查询为名称（复用本次采集缓存）；无法读取详情或名称为空时预览失败，不把 ID 当名称写入。需要平台授予部门详情、用户详情读取权限和相应通讯录可见范围。

   人员导入的保存、测试、预览和确认导入在当前弹窗内更新；后台任务结束时，窗口内显示“查看结果 / 继续下一步”。校验失败返回可正常访问的列表地址，不会停留在仅支持 POST 的操作地址。
4. 预览通过后点击“导入数据”。
5. 查看完成结果和人员列表。

连接成功不等于有权限读取完整通讯录。预览需要接口权限和部门可见范围；接口错误、分页异常、空/冲突工号会导致失败，完整校验未通过不会悄悄写入部分人员。

按工号匹配，仅将**同一来源、完整结果中已不存在的人员**标记停用，不删除，也不将其他来源人员停用。首次确认缺失时，系统将当天记为离职日期；后续同步仍缺失时保留首次日期。接口失败、快照不完整和其他来源的人员不会因此写入离职日期。

连接测试/预览由 Worker 执行。运行提示可关闭，完成后显示通知；关闭提示不是取消。手工预览绑定浏览器会话，有效期 300 秒；过期、相关数据或配置变化后重新预览，不能重复应用旧结果。成功同步只更新最近同步时间，不使连接测试失效；修改平台凭据或同步范围仍需重新测试。同步完成会使此前生成的预览失效，即使本次没有人员变化，也不能重复应用。

各平台可设置间隔/每日自动同步。配置需先测试成功；修改凭据、范围或启用状态后需重新测试。自动同步无需浏览器常驻，后台完整校验和事务应用。到期后 Worker 补入队一次并计算下一次时间，不逐次补发停机期间的全部计划；“下次执行”不代表任务已经入队，必须同时看最近入队和任务结果。旧 /api/upload_people/ 已删除。

## 域控同步与展示

分别展示域账号、域计算机和域分组。账号/计算机按启用与停用统计，各有列表/详情。配置服务器、端口、SSL、绑定身份和 Base DN，使用 ldap3 同步本地数据。

域分组来自 AD Group，不是本地人员分类。同步同时保存直接成员关系、主组和实际 OU（含空 OU）。账户与计算机列表新增“所属分组”，可筛选、排序和按筛选导出；数据来自最近同步或成功执行的目录操作，不在打开列表时查询 AD。PC 分析关联 OU 前应先同步目录。

“所属分组”展示同步范围内的直接分组与标注的主组，不展开嵌套继承权限。AD 主组不在普通 `memberOf`/`member` 关系内，按对象 SID 与 `primaryGroupID` 补齐，参见 [Microsoft 主组说明](https://learn.microsoft.com/en-us/windows/win32/ad/security-properties)。分组总成员数保留 AD `member` 的直接成员口径，可能含未同步账户或嵌套分组，与窗口中的可管理账户/计算机数量不同。

域账号和域计算机方块分别显示启用对象超过设定天数未登录及缺少登录记录的数量；管理员可在右上角“未登录天数设置”弹窗修改阈值（默认 60 天，范围 1–36500 天，保存到数据库，两类对象共用）。保存后弹窗保持打开，方块统计局部更新。统计说明和最近成功同步时间放在“同步域控对象”方块。这些统计由本地数据库聚合得到，不会在打开页面时连接 AD。缺少登录记录不直接等同于长期未登录；`lastLogonTimestamp` 本身有复制延迟，旧版同步遗漏的日期需要重新同步一次才能补全。升级时应用全部迁移，并在同一数据库环境重启 Web/Worker。

绑定账号填写 `DOMAIN\user` 时使用 NTLM，填写 UPN（`user@example.com`）、完整 DN 或裸用户名时使用 SIMPLE（裸用户名按 Base DN 补成 UPN）。只尝试指定身份一次，不自动轮换账号格式，避免连续失败锁定账号。测试连接、同步和写操作共用同一连接实现；连接成功不代表拥有目录修改权限。

## 管理员批量操作

连接设置、测试/同步、目录写操作仅活跃的 staff 或超级用户可使用；普通账号即使拥有旧的 net.manage_domain_operations 权限，也不能执行这些操作。

| 对象 | 支持操作 |
| --- | --- |
| 域账号 | 添加用户、CSV/XLSX 批量创建、移动 OU、加入安全组、重置密码、下次登录改密、密码永不过期、解锁、启用/停用 |
| 域计算机 | 移动 OU、加入安全组、启用/停用、单机获取 BitLocker 恢复密钥 |
| 域分组 | 同步、列表和详情查询、弹窗搜索并移出选中的账户/计算机成员 |

加入安全组和移动 OU 从已同步目录选择，同名对象通过完整 DN 区分；没有选项或目标已失效时先重新同步。

分组列表点击蓝色“成员管理”打开当前页面弹窗，可切换现有成员和添加成员，按域账户或域计算机搜索、分页并批量选择。添加、移除均通过后台任务执行；移除只解除选中直接成员的关系，不删除对象，也不替换组内其他成员。主组禁止直接移出，Worker 执行前再次读取 AD 核对。成功后更新本地关系和成员数量；失败保留原镜像，可从任务查看原因并重试。

目录同步与修改任务互斥，避免同步旧快照覆盖刚完成的修改。加入组不等于替换全部成员关系。域用户表格导入会真实创建 AD 用户，不是普通本地导入。

升级应用 `0051_domain_memberships_and_ous` 后重启现有 Web/Worker，再执行一次域控同步填充分组关系和 OU 选项；迁移不连接或修改 AD，也不改写历史任务。

操作校验 DN、Base DN 范围和目标，由 Worker 执行并记录逐目标审计。部分失败保留成功项，可对支持的操作仅重试失败目标。

域计算机列表每行的“获取 BitLocker 密钥”仅管理员可用，在当前弹窗中按需读取该计算机 AD 子对象中的恢复密钥 ID、备份时间和 48 位恢复密码。需要域控连接启用 LDAPS、证书可信且绑定账号具备读取 BitLocker 恢复信息的权限；不会自动为终端启用 BitLocker 或上传密钥。未找到可见记录不代表设备未加密；有记录但密码不可见时提示检查权限。查询为单机限时只读请求，不创建后台任务或保存恢复密码，响应禁止缓存，关闭弹窗后清除显示内容。查询日志仅记录操作者、计算机 ID 和记录数量或失败状态。计算机解锁已禁止，历史操作记录保留；域账号解锁不变。AD 恢复信息的含义参见 [Microsoft 文档](https://learn.microsoft.com/zh-cn/windows/security/operating-system-security/data-protection/bitlocker/recovery-overview)。

域操作参考 `ad_core.py` 的 LDAP 分支实现，保留跨平台 ldap3，不引入依赖 Windows COM、进程全局凭据的 pyad。启用/停用和密码永不过期只修改对应 UAC 标志，不覆盖其他已读取状态。支持断言控件的目录保留并发条件更新；Windows AD 拒绝 RFC 4528 控件（错误 12）时，会重新读取最新状态、普通 LDAP 修改、回读核对。普通修改不能原子隔离外部管理员的同时更改，因此请勿同时从多个工具修改同一对象状态。权限不足、密码策略、目录对象缺失等返回明确提示，不因这些错误切换写入方式。域账号解锁使用 `lockoutTime=0`，移动 OU 保留原 RDN，加入分组使用成员追加；停用不会自动移动 OU。

添加用户/重置密码必须使用受信任证书的 LDAPS。一次性密码材料领取后删除；领取后中断不能盲目自动重放，应重新提交并输入新密码。见下文 [Windows 内部 CA 与 LDAPS 排障](#windows-内部-ca-与-ldaps-排障)。

---

## Windows 内部 CA 与 LDAPS 排障

[返回人员与域控指南](#域控同步与展示)

适用于 636 端口已开放、域控已有服务器证书，但连接报 `unable to get local issuer certificate` 的情况。当前项目通过 `ldap3.Tls(validate=ssl.CERT_REQUIRED)` 使用默认信任证书；在运行项目的 Windows 电脑上正确导入 CA 后，无需改代码或关闭证书验证。

以下域名 `dc01.example.com`、CA 名称 `Example-Root-CA`、`Example-Issuing-CA` 和目录 `C:\Projects\net` 均为示例，需替换为自己的环境。只导出 CA 公开证书，不需要私钥、密码或 `.pfx` 文件。

**1. 在域控或 CA 服务器导出证书链**

1. 按 `Win + R`，运行 `certlm.msc`，打开“证书—本地计算机”。
2. 在“个人 → 证书”中找到用于 LDAPS 的域控服务器证书，双击打开“证书路径”。核对证书 DNS 名称与项目填写的完整域名一致，并检查有效期。
3. 如果路径只有“根 CA → 域控服务器”，只需导出根 CA；如果是“根 CA → 中间/签发 CA → 域控服务器”，还需导出每一级中间 CA。不能只凭 CA 名称判断它是不是根 CA。
4. 在路径中选中最上面的根 CA，点击“查看证书 → 详细信息 → 复制到文件”。若询问私钥，选择“不导出私钥”；格式选“Base-64 编码 X.509（.CER）”，保存为 `root-ca.cer`。
5. 有中间 CA 时用同样方法分别导出，例如 `issuing-ca.cer`。不要把最下面的域控服务器证书当作根证书导入。

参见 [微软根 CA 导出说明](https://learn.microsoft.com/en-us/troubleshoot/windows-server/certificates-and-public-key-infrastructure-pki/export-root-certification-authority-certificate)。

**2. 在运行项目的 Windows 电脑导入证书**

从受管理的域控或 CA 复制证书，例如放到 `C:\Temp`，核对证书名称和指纹与源端一致。导入根 CA 会影响整台电脑的信任范围，只导入已确认属于本组织的 CA。Web 与 Worker 分别部署时，每台运行主机都要配置。

用管理员权限打开 `certlm.msc`，选择以下证书库，右键“证书 → 所有任务 → 导入”，选中文件并完成向导：

| 文件 | 本地计算机证书库 |
| --- | --- |
| `root-ca.cer` | 受信任的根证书颁发机构 → 证书 |
| `issuing-ca.cer`（若有） | 中间证书颁发机构 → 证书 |

使用本地计算机证书库，而不是 `certmgr.msc` 的当前用户证书库，避免 Web、Worker 使用其他账号时无法读取信任证书。参见 [微软证书库说明](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/trusted-root-certification-authorities-certificate-store)。

也可在管理员 PowerShell 中执行以下命令，与图形界面操作二选一：

~~~powershell
Import-Certificate -FilePath "C:\Temp\root-ca.cer" -CertStoreLocation "Cert:\LocalMachine\Root"

# 仅存在中间 CA 时执行，多级中间 CA 分别导入。
Import-Certificate -FilePath "C:\Temp\issuing-ca.cer" -CertStoreLocation "Cert:\LocalMachine\CA"
~~~

**3. 用项目实际运行的 Python 验证**

替换以下目录和域名。若部署使用其他虚拟环境，改用 Web/Worker 实际使用的 Python 路径，不要用另一套 Python 的成功结果代替验证。

~~~powershell
Set-Location "C:\Projects\net"

@'
import socket
import ssl

host = "dc01.example.com"
context = ssl.create_default_context()

with socket.create_connection((host, 636), timeout=5) as sock:
    with context.wrap_socket(sock, server_hostname=host) as tls:
        print("Certificate verification and LDAPS handshake succeeded")
        print("TLS:", tls.version())
'@ | .\.venv\Scripts\python.exe -
~~~

这个测试只验证网络和 TLS 证书，不提交域账号密码、不修改域控，也不代表 LDAP 绑定或 BitLocker 读取权限已验证。

| 错误 | 排查方向 |
| --- | --- |
| `unable to get local issuer certificate` | 是否导入实际运行主机的正确证书库；根 CA 是否对应；是否缺少中间证书 |
| `hostname mismatch` | 使用证书包含的完整 DNS 域名，不要随意换成 IP |
| `certificate has expired` | 检查证书链有效期和系统时间 |
| 超时或拒绝连接 | 检查 DNS、网络、防火墙、636 端口和域控 LDAPS 服务 |

**4. 配置项目并验证业务权限**

在“域控连接设置”中填写证书对应的完整域名（示例 `dc01.example.com`）、端口 `636`，开启 SSL，保留正确的绑定身份和 Base DN，点击“测试连接”。正常重启现有 Web 和 Worker，使进程使用更新后的配置，不要重复启动多套服务。无需数据库迁移或修改 Django `SECRET_KEY`。

连接通过后再到域计算机列表获取 BitLocker 密钥。若有恢复记录但密码不可读取，检查绑定账号对 AD 恢复信息的读取权限；信任 CA 不会自动授予目录权限。不要关闭证书校验或退回明文 LDAP 传输恢复密码来绕过错误。

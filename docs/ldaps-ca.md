# Windows 内部 CA 与 LDAPS 排障

[返回人员与域控指南](identity-management.md#域控同步与展示)

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

# Windows Server 巡检 HTTP 服务

在后台服务器列表下载 `InspectionHttpService.ps1`。首次安装先停止原来运行脚本的 PowerShell 窗口，再在被巡检服务器上以管理员身份打开 **Windows PowerShell 5.1**：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\InspectionHttpService.ps1 -Port 9180 -Token "与后台配置一致的令牌"
```

默认安装或更新 Windows 服务 `NetworkInspectionHttpService`，立即启动并设置开机自动运行；可以关闭终端。`-Install` 可显式指定安装模式，效果与默认相同。仅需前台排障时加 `-Console`，应先停止已安装服务，避免端口冲突。首次安装需要令牌；更新时省略 `-Port`、`-Token` 会沿用已安装配置。

服务由脚本内嵌的 .NET Framework 宿主运行，使用 LocalSystem。无需 Python、NSSM 或额外下载。宿主等待 HTTP 监听器启动才报告就绪；子进程意外退出会触发服务恢复重启，停止服务会同时结束巡检进程。

服务程序、`settings.json` 和 `service.log` 位于 `%ProgramData%\NetworkInspectionAgent`，目录仅管理员和 SYSTEM 可访问，服务命令行不包含令牌。日志达到约 2 MiB 时保留上一份轮转文件。不要删除该目录中的配置或安装标识。安装失败保留日志和标识以便安全重试；更新失败尝试恢复旧版本与配置。已有同名项目计划任务会停用但不删除。安装只管理自身的 Windows 服务，不修改其他业务服务的权限、启动类型或状态。

后台配置：

- 类型选择 Windows，IP 填被巡检服务器地址。
- 填相同巡检令牌；默认接口为 `http://IP:9180/inspection`。
- 只有端口、路径或 HTTPS 不同时才填写高级自定义地址。
- 安装创建所选端口的域/专用网络入站规则；公用网络不会自动开放。

更新时重新下载脚本，执行上述安装命令即可。不要仅覆盖原下载目录的文件后重启服务：真正运行的副本在 ProgramData 中，安装命令负责更新它。

```powershell
Get-Service NetworkInspectionHttpService
Restart-Service NetworkInspectionHttpService
Get-Content "$env:ProgramData\NetworkInspectionAgent\service.log" -Tail 30
```

权限不足的服务实例单独放在 `collection_errors.services`；其他已读到的记录继续返回。管理员身份不代表能绕过每个服务对象的访问控制。后台将部分证据标为提示；自动启动但当前停止的服务也可能由触发器管理，未取得触发器证据时不能直接判故障。

`network_info` 返回接口对象数组，每项包含 `interface`、`ipv4`、`gateway`、`dns`；地址缺失可为 `[]` 或 `[null]`，接口整体缺失或错误类型不视为有效证据。`services`、`logs` 只有在没有对应采集错误时，空数组才表示未发现相关记录。

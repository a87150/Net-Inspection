# Windows Server 巡检 HTTP 服务

在后台服务器列表下载 `InspectionHttpService.ps1`。首次安装先停止原来运行脚本的 PowerShell 窗口，再在被巡检服务器上以管理员身份打开 **Windows PowerShell 5.1 或 PowerShell 7**：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\InspectionHttpService.ps1 -Port 9180 -Token "与后台配置一致的令牌"
```

PowerShell 7 也可直接执行 `.\InspectionHttpService.ps1 -Port 9180 -Token "与后台配置一致的令牌"`。脚本会自动调用系统 Windows PowerShell 5.1，保留显式参数和管理员权限；系统仍须安装 Windows PowerShell 5.1。转交子进程仅使用 Windows PowerShell 系统模块目录，避免继承 PowerShell 7 模块后出现 `Get-Acl` / `Microsoft.PowerShell.Security` 无法加载；不修改系统或当前终端的模块路径。令牌通过进程管道传递，不放入子进程命令行或临时文件。旧版出现 `Run this script with Windows PowerShell 5.1` 时，重新下载新版，或使用上面的 `powershell.exe` 命令。

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

若 PowerShell 7 仅显示 `Windows PowerShell could not complete the operation (exit 1)`，这是旧版转交逻辑未保留子进程错误。重新下载新版，失败时会显示原始原因与脚本行号（令牌隐藏）。也可用本文开头的 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ...` 命令直接运行以取得错误；仅凭退出码不能判断是权限、端口还是服务安装问题。

Windows 巡检会将已选项目中有效的 CPU 型号、物理核数/逻辑处理器数、系统可见内存总容量，以及系统识别的磁盘设备总容量自动更新到服务器资料。内存和磁盘按 GiB 换算；磁盘使用 `Win32_DiskDrive`，不累加逻辑分区，虚拟机显示其虚拟磁盘。磁盘清单缺失、容量无效或设备重复时保留原值。使用率只保留在巡检记录中。旧脚本可更新已有的内存总容量和逻辑处理器数，CPU 型号及物理磁盘容量需要重新下载并执行新版安装命令后巡检；历史资料不自动回填。

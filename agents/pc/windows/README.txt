Windows PC 单文件 EXE 采集包

包内 PCCollector.exe 已嵌入采集脚本、共享目录配置和 OpenHardwareMonitorLib；不需要旁置 DLL 或监控界面。请完整解压 ZIP。

在客户端管理员 Windows PowerShell 5.1 中执行：
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-PCCollector.ps1
也可将此安装脚本作为域策略“计算机启动脚本”运行。普通用户登录脚本没有安装 SYSTEM 任务的权限。

安装到 %ProgramData%\PCDailyCollector，创建 PCDailyCollector 计划任务，以 SYSTEM/最高权限立即请求一次执行，之后每两小时及用户登录时尝试。重复安装更新同一任务，不重复创建。正在运行时需稍后重试更新。
共享目录及 NTFS 权限需要允许客户端计算机账户（域\计算机名$）写入。SYSTEM 不使用登录用户的网络凭据。

每天成功上传一次；两小时间隔是检查/失败重试，不覆盖当天日志。没有交互登录用户或采集结果缺少人员身份时等待，不写当天完成标记；登录后或下次定时再试。更新此逻辑需重新运行安装脚本更新客户端 EXE 和同名任务。预览当前数据：PCCollector.exe -Preview
只校验 EXE 内嵌文件和 PowerShell 运行环境，不采集/不上传：PCCollector.exe --self-test
运行要求：Windows PowerShell 5.1 / .NET Framework 4，CPU 驱动访问需要管理员或 SYSTEM。
运行中在当前账户临时目录解包，限制目录权限并在结束时清理。日标记和日志在运行账户 LocalAppData\PCDailyCollector；collector.log 超过 1 MiB 保留上一份。任务忽略重复实例、运行上限 20 分钟，EXE 内部采集超时 15 分钟。
任务状态：Get-ScheduledTaskInfo -TaskName PCDailyCollector

温度优先使用已有 WMI，再尝试内嵌硬件库。硬件/驱动策略不支持时仍可能没有温度；请查看采集诊断，不要关闭系统安全保护。EXE 为未签名的内部工具，可按组织的软件签名流程签名分发。

OpenHardwareMonitorLib 0.9.6 官方原版 SHA256:
ef02b0991aac678052bb79dfdfd5bfa0b42b1f34b209e35819ba606909655f58
来源: https://openhardwaremonitor.org/files/openhardwaremonitor-v0.9.6.zip
源代码: https://github.com/openhardwaremonitor/openhardwaremonitor/tree/v0.9.6
许可证: License.html。本包未修改硬件库。EXE 不加密配置，不能在采集配置中放入密码。

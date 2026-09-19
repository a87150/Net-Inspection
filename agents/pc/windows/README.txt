Windows PC 单文件 EXE 采集包

包内 PCCollector.exe 已嵌入采集脚本、监控 API 配置和 LibreHardwareMonitorLib；不需要旁置 DLL 或监控界面。请完整解压 ZIP。

在客户端管理员 Windows PowerShell 5.1 中执行：
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-PCCollector.ps1
也可将此安装脚本作为域策略“计算机启动脚本”运行。普通用户登录脚本没有安装 SYSTEM 任务的权限。

安装到 %ProgramData%\PCDailyCollector，创建 PCDailyCollector 计划任务，以 SYSTEM/最高权限立即请求一次执行，之后每两小时及用户登录时尝试。重复安装更新同一任务，不重复创建。正在运行时需稍后重试更新。
采集包内嵌监控 API 完整地址与专用 Bearer 令牌；令牌可发送日志，需按内部凭据处理。SYSTEM 不需要登录用户或共享目录凭据。

正常运行会先覆盖本地 latest.json，再输出并记录 PC_UPLOAD_COMPLETE；API 失败会输出 PC_UPLOAD_FAILED，保留本地文件并以非零退出。验证更新请使用 -Preview。

每两小时采集、覆盖并上报一次，不等待交互登录，也没有每日完成标记。Windows 任务运行身份为 SYSTEM 时，本地路径通常是系统账户的 LocalApplicationData\PCDailyCollector\latest.json；普通账户手动执行时为该账户的 LocalApplicationData。更新此逻辑需重新运行安装脚本更新客户端 EXE 和同名任务。预览当前数据：PCCollector.exe -Preview
只校验 EXE 内嵌文件和 PowerShell 运行环境，不采集/不上传：PCCollector.exe --self-test
运行要求：Windows 10/11、Windows PowerShell 5.1 / .NET Framework 4.7.2 或以上，CPU 驱动访问需要管理员或 SYSTEM。
运行中在当前账户临时目录解包，限制目录权限并在结束时清理。latest.json 和日志在运行账户 LocalAppData\PCDailyCollector；collector.log 超过 1 MiB 保留上一份。任务忽略重复实例、运行上限 20 分钟，EXE 内部采集超时 15 分钟。
任务状态：Get-ScheduledTaskInfo -TaskName PCDailyCollector

温度优先使用已有 WMI，再尝试内嵌硬件库。硬件/驱动策略不支持时仍可能没有温度；请查看采集诊断，不要关闭系统安全保护。EXE 为未签名的内部工具，可按组织的软件签名流程签名分发。

采集器更新：硬件信息只保留中文字段，删除英文别名和额外磁盘卷列表。磁盘只保留总量与一份摘要，摘要以精确字节数记录每卷容量/剩余空间，后台继续计算告警；旧格式日志仍可分析。

温度驱动：完整包带有 PawnIO_setup.exe。安装脚本检查 SHA256 和数字签名，缺失或低于 2.2.0 才静默安装。重复运行不重复安装。组织自行管理驱动时可传 -SkipTemperatureDriver；这不会保证 CPU 温度可读。不要使用旧包的安装脚本搭配新版 EXE。
PawnIO 是系统组件，安装后保留；采集器安装失败不会自动卸载它，避免影响其它硬件监控工具。计划任务本身仍尝试恢复旧 EXE 和任务。
温度诊断：CPU_TEMP_NOT_ELEVATED=未提升权限；CPU_TEMP_PAWNIO_MISSING=缺少合适驱动；CPU_TEMP_NO_SENSOR=没有有效 CPU 传感器；CPU_TEMP_LIBRARY_ERROR=运行库或依赖初始化失败。缺失温度保留 null，不能视为正常温度。

依赖均为官方原版，文件校验清单维护在项目 agents/pc/windows/sensors.json。
LibreHardwareMonitorLib 0.9.6 发布包 SHA256:
086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001
来源/源代码: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/tag/v0.9.6
PawnIO 2.2.0 安装器 SHA256:
1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032
来源: https://github.com/namazso/PawnIO.Setup/releases/tag/2.2.0
驱动源代码: https://github.com/namazso/PawnIO
监控模块源代码: https://github.com/namazso/PawnIO.Modules
许可证见 licenses/。本包未修改第三方库或驱动。EXE 不加密配置，不要在采集配置中放入密码。

# PC 日志采集与分析（0.9.1）

[返回项目首页](../README.md)

## 工作方式

Windows 计划任务以 SYSTEM、最高权限运行：安装后立即请求一次运行，随后每两小时及用户登录时触发；手动运行与定时运行相同。

Windows / macOS 采集器先本机原子覆盖 latest.json，再以 Bearer Token 将同一份 UTF-8 JSON POST 到 /api/pc/logs/。Web 校验后写入数据库；手动和定时分析从数据库为每台 PC 选择最新日志并冻结入队，Worker 保存分析结果、问题与通知。最新按日志时间排序，不按上传到达时间排序；分析不会远程启动采集器。

## 配置、部署与安全

管理员在“日志分析记录”的新版配置弹窗保存 PCUploadConfig 后下载采集包。配置有 endpoint_url（完整 HTTP(S) 地址，不能含账号、查询参数或片段）、is_enabled 和 log_retention。首次保存自动生成令牌，页面不再显示；重置令牌后旧包失效，必须重新下载并部署。普通用户不能配置、下载、执行或读取令牌。

令牌以 PC_LOG_SOURCE_ENCRYPTION_KEY 的 Fernet 密钥加密保存，并另存哈希校验。Web 与 Worker 必须使用同一稳定密钥；更换或丢失密钥后应重置令牌、重新下载部署包。

Windows 包安装到 %ProgramData%\PCDailyCollector，并建立唯一的 PCDailyCollector 任务。安装后立即请求运行，之后每两小时及用户登录触发。每次先原子替换 LocalApplicationData\PCDailyCollector\latest.json，再最多重试三次上传。任务以 SYSTEM 运行时，该 LocalApplicationData 位于 SYSTEM 配置文件；普通用户手动运行时位于该用户目录。API 失败保留本地文件并以非零退出。-Preview 只输出 JSON，-PackageSelfTest 只检查传感器库加载。

macOS 脚本同样先覆盖 ~/Library/Application Support/PCDailyCollector/latest.json，再上传该 JSON。两端都不把 local latest.json 作为服务器重放队列。

Windows 温度采集使用 LibreHardwareMonitor 的 CPU 传感器，只使用有效 CPU 温度，不以 GPU 或主板温区替代。安装脚本在需要时校验并安装包内签名、固定校验值的 PawnIO 2.2.0+ 驱动；已由组织管理时可用 -SkipTemperatureDriver，但温度仍可能因权限、硬件或驱动策略不可用。

## API、留存与页面

POST /api/pc/logs/ 仅接受 Content-Type: application/json 与 Authorization: Bearer <token>。请求是小于等于 16 MiB 的 UTF-8 JSON，至少含 platform（windows 或 macos）、日志时间和系统信息概览.计算机名；日志时间不能晚于平台当前时间十分钟。令牌仅授权写入。成功返回 201（新日志）或 200（重复/较旧日志忽略）以及 status、普通数值 log_id；认证、类型、大小、内容、暂存错误分别为 401、415、413、400、503。

日志 daily_latest 按 PC 加日志日期保留该日最新版本，往日仍保留；all 保留全部版本。排队或运行中的日志会待任务结束后再清理。分析配置的 analysis_retention 独立按分析日期实行 daily_latest 或 all。分析 all 不因日志清理而删除。

分析保存结果、规则快照、来源采集时间和普通 log_id 标识，不复制完整原始日志，也不建立数据库外键。详情按需以 log_id 读取原始日志；日志被保留策略清理后页面提示原始日志不可用，分析结果仍可查看。页面有日志列表和详情、筛选批量分析，以及分析结果筛选后紧邻的导出入口。

## 升级

升级 0.9.1 时备份数据库和稳定密钥，停止 Web/Worker，更新代码并执行迁移，再重启两者。重新下载并部署每台终端包。迁移会移除旧 PC 来源、传输、归档表，并清空旧 PC 日志、分析和相关任务；当前版本只支持 API 上报。

# PC 日志与分析合同（0.9.1）

PC 日志以 UTF-8 JSON 直传 POST /api/pc/logs/。这是仅写入的 Bearer Token API：不创建网站会话，也不授予读取、下载或执行权限。服务端不扫描共享目录、FTP/FTPS，不远程运行 PowerShell 或登录终端。

## 接收和存储

请求必须是 application/json、小于等于 16 MiB，并带 Authorization: Bearer <token>。JSON 必须有 platform（windows 或 macos）、可解析的 日志时间 和 系统信息概览.计算机名；日志时间不能比服务端当前时间晚十分钟。相同内容哈希返回原有 log_id。日志为 daily_latest 时，同一 PC 同一日志日期的较旧版本返回已保存的较新 log_id。

新日志按顶级字段拆到 JSON 列：已知采集区段进入对应列，出现的区段名进入 present_sections，其余顶级字段进入 extra_fields；读取时再组合逻辑 payload。旧记录仍用 legacy_payload 保存原始 JSON。0053_pc_api_logs 回填可得到的 collected_at 等字段，不删除历史记录。

## 选择性分析

仅任务选中的项目生成结果和问题。缺少字段为 missing；空值、结构错误、未知状态或无效嵌套值为 unknown，不会伪造成正常数据。

| 项目 | 主要字段 | 语义 |
| --- | --- | --- |
| activation | Windows激活信息 | 已授权为正常；KMS 配置存在时检查。 |
| software / processes | 已安装软件列表 / 当前运行进程清单 | 空数组表示已采集但无条目；软件可使用白名单或黑名单。 |
| bitlocker | BitLocker状态.磁盘卷信息 | 空卷不能证明加密状态。 |
| defender / patches | WindowsDefender状态 / 系统更新历史 | 按配置时限检查。 |
| domain / system / uptime | 域通信和策略、系统信息概览 | 按域、版本、开机时长检查；macOS 不执行 Windows 专用项目。 |
| resource / disk | 计算机硬件资源情况 | CPU/内存百分比为 0–100；磁盘使用率取有效容量与空闲量。 |
| event_findings | 事件发现（兼容旧事件日志） | 警告、错误、严重事件形成问题。 |

日期接受 YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DD。success 仅表示所选项目满足已实现规则，不是完整安全或合规认证。

## 入队、引用和留存

手动和定时分析选择每台 PC 的最新保留日志，按 collected_at 再按 ID 排序，并在入队时冻结。分析保存普通 log_id、来源采集时间、规则快照、结果和问题；不保存完整原始日志，也不建立日志外键。

日志 daily_latest 每台 PC 每日志日期仅保留最新版本，往日保留；all 保留所有版本。分析的 analysis_retention 独立按分析日期管理 daily_latest 或 all。日志清理不删除分析结果；详情按需用 log_id 读原始日志，已清理时页面说明不可用。已被排队或运行任务选中的日志延后至任务结束清理，分析 all 的结果不随日志删除。

PCUploadConfig 只有一份，含 endpoint_url、is_enabled、log_retention。首次保存生成令牌，以 PC_LOG_SOURCE_ENCRYPTION_KEY 加密且不回显；重置后重新下载部署包。普通用户不能配置、下载、执行或读取令牌。规则在入队时冻结。真实迁移与终端 API 连通性仍需部署验收。

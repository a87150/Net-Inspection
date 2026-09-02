# Windows Server 巡检 HTTP 服务

以管理员身份打开 PowerShell，在本目录执行：

```powershell
.\Install-InspectionHttpService.ps1 -Port 9180 -Token "请设置一个随机令牌"
```

安装脚本会创建开机自启动的 SYSTEM 计划任务和入站防火墙规则。系统中的 Windows 服务器资产应配置：

- 服务器类型：`Windows`
- Windows API 地址：`http://服务器IP:9180/inspection`
- API 令牌：与安装时的 Token 一致

### 网络证据格式

选择 `network_info` 时，服务返回接口对象数组，例如：

```json
{"network_info":[{"interface":"Ethernet","ipv4":["192.0.2.40"],"gateway":["192.0.2.1"],"dns":["192.0.2.53","2001:db8::53"]}]}
```

每个对象必须有非空接口名和三个地址数组；`ipv4`、`gateway` 中为 IPv4，`dns` 可为 IPv4/IPv6。
无接口的显式 `[]` 是有效采集结果。无地址/网关时，PowerShell 可返回 `[]` 或 `[null]`，两者均保留。
字段缺失、顶层 `null`、任意字典或错误类型/地址不视为有效证据；与其他项目一起采集时，仅有效项目保留，状态为部分成功。
采集器现有 `network` 字段别名继续支持同一数组格式，不改变内容。此格式不是 Linux SSH 的 `interfaces/routes` 字典。

检查服务：

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:9180/inspection -Headers @{Authorization="Bearer 请设置一个随机令牌"}
```

卸载时执行：

```powershell
Stop-ScheduledTask -TaskName NetworkInspectionHttpService
Unregister-ScheduledTask -TaskName NetworkInspectionHttpService -Confirm:$false
```

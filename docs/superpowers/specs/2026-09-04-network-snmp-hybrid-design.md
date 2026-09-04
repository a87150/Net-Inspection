# 网络设备 SNMP 与 SSH 混合巡检设计

## 目标

在保留现有 SSH 巡检、日志采集和配置导出的基础上，为网络设备增加 SNMPv2c/SNMPv3 主动轮询。设备可以选择 `ssh`、`snmp`、`hybrid` 或 `auto` 四种巡检方式；所有方式继续使用现有任务队列、独立 Worker、多线程目标执行、巡检记录、异常和告警链路。

本阶段不实现 SNMP Trap、SNMP SET、自动发现设备、远程配置修改或完整厂商私有 MIB 库。

## 方案选择

采用独立 SNMP 采集器和网络设备协议编排器，而不是把 SNMP 分支继续堆入现有 SSH 文件。SNMP 采集器只负责 OID 查询和规范化，SSH 采集器保持现状；协议编排器根据连接方式和巡检项目调用一个或两个采集器并合并 `CollectionResult`。

相比用系统 `snmpget/snmpwalk` 命令，该方案不依赖 Windows/Linux 外部程序。项目增加 `pysnmp==7.1.29`，使用 PySNMP 7.1 的 asyncio v3arch API，同时在采集器边界封装为同步函数，适配现有线程 Worker。

## 设备配置模型

沿用 `Network_Device.connection_type`，规范支持以下值：

- `ssh`：所有当前项目使用 SSH，保持原有行为。
- `snmp`：只执行 SNMP 支持的项目；日志和配置会明确标记为不支持，而不是静默成功。
- `hybrid`：设备信息、CPU、内存、温度、接口和 VLAN 使用 SNMP；日志和配置使用 SSH。任一协议失败时保留另一协议的有效结果，任务状态为部分成功或失败。
- `auto`：SNMP 优先获取动态项目；SNMP 未返回有效证据的项目使用 SSH 回退；日志和配置仍直接使用 SSH。

新增非秘密字段：

- `snmp_version`：`v2c` 或 `v3`，默认 `v2c`。
- `snmp_port`：默认 `161`。
- `snmp_security_level`：`noAuthNoPriv`、`authNoPriv`、`authPriv`。
- `snmp_username`、`snmp_auth_protocol`、`snmp_priv_protocol`、`snmp_context_name`。
- `snmp_retries`：默认 `1`，限制为 0–5。

新增只写秘密字段：

- `snmp_community`。
- `snmp_auth_password`。
- `snmp_priv_password`。

SNMPv2c 要求 community；SNMPv3 要求用户名，并根据安全级别要求认证密码或认证与加密密码。支持 SHA-1/SHA-224/SHA-256/SHA-384/SHA-512 认证和 AES-128/AES-192/AES-256 加密。设备导入模板和 Django 管理页面都能设置这些字段；空白秘密在编辑已有设备时保留原值。秘密不进入资产列表、搜索字段、任务快照、巡检详情或错误信息。

## SNMP 数据范围

第一版使用数字 OID，避免运行时下载 MIB：

- 设备信息：`sysDescr`、`sysObjectID`、`sysUpTime`、`sysName`。
- 接口：`ifDescr/ifName`、MAC、管理状态、运行状态、速率、HC 入站/出站字节、丢弃和错误计数。
- VLAN：优先读取 Q-BRIDGE-MIB 的 VLAN 名称表；设备不支持时该项目返回无有效证据。
- CPU：优先厂商 OID 候选，其次 HOST-RESOURCES-MIB 处理器负载，多个实例取平均值。
- 内存：优先厂商 OID 候选，其次 HOST-RESOURCES-MIB 存储表中 RAM 条目。
- 温度：读取 ENTITY-SENSOR-MIB 温度传感器，忽略无法验证单位或数值的条目。

厂商 OID 通过只读 Python 注册表隔离，首批包含 Cisco、Huawei、H3C 和 Ruijie 的候选路径。解析器必须允许单个 OID 不存在；只有实际取得并成功规范化的项目才算完成。

## 采集与合并接口

新增：

- `net.devices.network.snmp.collect_network_snmp(device, timeout=12, selected_items=None) -> CollectionResult`
- `net.devices.network.collector.collect_network(device, timeout=12, selected_items=None) -> CollectionResult`

SNMP 采集器通过可替换的 `SnmpSession` 查询接口测试，不访问真实设备。生产 `PySnmpSession` 负责 PySNMP 鉴权、GET/BULK WALK、超时、重试和关闭 dispatcher。

协议编排器按照项目拆分调用：

- SNMP 项目：`device_info`、`cpu`、`memory`、`temperature`、`interface_status`、`vlan_status`。
- SSH 项目：`logs`、`config_info`；在 `ssh` 模式下包括全部项目，在 `auto` 模式下还包括 SNMP 缺失项目。

合并时项目级数据不互相覆盖；`raw` 证据保留项目名或 SSH 命令键，并继续经过现有脱敏与选择过滤。总体状态根据请求项目是否都有有效证据计算：全部具备为成功，部分具备为部分成功，全部缺失为失败。

## 任务和错误处理

任务入队时冻结连接方式、SNMP 版本、端口、安全级别、协议名、上下文和重试次数，但不冻结任何 SNMP/SSH 秘密。Worker 执行目标前从设备表读取最新秘密，和当前 SSH 凭据策略一致。

错误消息只记录固定类别，包括未配置凭据、认证失败、超时、不可达、SNMP 响应错误、项目无有效 OID 和依赖缺失。不得保存 community、用户名、认证/加密密码、远端响应中的秘密或异常原文中的秘密。

在混合模式下，SNMP 失败不阻止 SSH 日志/配置采集，SSH 失败也不丢弃 SNMP 指标。现有巡检记录、异常记录和告警继续以合并后的最终状态处理。

## 导入、展示和管理

- 网络设备 CSV/XLSX 模板增加连接方式及 SNMP 字段，并提供不包含真实秘密的混合模式示例。
- 网络设备列表增加 SNMP 版本和 SNMP 端口作为可选列；秘密不展示、不导出。
- Django 管理页面将 SNMP community、认证密码和加密密码显示为只写密码框，列表仅显示方式、版本和端口。
- 演示数据使用不可连接的文档地址和 `DEMO-ONLY-NOT-A-SECRET`，不启用真实轮询。

## 兼容与迁移

已有 `connection_type` 为空或非新枚举值的设备按 `ssh` 处理，迁移把标准 `ssh` 设为默认，不删除已有设备或巡检记录。现有 SSH 命令、配置导出和调用接口保持可用；架构兼容模块 `net.devices.network.ssh` 不变。

## 验证

- 模型、导入模板和管理页面验证 SNMPv2c/v3 字段及秘密保护。
- SNMP 单元测试使用内存会话覆盖设备信息、接口、CPU、内存、温度、VLAN、超时和认证错误。
- 协议编排测试覆盖四种连接方式、混合部分成功、自动回退和 SSH 日志/配置路由。
- Worker 集成测试 mock 两个协议采集器，不连接真实网络设备。
- 最终运行相关 Django 测试、依赖检查、系统检查和迁移漂移检查；不执行真实 SNMP、SSH 或配置采集。

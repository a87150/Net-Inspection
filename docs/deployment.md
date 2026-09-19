# 部署补充导航

正式安装、环境配置、运行身份、日志、升级与启停统一见[一键部署指南](../deploy/README.md)。旧手工部署请先核对并停止原服务，不要同时运行多个入口。

## PC 来源与采集脚本

见 [PC 日志采集与分析](pc-collection.md)，包含 API 配置、终端 EXE、域启动脚本、按日留存与失败恢复。终端必须能够访问 Web 的上传端点。

## 网络设备 SNMP / SSH 巡检

见 [设备巡检指南](device-inspection.md#网络设备sshsnmp-与深信服-api)。网络请求从 Worker 发出，检查它到设备的 SSH TCP 端口和 SNMP UDP 端口、返回流量、只读视图与账号。新设备默认 auto；项目显式指定协议时按项目设置执行，不以 Ping 通代替采集验收。

## 域控操作

见 [人员与域控管理](identity-management.md#管理员批量操作)和 [LDAPS 证书指南](ldaps-ca.md)。业务管理权限按活跃 staff/超级用户判断，旧 `net.manage_domain_operations` 权限组不单独授予操作权限。一次性域密码任务需要稳定的 `DOMAIN_OPERATION_ENCRYPTION_KEY`，领取后中断应重新提交，不能直接重放。

上线验收需在授权的测试设备、测试 OU 或平台范围内验证真实连接及读写结果。隔离单元测试通过不表示生产网络、证书、目录权限已验证；不要对真实用户随意执行域修改或发送外部通知。

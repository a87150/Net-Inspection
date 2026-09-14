"""Synthetic device field examples shared by forms and inventory import help."""

SNMP_EXAMPLES = {
    'snmp_version': ('v2c', '一般选择 v2c；设备启用 SNMPv3 时选择 v3。'),
    'snmp_port': ('161', '通常保持 161，范围 1–65535。'),
    'snmp_community': ('CHANGE-ME', '替换为设备实际只读 Community；仅用于 v2c。'),
    'snmp_username': ('snmp-reader', '与设备上的 SNMPv3 用户一致。'),
    'snmp_security_level': ('authPriv', 'noAuthNoPriv 仅用户名；authNoPriv 加认证；authPriv 再加加密。'),
    'snmp_auth_protocol': ('sha256', '可填 md5、sha1、sha224、sha256、sha384、sha512，与设备一致。'),
    'snmp_auth_password': ('CHANGE-ME', '替换为 SNMPv3 认证密码。'),
    'snmp_priv_protocol': ('aes128', '可填 des、aes128、aes192、aes256，与设备一致。'),
    'snmp_priv_password': ('CHANGE-ME', '替换为 SNMPv3 加密密码。'),
    'snmp_context_name': ('context1', '仅设备配置了特定上下文时填写，通常留空。'),
    'snmp_retries': ('1', '失败后的重试次数，范围 0–5。'),
}
COMMON_EXAMPLES = {
    'ip': ('192.0.2.10', '填写设备管理 IP，不加协议、端口或路径。'),
    'port': ('22', 'SSH 管理端口，范围 1–65535。'),
    'username': ('readonly', '填写设备实际 SSH 登录账号。'),
    'password': ('CHANGE-ME', '替换为实际 SSH 密码；编辑时留空保留原密码。'),
    'verify_ssl': ('是', 'HTTPS 通常启用证书校验；导入填写 是/否 或 true/false。'),
}
DEVICE_EXAMPLES = {
    'networks': {
        **COMMON_EXAMPLES, **SNMP_EXAMPLES,
        'device_name': ('核心交换机', '方便辨认设备的名称。'),
        'device_type': ('switch', 'router 路由器、switch 交换机、ac 无线控制器、ap 无线 AP、ac_gateway 安全网关、firewall 防火墙、other 其他。'),
        'vendor': ('huawei', 'huawei 华为、h3c 华三、ruijie 锐捷、cisco 思科、sangfor 深信服、generic 其他。'),
        'connection_type': ('auto', 'auto 默认 SNMP + SSH；也可填 hybrid、snmp、ssh；深信服 AC 填 sangfor_api。'),
        'api_url': ('http://192.0.2.13:9999', '深信服开放 API 根地址，按实际协议和端口填写，不加 /v1 路径。'),
        'api_shared_secret': ('CHANGE-ME', '替换为深信服开放接口共享密钥，不是设备登录密码。'),
        'os_version': ('VRP V200R019C10', '可选，用于匹配系统版本模板；成功采集版本后自动更新。留空按厂商和类型匹配。'),
    },
    'servers': {
        **COMMON_EXAMPLES,
        'name': ('应用服务器', '方便辨认设备的名称。'),
        'ip': ('192.0.2.20', 'Linux 与 Windows 都填写设备管理 IP，不加端口。'),
        'server_type': ('Linux', '填写 Linux 或 Windows；Linux 用 SSH，Windows 用巡检脚本 HTTP 服务。'),
        'port': ('22', '仅 Linux SSH 使用；Windows 脚本默认 9180，改端口请填写自定义巡检地址。'),
        'os_version': ('24.04', '可选：Ubuntu 可填 24.04，Windows 可填 10.0.20348；成功采集后自动更新。服务器模板按 Linux/Windows 类型选择。'),
        'api_url': ('http://192.0.2.21:9180/inspection', '仅 Windows；通常留空，自动使用设备 IP 与 9180。改过脚本地址才填此项。'),
        'api_token': ('CHANGE-ME', '仅 Windows；替换为 InspectionHttpService.ps1 的 Token，Linux 留空。'),
    },
    'monitors': {
        'device_name': ('前门摄像机', '方便辨认设备的名称，如机房录像机、前门门禁。'),
        'ip': ('192.0.2.30', '设备管理 IP，也用于 Ping 在线检查。'),
        'device_type': ('camera', 'camera 摄像机、nvr 录像机、access 门禁、other 其他。'),
        'vendor': ('hikvision', 'hikvision 海康、dahua 大华、uniview 宇视、tiandy 天地伟业、zkteco 中控、generic 其他。'),
        'api_url': ('https://192.0.2.30/api/status', '仅演示地址格式，并非厂商通用接口；请按实际接口填写。无 API 可留空，在巡检设置中选择 SNMP 或 Ping。'),
        'api_username': ('readonly', '使用账号密码认证时填写实际只读账号；仅令牌认证时留空。'),
        'api_password': ('CHANGE-ME', '替换为实际 API 密码；编辑时留空保留原密码。'),
        'api_token': ('CHANGE-ME', '仅接口要求令牌认证时填写；编辑时留空保留原令牌。'),
        'verify_ssl': COMMON_EXAMPLES['verify_ssl'],
    },
}

"""Examples for PC sources and inspection parameters; never submitted defaults."""
from index.common.form_examples import apply_field_examples
from net.data_exchange.inventory_guidance import SNMP_EXAMPLES

PC_SOURCE_EXAMPLES = {
    'shared_path': (r'\\192.0.2.40\PCLogs\incoming', '填写终端日志汇总目录的 UNC 路径。'),
    'ftp_directory': ('/logs/incoming', 'FTP 中存放日志的目录，根目录填 /。'),
    'host': ('192.0.2.40', 'FTP 主机 IP 或名称，不加 ftp://。'),
    'username': (r'EXAMPLE\log-reader', 'SMB 默认使用进程身份；手动凭据才填，FTP 可填 log-reader。'),
    'password': ('CHANGE-ME', '填写实际连接密码；SMB 使用进程身份时不需要。'),
    'port': ('445', '共享目录默认 445；FTP/显式 FTPS 默认 21。'),
    'domain': ('EXAMPLE', '账号未包含域名前缀时可单独指定，通常留空。'),
    'local_staging_directory': (r'C:\Inspection\pc-staging', 'Worker 本地绝对路径；留空自动填写。Linux 可填 /var/lib/inspection/pc-staging。'),
    'remote_processed_directory': ('incoming/_processed', '相对于远程根目录，留空自动填写。'),
    'remote_failed_directory': ('incoming/_failed', '相对于远程根目录，不要与已处理目录重叠。'),
    'terminal_windows_path': (r'\\192.0.2.40\PCLogs\incoming', '采集脚本写入路径；共享来源自动填写。'),
    'terminal_macos_path': ('/Volumes/PCLogs/incoming', '终端挂载共享目录后的路径。'),
    'recent_days': ('7', '读取最近 7 天的日志。'),
    'range_start_date': ('2026-01-01', '日期范围的开始日期，格式 YYYY-MM-DD。'),
    'range_end_date': ('2026-01-07', '日期范围的结束日期，不能早于开始日期。'),
}
PROFILE_EXAMPLES = {
    'name': ('每日巡检', '填写便于区分用途的配置名称。'),
    'timeout_seconds': ('60', '单个目标执行超时秒数，范围 1–3600。'),
    'concurrent_workers': ('4', '同时处理的目标数，范围 1–64。'),
    'interval_value': ('2', '配合“小时”表示每 2 小时执行；配合“分钟”表示每 2 分钟执行。'),
    'daily_time': ('09:30', '按项目配置时区每天执行，格式 HH:MM。'),
    'cpu_max_percent': ('80', '填写 80 表示 CPU 使用率超过 80% 告警，不填百分号。'),
    'memory_max_percent': ('85', '填写内存使用率百分数，不填百分号。'),
    'disk_max_percent': ('90', '填写磁盘使用率百分数，不是磁盘容量。'),
    'cpu_temperature_max_celsius': ('85', '温度单位 ℃，只填数字。'),
    'uptime_max_hours': ('168', '连续开机 7 天为 168 小时。'),
    'minimum_windows_release': ('22H2', '填写 Windows 发布版本，例如 22H2，不填设备名称。'),
    'defender_update_max_days': ('7', '病毒库更新的最大间隔天数。'),
    'defender_scan_max_days': ('7', 'Defender 扫描的最大间隔天数。'),
    'patch_max_days': ('30', '系统补丁的最大间隔天数。'),
    'site_ip_prefixes': ('{"192.0.2.0/24": "示例站点"}', 'JSON 对象，网段作为键，站点名称作为值。'),
    'kms_servers_text': ('kms.example.invalid', '每行一个允许的 KMS 服务器地址。'),
}


def collection_examples(form):
    examples = {
        **SNMP_EXAMPLES,
        'snmp_oids': ('{"sys_descr": "1.3.6.1.2.1.1.1.0"}', 'JSON 对象；指标键需与采集器对应，OID 依实际设备 MIB 填写。'),
        'mib_modules': ('[{"name":"EXAMPLE-MIB","symbols":{"sysDescr":"1.3.6.1.2.1.1.1"}}]', '已导入符号映射示例；也可直接上传厂商 .mib 文件。'),
        'mib_file': ('VENDOR-MIB.mib', '上传厂商提供的 ASN.1 文本文件。'),
        'protocol': ('ping', '没有 API/SNMP 时选择仅 Ping；只能判断在线，不能采集硬件或业务指标。'),
    }
    command_examples = {
        'device_info': 'display version', 'cpu': 'display cpu-usage',
        'memory': 'display memory-usage', 'temperature': 'display environment',
        'interface_status': 'display interface brief', 'vlan_status': 'display vlan',
        'logs': 'display logbuffer', 'routing_table': 'display ip routing-table',
        'arp_table': 'display arp all', 'mac_table': 'display mac-address',
        'lldp_neighbors': 'display lldp neighbor', 'wireless_aps': 'display ap all',
        'wireless_clients': 'display station all',
    }
    scalar_oids = {
        'cpu': '1.3.6.1.4.1.9.2.1.56.0', 'memory_total': '1.3.6.1.4.1.9.2.1.8.0',
        'memory_used': '1.3.6.1.4.1.9.2.1.9.0', 'temperature': '1.3.6.1.4.1.9.2.1.58.0',
    }
    for name in form.fields:
        if name.startswith('threshold_'):
            unit = '℃' if name == 'threshold_temperature' else '%'
            examples[name] = ('80', f'数字 80 表示 80{unit}，不要填写单位；留空继承。')
        elif name.startswith('snmp_oid_'):
            examples[name] = (scalar_oids[name.removeprefix('snmp_oid_')], '思科标量 OID 格式示例；必须按实际厂商、型号及实例修改，不能直接套用到所有设备。')
        elif name.startswith('snmp_scale_'):
            examples[name] = ('0.1', '例如原值 650 代表 65℃ 时填 0.1；无需换算填 1。')
        elif name.startswith('snmp_offset_'):
            examples[name] = ('0', '通常不偏移；结果 = 原值 × 倍率 + 偏移。')
        elif name.startswith('commands_'):
            examples[name] = (command_examples.get(name.removeprefix('commands_'), 'display version'), '华为命令格式示例，每行一条；须替换为当前厂商及巡检项目的只读命令。留空继承。')
        elif name == 'template_cpu':
            example = r'CPU Usage:\s*(?P<usage_percent>\d+(?:\.\d+)?)%'
            examples[name] = (example, 'CPU 正则示例，仅配合正则解析；其他项目按其结构字段调整。TextFSM 模式填写完整模板，先用实际回显测试。')
        elif name.startswith('flags_'):
            examples[name] = ('im', 'i 忽略大小写，m 多行，s 让点匹配换行；可组合，留空不启用。')
        elif name.startswith('empty_'):
            examples[name] = (r'^No entries found\.$', '仅填写设备明确返回空表的提示正则，不用来忽略采集失败。')
        elif name == 'template_memory':
            examples[name] = (r'Memory Using Percentage:\s*(?P<usage_percent>\d+(?:\.\d+)?)%', '内存百分比正则示例；TextFSM 模式需填写完整模板。')
        elif name == 'template_device_info':
            examples[name] = (r'Version\s+(?P<version>[^\r\n]+)', '设备版本正则示例，version 字段用于回填台账版本。')
        elif name.startswith('template_'):
            examples[name] = (r'^(?P<name>\S+)\s+(?P<state>\S+)$', '两列表格的正则语法示例；字段名及列结构必须对应当前项目，请参照父模板的完整规则调整。TextFSM 需填写完整模板。')
        elif name.startswith('sample_'):
            sample = {'sample_cpu': 'CPU Usage: 35%', 'sample_memory': 'Memory Using Percentage: 60%', 'sample_device_info': 'Version V200R019C10'}.get(name, '示例对象 up')
            examples[name] = (sample, '仅演示回显格式；测试时粘贴当前项目实际回显，不保存此内容。')
    apply_field_examples(form, examples)

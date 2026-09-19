from dataclasses import dataclass
from net.inspections.issues import RULES

PROBLEM_TYPE_CHOICES = tuple((label, label) for label in dict.fromkeys(
    [category for category, _label in RULES.values()] + ['其他']))


@dataclass(frozen=True)
class TableField:
    key: str
    label: str
    source: str
    kind: str
    default_visible: bool
    default_filter: bool
    sortable: bool
    choices: tuple[tuple[str, str], ...] = ()
    comparison: str = 'exact'
    filterable: bool = True
    option_mode: str = 'none'
    option_limit: int = 100
    export_source: str = ''
    query_source: str = ''


@dataclass(frozen=True)
class TableDefinition:
    key: str
    title: str
    fields: tuple[TableField, ...]
    default_sort: str
    default_order: str
    search_fields: tuple[str, ...]
    default_page_size: int
    complete_options: bool = False
    record_semantics: bool = False


def _field(
    key, label, kind='text', *, source=None, visible=True, filterable=True,
    sortable=True, choices=(), comparison='exact', option_mode=None,
    option_limit=100, default_filter=None, export_source='',
):
    choices = tuple(choices)
    if option_mode is None:
        if choices:
            option_mode = 'fixed'
        elif kind == 'choice':
            option_mode = 'distinct'
        elif kind == 'text':
            option_mode = 'suggest'
        else:
            option_mode = 'none'
    if default_filter is None:
        default_filter = filterable
    return TableField(
        key=key,
        label=label,
        source=source or key,
        kind=kind,
        default_visible=visible,
        default_filter=default_filter,
        sortable=sortable,
        choices=choices,
        comparison=comparison,
        filterable=filterable,
        option_mode=option_mode,
        option_limit=option_limit,
        export_source=export_source,
    )


TABLE_DEFINITIONS = {
    'people': TableDefinition(
        'people', '人员', (
            _field('name', '姓名'),
            _field('employee_id', '工号'),
            _field('department', '部门', 'choice'),
            _field('email', '邮箱', default_filter=False),
            _field('phone', '手机号', default_filter=False),
            _field('leader', '上级', default_filter=False),
            _field('is_active', '是否启用', 'boolean', default_filter=False),
            _field('hire_date', '入职日期', 'date', default_filter=False),
            _field('departure_date', '离职日期', 'date', default_filter=False),
            _field('source', '数据来源', 'choice', visible=False, default_filter=False),
            _field('platform_user_id', '平台用户 ID', visible=False, default_filter=False),
            _field('last_synced_at', '最后同步时间', 'datetime', visible=False, default_filter=False),
        ), 'name', 'asc', ('name', 'employee_id', 'department', 'email', 'phone', 'leader'), 20,
    ),
    'computers': TableDefinition(
        'computers', 'PC', (
            _field('computer_name', 'PC 名称'),
            _field('ip_addresses', 'IP 地址'),
            _field('os', '操作系统'),
            _field('user_name', '当前用户', default_filter=False),
            _field('last_report_at', '最后上报时间', 'datetime', default_filter=False),
            _field('mac_addresses', 'MAC 地址', visible=False, default_filter=False),
            _field('os_version', '系统版本', visible=False, default_filter=False),
            _field('os_build', '系统构建号', visible=False, default_filter=False),
            _field('system_installed_at', '系统安装时间', visible=False, default_filter=False),
            _field('manufacturer', '制造商', visible=False, default_filter=False),
            _field('model', '型号', visible=False, default_filter=False),
            _field('serial_number', '序列号', visible=False, default_filter=False),
            _field('architecture', '系统架构', visible=False, default_filter=False),
            _field('cpu_model', 'CPU 型号', visible=False, default_filter=False),
            _field('cpu_physical_core_count', 'CPU 物理核心数', 'number', visible=False, default_filter=False),
            _field('cpu_logical_processor_count', 'CPU 逻辑处理器数', 'number', visible=False, default_filter=False),
            _field('memory_total_gb', '内存总量', 'number', visible=False, default_filter=False),
            _field('disk_total_gb', '磁盘总量', 'number', visible=False, default_filter=False),
        ), 'computer_name', 'asc', ('computer_name', 'os', 'user_name', 'ip_addresses', 'mac_addresses'), 20,
    ),
    'networks': TableDefinition(
        'networks', '网络设备', (
            _field('device_name', '设备名称'), _field('ip', 'IP 地址'),
            _field('device_type', '设备类型', 'choice'), _field('vendor', '厂商', 'choice'),
            _field('os_version', '系统版本', visible=False, default_filter=False),
            _field('model', '型号', default_filter=False),
            _field('connection_type', '连接方式', 'choice', default_filter=False),
            _field('port', '管理端口', 'choice', default_filter=False),
            _field('snmp_version', 'SNMP 版本', 'choice', visible=False, default_filter=False),
            _field('snmp_port', 'SNMP 端口', 'number', visible=False, default_filter=False),
            _field('cpu_model', 'CPU 型号', default_filter=False),
            _field('memory_total_gb', '内存总量', 'number', default_filter=False),
            _field('disk_total_gb', '磁盘总量', 'number', default_filter=False),
            _field('port_count', '端口总数', 'number', default_filter=False),
            _field('vlan_count', 'VLAN 数量', 'number', default_filter=False),
        ), 'device_name', 'asc', ('device_name', 'ip', 'device_type', 'vendor', 'model', 'os_version'), 20,
    ),
    'servers': TableDefinition(
        'servers', '服务器', (
            _field('name', '名称'), _field('ip', 'IP 地址'),
            _field('server_type', '服务器类型', 'choice', choices=(('linux', 'Linux'), ('windows', 'Windows'))),
            _field('os', '操作系统', default_filter=False),
            _field('port', '管理端口', 'choice', default_filter=False),
            _field('api_url', '巡检 API 地址', source='public_api_url', filterable=False, sortable=False),
            _field('verify_ssl', '验证 TLS 证书', 'boolean'),
            _field('os_version', '系统版本', visible=False, default_filter=False),
            _field('os_build', '系统构建号', visible=False, default_filter=False),
            _field('system_installed_at', '系统安装时间', visible=False, default_filter=False),
            _field('manufacturer', '制造商', visible=False, default_filter=False),
            _field('model', '型号', visible=False, default_filter=False),
            _field('serial_number', '序列号', visible=False, default_filter=False),
            _field('architecture', '系统架构', visible=False, default_filter=False),
            _field('cpu_model', 'CPU 型号', default_filter=False),
            _field('cpu_physical_core_count', 'CPU 物理核心数', 'number', visible=False, default_filter=False),
            _field('cpu_logical_processor_count', 'CPU 逻辑处理器数', 'number', visible=False, default_filter=False),
            _field('memory_total_gb', '内存总量', 'number', default_filter=False),
            _field('disk_total_gb', '磁盘总量', 'number', default_filter=False),
        ), 'name', 'asc', ('name', 'ip', 'server_type', 'os'), 20,
    ),
    'monitors': TableDefinition(
        'monitors', '安防设备', (
            _field('device_name', '设备名称'), _field('ip', 'IP 地址'),
            _field('device_type', '设备类型', 'choice'), _field('vendor', '厂商', 'choice'),
            _field('model', '型号', default_filter=False),
            _field('api_url', '巡检 API 地址', source='public_api_url', filterable=False, sortable=False),
            _field('verify_ssl', '验证 TLS 证书', 'boolean'),
            _field('cpu_model', 'CPU 型号', default_filter=False),
            _field('memory_total_gb', '内存总量', 'number', default_filter=False),
            _field('disk_total_gb', '磁盘总量', 'number', default_filter=False),
        ), 'device_name', 'asc', ('device_name', 'ip', 'device_type', 'vendor', 'model'), 20,
    ),
    'domain_accounts': TableDefinition(
        'domain_accounts', '域账户', (
            _field('account_name', '账户名称'), _field('login_name', '登录名'),
            _field('is_active', '是否启用', 'boolean'), _field('ou', '组织单位'),
            _field('group_names', '所属分组'),
            _field('allowed_workstations', '允许登录域计算机', default_filter=False),
            _field('last_login_date', '最后登录日期', 'date', default_filter=False),
        ), 'login_name', 'asc', ('account_name', 'login_name', 'ou', 'allowed_workstations', 'group_names'), 20,
    ),
    'domain_computers': TableDefinition(
        'domain_computers', '域计算机', (
            _field('computer_name', '域计算机名'), _field('os', '操作系统'),
            _field('is_active', '是否启用', 'boolean'), _field('ou', '组织单位'),
            _field('group_names', '所属分组'),
            _field('last_login_date', '最后登录日期', 'date', default_filter=False),
        ), 'computer_name', 'asc', ('computer_name', 'os', 'ou', 'group_names'), 20,
    ),
    'domain_groups': TableDefinition(
        'domain_groups', '域分组', (
            _field('group_name', '组名称'),
            _field('login_name', '登录名'),
            _field('group_category', '组类型', 'choice', choices=(
                ('security', '安全组'), ('distribution', '通讯组'),
            )),
            _field('group_scope', '组范围', 'choice', choices=(
                ('domain_local', '域本地'), ('global', '全局'),
                ('universal', '通用'), ('unknown', '未知'),
            )),
            _field('ou', '组织单位'),
            _field('member_count', '成员数量', 'number'),
            _field('description', '描述', default_filter=False),
            _field('distinguished_name', 'DN', visible=False, default_filter=False),
        ), 'group_name', 'asc', (
            'group_name', 'login_name', 'ou', 'description', 'distinguished_name',
        ), 20,
    ),
    'inspection_records': TableDefinition(
        'inspection_records', '巡检与分析记录', (
            _field('problem_types', '问题类型', choices=PROBLEM_TYPE_CHOICES),
            _field('result_level', '问题等级', 'choice', choices=(('normal', '正常'), ('info', '提示'), ('warning', '警告'), ('critical', '严重'))),
            _field('category', '执行类型', 'choice'), _field('asset', '设备名称'),
            _field('time', '执行时间', 'datetime', default_filter=False),
            _field(
                'status', '执行结果', 'choice', source='ok',
                choices=(('normal', '正常'), ('abnormal', '异常')),
                comparison='boolean',
            ),
            _field('summary', '摘要', sortable=False, default_filter=False),
            _field('execution_status', '采集状态', 'choice', choices=(('成功', '成功'), ('部分成功', '部分成功'), ('失败', '失败'))),
            _field('task_source', '任务来源', 'choice', default_filter=False),
            _field('error_count', '异常数', filterable=False),
            _field('key_metrics', '关键指标', filterable=False, sortable=False),
        ), 'time', 'desc', ('category', 'asset', 'summary'), 20,
    ),
    'error_records': TableDefinition(
        'error_records', '异常记录', (
            _field('category', '设备类型', 'choice'), _field('asset', '设备名称'),
            _field('time', '异常时间', 'datetime', default_filter=False),
            _field('type', '异常类型', 'choice'),
            _field('message', '异常说明', sortable=False, default_filter=False),
        ), 'time', 'desc', ('category', 'asset', 'type', 'message'), 20,
    ),
    'computer_inspections': TableDefinition(
        'computer_inspections', 'PC 日志分析记录', (
            _field('problem_types', '问题类型', choices=PROBLEM_TYPE_CHOICES),
            _field('execution_status', '执行状态', filterable=False, sortable=False),
            _field('task_source', '任务来源', filterable=False, sortable=False),
            _field('error_count', '异常数', filterable=False, sortable=False),
            _field('key_metrics', '关键指标', filterable=False, sortable=False),
            _field('computer_name', 'PC 名称', source='computer__computer_name'),
              _field('user_name', '登录用户', source='computer__user_name', default_filter=False),
              _field('employee_number', '工号', source='details__enrichment__employee_number', default_filter=False),
              _field('personnel_name', '人员姓名', source='details__enrichment__personnel_name', default_filter=False),
              _field('department', '部门', source='details__enrichment__department', default_filter=False),
              _field('user_ou', '用户 OU', source='details__enrichment__user_ou', visible=False, default_filter=False),
              _field('computer_ou', '计算机 OU', source='details__enrichment__computer_ou', visible=False, default_filter=False),
              _field('site', '站点', source='details__enrichment__site', default_filter=False),
            _field('log_time', '日志时间', 'datetime', source='source_collected_at', default_filter=False),
            _field('created_at', '入库时间', 'datetime', default_filter=False),
            _field(
                'status', '分析结果', 'choice', source='result_level',
                choices=(('normal', '正常'), ('info', '提示'), ('warning', '警告'), ('critical', '严重')),
            ),
        ), 'created_at', 'desc', ('computer_name', 'user_name'), 20,
    ),
    'computer_errors': TableDefinition(
        'computer_errors', 'PC 异常记录', (
            _field('computer_name', 'PC 名称', source='inspection__computer__computer_name'),
            _field('user_name', '登录用户', source='inspection__computer__user_name', default_filter=False),
            _field('log_time', '日志时间', 'datetime', source='inspection__source_collected_at', default_filter=False),
            _field('time', '异常时间', 'datetime', source='inspection__created_at', default_filter=False),
            _field('type', '问题类型', 'choice', source='error_type'),
            _field('message', '详细问题', source='error_message', sortable=False, default_filter=False),
        ), 'time', 'desc', ('computer_name', 'user_name', 'type', 'message'), 20,
    ),
      'computer_logs': TableDefinition(
          'computer_logs', 'PC 日志文件', (
              _field('computer_name', 'PC 名称', source='computer__computer_name'),
              _field('platform', '平台', 'choice', default_filter=False),
              _field('collected_at', '采集时间', 'datetime', default_filter=False),
              _field('content_hash', '内容哈希', default_filter=False),
        ), 'collected_at', 'desc', ('content_hash',), 20,
    ),
    'access_records': TableDefinition(
        'access_records', '门禁记录', (
            _field('occurred_at', '通行时间', 'datetime'),
            _field('person_name', '人员姓名'), _field('employee_number', '工号'),
            _field('door_name', '门点名称'),
            _field('direction', '方向', 'choice', choices=(('in', '进入'), ('out', '离开'), ('unknown', '未知'))),
            _field('result', '通行状态', 'choice', choices=(('passed', '通过'), ('denied', '拒绝'), ('unknown', '未知'))),
            _field('card_number', '卡号', default_filter=False),
            _field('source', '平台来源', source='source__name', default_filter=False),
            _field('imported_at', '入库时间', 'datetime', visible=False, default_filter=False),
        ), 'occurred_at', 'desc', ('person_name', 'employee_number', 'door_name', 'card_number', 'source'), 20,
    ),    'task_runs': TableDefinition(
        'task_runs', '后台任务', (
            _field('task_type', '任务类型', 'choice', choices=(
                ('inspection', '设备巡检'),
                ('computer_analysis', 'PC 日志分析'),
                ('people_sync', '人员自动同步'),
                ('domain_operation', '域控操作'),
                ('domain_sync', '域控同步'),
            )),
            _field('source', '来源', 'choice', choices=(
                ('manual', '手动执行'), ('scheduled', '定时执行'),
            )),
            _field('status', '状态', 'choice', choices=(
                ('queued', '等待'), ('running', '运行中'), ('success', '成功'),
                ('partial', '部分成功'), ('failed', '失败'), ('cancelled', '已取消'),
            )),
            _field(
                'progress', '进度', 'text', default_filter=False,
                filterable=False, sortable=False,
            ),
            _field('created_at', '创建时间', 'datetime', default_filter=False),
        ), 'created_at', 'desc', ('task_type', 'source', 'status'), 20,
    ),
    'task_targets': TableDefinition(
        'task_targets', '任务目标', (
            _field('target_type', '目标类型', 'choice', choices=(
                ('network_device', '网络设备'), ('server', '服务器'),
                ('monitor', '安防设备'), ('computer_log', 'PC 日志'),
                ('domain_account', '域账号'), ('domain_computer', '域计算机'),
                ('people_source', '人员 API 平台'),
            )),
            _field('target_id', '目标标识'),
            _field('status', '执行状态', 'choice', choices=(
                ('queued', '等待'), ('running', '运行中'), ('success', '成功'),
                ('partial', '部分成功'), ('failed', '失败'), ('cancelled', '已取消'),
            )),
            _field('result_type', '结果类型', default_filter=False),
            _field('error_message', '错误信息', sortable=False, default_filter=False),
        ), 'target_id', 'asc', ('target_type', 'target_id', 'result_type', 'error_message'), 20,
    ),
    'alert_events': TableDefinition(
        'alert_events', '告警记录', (
            _field('event_type', '事件类型', 'choice', choices=(
                ('summary', '任务总结'), ('abnormal', '异常记录'), ('recovery', '恢复记录'),
            )),
            _field('profile_type', '项目', 'choice', choices=(
                ('inspection_profile', '巡检配置'),
                ('computer_analysis_profile', '分析配置'),
            )),
            _field('target_id', '目标'),
            _field('severity', '严重级别', 'choice', choices=(
                ('critical', '严重'), ('warning', '警告'), ('info', '提示'),
            )),
            _field('status', '事件状态', 'choice', choices=(
                ('pending', '待发送'), ('sending', '发送中'), ('delivered', '已送达'),
                ('partial', '部分送达'), ('failed', '发送失败'), ('recorded', '仅站内记录'),
            )),
            _field('delivery_outcome', '渠道结果', 'choice', source='deliveries__status',
                   comparison='related', sortable=False, export_source='delivery_outcomes', choices=(
                ('pending', '待发送'), ('sending', '发送中'), ('sent', '发送成功'),
                ('retry', '等待重试'), ('failed', '发送失败'),
            )),
            _field('occurred_at', '发生时间', 'datetime', default_filter=False),
        ), 'occurred_at', 'desc', ('profile_type', 'target_id', 'severity', 'summary'), 20,
    ),
}


def get_table_definition(key: str) -> TableDefinition:
    return TABLE_DEFINITIONS[key]


def project_record_definition(project):
    from dataclasses import replace
    from net.inspections.issues import PROJECT_RULES
    definition = get_table_definition('computer_inspections' if project == 'computers' else 'inspection_records')
    labels = list(dict.fromkeys(category for category, _ in PROJECT_RULES[project].values())) + ['其他']
    query_sources = {
        'problem_types': 'report_problem_types', 'result_level': '_report_level',
        'execution_status': '_report_execution_status', 'task_source': '_report_task_source',
        'error_count': '_report_error_count', 'key_metrics': 'report_metrics',
        'asset': '_report_asset', 'category': '_report_category', 'time': 'created_at',
        'summary': '_report_summary',
    }
    fields = []
    for field in definition.fields:
        changes = {'query_source': query_sources.get(field.key, field.source)}
        if field.key == 'problem_types':
            changes['choices'] = tuple((label, label) for label in labels)
        if project == 'computers':
            if field.key == 'status':
                changes['query_source'] = '_report_level'
            if field.source.startswith('details__enrichment__'):
                changes['source'] = field.source.replace('details__enrichment__', 'report_enrichment__')
                changes['query_source'] = f'_report_enrichment_{field.key}'
        fields.append(replace(field, **changes))
    return replace(definition, fields=tuple(fields), complete_options=True, record_semantics=True)

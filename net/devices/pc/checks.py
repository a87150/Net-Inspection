import configparser
import os
import re
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone


DATETIME_FORMATS = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d')


def load_config_as_dict(file_path: str) -> dict:
    """读取软件策略 INI，返回 section -> key -> list。"""
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    if not file_path or not path.is_file():
        raise FileNotFoundError(f'软件策略文件不存在：{path}')
    config = configparser.ConfigParser()
    config.optionxform = str
    with path.open(encoding='utf-8-sig') as source:
        config.read_file(source)
    return {
        section: {
            key: [item.strip() for item in value.split(',') if item.strip()]
            for key, value in config.items(section)
        }
        for section in config.sections()
    }


def parse_local_datetime(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return None


def add_issue(issues, issue_type, detail):
    issues.append({'问题类型': issue_type, '详细问题': detail})


def check_software(data, identity, current_issues, config, *, mode='legacy'):
    if mode not in {'whitelist', 'blacklist', 'legacy'}:
        raise ValueError('软件分析模式无效。')
    whitelist = [
        item.lower()
        for values in config.get('WHITELIST', {}).values()
        for item in values
    ]
    special_whitelist = config.get('SPECIAL_WHITELIST', {})
    blacklist = [item.lower() for item in config.get('BLACKLIST', {}).get('keywords', [])]
    problem_softwares = []
    for software in data.get('已安装软件列表') or []:
        if not isinstance(software, dict):
            continue
        name = str(software.get('软件名') or '').strip()
        lowered_name = name.lower()
        if not name:
            continue
        if mode != 'whitelist' and any(keyword in lowered_name for keyword in blacklist):
            problem_softwares.append(name)
            continue
        if mode == 'blacklist':
            continue
        if any(keyword in lowered_name for keyword in whitelist):
            continue
        if any(
            software_name.lower() in lowered_name and identity in allowed_identities
            for software_name, allowed_identities in special_whitelist.items()
        ):
            continue
        problem_softwares.append(name)
    if problem_softwares:
        add_issue(current_issues, '软件问题', '，'.join(sorted(set(problem_softwares))))


def check_bitlocker(data, current_issues):
    volumes = (data.get('BitLocker状态') or {}).get('磁盘卷信息') or []
    if not volumes:
        add_issue(current_issues, 'BitLocker数据缺失', '未采集到磁盘加密状态')
        return
    problem_details = []
    for volume in volumes:
        status = volume.get('转换状态', '') if isinstance(volume, dict) else ''
        if status not in {'完全加密', '仅加密了已用空间', '加密进行中'}:
            problem_details.append(f"{volume.get('卷', '')}卷（{status or '状态未知'}）")
    if problem_details:
        add_issue(current_issues, 'BitLocker问题', '，'.join(problem_details))


def _local_naive(value):
    if value is None:
        return None
    if timezone.is_aware(value):
        return timezone.localtime(value).replace(tzinfo=None)
    return value


def check_defender(
    data, current_issues, collected_at,
    update_max_days=7, scan_max_days=7,
):
    defender = data.get('WindowsDefender状态') or {}
    if not defender:
        add_issue(current_issues, 'WindowsDefender数据缺失', '未采集到 Defender 状态')
        return
    problems = []
    update_value = defender.get('上次更新时间')
    update_time = parse_local_datetime(update_value)
    if not update_time:
        problems.append('病毒库更新时间缺失或格式错误')
    else:
        days = (_local_naive(collected_at) - _local_naive(update_time)).days
        if days > update_max_days:
            problems.append(f'病毒库最后更新时间为 {update_value}，已过 {days} 天')
    scan_info = defender.get('扫描信息') or {}
    scan_time = parse_local_datetime(scan_info.get('时间'))
    if not scan_time:
        problems.append('未发现有效的病毒扫描记录')
    else:
        days = (_local_naive(collected_at) - _local_naive(scan_time)).days
        if days > scan_max_days:
            problems.append(f"上次扫描时间为 {scan_info.get('时间')}，已过 {days} 天")
    if problems:
        add_issue(current_issues, 'WindowsDefender问题', '；'.join(problems))


def check_update_history(data, current_issues, collected_at, max_age_days=30):
    dates = [
        parsed
        for item in (data.get('系统更新历史') or [])
        if isinstance(item, dict)
        for parsed in [parse_local_datetime(item.get('日期'))]
        if parsed
    ]
    if not dates:
        add_issue(current_issues, '系统更新数据缺失', '未采集到有效的系统更新记录')
        return
    latest = max(dates)
    days = (_local_naive(collected_at) - _local_naive(latest)).days
    if days > max_age_days:
        add_issue(current_issues, '系统更新历史问题', f'上次更新日期为 {latest:%Y-%m-%d}，距今 {days} 天')


def _release_key(value):
    match = re.fullmatch(r'(\d{2})H([12])', str(value or '').upper().strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def check_system_version(data, current_issues, minimum_release=None):
    minimum_release = minimum_release or os.getenv('MIN_WINDOWS_RELEASE', '23H2')
    version = (data.get('系统信息概览') or {}).get('系统主要版本名', '')
    actual_key = _release_key(version)
    if not actual_key:
        # The terminal producer reports the OS caption and its numeric build.
        system = data.get('系统信息概览') or {}
        release = re.search(r'\b(\d{2}H[12])\b', str(version), flags=re.IGNORECASE)
        if release:
            actual_key = _release_key(release[1])
        elif re.match(r'^(?:microsoft\s+)?windows\b', str(version).strip(), re.IGNORECASE):
            build = re.fullmatch(r'10\.0\.(\d+)(?:\.\d+)?', str(system.get('系统详细版本', '')).strip())
            releases = {19044: '21H2', 19045: '22H2', 22000: '21H2',
                        22621: '22H2', 22631: '23H2', 26100: '24H2'}
            actual_key = _release_key(releases.get(int(build[1]))) if build else None
    minimum_key = _release_key(minimum_release)
    if not actual_key:
        add_issue(current_issues, '系统版本数据缺失', f'无法识别系统版本：{version or "空"}')
    elif minimum_key and actual_key < minimum_key:
        add_issue(current_issues, '系统版本过旧', f'系统版本为 {version}，最低要求为 {minimum_release}')


def check_activation(data, current_issues, expected_kms_servers=()):
    activation = data.get('Windows激活信息') or {}
    if not activation:
        add_issue(current_issues, '系统激活数据缺失', '未采集到 Windows 激活信息')
        return
    problems = []
    license_status = activation.get('许可证状态', '')
    if license_status != '已授权':
        problems.append(f'许可证状态为：{license_status or "未知"}')
    product_text = f"{activation.get('描述', '')} {activation.get('产品密钥通道', '')}".upper()
    if 'KMS' in product_text:
        kms_status = data.get('KMS服务器连通情况', '')
        if normalize_windows_state(kms_status) != 'known':
            problems.append(f'KMS服务器通讯状态：{kms_status or "未知"}')
        kms_ip = activation.get('KMS 计算机 IP 地址', '')
        kms_name = activation.get('已注册的 KMS 计算机名称', '')
        expected = {str(value).strip().casefold() for value in expected_kms_servers if str(value).strip()}
        actual = {str(kms_ip).strip().casefold(), str(kms_name).split(':')[0].strip().casefold()}
        if expected and not expected.intersection(actual):
            problems.append('未使用配置中允许的 KMS 服务器激活')
    if problems:
        add_issue(current_issues, '系统激活问题', '；'.join(problems))


def check_uptime(data, current_issues, collected_at, threshold_hours=168):
    boot_value = (data.get('系统信息概览') or {}).get('开机时间')
    boot_time = parse_local_datetime(boot_value)
    if not boot_time:
        add_issue(current_issues, '开机时间数据缺失', '开机时间缺失或格式错误')
        return
    uptime_hours = (_local_naive(collected_at) - _local_naive(boot_time)).total_seconds() / 3600
    if uptime_hours >= threshold_hours:
        add_issue(current_issues, '长时间未关机', f'开机时间：{boot_value}，距今 {int(uptime_hours)} 小时')


def check_resource(data, current_issues, cpu_max_percent=90, memory_max_percent=90):
    resource = data.get('计算机硬件资源情况') or {}
    problems = []
    for key, label, threshold in (
        ('当前CPU占用率', 'CPU', cpu_max_percent),
        ('当前内存使用率', '内存', memory_max_percent),
    ):
        value = str(resource.get(key) or '').strip()
        try:
            percent = float(value.removesuffix('%'))
        except ValueError:
            continue
        if percent > threshold:
            problems.append(f'{label}占用率 {percent:g}% 超过阈值 {threshold}%')
    if problems:
        add_issue(current_issues, '资源使用问题', '；'.join(problems))


def evaluate_computer(data, config, collected_at):
    issues = []
    system_info = data.get('系统信息概览') or {}
    identity = str(system_info.get('当前登录用户工号') or system_info.get('计算机名') or '')
    identity = identity.rsplit('\\', 1)[-1]
    check_software(data, identity, issues, config)
    check_bitlocker(data, issues)
    check_defender(data, issues, collected_at)
    check_update_history(data, issues, collected_at)
    check_system_version(data, issues)
    check_activation(data, issues)
    check_uptime(data, issues, collected_at)
    return issues


def extract_network_info(data, field):
    network_info = data.get('网络信息') or []
    if isinstance(network_info, dict):
        network_info = [network_info]
    return ';'.join(
        str(item.get(field))
        for item in network_info
        if isinstance(item, dict) and item.get(field)
    )


def normalize_windows_state(value):
    text = str(value or '').strip().casefold()
    if text in {'正常', '正常通讯', '成功', 'true', 'ok', 'success', 'healthy'}:
        return 'known'
    if text in {'失败', '无法访问', '未加入域', 'false', 'failed', 'error'}:
        return 'failed'
    return 'unknown'


def _measurement(value, kind):
    if isinstance(value, bool):
        return None
    match = re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*([a-z°℃℉]*)',
                         str(value).strip(), flags=re.IGNORECASE)
    if not match:
        return None
    number, unit = float(match[1]), match[2].casefold()
    if kind == 'temperature':
        if unit in {'f', '°f', '℉'}:
            number = (number - 32) * 5 / 9
        elif unit not in {'', 'c', '°c', '℃'}:
            return None
        return number if -100 <= number <= 250 else None
    factors = {'': 1, 'mhz': 1, 'ghz': 1000, 'khz': .001, 'hz': .000001}
    return number * factors[unit] if unit in factors and number > 0 else None


def _reference_person_name(value):
    """Ignore name descriptions and numeric disambiguation suffixes."""
    from net.devices.pc.matching import personnel_name
    return re.sub(r'\d+$', '', personnel_name(value)).rstrip()


def check_remote_item(item, payload, issues, rules):
    """Evaluate producer evidence without collapsing unknown into an empty success."""
    from net.devices.pc.enrichment import normalize_login

    fields = {'browser_extensions': '浏览器插件情况', 'group_policy': '已应用策略',
              'identity_match': '系统信息概览', 'domain_trust': '当前与域服务器通讯情况',
              'cpu_health': '计算机硬件资源情况'}
    field = fields[item]
    evidence = payload.get(field)
    result = {'evidence': evidence, 'data_state': 'known'}
    if item == 'domain_trust':
        result['data_state'] = normalize_windows_state(evidence) if field in payload else 'missing'
        if result['data_state'] == 'failed':
            add_issue(issues, '域信任问题', f'域通讯/信任状态：{evidence}')
    elif item == 'cpu_health':
        hardware = evidence if isinstance(evidence, dict) else {}
        temperature = _measurement(hardware.get('当前CPU温度'), 'temperature')
        frequency = _measurement(hardware.get('当前CPU频率'), 'frequency')
        result.update(temperature_celsius=temperature, frequency_mhz=frequency,
                      temperature_max_celsius=rules['cpu_temperature_max_celsius'])
        if temperature is None or frequency is None:
            result['data_state'] = 'partial' if temperature is not None or frequency is not None else 'unknown'
        if temperature is not None and temperature > rules['cpu_temperature_max_celsius']:
            add_issue(issues, 'CPU温度问题', f"CPU 温度 {temperature:g}°C 超过阈值 {rules['cpu_temperature_max_celsius']}°C")
    elif item == 'identity_match':
        system = evidence if isinstance(evidence, dict) else {}
        login = normalize_login(system.get('当前登录用户工号'))
        name = system.get('计算机名')
        result.update(login_identifier=login, reported_match=payload.get('计算机和用户匹配情况'))
        person_name = str(system.get('当前登录用户姓名') or system.get('当前登录用户名')
                          or system.get('姓名') or '').strip()
        computer_name = name.strip() if isinstance(name, str) else ''
        unknown_values = {'', '未知', 'unknown', '未采集', '无法获取', 'none', 'null'}
        normalized_name = _reference_person_name(person_name)
        signals = {
            '姓名': normalized_name if normalized_name.casefold() not in unknown_values else '',
            '计算机名': computer_name if computer_name.casefold() not in unknown_values else '',
            '当前登录用户工号': login if login.casefold() not in unknown_values else '',
        }
        roster = rules.get('personnel_roster')
        if roster is None:
            from django.db.models import Q
            from net.models import People
            identifiers = [signals[key] for key in ('计算机名', '当前登录用户工号') if signals[key]]
            query = Q(pk__in=[])
            for identifier in identifiers:
                query |= Q(employee_id__iexact=identifier)
            roster = list(People.objects.filter(query).values('employee_id', 'name')) if identifiers else []
        reference_matches = []
        for person in roster:
            employee_id = str(person.get('employee_id') or '').strip()
            expected = {'姓名': _reference_person_name(person.get('name')),
                        '计算机名': employee_id, '当前登录用户工号': employee_id}
            matched_fields = [key for key, value in signals.items()
                              if value and expected[key] and value.casefold() == expected[key].casefold()]
            if employee_id and len(matched_fields) >= 2:
                reference_matches.append((person, matched_fields))
        result.update(matching_rule='person_two_of_three', matched_fields=[])
        if len(reference_matches) == 1:
            # A reference association, not proof of the authenticated account.
            person, matched_fields = reference_matches[0]
            basis = {('姓名', '计算机名'): 'person_name_and_computer',
                     ('姓名', '当前登录用户工号'): 'person_name_and_login',
                     ('计算机名', '当前登录用户工号'): 'person_computer_and_login'}
            result.update(match_basis=basis.get(tuple(matched_fields), 'person_all_three'),
                          matched_fields=matched_fields,
                          reference_employee_id=person['employee_id'],
                          reference_person_name=person.get('name') or '')
        elif len(reference_matches) > 1:
            result.update(data_state='unknown', match_basis='ambiguous_person')
        elif sum(bool(value) for value in signals.values()) < 2 or not roster:
            result.update(data_state='unknown', match_basis='insufficient_personnel_evidence')
        else:
            result['data_state'] = 'failed'
            result['match_basis'] = 'no_two_fields_match'
            add_issue(issues, '计算机和用户不匹配',
                      f'姓名 {person_name or "未采集"}、计算机名 {computer_name or "未采集"}、'
                      f'登录工号 {login or "未采集"} 未能以任意两项对应同一人员')
    elif item == 'browser_extensions':
        # Plugin collection is presence-only: no installed extensions is normal.
        # Keep the producer's evidence intact; the final missing-field check applies.
        result['data_state'] = 'known'
    else:
        def collected(values):
            return isinstance(values, list) and all(
                isinstance(value, str) and bool(value.strip())
                and value.strip().casefold() not in {
                    '未知', '检测失败', '获取失败', '未采集', 'unknown', 'unavailable',
                } for value in values)
        if not isinstance(evidence, dict) or not evidence:
            result['data_state'] = 'unknown'
        elif item == 'group_policy':
            states = [collected(evidence.get(key)) for key in ('计算机策略', '用户策略')]
            result['data_state'] = 'known' if all(states) else 'partial' if any(states) else 'unknown'
        elif not all(isinstance(key, str) and key.strip() and collected(values)
                     for key, values in evidence.items()):
            result['data_state'] = 'unknown'
    if field not in payload:
        result['data_state'] = 'missing'
    if result['data_state'] in {'missing', 'unknown', 'partial'}:
        issues.append({'问题类型': f'{item}数据缺失或未知',
                       '详细问题': f"所选项目 {item} 数据状态为 {result['data_state']}，不能判定正常。",
                       'data_state': result['data_state']})
    return result

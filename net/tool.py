import configparser
import os
import re
from datetime import datetime


DATETIME_FORMATS = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d')


def load_config_as_dict(file_path: str) -> dict:
    """读取软件策略 INI，返回 section -> key -> list。"""
    if not file_path or not os.path.exists(file_path):
        raise FileNotFoundError(f'软件策略文件不存在：{file_path}')
    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(file_path, encoding='utf-8-sig')
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


def check_software(data, identity, current_issues, config):
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
        if any(keyword in lowered_name for keyword in blacklist):
            problem_softwares.append(name)
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


def check_defender(data, current_issues, collected_at):
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
        days = (collected_at - update_time).days
        if days > 7:
            problems.append(f'病毒库最后更新时间为 {update_value}，已过 {days} 天')
    scan_info = defender.get('扫描信息') or {}
    scan_time = parse_local_datetime(scan_info.get('时间'))
    if not scan_time:
        problems.append('未发现有效的病毒扫描记录')
    else:
        days = (collected_at - scan_time).days
        if days > 7:
            problems.append(f"上次扫描时间为 {scan_info.get('时间')}，已过 {days} 天")
    if problems:
        add_issue(current_issues, 'WindowsDefender问题', '；'.join(problems))


def check_update_history(data, current_issues, collected_at):
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
    days = (collected_at - latest).days
    if days >= 7:
        add_issue(current_issues, '系统更新历史问题', f'上次更新日期为 {latest:%Y-%m-%d}，距今 {days} 天')


def _release_key(value):
    match = re.fullmatch(r'(\d{2})H([12])', str(value or '').upper().strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def check_system_version(data, current_issues, minimum_release=None):
    minimum_release = minimum_release or os.getenv('MIN_WINDOWS_RELEASE', '23H2')
    version = (data.get('系统信息概览') or {}).get('系统主要版本名', '')
    actual_key = _release_key(version)
    minimum_key = _release_key(minimum_release)
    if not actual_key:
        add_issue(current_issues, '系统版本数据缺失', f'无法识别系统版本：{version or "空"}')
    elif minimum_key and actual_key < minimum_key:
        add_issue(current_issues, '系统版本过旧', f'系统版本为 {version}，最低要求为 {minimum_release}')


def check_activation(data, current_issues):
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
        if kms_status != '正常通讯':
            problems.append(f'KMS服务器通讯状态：{kms_status or "未知"}')
        kms_ip = activation.get('KMS 计算机 IP 地址', '')
        kms_name = activation.get('已注册的 KMS 计算机名称', '')
        expected_ip = os.getenv('KMS_SERVER_IP', '10.14.1.111')
        if expected_ip not in {kms_ip, str(kms_name).split(':')[0]}:
            problems.append('未使用指定的 KMS 服务器激活')
    if problems:
        add_issue(current_issues, '系统激活问题', '；'.join(problems))


def check_uptime(data, current_issues, collected_at, threshold_hours=168):
    boot_value = (data.get('系统信息概览') or {}).get('开机时间')
    boot_time = parse_local_datetime(boot_value)
    if not boot_time:
        add_issue(current_issues, '开机时间数据缺失', '开机时间缺失或格式错误')
        return
    uptime_hours = (collected_at - boot_time).total_seconds() / 3600
    if uptime_hours >= threshold_hours:
        add_issue(current_issues, '长时间未关机', f'开机时间：{boot_value}，距今 {int(uptime_hours)} 小时')


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

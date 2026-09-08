"""Stable personnel/log joins; never fabricate a device analysis for absent evidence."""
from collections.abc import Mapping
from types import SimpleNamespace


def personnel_snapshot():
    from net.models import People
    return [{**row, 'id': str(row['id'])} for row in People.objects.order_by('employee_id', 'pk').values(
        'id', 'employee_id', 'name', 'department', 'is_active')]


def match_person(system, roster):
    from net.devices.pc.enrichment import normalize_login
    identifier = normalize_login(system.get('当前登录用户工号') or system.get('工号'))
    if identifier:
        matches = [p for p in roster if str(p['employee_id']).casefold() == identifier.casefold()]
    else:
        name = str(system.get('当前登录用户名') or system.get('姓名') or '').strip()
        if not name:
            # Computer names are an established employee-number convention here.
            identifier = normalize_login(system.get('计算机名'))
            matches = [p for p in roster if identifier and str(p['employee_id']).casefold() == identifier.casefold()]
        else:
            matches = [p for p in roster if str(p.get('name') or '').strip() == name]
        if not identifier and not name:
            return None, 'missing'
    return (matches[0], 'matched') if len(matches) == 1 else (None, 'ambiguous' if matches else 'unmatched')


def join_analysis_rows(analyses, roster, mode):
    if mode != 'people':
        return analyses
    grouped = {}
    for analysis in analyses:
        person_id = analysis.details.get('enrichment', {}).get('personnel_id', '')
        grouped.setdefault(person_id, []).append(analysis)
    rows = []
    for person in roster:
        matches = grouped.get(str(person['id']), [])
        if matches:
            rows.extend(matches)
            continue
        rows.append(SimpleNamespace(
            pk=None, missing_log=True, computer=None, log_file=None, created_at=None,
            execution_status='未匹配日志', task_source='人员匹配', error_count=0,
            key_metrics='本任务范围内暂无匹配日志', result_level='info', ok=True,
            summary='提示：本任务范围内未匹配到日志，不代表设备故障。',
            details={'enrichment': {'personnel_id': str(person['id']),
                     'employee_number': person['employee_id'], 'personnel_name': person.get('name', ''),
                     'department': person.get('department', '')}},
        ))
    return rows

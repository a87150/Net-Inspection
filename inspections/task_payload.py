"""Detail payload for the background task page."""
from django.urls import reverse
from django.utils import timezone

from index.common.badges import severity_badge, status_badge
from index.common.table_payload import row_payloads
from index.common.detail_payload import (
    alert_section, detail_payload, field_section, form_action, form_section,
    link_action, list_section, table_section,
)


def _stamp(value):
    return timezone.localtime(value).strftime('%Y-%m-%d %H:%M:%S') if value else '-'


def _page_links(page_obj, request, key='page'):
    if page_obj is None or not page_obj.paginator.num_pages > 1:
        return None
    params = request.GET.copy()
    links = {}
    if page_obj.has_previous():
        params[key] = page_obj.previous_page_number()
        links['prev_url'] = '?' + params.urlencode()
    if page_obj.has_next():
        params[key] = page_obj.next_page_number()
        links['next_url'] = '?' + params.urlencode()
    links['label'] = '%s / %s' % (page_obj.number, page_obj.paginator.num_pages)
    return links


def _sort_columns(definition, table_state, request):
    columns = []
    params = request.GET.copy()
    for field in definition.fields:
        column = {'label': field.label}
        if field.sortable:
            current = table_state.get('sort') == field.key
            column['aria_sort'] = (
                'ascending' if current and table_state.get('order') == 'asc'
                else 'descending' if current else 'none'
            )
            params['sort'] = field.key
            params['order'] = 'desc' if current and table_state.get('order') == 'asc' else 'asc'
            params.pop('page', None)
            column['sort_url'] = '?' + params.urlencode()
        columns.append(column)
    return columns


def _target_row(target, task, domain_operation):
    if domain_operation:
        target_cell = {'text': target.domain_name, 'note': target.domain_dn}
    else:
        target_cell = target.target_id

    if task.task_type == 'domain_sync':
        alert_cell = {'text': '不参与设备告警'}
    elif target.alert_processing_error:
        alert_cell = {
            'badge': status_badge('retry', '告警处理待重试'),
            'text': target.alert_processing_error,
            'note': '最近尝试 ' + _stamp(target.alert_attempted_at),
        }
    elif target.alert_processed_at:
        alert_cell = {'badge': status_badge('success', '告警已处理')}
    else:
        alert_cell = {'badge': status_badge('queued', '等待处理')}

    counts = getattr(target, 'people_sync_counts', None)
    if counts:
        result_cell = {'text': '新增 %s · 更新 %s · 未变化 %s · 停用 %s · 跳过 %s' % (
            counts.get('created'), counts.get('updated'), counts.get('unchanged'),
            counts.get('deactivated'), counts.get('skipped'),
        )}
    elif getattr(target, 'result_url', ''):
        result_cell = {'url': target.result_url, 'label': '查看结果'}
    else:
        result_cell = '—'

    return [
        target.get_target_type_display(),
        target_cell,
        {'badge': status_badge(target.status, target.get_status_display())},
        target.result_type or '-',
        {'text': target.error_message or '等待执行或生成结果'},
        alert_cell,
        result_cell,
    ]


def task_detail_payload(*, task, request, page_obj, table_definition, table_state,
                        target_page_obj, domain_operation, domain_retry_available,
                        domain_retry_requires_password, domain_manual_intervention,
                        can_administer, result_context=None, alert_processing_page=None,
                        csrf=''):
    sections = []

    if task.task_type == 'domain_sync':
        messages = [
            target.result_snapshot.get('message')
            for target in target_page_obj
            if isinstance(getattr(target, 'result_snapshot', None), dict)
            and target.result_snapshot.get('message')
        ]
        sections.append(list_section('域控同步结果', [
            {'label': message, 'detail': ''} for message in messages
        ], empty='同步域账号、域计算机及域分组，不参与设备告警。'))

    if domain_operation:
        fields = [
            ('操作类型', domain_operation.get_action_display()),
            ('对象类型', domain_operation.get_object_type_display()),
            ('目标数量', '%s 个目标' % domain_operation.target_count),
        ]
        for key, value in (domain_operation.parameter_summary or {}).items():
            fields.append((key, value))
        inputs = []
        if domain_retry_requires_password:
            inputs.append({
                'name': 'password', 'label': '输入新的密码', 'type': 'password',
                'autocomplete': 'new-password',
            })
        heading = '域控操作：%s' % domain_operation.get_action_display()
        if can_administer and domain_retry_available:
            sections.append(form_section(
                heading, fields,
                ('重试失败目标', reverse('domain_operation_retry', args=[domain_operation.pk])),
                note='参数已脱敏',
                inputs=inputs,
            ))
        else:
            sections.append(field_section(heading, fields))
        if domain_manual_intervention:
            sections.append(alert_section(
                'warning', '新增用户结果不确定，请人工检查目录对象后再处理；系统已禁止自动重试。',
            ))

    progress = [
        ('总体进度', '%s / %s' % (task.completed_targets, task.total_targets)),
        ('成功目标', task.successful_targets),
        ('失败目标', task.failed_targets),
        ('下发时间', _stamp(task.created_at)),
    ]
    sections.append(field_section('执行进度', progress))

    if task.error_summary:
        sections.append(alert_section('danger', task.error_summary))
    inapplicable = (task.parameters_snapshot or {}).get('inapplicable_target_ids') or []
    if inapplicable:
        sections.append(alert_section(
            'info',
            '%s 台设备不适用于本次选择的巡检项目，未纳入执行与异常统计。' % len(inapplicable),
        ))

    result_context = result_context or {}
    if result_context.get('show_result_table'):
        if alert_processing_page is not None and alert_processing_page.paginator.count:
            sections.append(list_section('告警处理待重试', [
                {
                    'label': diagnostic.target_id,
                    'meta': _stamp(diagnostic.alert_attempted_at),
                    'detail': diagnostic.alert_processing_error,
                }
                for diagnostic in alert_processing_page
            ]))
        definition = result_context['table_definition']
        is_analysis = result_context.get('record_type') == 'computer_analysis'
        rows = []
        for record in result_context['page_obj']:
            payload = row_payloads([record], definition)[0]
            cells = [payload['cells'][field.key] for field in definition.fields]
            if is_analysis:
                record_pk = getattr(record, 'pk', None)
                url = reverse('computer_analysis_detail', args=[record_pk]) if record_pk else ''
                # 状态列在分析结果里是分级徽章，不是纯文本。
                for index, field in enumerate(definition.fields):
                    if field.key == 'status':
                        cells[index] = {
                            'badge': severity_badge(getattr(record, 'result_level', '')),
                        }
                op_cell = ({'url': url, 'label': '查看详情'} if url
                           else {'text': '异常：未匹配日志'})
            else:
                op_cell = {'url': payload['url'], 'label': '查看详情'} if payload['url'] else '—'
            rows.append([*cells, op_cell])
        sections.append(table_section(
            '任务结果记录',
            _sort_columns(definition, result_context['table_state'], request) + ['操作'],
            rows,
            empty='最新任务尚未产生巡检结果。',
            pagination=_page_links(result_context['page_obj'], request),
            workspace=result_context.get('table_preference_key', ''),
        ))
        execution_page = result_context.get('execution_page')
        if execution_page is not None and execution_page.paginator.count:
            sections.append(table_section(
                '尚未生成结果的目标（%s）' % execution_page.paginator.count,
                ['目标', '执行状态', '说明'],
                [
                    [
                        target.target_id,
                        target.get_status_display(),
                        target.error_message or '等待执行或生成结果',
                    ]
                    for target in execution_page
                ],
                pagination=_page_links(execution_page, request, key='execution_page'),
            ))
    else:
        rows = []
        for target in target_page_obj:
            target.result_url = getattr(target, 'result_url', '')
            rows.append(_target_row(target, task, domain_operation))
        sections.append(table_section(
            '目标执行详情',
            [column for column in _sort_columns(table_definition, table_state, request)]
            + ['告警处理', '结果'],
            rows,
            empty='任务开始处理目标后，进度会显示在这里。',
            pagination=_page_links(target_page_obj, request),
            workspace='task-targets',
        ))

    actions = []
    if can_administer and task.status in task.ACTIVE_STATUSES:
        actions.append(form_action(
            '结束任务', reverse('task_cancel', args=[task.pk]),
            confirm='确定结束这个任务吗？尚未开始的目标会被取消，正在执行的外部调用将在超时后退出。',
        ))
    actions.append(link_action('返回任务列表', reverse('task_list')))

    return detail_payload(
        title='任务详情',
        eyebrow='执行中心',
        description='%s · %s · %s' % (
            task.get_task_type_display(), task.get_source_display(), task.get_status_display()),
        actions=actions,
        sections=sections,
        csrf=csrf,
    )

from urllib.parse import quote

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from net.data_exchange.inventory_csv import (
    IMPORTABLE_ENTITIES,
    export_csv,
    export_xlsx_template,
    get_spec,
    import_file,
)


def download_inventory_template(request, entity, file_format='csv'):
    if entity not in IMPORTABLE_ENTITIES:
        raise Http404('该数据由系统自动获取，不提供导入模板')
    spec = get_spec(entity)
    if file_format == 'csv':
        content = export_csv(entity, template_only=True)
        content_type = 'text/csv; charset=utf-8'
    elif file_format == 'xlsx':
        content = export_xlsx_template(entity)
        content_type = (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    else:
        raise Http404('不支持的模板格式')
    response = HttpResponse(content, content_type=content_type)
    filename = quote(f"{spec['name']}_导入模板.{file_format}")
    response['Content-Disposition'] = f"attachment; filename*=UTF-8''{filename}"
    return response


@require_POST
def import_inventory(request, entity):
    try:
        get_spec(entity)
    except ValueError as exc:
        raise Http404(str(exc)) from exc
    if entity not in IMPORTABLE_ENTITIES:
        messages.error(request, '该数据由系统自动获取，不支持手动导入。')
        if entity in {'accounts', 'domain_computers'}:
            return redirect('domain_controller_settings')
        return redirect('item_list', item=entity)

    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        messages.error(request, '请选择 CSV 文件。')
        request.session['open_import_modal'] = entity
        return redirect('item_list', item=entity)

    try:
        created, updated = import_file(entity, uploaded_file)
        messages.success(request, f'导入完成：新增 {created} 条，更新 {updated} 条。')
    except ValueError as exc:
        messages.error(request, f'导入失败：{exc}')
        request.session['open_import_modal'] = entity
    return redirect('item_list', item=entity)

from django.db import migrations


OBSOLETE_SECTIONS = {'日志文件元数据', '计算机和用户匹配情况'}


def repair_pc_projections(apps, schema_editor):
    ComputerAnalysis = apps.get_model('net', 'ComputerAnalysis')
    ComputerLogFile = apps.get_model('net', 'ComputerLogFile')
    for analysis in ComputerAnalysis.objects.only('pk', 'exceptions').iterator(chunk_size=500):
        count = sum(
            1 for issue in analysis.exceptions or []
            if isinstance(issue, dict) and issue.get('severity') != 'info'
        )
        if analysis.actionable_issue_count != count:
            ComputerAnalysis.objects.filter(pk=analysis.pk).update(actionable_issue_count=count)
    for log_file in ComputerLogFile.objects.only('pk', 'present_sections', 'extra_fields').iterator(chunk_size=500):
        sections = [section for section in log_file.present_sections or []
                    if section not in OBSOLETE_SECTIONS]
        extras = {key: value for key, value in (log_file.extra_fields or {}).items()
                  if key not in OBSOLETE_SECTIONS}
        updates = {}
        if sections != (log_file.present_sections or []):
            updates['present_sections'] = sections
        if extras != (log_file.extra_fields or {}):
            updates['extra_fields'] = extras
        if updates:
            ComputerLogFile.objects.filter(pk=log_file.pk).update(**updates)


class Migration(migrations.Migration):
    dependencies = [('net', '0056_remove_pc_log_redundancy')]
    operations = [migrations.RunPython(repair_pc_projections, migrations.RunPython.noop)]

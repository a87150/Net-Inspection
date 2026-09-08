from django.db import migrations, models


def backfill_reports(apps, schema_editor):
    from ._record_report_0036 import record_report_values, REPORT_FIELDS
    alias = schema_editor.connection.alias
    for name in ('ComputerAnalysis', 'Network_Device_Inspection', 'Server_Inspection', 'Monitor_Inspection'):
        model = apps.get_model('net', name)
        fields = ['pk', 'details', 'status', 'exceptions' if name == 'ComputerAnalysis' else 'is_reachable']
        last_pk = None
        while True:
            query = model.objects.using(alias).only(*fields).order_by('pk')
            if last_pk is not None:
                query = query.filter(pk__gt=last_pk)
            # SQL LIMIT bounds memory even on drivers which buffer result sets.
            batch = list(query[:200])
            if not batch:
                break
            for record in batch:
                for key, value in record_report_values(record).items():
                    setattr(record, key, value)
            model.objects.using(alias).bulk_update(batch, REPORT_FIELDS, batch_size=200)
            last_pk = batch[-1].pk


class Migration(migrations.Migration):
    dependencies = [('net', '0035_project_issue_policies')]
    operations = [
        migrations.AddField(model_name=name, name=field, field=definition)
        for name in ('computeranalysis', 'network_device_inspection', 'server_inspection', 'monitor_inspection')
        for field, definition in (
            ('report_metrics', models.TextField(blank=True, default='无可用指标')),
            ('report_problem_types', models.TextField(blank=True, default='')),
            ('report_severity', models.CharField(max_length=16, blank=True, default='')),
            ('report_enrichment', models.JSONField(default=dict, blank=True)),
        )
    ] + [migrations.RunPython(backfill_reports, migrations.RunPython.noop)]

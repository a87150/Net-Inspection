from django.db import migrations, models


def split_policy(apps, schema_editor):
    Policy = apps.get_model('net', 'IssueSeverityPolicy')
    policy = Policy.objects.using(schema_editor.connection.alias).first()
    if not policy:
        return
    device = {key: value for key, value in policy.overrides.items() if key in {'inspection_collection', 'missing.inspection_collection'}}
    policy.overrides = {key: value for key, value in policy.overrides.items() if key not in device}
    policy.save(update_fields=['overrides'])
    for project in ('networks', 'servers', 'monitors'):
        Policy.objects.using(schema_editor.connection.alias).get_or_create(project=project, defaults={'overrides': device})


class Migration(migrations.Migration):
    dependencies = [('net', '0034_issue_severity_policy')]
    operations = [
        migrations.RemoveConstraint(model_name='issueseveritypolicy', name='net_issue_policy_singleton'),
        migrations.AlterField(model_name='issueseveritypolicy', name='id', field=models.BigAutoField(primary_key=True, serialize=False)),
        migrations.AddField(model_name='issueseveritypolicy', name='project', field=models.CharField(max_length=16, unique=True, default='computers', choices=[('computers', 'PC 日志分析'), ('networks', '网络设备巡检'), ('servers', '服务器巡检'), ('monitors', '安防设备巡检')])),
        migrations.AddField(model_name='issueseveritypolicy', name='thresholds', field=models.JSONField(default=dict, blank=True)),
        migrations.RunPython(split_policy, migrations.RunPython.noop),
    ]

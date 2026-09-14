from django.db import migrations, models


def create_sangfor_templates(apps, schema_editor):
    Template = apps.get_model('net', 'DeviceCollectionTemplate')
    base, _ = Template.objects.get_or_create(
        kind='networks', vendor='sangfor', subtype='',
        defaults={'name': '深信服 AC Open API 基础模板', 'settings': {
            'thresholds': {'cpu': 90, 'memory': 90, 'disk_usage': 90, 'bandwidth_usage': 90},
            # These are the complete documented AC Open API status items.  The
            # gateway child inherits them; wireless controller items are deliberately
            # absent because ac_gateway is not a wireless AC.
            'item_enabled': {
                'device_info': True, 'cpu': True, 'memory': True, 'disk_usage': True,
                'bandwidth_usage': True, 'online_users': True, 'sessions': True,
                'inside_libraries': True, 'log_statistics': True, 'system_time': True,
                'throughput': True,
            },
        }},
    )
    Template.objects.get_or_create(
        kind='networks', vendor='sangfor', subtype='ac_gateway',
        defaults={'name': '深信服上网行为管理 / 安全网关 AC', 'parent': base, 'settings': {}},
    )


class Migration(migrations.Migration):
    dependencies = [('net', '0047_schedule_dispatch_status')]
    operations = [
        migrations.AddField(model_name='network_device', name='api_url', field=models.URLField(blank=True, default='', max_length=500)),
        migrations.AddField(model_name='network_device', name='api_shared_secret', field=models.CharField(blank=True, default='', max_length=500)),
        migrations.AddField(model_name='network_device', name='verify_ssl', field=models.BooleanField(default=True)),
        migrations.AlterField(model_name='network_device', name='connection_type', field=models.CharField(blank=True, choices=[('ssh', 'SSH'), ('snmp', 'SNMP'), ('hybrid', 'SSH + SNMP'), ('auto', '自动'), ('sangfor_api', '深信服 AC Open API')], default='auto', max_length=255, null=True)),
        migrations.RunPython(create_sangfor_templates, migrations.RunPython.noop),
    ]

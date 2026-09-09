import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


def retire_device_deliveries(apps, schema_editor):
    alias = schema_editor.connection.alias
    now = timezone.now()
    TaskRun = apps.get_model('net', 'TaskRun')
    AlertEvent = apps.get_model('net', 'AlertEvent')
    AlertDelivery = apps.get_model('net', 'AlertDelivery')
    TaskRun.objects.using(alias).filter(
        status__in=('success', 'partial', 'failed', 'cancelled'),
        alert_summary_processed_at__isnull=True,
    ).update(alert_summary_processed_at=now)
    AlertDelivery.objects.using(alias).exclude(event__event_type='summary').filter(
        status__in=('pending', 'retry', 'sending'),
    ).update(
        status='failed', next_attempt_at=None, lease_token='',
        lease_expires_at=None, delivered_at=None,
        error_summary='已切换任务汇总，旧设备告警不再投递。', updated_at=now,
    )
    sent_events = AlertDelivery.objects.using(alias).filter(status='sent').values('event_id')
    AlertEvent.objects.using(alias).exclude(event_type='summary').exclude(
        status__in=('delivered', 'partial'),
    ).exclude(pk__in=sent_events).update(status='recorded', updated_at=now)


class Migration(migrations.Migration):
    dependencies = [('net', '0041_configurationbackups')]

    operations = [
        migrations.CreateModel(
            name='AlertNotificationTemplate',
            fields=[
                ('key', models.CharField(default='task_summary', max_length=32, primary_key=True, serialize=False)),
                ('mode', models.CharField(choices=[('compact', '简洁'), ('detailed', '详细')], default='compact', max_length=16)),
                ('title_template', models.CharField(blank=True, max_length=255)),
                ('body_template', models.TextField(blank=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.RemoveConstraint(model_name='alertevent', name='net_alert_event_type_ck'),
        migrations.RemoveConstraint(model_name='alertevent', name='net_alert_event_status_ck'),
        migrations.AddField(model_name='alertevent', name='summary_data', field=models.JSONField(default=dict)),
        migrations.AddField(model_name='alertevent', name='summary_task', field=models.OneToOneField(
            blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='summary_alert', to='net.taskrun')),
        migrations.AddField(model_name='taskrun', name='alert_summary_attempted_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='taskrun', name='alert_summary_error', field=models.TextField(blank=True)),
        migrations.AddField(model_name='taskrun', name='alert_summary_processed_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AlterField(model_name='alertevent', name='event_type', field=models.CharField(
            choices=[('abnormal', '异常告警'), ('recovery', '恢复通知'), ('summary', '任务总结')], max_length=16)),
        migrations.AlterField(model_name='alertevent', name='status', field=models.CharField(
            choices=[('pending', '待发送'), ('sending', '发送中'), ('delivered', '已送达'),
                     ('recorded', '仅记录'), ('partial', '部分送达'), ('failed', '发送失败')],
            default='pending', max_length=16)),
        migrations.AlterField(model_name='alertevent', name='target_run', field=models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='alert_events', to='net.tasktargetrun')),
        migrations.AddConstraint(model_name='alertevent', constraint=models.CheckConstraint(
            condition=models.Q(event_type__in=('abnormal', 'recovery', 'summary')), name='net_alert_event_type_ck')),
        migrations.AddConstraint(model_name='alertevent', constraint=models.CheckConstraint(
            condition=models.Q(status__in=('pending', 'sending', 'delivered', 'partial', 'failed', 'recorded')),
            name='net_alert_event_status_ck')),
        migrations.AddConstraint(model_name='alertevent', constraint=models.CheckConstraint(
            condition=(models.Q(event_type='summary', summary_task__isnull=False,
                                summary_task=models.F('task'), target_run__isnull=True, target_type='task')
                       | models.Q(event_type__in=('abnormal', 'recovery'),
                                  summary_task__isnull=True, target_run__isnull=False)),
            name='net_alert_event_summaryshape_ck')),
        migrations.AddConstraint(model_name='alertnotificationtemplate', constraint=models.CheckConstraint(
            condition=models.Q(key='task_summary'), name='net_alert_template_key_ck')),
        migrations.AddConstraint(model_name='alertnotificationtemplate', constraint=models.CheckConstraint(
            condition=models.Q(mode__in=('compact', 'detailed')), name='net_alert_template_mode_ck')),
        # Worker must be stopped before applying this migration. Retirement is
        # intentionally irreversible: reversing must never requeue old notices.
        migrations.RunPython(retire_device_deliveries, migrations.RunPython.noop),
    ]

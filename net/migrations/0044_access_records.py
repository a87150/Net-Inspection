# Generated manually for the access-control management-platform feature.

import django.db.models.deletion
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('net', '0043_device_collection_templates')]

    operations = [
        migrations.CreateModel(
            name='AccessRecordSource',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255, unique=True)),
                ('platform', models.CharField(choices=[('zkteco_v6600', '中控万傲瑞达 V6600'), ('isecure_center', '海康 iSecure Center（待接入）'), ('dss', '大华 DSS（待接入）')], max_length=32)),
                ('api_version', models.CharField(max_length=128)),
                ('base_url', models.URLField(max_length=500)),
                ('event_path', models.CharField(blank=True, max_length=500)),
                ('authentication_mode', models.CharField(choices=[('query_token', '查询参数令牌'), ('header_token', '请求头令牌')], default='query_token', max_length=32)),
                ('authentication_name', models.CharField(default='access_token', max_length=128)),
                ('username', models.CharField(blank=True, max_length=255)),
                ('credential_ciphertext', models.BinaryField(blank=True, editable=False)),
                ('verify_ssl', models.BooleanField(default=True)),
                ('is_enabled', models.BooleanField(default=True)),
                ('cursor_at', models.DateTimeField(blank=True, null=True)),
                ('last_success_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True)),
                ('default_lookback_minutes', models.PositiveIntegerField(default=60)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('security_device', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='access_record_sources', to='net.securitydevice')),
            ],
            options={'indexes': [models.Index(fields=['platform', 'is_enabled'], name='net_access_source_platform_idx')]},
        ),
        migrations.CreateModel(
            name='AccessRecord',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_event_id', models.CharField(max_length=255)),
                ('occurred_at', models.DateTimeField()),
                ('employee_number', models.CharField(blank=True, max_length=255)),
                ('person_name', models.CharField(blank=True, max_length=255)),
                ('door_name', models.CharField(blank=True, max_length=255)),
                ('direction', models.CharField(choices=[('in', '进入'), ('out', '离开'), ('unknown', '未知')], default='unknown', max_length=16)),
                ('result', models.CharField(choices=[('passed', '通过'), ('denied', '拒绝'), ('unknown', '未知')], default='unknown', max_length=16)),
                ('card_number', models.CharField(blank=True, max_length=255)),
                ('imported_at', models.DateTimeField(auto_now_add=True)),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='records', to='net.accessrecordsource')),
            ],
            options={
                'indexes': [models.Index(fields=['source', '-occurred_at', '-id'], name='net_access_source_time_idx'), models.Index(fields=['-occurred_at', '-id'], name='net_access_time_idx')],
                'constraints': [models.UniqueConstraint(fields=('source', 'source_event_id'), name='net_access_event_source_uniq')],
            },
        ),
        migrations.AlterField(
            model_name='taskrun', name='task_type',
            field=models.CharField(choices=[('domain_sync', '域控同步'), ('inspection', '设备巡检'), ('computer_analysis', '计算机日志分析'), ('computer_fetch', 'PC 日志获取'), ('people_test', '人员目录连接测试'), ('people_preview', '人员目录同步预览'), ('people_sync', '人员自动同步'), ('domain_operation', '域控操作'), ('access_sync', '门禁记录采集')], max_length=32),
        ),
        migrations.AlterField(
            model_name='tasktargetrun', name='target_type',
            field=models.CharField(choices=[('domain_config', '域控目录'), ('network_device', '网络设备'), ('server', '服务器'), ('monitor', '安防设备'), ('computer_log', '计算机日志'), ('computer_source', 'PC 日志来源'), ('people_source', '人员目录来源'), ('domain_account', '域账号'), ('domain_computer', '域计算机'), ('access_source', '门禁平台来源')], max_length=32),
        ),
        migrations.RemoveConstraint(model_name='taskrun', name='net_task_binding_shape_ck'),
        migrations.AddConstraint(
            model_name='taskrun',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(task_type__in=('people_test', 'people_preview'), people_source__isnull=False, inspection_profile__isnull=True, analysis_profile__isnull=True, schedule__isnull=True, source='manual')
                    | models.Q(task_type='people_sync', people_source__isnull=False, inspection_profile__isnull=True, analysis_profile__isnull=True, schedule__isnull=False, source='scheduled', people_applied_at__isnull=True)
                    | models.Q(task_type='inspection', people_source__isnull=True, inspection_profile__isnull=False, analysis_profile__isnull=True, people_applied_at__isnull=True)
                    | models.Q(task_type__in=('computer_analysis', 'computer_fetch'), people_source__isnull=True, inspection_profile__isnull=True, analysis_profile__isnull=False, people_applied_at__isnull=True)
                    | models.Q(task_type='domain_sync', people_source__isnull=True, inspection_profile__isnull=True, analysis_profile__isnull=True, people_applied_at__isnull=True)
                    | models.Q(task_type='domain_operation', people_source__isnull=True, inspection_profile__isnull=True, analysis_profile__isnull=True, schedule__isnull=True, source='manual', people_applied_at__isnull=True)
                    | models.Q(task_type='access_sync', people_source__isnull=True, inspection_profile__isnull=True, analysis_profile__isnull=True, schedule__isnull=True, source='manual', people_applied_at__isnull=True)
                ), name='net_task_binding_shape_ck',
            ),
        ),
    ]

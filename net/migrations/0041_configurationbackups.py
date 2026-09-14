import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('net', '0040_mariadb_active_target_scope')]

    operations = [migrations.CreateModel(
        name='DeviceConfigurationBackup',
        fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('device_type', models.CharField(choices=[('network_device', 'Network device'), ('monitor', 'Security device')], max_length=20)),
            ('device_id', models.UUIDField()),
            ('backup_date', models.DateField()),
            ('captured_at', models.DateTimeField()),
            ('filename', models.CharField(max_length=255)),
            ('media_type', models.CharField(max_length=100)),
            ('scope', models.CharField(max_length=100)),
            ('vendor', models.CharField(max_length=100)),
            ('sha256', models.CharField(max_length=64)),
            ('byte_size', models.PositiveIntegerField()),
            ('ciphertext', models.BinaryField()),
            ('task_target', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='configuration_backups', to='net.tasktargetrun')),
        ],
        options={
            'ordering': ['-backup_date', '-captured_at', '-id'],
            'constraints': [models.UniqueConstraint(fields=('device_type', 'device_id', 'backup_date'), name='net_config_backup_device_day')],
        },
    )]

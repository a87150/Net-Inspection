import django.db.models.deletion
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('net', '0009_target_alert_processing')]

    operations = [
        migrations.CreateModel(
            name='AlertTestSend',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('sent', '发送成功'), ('failed', '发送失败')], max_length=16)),
                ('response_summary', models.CharField(blank=True, max_length=1000)),
                ('error_summary', models.CharField(blank=True, max_length=1000)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('channel', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='test_send_audits', to='net.alertchannel')),
            ],
        ),
        migrations.AddIndex(
            model_name='alerttestsend',
            index=models.Index(fields=['channel', 'created_at'], name='net_alert_test_channel_idx'),
        ),
    ]

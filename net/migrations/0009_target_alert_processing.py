from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('net', '0008_alert_delivery_leases')]

    operations = [
        migrations.AddField(
            model_name='tasktargetrun', name='alert_processed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='tasktargetrun', name='alert_attempted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='tasktargetrun', name='alert_processing_error',
            field=models.CharField(blank=True, max_length=1000),
        ),
        migrations.AddIndex(
            model_name='tasktargetrun',
            index=models.Index(
                fields=['alert_processed_at', 'alert_attempted_at', 'finished_at'],
                name='net_target_alert_pending_idx',
            ),
        ),
    ]

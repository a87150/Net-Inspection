from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('net', '0046_pc_disk_threshold')]

    operations = [
        migrations.AddField(model_name='schedule', name='last_schedule_attempt_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='schedule', name='last_schedule_status', field=models.CharField(blank=True, default='', max_length=16)),
        migrations.AddField(model_name='schedule', name='last_schedule_error', field=models.CharField(blank=True, default='', max_length=500)),
    ]

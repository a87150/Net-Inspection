from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('net', '0036_record_report_fields')]
    operations = [
        migrations.AddIndex(model_name='taskrun', index=models.Index(
            fields=('task_type', '-created_at', '-id'), name='net_task_type_created_idx')),
        migrations.AddIndex(model_name='taskrun', index=models.Index(
            fields=('inspection_profile', '-created_at', '-id'), name='net_task_profile_created_idx')),
    ]

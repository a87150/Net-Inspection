from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('net', '0037_task_history_indexes')]

    operations = [
        migrations.AddField(
            model_name='people',
            name='phone',
            field=models.CharField('手机号', max_length=64, blank=True, default=''),
        ),
    ]

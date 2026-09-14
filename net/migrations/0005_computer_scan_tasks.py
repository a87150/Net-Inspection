from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('net', '0004_computer_log_archive')]

    operations = [
        migrations.AlterField(
            model_name='taskrun',
            name='task_type',
            field=models.CharField(
                choices=[
                    ('inspection', '设备巡检'),
                    ('computer_analysis', '计算机日志分析'),
                    ('computer_scan', '计算机日志扫描'),
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name='tasktargetrun',
            name='target_type',
            field=models.CharField(
                choices=[
                    ('network_device', '网络设备'),
                    ('server', '服务器'),
                    ('monitor', '安防设备'),
                    ('computer_log', '计算机日志'),
                    ('computer_scan', '计算机日志扫描'),
                ],
                max_length=32,
            ),
        ),
    ]

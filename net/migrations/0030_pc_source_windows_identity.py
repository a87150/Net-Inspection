from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('net', '0029_pc_analysis_handoff_recovery')]
    operations = [
        migrations.AddField(model_name='pclogsourceconfig', name='smb_auth_mode',
            field=models.CharField(max_length=16, default='system', choices=[
                ('system', '使用当前 Windows 运行账号（默认）'), ('credentials', '手动指定账号密码')])),
        migrations.AlterField(model_name='pclogsourceconfig', name='username',
            field=models.CharField(max_length=255, blank=True)),
    ]

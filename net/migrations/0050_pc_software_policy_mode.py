from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('net', '0049_network_version_templates')]
    operations = [migrations.AddField(
        model_name='computeranalysisprofile', name='software_policy_mode',
        field=models.CharField('软件分析模式', max_length=16, default='whitelist',
            db_default='whitelist', choices=[('whitelist', '白名单模式'), ('blacklist', '黑名单模式')]),
    )]

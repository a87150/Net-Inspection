from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('net', '0032_split_pc_domain_check')]
    operations = [migrations.AddField(
        model_name='computeranalysisprofile', name='matching_mode',
        field=models.CharField(max_length=16, default='logs', choices=[
            ('logs', '不按人员匹配（日志为主）'), ('people', '按人员匹配（人员为主）')]),
    )]

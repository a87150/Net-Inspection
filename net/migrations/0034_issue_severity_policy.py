from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('net', '0033_pc_matching_mode')]
    operations = [migrations.CreateModel(name='IssueSeverityPolicy', fields=[
        ('id', models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False, serialize=False)),
        ('overrides', models.JSONField(default=dict, blank=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
    ], options={'verbose_name': '问题严重等级设置', 'verbose_name_plural': '问题严重等级设置',
                'constraints': [models.CheckConstraint(condition=models.Q(id=1), name='net_issue_policy_singleton')]})]

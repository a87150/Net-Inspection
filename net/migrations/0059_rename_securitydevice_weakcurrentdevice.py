from django.db import migrations, models


# ``monitors`` was a stored choice value on three columns.  Changing the choice
# label alone would leave existing rows pointing at a value the model rejects,
# so the stored value moves with it.
_RENAMED_CHOICE_COLUMNS = (
    ('IssueSeverityPolicy', 'project'),
    ('DeviceCollectionBinding', 'kind'),
    ('DeviceCollectionTemplate', 'kind'),
)


def _rewrite_choice_value(apps, old, new):
    for model_name, field_name in _RENAMED_CHOICE_COLUMNS:
        model = apps.get_model('net', model_name)
        model.objects.filter(**{field_name: old}).update(**{field_name: new})


def rename_monitors_to_weakcurrent(apps, schema_editor):
    _rewrite_choice_value(apps, 'monitors', 'weakcurrent')


def revert_weakcurrent_to_monitors(apps, schema_editor):
    _rewrite_choice_value(apps, 'weakcurrent', 'monitors')


class Migration(migrations.Migration):
    """Rename 安防设备 to 弱电设备 and let devices be switched off.

    Django's autodetector emitted Delete+Create here, which would have dropped
    every existing 弱电设备 row. RenameModel/RenameField keep the data and just
    rename the table, the model and the foreign key column.
    """

    dependencies = [
        ('net', '0058_network_topology'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='SecurityDevice',
            new_name='WeakCurrentDevice',
        ),
        migrations.AlterModelOptions(
            name='weakcurrentdevice',
            options={'verbose_name': '弱电设备', 'verbose_name_plural': '弱电设备',
                     'db_table': 'net_weakcurrentdevice'},
        ),
        migrations.RenameField(
            model_name='accessrecordsource',
            old_name='security_device',
            new_name='weak_current_device',
        ),
        migrations.AddField(
            model_name='weakcurrentdevice',
            name='is_enabled',
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name='network_device',
            name='is_enabled',
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name='server',
            name='is_enabled',
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AlterField(
            model_name='devicecollectionbinding',
            name='kind',
            field=models.CharField(choices=[('networks', '网络设备'), ('servers', '服务器'),
                                           ('weakcurrent', '弱电设备')], max_length=16),
        ),
        migrations.AlterField(
            model_name='devicecollectiontemplate',
            name='kind',
            field=models.CharField(choices=[('networks', '网络设备'), ('servers', '服务器'),
                                           ('weakcurrent', '弱电设备')], max_length=16),
        ),
        migrations.AlterField(
            model_name='inspectionprofile',
            name='device_type',
            field=models.CharField(choices=[('network_device', '网络设备'), ('server', '服务器'),
                                           ('monitor', '弱电设备')], max_length=32),
        ),
        migrations.AlterField(
            model_name='issueseveritypolicy',
            name='project',
            field=models.CharField(choices=[('computers', 'PC 日志分析'),
                                           ('networks', '网络设备巡检'),
                                           ('servers', '服务器巡检'),
                                           ('weakcurrent', '弱电设备巡检')],
                                  default='computers', max_length=16, unique=True),
        ),
        migrations.AlterField(
            model_name='tasktargetrun',
            name='target_type',
            field=models.CharField(choices=[('domain_config', '域控目录'),
                                           ('network_device', '网络设备'),
                                           ('server', '服务器'),
                                           ('monitor', '弱电设备'),
                                           ('computer_log', '计算机日志'),
                                           ('people_source', '人员目录来源'),
                                           ('domain_account', '域账号'),
                                           ('domain_computer', '域计算机'),
                                           ('access_source', '门禁平台来源')], max_length=32),
        ),
        migrations.RunPython(rename_monitors_to_weakcurrent, revert_weakcurrent_to_monitors),
    ]

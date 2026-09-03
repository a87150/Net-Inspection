from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('net', '0019_domain_groups')]

    operations = [
        migrations.RenameModel(old_name='Monitor', new_name='SecurityDevice'),
        migrations.RemoveField(model_name='computer', name='disk_summary'),
        migrations.RemoveField(model_name='network_device', name='active_port_count'),
        migrations.AddField(model_name='server', name='architecture', field=models.CharField(blank=True, max_length=255, null=True)),
        migrations.AddField(model_name='server', name='cpu_logical_processor_count', field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='server', name='cpu_physical_core_count', field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='server', name='manufacturer', field=models.CharField(blank=True, max_length=255, null=True)),
        migrations.AddField(model_name='server', name='model', field=models.CharField(blank=True, max_length=255, null=True)),
        migrations.AddField(model_name='server', name='os_build', field=models.CharField(blank=True, max_length=255, null=True)),
        migrations.AddField(model_name='server', name='os_version', field=models.CharField(blank=True, max_length=255, null=True)),
        migrations.AddField(model_name='server', name='serial_number', field=models.CharField(blank=True, max_length=255, null=True)),
        migrations.AddField(model_name='server', name='system_installed_at', field=models.CharField(blank=True, max_length=255, null=True)),
    ]

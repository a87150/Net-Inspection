from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("net", "0069_ip_address_plan"),
    ]

    operations = [
        migrations.RenameField(
            model_name="maintenancewindow",
            old_name="device_id",
            new_name="target_id",
        ),
        migrations.AddField(
            model_name="maintenancewindow",
            name="target_type",
            field=models.CharField(
                choices=[
                    ("network_device", "网络设备"),
                    ("server", "服务器"),
                    ("monitor", "弱电设备"),
                ],
                default="network_device",
                max_length=32,
            ),
        ),
    ]

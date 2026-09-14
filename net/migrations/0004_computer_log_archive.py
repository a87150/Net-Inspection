from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('net', '0003_computer_log_file_metadata')]

    operations = [
        migrations.CreateModel(
            name='ComputerLogArchive',
            fields=[
                ('id', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('source_path', models.TextField()),
                ('destination_path', models.TextField()),
                ('identity', models.JSONField()),
                ('status', models.CharField(db_index=True, default='pending', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('log_file', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='archives', to='net.computerlogfile')),
            ],
        ),
    ]

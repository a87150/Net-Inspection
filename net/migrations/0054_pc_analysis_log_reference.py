from django.db import migrations, models
import django.db.models.deletion


def backfill_analysis_identity(apps, schema_editor):
    Log = apps.get_model('net', 'ComputerLogFile')
    Analysis = apps.get_model('net', 'ComputerAnalysis')
    alias = schema_editor.connection.alias
    last_pk = None
    while True:
        rows = Analysis.objects.using(alias).order_by('pk')
        if last_pk is not None:
            rows = rows.filter(pk__gt=last_pk)
        batch = list(rows[:200])
        if not batch:
            break
        stamps = dict(Log.objects.using(alias).filter(pk__in=[row.log_id for row in batch])
                      .values_list('pk', 'collected_at'))
        for row in batch:
            row.source_collected_at = row.source_collected_at or stamps.get(row.log_id)
        Analysis.objects.using(alias).bulk_update(batch, ['source_collected_at'], batch_size=200)
        last_pk = batch[-1].pk


class Migration(migrations.Migration):
    dependencies = [('net', '0053_pc_api_logs')]
    operations = [
        migrations.AlterField('computeranalysis', 'log_file', models.ForeignKey(
            to='net.computerlogfile', null=True, blank=True, db_constraint=False,
            on_delete=django.db.models.deletion.DO_NOTHING, related_name='analyses')),
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
            migrations.RemoveField('computeranalysis', 'log_file'),
            migrations.AddField('computeranalysis', 'log_id', models.PositiveBigIntegerField(
                null=True, blank=True, db_index=True, db_column='log_file_id')),
        ]),
        migrations.RunPython(backfill_analysis_identity, migrations.RunPython.noop),
    ]

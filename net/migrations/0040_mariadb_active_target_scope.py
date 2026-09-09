"""MariaDB/MySQL equivalent of the existing partial unique target index.

Keep the partial constraint for SQLite/PostgreSQL. MySQL-family backends ignore
it, so use a generated nullable key: finished targets evaluate to NULL and active
ones to their execution scope. The database enforces this even for bulk writes.
"""
from django.db import migrations


def install(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    schema_editor.execute("""
        ALTER TABLE net_tasktargetrun
        ADD COLUMN net_active_execution_scope VARCHAR(64)
        GENERATED ALWAYS AS (
            CASE WHEN status IN ('queued', 'running') THEN execution_scope_key ELSE NULL END
        ) STORED,
        ADD UNIQUE INDEX net_target_active_scope_mysql (net_active_execution_scope)
    """)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    schema_editor.execute('ALTER TABLE net_tasktargetrun DROP INDEX net_target_active_scope_mysql, '
                          'DROP COLUMN net_active_execution_scope')


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('net', '0039_domain_inactive_days')]
    operations = [migrations.RunPython(install, uninstall, atomic=False)]

"""Startup gate for the MariaDB time zone tables.

The date filters in index/common/table_query.py deliberately use a half-open range
instead of __date, which is why nothing is broken today. That protection does not
generalise: any future TruncMonth or ExtractHour makes Django emit
CONVERT_TZ(col, UTC, Asia/Shanghai), and with an empty mysql.time_zone_name that
silently evaluates to NULL -- a filter that returns nothing instead of raising.
MariaDB needs the tables imported once (mariadb-tzinfo-to-sql), and the official
CI image does not do it either, so the problem is invisible until it matters.
"""
from django.core.checks import Warning as CheckWarning
from django.core.checks import register
from django.db import connections


@register('net')
def check_mysql_timezone_tables(app_configs, **kwargs):
    if not connections['default'].vendor == 'mysql':
        return []
    connection = connections['default']
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT CONVERT_TZ(NOW(), %s, %s)',
                           ['+00:00', connection.settings_dict.get('TIME_ZONE') or 'UTC'])
            usable = cursor.fetchone()[0] is not None
    except Exception:
        # A fresh install has no database yet; that is not this check's business.
        return []
    if usable:
        return []
    return [CheckWarning(
        'MariaDB has no usable time zone tables, so named zones such as Asia/Shanghai'
        'evaluate to NULL.',
        hint=('Import them once on the database server:  '
              'mariadb-tzinfo-to-sql /usr/share/zoneinfo | mysql mysql  '
              '(Windows: mysql_tzinfo_to_sql.exe). Until then, avoid TruncMonth and'
              ' ExtractHour on DateTimeField.'),
        id='net.W001',
    )]
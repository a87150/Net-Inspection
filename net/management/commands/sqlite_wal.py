import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection

from net.infrastructure.database import initialize_sqlite_wal


class Command(BaseCommand):
    help = 'Inspect SQLite journal mode; --apply enables WAL during an explicit maintenance window.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        try:
            result = initialize_sqlite_wal(connection, apply=options['apply'])
        except DatabaseError as exc:
            raise CommandError(f'WAL maintenance failed; stop Web/Workers and retry: {exc}') from exc
        self.stdout.write(json.dumps(result))

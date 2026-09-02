from django.core.management.base import BaseCommand

from net.models import Computer
from net.devices.pc.snapshot import SNAPSHOT_FIELD_NAMES, update_computer_snapshot


class Command(BaseCommand):
    help = '从每台计算机的最新巡检记录回填摘要字段'

    def handle(self, *args, **options):
        scanned = 0
        updated = 0

        for computer in Computer.objects.all():
            scanned += 1
            analysis = computer.analyses.select_related('log_file').order_by(
                '-log_file__modified_at', '-created_at',
            ).first()
            if analysis is None:
                continue

            previous_values = {
                field_name: getattr(computer, field_name)
                for field_name in SNAPSHOT_FIELD_NAMES
            }
            update_computer_snapshot(computer, analysis)
            if any(
                getattr(computer, field_name) != value
                for field_name, value in previous_values.items()
            ):
                updated += 1

        self.stdout.write(f'Scanned: {scanned}; updated: {updated}')

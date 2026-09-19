from django.core.management.base import BaseCommand

from net.models import Computer, ComputerLogFile
from net.devices.pc.snapshot import SNAPSHOT_FIELD_NAMES, update_computer_snapshot


class Command(BaseCommand):
    help = '从每台计算机的最新巡检记录回填摘要字段'

    def handle(self, *args, **options):
        scanned = 0
        updated = 0

        for computer in Computer.objects.all():
            scanned += 1
            log_ids = computer.analyses.exclude(log_id__isnull=True).values_list('log_id', flat=True)
            log = ComputerLogFile.objects.filter(pk__in=log_ids).order_by('-collected_at', '-pk').first()
            analysis = (computer.analyses.filter(log_id=log.pk).order_by('-created_at').first()
                        if log else None)
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

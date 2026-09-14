from django.core.management.base import BaseCommand
from django.db.models import OuterRef, Subquery
from net.models import Computer, ComputerLogFile
from net.devices.pc.snapshot import extract_computer_snapshot


class Command(BaseCommand):
    help = 'Fill only empty PC disk capacities from each computer latest imported log; dry run by default.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        latest = ComputerLogFile.objects.filter(computer_id=OuterRef('pk'), import_status='imported').order_by('-collected_date', '-modified_at', '-pk')
        rows = Computer.objects.filter(disk_total_gb__isnull=True).annotate(latest_log=Subquery(latest.values('pk')[:1])).values_list('pk', 'latest_log')
        found = updated = 0
        for pk, log_id in rows.iterator(chunk_size=100):
            if log_id is None:
                continue
            payload = ComputerLogFile.objects.filter(pk=log_id).values_list('payload', flat=True).first()
            if not isinstance(payload, dict):
                continue
            value = extract_computer_snapshot({}, [], payload.get('计算机硬件资源情况'), disk_payload=payload).get('disk_total_gb')
            if value is None or value <= 0:
                continue
            found += 1
            if options['apply']:
                updated += Computer.objects.filter(pk=pk, disk_total_gb__isnull=True).update(disk_total_gb=value)
        self.stdout.write(f'eligible={found}, updated={updated}, apply={options["apply"]}')

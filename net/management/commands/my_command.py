from django.core.management.base import BaseCommand
from net.models import ComputerAnalysis

class Command(BaseCommand):
    help = '打印所有巡检的计算机名'

    def add_arguments(self, parser):
        parser.add_argument('--recent', type=int, help='只列出最近 N 条记录')

    def handle(self, *args, **options):
        qs = ComputerAnalysis.objects.select_related('computer').order_by('-created_at')
        if options['recent']:
            qs = qs[:options['recent']]

        for analysis in qs:
            self.stdout.write(f"{analysis.created_at} - {analysis.computer.computer_name}")

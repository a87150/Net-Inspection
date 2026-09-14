from django.core.management.base import BaseCommand
from net.devices.network.default_profiles import create_default_network_templates


class Command(BaseCommand):
    help = '创建华为、华三、锐捷、思科基础与类型模板，保留已有模板。'

    def handle(self, *args, **options):
        rows=create_default_network_templates()
        self.stdout.write(f'已创建 {len(rows)} 个模板；已有模板保持不变。')

from django.core.management.base import BaseCommand
from net.devices.network.default_profiles import create_default_network_templates
from net.devices.weakcurrent.default_profiles import create_default_weak_current_templates


class Command(BaseCommand):
    help = '创建网络设备（华为、华三、锐捷、思科）和弱电设备默认模板，保留已有模板。'

    def handle(self, *args, **options):
        network_rows = create_default_network_templates()
        weak_current_rows = create_default_weak_current_templates()
        total = len(network_rows) + len(weak_current_rows)
        self.stdout.write(
            f'已创建 {len(network_rows)} 个网络模板、{len(weak_current_rows)} 个弱电模板；'
            f'已有模板保持不变（共 {total} 个新增）。'
        )

"""Compatibility command that creates infrastructure tasks without collecting inline."""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from net.models import InspectionProfile, Network_Device, SecurityDevice, Server, TaskRun
from net.inspections.queue import enqueue_task


_ASSET_TYPES = {
    'networks': (
        InspectionProfile.DeviceType.NETWORK_DEVICE,
        Network_Device,
        ['device_info', 'cpu', 'memory', 'temperature', 'interface_status', 'vlan_status', 'logs'],
        '命令行网络设备巡检',
    ),
    'servers': (
        InspectionProfile.DeviceType.SERVER,
        Server,
        ['computer_name', 'system_info', 'cpu', 'memory', 'storage_status', 'network_info', 'services', 'logs'],
        '命令行服务器巡检',
    ),
    'monitors': (
        InspectionProfile.DeviceType.MONITOR,
        SecurityDevice,
        ['device_info', 'status_data', 'channel_status', 'storage_status'],
        '命令行安防设备巡检',
    ),
}


def _command_profile(device_type, selected_items, name):
    profile, _created = InspectionProfile.objects.get_or_create(
        name=name,
        device_type=device_type,
        defaults={
            'selected_items': selected_items,
            'timeout_seconds': 60,
            'concurrent_workers': 4,
        },
    )
    return profile


class Command(BaseCommand):
    help = '创建网络设备、服务器和安防设备的后台巡检任务'

    def add_arguments(self, parser):
        parser.add_argument(
            '--asset-type',
            choices=['all', 'networks', 'servers', 'monitors'],
            default='all',
        )
        parser.add_argument('--asset-id', help='只为指定 UUID 资产创建巡检任务')
        # Preserve accepted legacy flags while the Worker owns execution knobs.
        parser.add_argument('--timeout', type=int, default=1500, help='已弃用：由巡检配置控制')
        parser.add_argument('--workers', type=int, default=20, help='已弃用：由 Worker 控制')

    def handle(self, *args, **options):
        created = 0
        skipped = 0
        selected_type = options['asset_type']
        for key, (device_type, model, selected_items, profile_name) in _ASSET_TYPES.items():
            if selected_type not in {'all', key}:
                continue
            queryset = model.objects.all()
            if options.get('asset_id'):
                queryset = queryset.filter(pk=options['asset_id'])
            target_ids = list(queryset.values_list('pk', flat=True))
            if not target_ids:
                continue
            profile = _command_profile(device_type, selected_items, profile_name)
            try:
                enqueue_task(profile, target_ids, TaskRun.Source.MANUAL)
            except ValidationError as exc:
                if '相同配置和目标范围已有活动任务' not in str(exc):
                    raise
                skipped += 1
            else:
                created += 1

        if created:
            self.stdout.write(self.style.SUCCESS(f'已创建 {created} 个巡检任务，等待 Worker 执行。'))
        elif skipped:
            self.stdout.write(self.style.WARNING('相同设备范围已有活动巡检任务。'))
        else:
            self.stdout.write(self.style.WARNING('没有符合条件的设备，未创建巡检任务。'))

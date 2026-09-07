"""Add offline remote-fetch examples without rewriting existing inventory."""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from net.devices.pc.configuration import source_snapshot
from net.management.commands.seed_demo_data import CURRENT_LOG_IDENTITIES, _demo_uuid
from net.models import (
    ComputerAnalysisProfile, ComputerLogFile, ComputerLogTransfer,
    PCLogSourceConfig, TaskRun, TaskTargetRun,
)


class Command(BaseCommand):
    help = '补充离线 PC 获取任务与传输展示数据；不访问网络、不覆盖已有配置。'

    @transaction.atomic
    def handle(self, *args, **options):
        source, _ = PCLogSourceConfig.objects.get_or_create(pk=1, defaults={
            'source_type': 'smb', 'host': 'files.example.invalid', 'port': 445,
            'username': 'demo-reader', 'share_name': 'logs',
            'remote_incoming_directory': 'incoming', 'file_time_mode': 'recent_days',
            'local_staging_directory': str(settings.BASE_DIR / 'runtime/pc-staging'),
            'terminal_windows_path': r'\\files.example.invalid\logs\incoming',
            'terminal_macos_path': '/Volumes/Logs/incoming',
        })
        if source.host != 'files.example.invalid':
            raise CommandError('已配置实际日志服务器，拒绝添加演示传输。')
        profile, _ = ComputerAnalysisProfile.objects.get_or_create(
            pk=_demo_uuid('demo-profile-analysis'),
            defaults={'name': '演示日志分析', 'analysis_items': ['resource'], 'concurrent_workers': 4})
        logs = []
        for path, digest in CURRENT_LOG_IDENTITIES:
            log = ComputerLogFile.objects.filter(source_path=path, content_hash=digest).first()
            if log:
                logs.append(log)
        finished = timezone.now() - timedelta(minutes=10)
        identity = _demo_uuid('remote-fetch-offline-v1')
        if TaskRun.objects.filter(pk=identity).exists():
            self.stdout.write('PC 远程获取演示已存在，保留原记录。')
            return
        task = TaskRun.objects.create(
            pk=identity, task_type='computer_fetch', source='manual',
            analysis_profile=profile, status='success', progress=100,
            total_targets=1, completed_targets=1, successful_targets=1,
            started_at=finished - timedelta(seconds=8), finished_at=finished,
            profile_snapshot={'name': profile.name, 'log_source': source_snapshot(source)},
            selected_items_snapshot=list(profile.analysis_items),
            parameters_snapshot={'demo_only': True},
            target_scope_snapshot={'target_type': 'computer_source', 'target_ids': ['1']})
        target = TaskTargetRun.objects.create(
            task=task, target_type='computer_source', target_id='1', status='success',
            target_snapshot={'source': source_snapshot(source)},
            started_at=task.started_at, finished_at=finished, alert_processed_at=finished,
            result_snapshot={'discovered': len(logs), 'downloaded': len(logs),
                             'imported': len(logs), 'duplicate': 0, 'failed': 0,
                             'skipped': 0, 'move_failures': 0, 'demo_only': True})
        for log in logs:
            ComputerLogTransfer.objects.create(
                source=source, task_target=target, log_file=log,
                source_snapshot=source_snapshot(source),
                remote_source_path=log.remote_source_path or f'incoming/{log.pk}.json',
                remote_archive_path=f'processed/demo-{log.pk}.json',
                remote_size=log.file_size, observed_mtime=log.modified_at,
                content_hash=log.content_hash, stage='completed', attempt_count=1)
        self.stdout.write(self.style.SUCCESS(
            f'新增离线获取任务 1 条、传输记录 {len(logs)} 条。未执行任何外部连接。'))

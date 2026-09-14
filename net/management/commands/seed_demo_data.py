import hashlib
import uuid
from datetime import date, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from net.models import (
    Computer,
    ComputerAnalysis,
    ComputerLogFile,
    Domain_Account,
    Domain_Computer,
    Domain_Controller_Config,
    Domain_Group,
    DomainOperation,
    Error_Computer,
    Error_Monitor,
    Error_Network_Device,
    Error_Server,
    SecurityDevice,
    Monitor_Inspection,
    Network_Device,
    Network_Device_Inspection,
    People,
    RecordStatus,
    Server,
    Server_Inspection,
    AlertChannel,
    AlertDelivery,
    AlertEvent,
    AlertPolicy,
    InspectionProfile,
    ComputerAnalysisProfile,
    PCLogSourceConfig,
    PeopleSyncSource,
    Schedule,
    TaskRun,
    TaskTargetRun,
)


DEMO_NAMESPACE = uuid.UUID('798f8a78-53bb-5b1f-a7c3-d6254c7fde4b')
DEMO_SECRET = 'DEMO-ONLY-NOT-A-SECRET'

DEMO_EMPLOYEE_IDS = tuple(f'DEMO-P00{index}' for index in range(1, 8))
DEMO_COMPUTER_NAMES = (
    'DEMO-PC-OPS-01',
    'DEMO-PC-DEV-02',
    'DEMO-PC-OFFLINE',
    'DEMO-PC-01',
    'DEMO-PC-02',
)
DEMO_NETWORK_IPS = ('192.0.2.11', '192.0.2.12', '192.0.2.13')
DEMO_SERVER_IPS = ('198.51.100.21', '198.51.100.22', '198.51.100.23')
DEMO_MONITOR_IPS = (
    '203.0.113.31', '203.0.113.32', '203.0.113.33',
    '203.0.113.34', '203.0.113.35',
)
DEMO_DOMAIN_LOGINS = (
    'demo.zhang@demo.invalid',
    'demo.li@demo.invalid',
    'demo.disabled@demo.invalid',
)
DEMO_DOMAIN_COMPUTERS = (
    'DEMO-DOMAIN-PC-01',
    'DEMO-DOMAIN-PC-02',
    'DEMO-DOMAIN-PC-OLD',
)
DEMO_DOMAIN_GROUPS = (
    'DEMO-IT-ADMINS',
    'DEMO-NOTICES',
    'DEMO-PC-OPERATORS',
)


def _demo_hash(value):
    return hashlib.sha256(f'net-demo:{value}'.encode('utf-8')).hexdigest()


def _demo_uuid(value):
    return uuid.uuid5(DEMO_NAMESPACE, value)


CURRENT_RECORD_IDENTITIES = (
    (ComputerAnalysis, 'computer-analysis', range(1, 5)),
    (Network_Device_Inspection, 'network-inspection', range(1, 6)),
    (Server_Inspection, 'server-inspection', range(1, 5)),
    (Monitor_Inspection, 'monitor-inspection', range(1, 5)),
)
CURRENT_ERROR_IDENTITIES = (
    (Error_Computer, 'computer-error', (3, 4)),
    (Error_Network_Device, 'network-error', (2, 4)),
    (Error_Server, 'server-error', (3, 4)),
    (Error_Monitor, 'monitor-error', (3, 4)),
)
CURRENT_LOG_IDENTITIES = tuple(
    (
        f'demo://powershell/{computer_name}-{index}.json',
        _demo_hash(f'computer-log-{index}'),
    )
    for computer_name, index in (
        ('DEMO-PC-OPS-01', 1),
        ('DEMO-PC-DEV-02', 2),
        ('DEMO-PC-DEV-02', 3),
        ('DEMO-PC-OFFLINE', 4),
    )
)
LEGACY_LOG_IDENTITIES = (
    (
        'demo://DEMO-PC-01.json',
        '03ce1696b1f9d7cf58632e13a534acba94ae49da32689ebf03cc5b93583baafc',
    ),
    (
        'demo://DEMO-PC-02.json',
        'a3b4fbf4c1fb082e7aca3f54099c27e92a4e7b31df6b76a00a5d872e4fb6cc8a',
    ),
)
LEGACY_COMPUTER_SPECS = (
    {
        'computer_name': 'DEMO-PC-01',
        'os': 'Windows 11',
        'user_name': '演示-张工',
    },
    {
        'computer_name': 'DEMO-PC-02',
        'os': 'Windows 10',
        'user_name': '演示-李工',
    },
)
LEGACY_ANALYSIS_SPECS = (
    {
        'computer_name': 'DEMO-PC-01',
        'source_path': 'demo://DEMO-PC-01.json',
        'content_hash': '03ce1696b1f9d7cf58632e13a534acba94ae49da32689ebf03cc5b93583baafc',
        'status': 'success',
        'summary': '分析正常',
        'details': {
            'system_info': {'操作系统': 'Windows 11'},
            'computer_info': {'CPU使用率': 15},
        },
        'exceptions': [],
        'error': None,
    },
    {
        'computer_name': 'DEMO-PC-02',
        'source_path': 'demo://DEMO-PC-02.json',
        'content_hash': 'a3b4fbf4c1fb082e7aca3f54099c27e92a4e7b31df6b76a00a5d872e4fb6cc8a',
        'status': 'failed',
        'summary': 'CPU 使用率过高',
        'details': {
            'system_info': {'操作系统': 'Windows 10'},
            'computer_info': {'CPU使用率': 70},
        },
        'exceptions': [{
            '问题类型': 'CPU 使用率过高',
            '详细问题': '演示数据：CPU 使用率达到 70%。',
        }],
        'error': (
            'CPU 使用率过高',
            '演示数据：CPU 使用率达到 70%。',
        ),
    },
)
LEGACY_SERVER_SPECS = (
    {
        'ip': '192.0.2.21',
        'name': '演示-Linux应用服务器',
        'status': 'success',
        'summary': '巡检成功',
        'details': {
            'cpu': {'usage_percent': 35},
            'memory': {'usage_percent': 52},
            'storage_status': [{'mount': '/', 'usage_percent': 62}],
        },
    },
    {
        'ip': '192.0.2.22',
        'name': '演示-Windows文件服务器',
        'status': 'success',
        'summary': '巡检成功',
        'details': {
            'cpu': {'usage_percent': 35},
            'memory': {'usage_percent': 52},
            'storage_status': [{'mount': 'C:', 'usage_percent': 62}],
        },
    },
    {
        'ip': '192.0.2.23',
        'name': '演示-数据库服务器',
        'status': 'failed',
        'summary': '磁盘空间不足（演示）',
        'details': {
            'cpu': {'usage_percent': 35},
            'memory': {'usage_percent': 52},
            'storage_status': [{'mount': '/', 'usage_percent': 62}],
        },
    },
)
LEGACY_MONITOR_SPECS = (
    {
        'ip': '192.0.2.31',
        'name': '演示-大厅摄像机',
        'status': 'success',
        'reachable': True,
        'summary': '巡检成功',
    },
    {
        'ip': '192.0.2.32',
        'name': '演示-NVR录像机',
        'status': 'success',
        'reachable': True,
        'summary': '巡检成功',
    },
    {
        'ip': '192.0.2.33',
        'name': '演示-仓库摄像机',
        'status': 'failed',
        'reachable': False,
        'summary': '摄像机离线（演示）',
    },
)

FIXED_ASSET_OWNERS = (
    *((People, f'people:{employee_id}', {'employee_id': employee_id})
      for employee_id in DEMO_EMPLOYEE_IDS),
    *((Computer, f'computer:{name}', {'computer_name': name})
      for name in DEMO_COMPUTER_NAMES[:3]),
    *((Network_Device, f'network:{ip}', {'ip': ip}) for ip in DEMO_NETWORK_IPS),
    *((Server, f'server:{ip}', {'ip': ip}) for ip in DEMO_SERVER_IPS),
    *((SecurityDevice, f'monitor:{ip}', {'ip': ip}) for ip in DEMO_MONITOR_IPS),
    *((Domain_Account, f'domain-account:{login}', {'login_name': login})
      for login in DEMO_DOMAIN_LOGINS),
    *((Domain_Computer, f'domain-computer:{name}', {'computer_name': name})
      for name in DEMO_DOMAIN_COMPUTERS),
    *((Domain_Group, f'domain-group:{name}', {'login_name': name})
      for name in DEMO_DOMAIN_GROUPS),
)
FIXED_RECORD_OWNERS = (
    (ComputerAnalysis, 'computer-analysis-1', {
        'computer__computer_name': 'DEMO-PC-OPS-01',
        'log_file__source_path': 'demo://powershell/DEMO-PC-OPS-01-1.json',
        'log_file__content_hash': _demo_hash('computer-log-1'),
    }),
    (ComputerAnalysis, 'computer-analysis-2', {
        'computer__computer_name': 'DEMO-PC-DEV-02',
        'log_file__source_path': 'demo://powershell/DEMO-PC-DEV-02-2.json',
        'log_file__content_hash': _demo_hash('computer-log-2'),
    }),
    (ComputerAnalysis, 'computer-analysis-3', {
        'computer__computer_name': 'DEMO-PC-DEV-02',
        'log_file__source_path': 'demo://powershell/DEMO-PC-DEV-02-3.json',
        'log_file__content_hash': _demo_hash('computer-log-3'),
    }),
    (ComputerAnalysis, 'computer-analysis-4', {
        'computer__computer_name': 'DEMO-PC-OFFLINE',
        'log_file__source_path': 'demo://powershell/DEMO-PC-OFFLINE-4.json',
        'log_file__content_hash': _demo_hash('computer-log-4'),
    }),
    *((Network_Device_Inspection, f'network-inspection-{index}', {'device__ip': ip})
      for index, ip in enumerate(('192.0.2.11', '192.0.2.11', '192.0.2.12', '192.0.2.13', '192.0.2.13'), 1)),
    *((Server_Inspection, f'server-inspection-{index}', {'server__ip': ip})
      for index, ip in enumerate(('198.51.100.21', '198.51.100.21', '198.51.100.22', '198.51.100.23'), 1)),
    *((Monitor_Inspection, f'monitor-inspection-{index}', {'monitor__ip': ip})
      for index, ip in enumerate(('203.0.113.31', '203.0.113.31', '203.0.113.32', '203.0.113.33'), 1)),
)
FIXED_ERROR_OWNERS = (
    (Error_Computer, 'computer-error-3', {'inspection_id': _demo_uuid('computer-analysis-3')}),
    (Error_Computer, 'computer-error-4', {'inspection_id': _demo_uuid('computer-analysis-4')}),
    (Error_Network_Device, 'network-error-2', {'inspection_id': _demo_uuid('network-inspection-2')}),
    (Error_Network_Device, 'network-error-4', {'inspection_id': _demo_uuid('network-inspection-4')}),
    (Error_Server, 'server-error-3', {'inspection_id': _demo_uuid('server-inspection-3')}),
    (Error_Server, 'server-error-4', {'inspection_id': _demo_uuid('server-inspection-4')}),
    (Error_Monitor, 'monitor-error-3', {'inspection_id': _demo_uuid('monitor-inspection-3')}),
    (Error_Monitor, 'monitor-error-4', {'inspection_id': _demo_uuid('monitor-inspection-4')}),
)
FIXED_ALERT_OWNERS = (
    (AlertChannel, 'alert-channel-feishu', {'name': '演示告警渠道-飞书'}),
    (AlertChannel, 'alert-channel-dingtalk', {'name': '演示告警渠道-钉钉'}),
    (AlertChannel, 'alert-channel-email', {'name': '演示告警渠道-邮件'}),
    (InspectionProfile, 'alert-profile-server', {'name': '演示告警巡检配置'}),
    (AlertPolicy, 'alert-policy-default', {'default_slot': AlertPolicy.DEFAULT_SLOT}),
    (AlertPolicy, 'alert-policy-server', {'inspection_profile_id': _demo_uuid('alert-profile-server')}),
    *((TaskRun, f'alert-task-{index}', {'inspection_profile_id': _demo_uuid('alert-profile-server')}) for index in (1, 2)),
    *((TaskTargetRun, f'alert-target-{index}', {'task_id': _demo_uuid(f'alert-task-{index}')}) for index in (1, 2)),
    (AlertEvent, 'alert-event-abnormal', {'target_run_id': _demo_uuid('alert-target-1'), 'event_type': 'abnormal'}),
    (AlertEvent, 'alert-event-recovery', {'target_run_id': _demo_uuid('alert-target-2'), 'event_type': 'recovery'}),
    (AlertDelivery, 'alert-delivery-abnormal-sent', {'event_id': _demo_uuid('alert-event-abnormal'), 'channel_id': _demo_uuid('alert-channel-feishu')}),
    (AlertDelivery, 'alert-delivery-abnormal-failed', {'event_id': _demo_uuid('alert-event-abnormal'), 'channel_id': _demo_uuid('alert-channel-dingtalk')}),
    (AlertDelivery, 'alert-delivery-recovery-sent', {'event_id': _demo_uuid('alert-event-recovery'), 'channel_id': _demo_uuid('alert-channel-email')}),
)

FIXED_PHASE4_OWNERS = (
    *((PeopleSyncSource, f'people-source-{provider}', {'source_key': f'people-provider-{provider}', 'source_type': provider})
      for provider in ('feishu', 'dingtalk')),
    (InspectionProfile, 'demo-profile-network', {'name': '演示网络巡检', 'device_type': 'network_device'}),
    (InspectionProfile, 'demo-profile-monitor', {'name': '演示安防巡检', 'device_type': 'monitor'}),
    (ComputerAnalysisProfile, 'demo-profile-analysis', {'name': '演示日志分析'}),
    (Schedule, 'demo-schedule-interval', {'inspection_profile_id': _demo_uuid('demo-profile-network')}),
    (Schedule, 'demo-schedule-daily', {'analysis_profile_id': _demo_uuid('demo-profile-analysis')}),
    *((TaskRun, f'demo-task-{status}', {'inspection_profile_id': _demo_uuid('demo-profile-network')})
      for status in TaskRun.Status.values),
    *((TaskTargetRun, f'demo-target-{status}-{index}', {'task_id': _demo_uuid(f'demo-task-{status}')})
      for status in TaskRun.Status.values for index in range(2 if status == 'partial' else 1)),
)
FIXED_DOMAIN_OPERATION_OWNERS = (
    (TaskRun, 'demo-domain-task-move-ou', {
        'task_type': TaskRun.TaskType.DOMAIN_OPERATION,
    }),
    (TaskRun, 'demo-domain-task-enable', {
        'task_type': TaskRun.TaskType.DOMAIN_OPERATION,
    }),
    (TaskTargetRun, 'demo-domain-target-move-ou', {
        'task_id': _demo_uuid('demo-domain-task-move-ou'),
    }),
    (TaskTargetRun, 'demo-domain-target-enable', {
        'task_id': _demo_uuid('demo-domain-task-enable'),
    }),
    (DomainOperation, 'demo-domain-operation-move-ou', {
        'task_id': _demo_uuid('demo-domain-task-move-ou'),
    }),
    (DomainOperation, 'demo-domain-operation-enable', {
        'task_id': _demo_uuid('demo-domain-task-enable'),
    }),
)


def _assert_fixed_uuid_owner(model, identity, owner_lookup):
    expected_pk = _demo_uuid(identity)
    existing = model.objects.filter(pk=expected_pk).first()
    if existing is not None and not model.objects.filter(
        pk=expected_pk, **owner_lookup,
    ).exists():
        raise CommandError(
            f'演示数据 UUID 冲突：{model._meta.verbose_name} '
            f'{expected_pk} 已被非演示数据占用。',
        )
    return existing


def _assert_current_log_owner(source_path, content_hash):
    hash_owner = ComputerLogFile.objects.filter(content_hash=content_hash).first()
    path_conflict = ComputerLogFile.objects.filter(
        source_path=source_path,
    ).exclude(content_hash=content_hash).exists()
    if (
        hash_owner is not None
        and hash_owner.source_path != source_path
    ) or path_conflict:
        raise CommandError(
            f'演示日志身份冲突：保留路径 {source_path} '
            f'与保留摘要 {content_hash} 未指向同一条日志。',
        )
    return hash_owner


def _upsert_log(source_path, content_hash, defaults):
    instance = _assert_current_log_owner(source_path, content_hash)
    if instance is None:
        instance = ComputerLogFile(
            source_path=source_path,
            content_hash=content_hash,
        )
    for field, value in defaults.items():
        setattr(instance, field, value)
    instance.save()
    return instance


def _upsert_asset(model, lookup, defaults, identity):
    _assert_fixed_uuid_owner(model, identity, lookup)
    instance = model.objects.filter(**lookup).first()
    if instance is None:
        instance = model(id=_demo_uuid(identity), **lookup)
    for field, value in defaults.items():
        setattr(instance, field, value)
    instance.save()
    return instance


def _upsert_record(model, identity, defaults, created_at):
    owner_lookup = {
        field: defaults[field]
        for field in ('computer', 'log_file', 'device', 'server', 'monitor')
        if field in defaults
    }
    _assert_fixed_uuid_owner(model, identity, owner_lookup)
    instance, _ = model.objects.update_or_create(
        pk=_demo_uuid(identity), defaults=defaults,
    )
    model.objects.filter(pk=instance.pk).update(created_at=created_at)
    instance.created_at = created_at
    return instance


def _upsert_error(model, identity, defaults):
    _assert_fixed_uuid_owner(
        model, identity, {'inspection': defaults['inspection']},
    )
    return model.objects.update_or_create(
        pk=_demo_uuid(identity), defaults=defaults,
    )[0]


class Command(BaseCommand):
    help = '创建可重复、完全离线的巡检中心演示资产与执行记录'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='仅清理带有内置演示标识的数据，再重新创建演示数据',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self._preflight_fixed_uuid_ownership()
        if options['reset']:
            self._delete_demo_rows()

        anchor = timezone.now().replace(second=0, microsecond=0)
        people = self._seed_people(anchor)
        computers = self._seed_computers(anchor)
        networks = self._seed_networks(anchor)
        servers = self._seed_servers(anchor)
        monitors = self._seed_monitors(anchor)
        domain_accounts, domain_computers, domain_groups = self._seed_domain(anchor)
        self._seed_domain_operations(anchor, domain_accounts, domain_computers)
        self._seed_alerts(anchor, servers)
        self._seed_sources(anchor, people)
        self._seed_tasks(anchor, networks)
        from net.devices.configuration_backups import list_configuration_backups
        backup_count = sum(list_configuration_backups(device).exists() for device in networks)

        self.stdout.write(self.style.SUCCESS(
            '演示数据已就绪：'
            f'人员 {len(people)}、计算机 {len(computers)}、'
            f'网络设备 {len(networks)}、服务器 {len(servers)}、'
            f'安防设备 {len(monitors)}、域账号 {len(domain_accounts)}、'
            f'域计算机 {len(domain_computers)}、域分组 {len(domain_groups)}、'
            '目录来源 2、计划 2（停用）、'
            f'任务 10（含域操作 2）、分析 4、告警事件 2、投递结果 3、可下载配置 {backup_count}。',
        ))

    def _preflight_fixed_uuid_ownership(self):
        for source_path, content_hash in CURRENT_LOG_IDENTITIES:
            _assert_current_log_owner(source_path, content_hash)
        for model, identity, owner_lookup in (
            *FIXED_ASSET_OWNERS,
            *FIXED_RECORD_OWNERS,
            *FIXED_ERROR_OWNERS,
            *FIXED_ALERT_OWNERS,
            *FIXED_PHASE4_OWNERS,
            *FIXED_DOMAIN_OPERATION_OWNERS,
        ):
            _assert_fixed_uuid_owner(model, identity, owner_lookup)

    def _delete_demo_rows(self):
        self._delete_demo_task_rows()
        self._delete_demo_alert_rows()
        for model, prefix, indexes in CURRENT_ERROR_IDENTITIES:
            model.objects.filter(pk__in=[
                _demo_uuid(f'{prefix}-{index}') for index in indexes
            ]).delete()

        for model, prefix, indexes in CURRENT_RECORD_IDENTITIES:
            for record in model.objects.filter(pk__in=[
                _demo_uuid(f'{prefix}-{index}') for index in indexes
            ]):
                if not record.errors.exists():
                    record.delete()

        self._delete_legacy_infrastructure_rows()
        self._delete_legacy_computer_rows()

        for source_path, content_hash in (
            *CURRENT_LOG_IDENTITIES,
            *LEGACY_LOG_IDENTITIES,
        ):
            log_file = ComputerLogFile.objects.filter(
                source_path=source_path,
                content_hash=content_hash,
            ).first()
            if log_file is not None and not log_file.analyses.exists():
                log_file.delete()

        for spec in LEGACY_COMPUTER_SPECS:
            computer = Computer.objects.filter(**spec).first()
            if computer is not None and not computer.analyses.exists():
                computer.delete()

    def _delete_demo_task_rows(self):
        task_ids = [
            *(_demo_uuid(f'demo-task-{status}') for status in TaskRun.Status.values),
            _demo_uuid('demo-domain-task-move-ou'),
            _demo_uuid('demo-domain-task-enable'),
        ]
        target_ids = [
            *(_demo_uuid(identity) for model, identity, _ in FIXED_PHASE4_OWNERS
              if model is TaskTargetRun),
            _demo_uuid('demo-domain-target-move-ou'),
            _demo_uuid('demo-domain-target-enable'),
        ]
        if (TaskTargetRun.objects.filter(task_id__in=task_ids).exclude(pk__in=target_ids).exists()
                or AlertEvent.objects.filter(task_id__in=task_ids).exists()):
            raise CommandError('演示任务仍被非演示目标或告警引用，无法重置。')
        TaskTargetRun.objects.filter(pk__in=target_ids).delete()
        DomainOperation.objects.filter(task_id__in=task_ids).delete()
        TaskRun.objects.filter(pk__in=task_ids).delete()
        # Sources/profiles/schedules are upserted, not cascaded: user references
        # must retain their identities. Existing fixed-UUID checks apply first.

    def _seed_sources(self, anchor, people):
        for provider in ('feishu', 'dingtalk'):
            credential_key = 'app_id' if provider == 'feishu' else 'app_key'
            source, _ = PeopleSyncSource.objects.update_or_create(
                pk=_demo_uuid(f'people-source-{provider}'), defaults={
                    'name': '飞书' if provider == 'feishu' else '钉钉',
                    'source_key': f'people-provider-{provider}',
                    'source_type': provider, 'is_enabled': False,
                    'credentials': {credential_key: DEMO_SECRET, 'app_secret': DEMO_SECRET},
                    'root_department_ids': ['0' if provider == 'feishu' else '1'],
                    'last_tested_at': anchor, 'last_synced_at': anchor,
                },
            )
            for person in people:
                if person.source == provider:
                    People.objects.filter(pk=person.pk).update(sync_source=source)

    def _seed_tasks(self, anchor, networks):
        from django.conf import settings
        PCLogSourceConfig.objects.get_or_create(pk=1, defaults={
            'source_type': 'smb', 'host': 'files.example.invalid', 'port': 445,
            'username': 'demo-reader', 'share_name': 'logs',
            'remote_incoming_directory': 'incoming', 'file_time_mode': 'recent_days',
            'local_staging_directory': str(settings.BASE_DIR / 'runtime' / 'pc-staging'),
            'terminal_windows_path': r'\\files.example.invalid\logs\incoming',
            'terminal_macos_path': '/Volumes/Logs/incoming',
        })
        profiles = {}
        for suffix, name, device_type, items in (
            ('network', '演示网络巡检', 'network_device', ['device_info', 'config_info']),
            ('monitor', '演示安防巡检', 'monitor', ['device_info', 'config_info']),
        ):
            profiles[suffix], _ = InspectionProfile.objects.update_or_create(
                pk=_demo_uuid(f'demo-profile-{suffix}'), defaults={
                    'name': name, 'device_type': device_type, 'selected_items': items,
                    'timeout_seconds': 60, 'concurrent_workers': 4, 'is_enabled': True,
                },
            )
        analysis, _ = ComputerAnalysisProfile.objects.update_or_create(
            pk=_demo_uuid('demo-profile-analysis'), defaults={
                'name': '演示日志分析', 'analysis_items': ['resource', 'event_findings'],
                'concurrent_workers': 4, 'is_enabled': True,
            },
        )
        schedule, _ = Schedule.objects.update_or_create(
            pk=_demo_uuid('demo-schedule-interval'), defaults={
                'inspection_profile': profiles['network'], 'kind': 'interval',
                'interval_value': 30, 'interval_unit': 'minutes', 'is_enabled': False,
                'analysis_profile': None, 'daily_time': None, 'next_run_at': None,
            },
        )
        Schedule.objects.update_or_create(pk=_demo_uuid('demo-schedule-daily'), defaults={
            'analysis_profile': analysis, 'kind': 'daily', 'daily_time': time(9), 'is_enabled': False,
            'inspection_profile': None, 'interval_value': None, 'interval_unit': '', 'next_run_at': None,
        })
        profile = profiles['network']
        for number, status in enumerate(TaskRun.Status.values):
            selected = [networks[number % 3]]
            if status == 'partial':
                selected.append(networks[(number + 1) % 3])
            scope = {'target_type': 'network_device', 'target_ids': [str(asset.pk) for asset in selected]}
            terminal = status in TaskRun.TERMINAL_STATUSES
            finished = anchor - timedelta(minutes=60 + number)
            succeeded = 1 if status in ('success', 'partial') else 0
            failed = 1 if status in ('partial', 'failed') else 0
            task, created = TaskRun.objects.get_or_create(pk=_demo_uuid(f'demo-task-{status}'), defaults={
                'task_type': 'inspection', 'source': 'scheduled' if terminal else 'manual',
                'inspection_profile': profile, 'schedule': schedule if terminal else None,
                'status': status, 'profile_snapshot': {
                    'id': str(profile.pk), 'name': profile.name, 'device_type': 'network_device',
                    'selected_items': ['device_info', 'config_info'],
                },
                'selected_items_snapshot': ['device_info', 'config_info'],
                'parameters_snapshot': {'demo_only': True}, 'target_scope_snapshot': scope,
                'total_targets': len(selected), 'completed_targets': len(selected) if terminal else 0,
                'successful_targets': succeeded, 'failed_targets': failed,
                'progress': 100 if terminal else 0,
                'available_at': anchor + timedelta(days=36500),
                'started_at': finished - timedelta(seconds=10) if status != 'queued' else None,
                'finished_at': finished if terminal else None,
                'worker_id': 'DEMO-ONLY-NOT-A-WORKER' if status == 'running' else '',
                'lease_expires_at': anchor + timedelta(days=36500) if status == 'running' else None,
                'error_summary': '离线演示任务，不启动真实采集。' if status in ('failed', 'partial') else '',
            })
            if not created:
                continue  # Never rewrite immutable snapshots on repeated seed.
            TaskRun.objects.filter(pk=task.pk).update(created_at=finished - timedelta(seconds=12))
            for index, asset in enumerate(selected):
                target_status = ('success' if index == 0 else 'failed') if status == 'partial' else status
                record = (asset.inspections.filter(status=RecordStatus.SUCCESS).order_by('-created_at').first()
                          if target_status == 'success' else None)
                TaskTargetRun.objects.create(
                    pk=_demo_uuid(f'demo-target-{status}-{index}'), task=task,
                    target_type='network_device', target_id=str(asset.pk),
                    target_snapshot={'device_name': asset.device_name, 'ip': asset.ip, 'vendor': asset.vendor},
                    status=target_status, started_at=task.started_at, finished_at=task.finished_at,
                    result_type='network_device_inspection' if record else '',
                    result_id=str(record.pk) if record else '',
                    error_message='演示采集失败（无设备调用）。' if target_status == 'failed' else '',
                    alert_processed_at=finished if terminal else None,
                )

    def _delete_demo_alert_rows(self):
        policy_ids = [_demo_uuid('alert-policy-default'), _demo_uuid('alert-policy-server')]
        channel_ids = [
            _demo_uuid('alert-channel-feishu'), _demo_uuid('alert-channel-dingtalk'),
            _demo_uuid('alert-channel-email'),
        ]
        if AlertPolicy.objects.filter(channels__pk__in=channel_ids).exclude(pk__in=policy_ids).exists():
            raise CommandError('演示告警渠道仍被非演示策略引用，无法重置。')
        if TaskRun.objects.filter(inspection_profile_id=_demo_uuid('alert-profile-server')).exclude(
            pk__in=[_demo_uuid('alert-task-1'), _demo_uuid('alert-task-2')],
        ).exists():
            raise CommandError('演示告警配置仍被非演示任务引用，无法重置。')
        AlertDelivery.objects.filter(pk__in=[
            _demo_uuid('alert-delivery-abnormal-sent'), _demo_uuid('alert-delivery-abnormal-failed'),
            _demo_uuid('alert-delivery-recovery-sent'),
        ]).delete()
        AlertEvent.objects.filter(pk__in=[_demo_uuid('alert-event-abnormal'), _demo_uuid('alert-event-recovery')]).delete()
        TaskTargetRun.objects.filter(pk__in=[_demo_uuid('alert-target-1'), _demo_uuid('alert-target-2')]).delete()
        TaskRun.objects.filter(pk__in=[_demo_uuid('alert-task-1'), _demo_uuid('alert-task-2')]).delete()
        AlertPolicy.objects.filter(pk__in=policy_ids).delete()
        InspectionProfile.objects.filter(pk=_demo_uuid('alert-profile-server')).delete()
        AlertChannel.objects.filter(pk__in=channel_ids).delete()

    def _seed_alerts(self, anchor, servers):
        """Build fixed offline examples directly; this path never calls a sender."""
        specs = (
            ('alert-channel-feishu', '演示告警渠道-飞书', 'feishu', {'webhook_url': 'https://demo.invalid/feishu'}),
            ('alert-channel-dingtalk', '演示告警渠道-钉钉', 'dingtalk', {'webhook_url': 'https://demo.invalid/dingtalk'}),
            ('alert-channel-email', '演示告警渠道-邮件', 'email', {
                'smtp_host': 'smtp.demo.invalid', 'smtp_port': 25, 'use_tls': False,
                'use_ssl': False, 'from_email': 'alerts@demo.invalid', 'recipients': ['ops@demo.invalid'],
            }),
        )
        channels = {}
        for identity, name, channel_type, settings in specs:
            _assert_fixed_uuid_owner(AlertChannel, identity, {'name': name})
            channels[identity], _ = AlertChannel.objects.update_or_create(
                pk=_demo_uuid(identity), defaults={
                    'name': name, 'channel_type': channel_type, 'settings': settings, 'is_enabled': False,
                },
            )
        profile_id = _demo_uuid('alert-profile-server')
        _assert_fixed_uuid_owner(InspectionProfile, 'alert-profile-server', {'name': '演示告警巡检配置'})
        profile, _ = InspectionProfile.objects.update_or_create(
            pk=profile_id, defaults={
                'name': '演示告警巡检配置', 'device_type': InspectionProfile.DeviceType.SERVER,
                'selected_items': ['cpu'], 'is_enabled': True,
            },
        )
        _assert_fixed_uuid_owner(AlertPolicy, 'alert-policy-default', {'default_slot': AlertPolicy.DEFAULT_SLOT})
        default_policy = AlertPolicy.objects.filter(default_slot=AlertPolicy.DEFAULT_SLOT).first()
        if default_policy is None or default_policy.pk == _demo_uuid('alert-policy-default'):
            default_policy, _ = AlertPolicy.objects.update_or_create(
                pk=_demo_uuid('alert-policy-default'), defaults={
                    'name': '演示默认告警策略', 'is_default': True, 'default_slot': AlertPolicy.DEFAULT_SLOT,
                    'mode': AlertPolicy.Mode.OVERRIDE,
                },
            )
            default_policy.channels.set([channels['alert-channel-feishu'], channels['alert-channel-dingtalk']])
        _assert_fixed_uuid_owner(AlertPolicy, 'alert-policy-server', {'inspection_profile_id': profile_id})
        policy, _ = AlertPolicy.objects.update_or_create(
            pk=_demo_uuid('alert-policy-server'), defaults={
                'name': '演示服务器覆盖策略', 'mode': AlertPolicy.Mode.OVERRIDE,
                'inspection_profile': profile,
            },
        )
        policy.channels.set([channels['alert-channel-dingtalk'], channels['alert-channel-email']])
        server = next(item for item in servers if item.ip == '198.51.100.21')
        targets = []
        for index in (1, 2):
            identity, target_identity = f'alert-task-{index}', f'alert-target-{index}'
            finished_at = anchor - timedelta(minutes=30 - index)
            scope = {'target_type': TaskTargetRun.TargetType.SERVER, 'target_ids': [str(server.pk)]}
            snapshot = {'id': str(profile.pk), 'name': profile.name, 'device_type': profile.device_type, 'selected_items': ['cpu']}
            _assert_fixed_uuid_owner(TaskRun, identity, {'inspection_profile_id': profile_id})
            task, _ = TaskRun.objects.update_or_create(
                pk=_demo_uuid(identity), defaults={
                    'task_type': TaskRun.TaskType.INSPECTION, 'source': TaskRun.Source.MANUAL,
                    'inspection_profile': profile, 'status': TaskRun.Status.SUCCESS, 'progress': 100,
                    'available_at': finished_at - timedelta(minutes=1), 'started_at': finished_at - timedelta(minutes=1),
                    'finished_at': finished_at, 'profile_snapshot': snapshot, 'parameters_snapshot': {},
                    'selected_items_snapshot': ['cpu'], 'target_scope_snapshot': scope,
                    'scope_key': TaskRun.build_scope_key(task_type=TaskRun.TaskType.INSPECTION, profile_id=profile.pk, target_scope_snapshot=scope),
                    'total_targets': 1, 'completed_targets': 1, 'successful_targets': 1, 'failed_targets': 0,
                },
            )
            _assert_fixed_uuid_owner(TaskTargetRun, target_identity, {'task_id': task.pk})
            target, _ = TaskTargetRun.objects.update_or_create(
                pk=_demo_uuid(target_identity), defaults={
                    'task': task, 'target_type': TaskTargetRun.TargetType.SERVER, 'target_id': str(server.pk),
                    'target_snapshot': {'name': server.name, 'ip': server.ip}, 'status': TaskRun.Status.SUCCESS,
                    'started_at': finished_at - timedelta(minutes=1), 'finished_at': finished_at,
                    'result_snapshot': {}, 'alert_processed_at': finished_at, 'alert_attempted_at': finished_at,
                },
            )
            targets.append((task, target, finished_at))
        event_specs = (
            ('alert-event-abnormal', targets[0], 'abnormal', 'partial', [{'key': 'demo.cpu', 'severity': 'critical', 'title': '演示 CPU 异常', 'detail': '演示告警：CPU 使用率超过阈值。'}]),
            ('alert-event-recovery', targets[1], 'recovery', 'delivered', [{'key': 'demo.cpu', 'severity': 'info', 'title': '演示 CPU 已恢复', 'detail': '演示告警：CPU 使用率恢复正常。'}]),
        )
        events = {}
        for identity, (task, target, occurred_at), event_type, status, findings in event_specs:
            _assert_fixed_uuid_owner(AlertEvent, identity, {'target_run_id': target.pk, 'event_type': event_type})
            events[identity], _ = AlertEvent.objects.update_or_create(
                pk=_demo_uuid(identity), defaults={
                    'task': task, 'target_run': target, 'policy': policy,
                    'profile_type': 'inspection_profile', 'profile_id': str(profile.pk),
                    'target_type': TaskTargetRun.TargetType.SERVER, 'target_id': str(server.pk),
                    'event_type': event_type, 'status': status, 'severity': findings[0]['severity'],
                    'summary': '演示告警记录', 'findings': findings, 'occurred_at': occurred_at,
                },
            )
        for identity, event_id, channel_id, status in (
            ('alert-delivery-abnormal-sent', 'alert-event-abnormal', 'alert-channel-feishu', 'sent'),
            ('alert-delivery-abnormal-failed', 'alert-event-abnormal', 'alert-channel-dingtalk', 'failed'),
            ('alert-delivery-recovery-sent', 'alert-event-recovery', 'alert-channel-email', 'sent'),
        ):
            event, channel = events[event_id], channels[channel_id]
            _assert_fixed_uuid_owner(AlertDelivery, identity, {'event_id': event.pk, 'channel_id': channel.pk})
            AlertDelivery.objects.update_or_create(
                pk=_demo_uuid(identity), defaults={
                    'event': event, 'channel': channel, 'status': status, 'attempt_count': 1,
                    'max_attempts': 3, 'attempted_at': event.occurred_at,
                    'delivered_at': event.occurred_at if status == 'sent' else None,
                    'response_summary': '演示渠道结果（无外发）。',
                    'error_summary': '演示发送失败。' if status == 'failed' else '',
                },
            )

    def _delete_legacy_computer_rows(self):
        for spec in LEGACY_ANALYSIS_SPECS:
            analyses = ComputerAnalysis.objects.filter(
                computer__computer_name=spec['computer_name'],
                log_file__source_path=spec['source_path'],
                log_file__content_hash=spec['content_hash'],
                status=spec['status'],
                started_at__isnull=True,
                finished_at__isnull=True,
                summary=spec['summary'],
                details=spec['details'],
                analysis_items=[],
                exceptions=spec['exceptions'],
            )
            for analysis in analyses:
                if spec['error'] is not None:
                    Error_Computer.objects.filter(
                        inspection=analysis,
                        error_type=spec['error'][0],
                        error_message=spec['error'][1],
                    ).delete()
                if not analysis.errors.exists():
                    analysis.delete()

    def _delete_legacy_infrastructure_rows(self):
        for spec in LEGACY_SERVER_SPECS:
            server = Server.objects.filter(
                ip=spec['ip'], name=spec['name'],
            ).first()
            if server is None:
                continue
            records = server.inspections.filter(
                status=spec['status'],
                summary=spec['summary'],
                is_reachable=True,
                duration_ms=0,
                raw_output__isnull=True,
                details=spec['details'],
            )
            for record in records:
                Error_Server.objects.filter(
                    inspection=record,
                    error_message={'演示异常': spec['summary']},
                ).delete()
                if not record.errors.exists():
                    record.delete()
            if not server.inspections.exists():
                server.delete()

        for spec in LEGACY_MONITOR_SPECS:
            monitor = SecurityDevice.objects.filter(
                ip=spec['ip'], device_name=spec['name'],
            ).first()
            if monitor is None:
                continue
            details = {
                'device_info': {'型号': 'DEMO'},
                'channel_status': [{
                    'channel': 1, 'online': spec['reachable'],
                }],
                'storage_status': [{'status': 'normal'}],
            }
            records = monitor.inspections.filter(
                status=spec['status'],
                summary=spec['summary'],
                is_reachable=spec['reachable'],
                duration_ms=0,
                raw_output__isnull=True,
                details=details,
            )
            for record in records:
                Error_Monitor.objects.filter(
                    inspection=record,
                    error_message={'演示异常': spec['summary']},
                ).delete()
                if not record.errors.exists():
                    record.delete()
            if not monitor.inspections.exists():
                monitor.delete()

    def _seed_people(self, anchor):
        specs = (
            ('DEMO-P001', '演示-张瑾', '技术运营中心', 'manual', '', True, '演示-周主管'),
            ('DEMO-P002', '演示-李然', '网络运维部', 'csv', '', True, '演示-周主管'),
            ('DEMO-P003', '演示-王航', '安全运营部', 'feishu', 'ou_demo_feishu_003', True, '演示-孙主管'),
            ('DEMO-P004', '演示-陈晓', '网络运维部', 'dingtalk', 'demo_dingtalk_004', True, '演示-周主管'),
            ('DEMO-P005', '演示-赵离', '行政部', 'csv', '', False, '演示-钱主管'),
            ('DEMO-P006', '演示-飞书离职', '历史人员', 'feishu', 'ou_demo_feishu_006', False, ''),
            ('DEMO-P007', '演示-钉钉离职', '历史人员', 'dingtalk', 'demo_dingtalk_007', False, ''),
        )
        people = []
        for employee_id, name, department, source, platform_id, active, leader in specs:
            people.append(_upsert_asset(
                People,
                {'employee_id': employee_id},
                {
                    'name': name,
                    'email': f'{employee_id.lower()}@demo.invalid',
                    'department': department,
                    'leader': leader,
                    'is_active': active,
                    'source': source,
                    'platform_user_id': platform_id,
                    'last_synced_at': anchor if platform_id else None,
                    'hire_date': date(2020 + (int(employee_id[-1]) % 4), 3, 1 + int(employee_id[-1])),
                    'departure_date': date(2026, 5, 31) if not active else None,
                },
                f'people:{employee_id}',
            ))
        return people

    def _seed_computers(self, anchor):
        specs = (
            {
                'computer_name': 'DEMO-PC-OPS-01',
                'os': 'Windows 11 专业版',
                'user_name': '演示-张瑾',
                'login_account': 'DEMO\\zhang.jin',
                'ip_addresses': '192.0.2.101; 2001:db8::101',
                'mac_addresses': '02-00-00-00-01-01',
                'os_version': '23H2',
                'os_build': '22631.4037',
                'system_installed_at': '2026-01-15 10:00:00',
                'last_report_at': anchor - timedelta(minutes=8),
                'is_active': True,
                'manufacturer': 'Dell', 'model': 'Latitude 7450', 'serial_number': 'DEMO-OPS-001',
                'architecture': 'x64', 'cpu_model': 'Intel Core Ultra 7 165U',
                'cpu_physical_core_count': 12, 'cpu_logical_processor_count': 14,
                'memory_total_gb': 32, 'disk_total_gb': 1024,
            },
            {
                'computer_name': 'DEMO-PC-DEV-02',
                'os': 'Windows 10 企业版',
                'user_name': '演示-李然',
                'login_account': 'DEMO\\li.ran',
                'ip_addresses': '192.0.2.102',
                'mac_addresses': '02-00-00-00-01-02',
                'os_version': '22H2',
                'os_build': '19045.4780',
                'system_installed_at': '2025-11-03 09:20:00',
                'last_report_at': anchor - timedelta(minutes=18),
                'is_active': True,
                'manufacturer': 'Lenovo', 'model': 'ThinkPad T14 Gen 5', 'serial_number': 'DEMO-DEV-002',
                'architecture': 'x64', 'cpu_model': 'AMD Ryzen 7 PRO 8840U',
                'cpu_physical_core_count': 8, 'cpu_logical_processor_count': 16,
                'memory_total_gb': 16, 'disk_total_gb': 512,
            },
            {
                'computer_name': 'DEMO-PC-OFFLINE',
                'os': 'Windows 10 专业版',
                'user_name': '演示-赵离',
                'login_account': 'DEMO\\zhao.li',
                'ip_addresses': '192.0.2.103',
                'mac_addresses': '02-00-00-00-01-03',
                'os_version': '21H2',
                'os_build': '19044.3208',
                'system_installed_at': '2024-06-20 14:00:00',
                'last_report_at': anchor - timedelta(days=3),
                'is_active': False,
                'manufacturer': 'HP', 'model': 'EliteBook 840 G8', 'serial_number': 'DEMO-OFF-003',
                'architecture': 'x64', 'cpu_model': 'Intel Core i5-1135G7',
                'cpu_physical_core_count': 4, 'cpu_logical_processor_count': 8,
                'memory_total_gb': 16, 'disk_total_gb': 512,
            },
        )
        computers = {
            spec['computer_name']: _upsert_asset(
                Computer,
                {'computer_name': spec['computer_name']},
                {key: value for key, value in spec.items() if key != 'computer_name'},
                f"computer:{spec['computer_name']}",
            )
            for spec in specs
        }

        analysis_specs = (
            ('DEMO-PC-OPS-01', 1, 75, RecordStatus.SUCCESS, '分析正常', 18, 43, None),
            ('DEMO-PC-DEV-02', 2, 130, RecordStatus.SUCCESS, '分析正常', 31, 57, None),
            ('DEMO-PC-DEV-02', 3, 28, RecordStatus.FAILED, '发现异常：系统盘空间不足', 72, 81, ('磁盘空间不足', '系统盘剩余空间低于 10%。')),
            ('DEMO-PC-OFFLINE', 4, 12, RecordStatus.PARTIAL, '发现异常：日志超过预期上报时间', 12, 39, ('日志上报延迟', '最近一次日志文件超过 72 小时。')),
        )
        for computer_name, index, minutes_ago, status, summary, cpu, memory, error in analysis_specs:
            computer = computers[computer_name]
            event_time = anchor - timedelta(minutes=minutes_ago)
            log_file = _upsert_log(
                f'demo://powershell/{computer_name}-{index}.json',
                _demo_hash(f'computer-log-{index}'),
                {
                    'modified_at': event_time - timedelta(minutes=1),
                    'computer': computer,
                    'collected_date': timezone.localdate(event_time - timedelta(days=index)),
                    'platform': 'windows',
                    'source_protocol': 'smb',
                    'remote_source_path': f'incoming/{computer_name}-{index}.json',
                    'import_status': 'imported',
                    'archived_path': f'demo://archive/{computer_name}-{index}.json',
                    'parse_error': '',
                    'payload': {
                        '日志时间': (event_time - timedelta(days=index)).isoformat(),
                        '系统信息概览': {
                            '计算机名': computer_name,
                            '操作系统': computer.os,
                            'IP地址': computer.ip_addresses,
                            'MAC地址': computer.mac_addresses,
                        },
                        '计算机硬件资源情况': {
                            '当前CPU占用率': f'{cpu}%',
                            '当前内存使用率': f'{memory}%',
                        },
                        '事件发现': [],
                    },
                },
            )
            exceptions = []
            if error:
                exceptions.append({'问题类型': error[0], '详细问题': error[1]})
            analysis = _upsert_record(
                ComputerAnalysis,
                f'computer-analysis-{index}',
                {
                    'computer': computer,
                    'log_file': log_file,
                    'status': status,
                    'started_at': event_time - timedelta(seconds=18),
                    'finished_at': event_time,
                    'summary': summary,
                    'details': {
                        'system_info': {'操作系统': computer.os, '构建号': computer.os_build},
                        'computer_info': {'CPU使用率': cpu, '内存使用率': memory},
                        'storage_status': [{
                            'drive': 'C:',
                            'usage_percent': 93 if error and error[0] == '磁盘空间不足' else 48,
                        }],
                    },
                    'analysis_items': ['系统信息', 'CPU', '内存', '磁盘', '日志时效'],
                    'exceptions': exceptions,
                },
                event_time,
            )
            if error:
                _upsert_error(
                    Error_Computer,
                    f'computer-error-{index}',
                    {
                        'inspection': analysis,
                        'error_type': error[0],
                        'error_message': f'演示数据：{error[1]}',
                    },
                )
        return list(computers.values())

    def _seed_networks(self, anchor):
        specs = (
            {
                'ip': '192.0.2.11', 'name': '演示-核心交换机',
                'device_type': '交换机', 'vendor': 'Huawei',
                'model': 'CloudEngine S5735-L', 'port': 22,
                'connection_type': 'ssh',
            },
            {
                'ip': '192.0.2.12', 'name': '演示-接入交换机',
                'device_type': '交换机', 'vendor': 'H3C', 'model': 'S5130S',
                'port': 22, 'connection_type': 'hybrid',
                'snmp_version': 'v2c', 'snmp_community': DEMO_SECRET,
            },
            {
                'ip': '192.0.2.13', 'name': '演示-边界路由器',
                'device_type': '路由器', 'vendor': 'Cisco', 'model': 'ISR 4331',
                'port': 2222, 'connection_type': 'auto', 'snmp_version': 'v3',
                'snmp_username': 'demo-snmp-reader',
                'snmp_security_level': 'authPriv',
                'snmp_auth_protocol': 'sha256',
                'snmp_auth_password': DEMO_SECRET,
                'snmp_priv_protocol': 'aes128',
                'snmp_priv_password': DEMO_SECRET,
                'snmp_context_name': 'demo-context',
            },
        )
        devices = {}
        for spec in specs:
            ip = spec['ip']
            devices[ip] = _upsert_asset(
                Network_Device,
                {'ip': ip},
                {
                    'device_name': spec['name'],
                    'device_type': spec['device_type'],
                    'model': spec['model'],
                    'vendor': spec['vendor'],
                    'connection_type': spec['connection_type'],
                    'port': spec['port'],
                    'username': 'demo-inspector',
                    'password': DEMO_SECRET,
                    'snmp_version': spec.get('snmp_version', 'v2c'),
                    'snmp_port': 161,
                    'snmp_community': spec.get('snmp_community', ''),
                    'snmp_security_level': spec.get(
                        'snmp_security_level', 'noAuthNoPriv',
                    ),
                    'snmp_username': spec.get('snmp_username', ''),
                    'snmp_auth_protocol': spec.get('snmp_auth_protocol', ''),
                    'snmp_auth_password': spec.get('snmp_auth_password', ''),
                    'snmp_priv_protocol': spec.get('snmp_priv_protocol', ''),
                    'snmp_priv_password': spec.get('snmp_priv_password', ''),
                    'snmp_context_name': spec.get('snmp_context_name', ''),
                    'snmp_retries': 1,
                    'cpu_model': 'ARM Cortex-A72',
                    'memory_total_gb': 8,
                    'disk_total_gb': 64,
                    'port_count': 48 if ip == '192.0.2.11' else 24,
                    'vlan_count': 12 if ip != '192.0.2.13' else 4,
                },
                f'network:{ip}',
            )
        records = (
            ('192.0.2.11', 1, 180, RecordStatus.SUCCESS, True, '巡检正常', None),
            ('192.0.2.11', 2, 22, RecordStatus.PARTIAL, True, '发现 2 个接入端口未连接', '接口状态异常'),
            ('192.0.2.12', 3, 42, RecordStatus.SUCCESS, True, '巡检正常', None),
            ('192.0.2.13', 4, 15, RecordStatus.FAILED, False, '设备不可达（演示）', '设备不可达'),
            ('192.0.2.13', 5, 200, RecordStatus.SUCCESS, True, '已保存 Cisco 运行配置（离线演示）', None),
        )
        for ip, index, minutes_ago, status, reachable, summary, error_type in records:
            event_time = anchor - timedelta(minutes=minutes_ago)
            inspection = _upsert_record(
                Network_Device_Inspection,
                f'network-inspection-{index}',
                {
                    'device': devices[ip],
                    'status': status,
                    'started_at': event_time - timedelta(seconds=8),
                    'finished_at': event_time,
                    'summary': summary,
                    'details': {
                        'config_info': ({
                            'status': 'success', 'vendor': 'h3c' if index == 3 else 'cisco',
                            'scope': 'current-configuration' if index == 3 else 'running-config',
                            'format': 'text', 'complete': True, 'full_backup': False,
                            'content': 'sysname DEMO-H3C\nreturn\n' if index == 3 else 'hostname DEMO-CISCO\nend\n',
                        } if index in (3, 5) else {
                            'status': 'failed' if index == 4 else 'unsupported',
                            'message': '离线演示：未获取配置或此厂商不支持配置导出。',
                        }),
                        'cpu': {'usage_percent': 24 + index},
                        'memory': {'usage_percent': 41 + index},
                        'interface_status': {'up': 22, 'down': 2 if error_type else 0},
                    },
                    'is_reachable': reachable,
                    'duration_ms': 800 + index * 110,
                    'raw_output': {'demo': True, 'command': 'display device; display interface brief'},
                },
                event_time,
            )
            if index in (3, 5) and settings.DEVICE_BACKUP_ENCRYPTION_KEY:
                from net.devices.configuration_backups import store_configuration_backup
                store_configuration_backup(
                    devices[ip], inspection.details['config_info'], captured_at=event_time,
                )
            if error_type:
                _upsert_error(
                    Error_Network_Device,
                    f'network-error-{index}',
                    {
                        'inspection': inspection,
                        'error_message': {error_type: summary},
                    },
                )
        return list(devices.values())

    def _seed_servers(self, anchor):
        specs = (
            {
                'ip': '198.51.100.21', 'name': '演示-Linux应用服务器',
                'server_type': 'linux', 'os': 'Ubuntu Server 24.04 LTS',
                'port': 22, 'username': 'demo-inspector', 'password': DEMO_SECRET,
                'api_url': '', 'api_token': '', 'verify_ssl': True,
                'os_version': '24.04', 'os_build': '6.8.0', 'system_installed_at': '2025-05-10',
                'manufacturer': 'Dell', 'model': 'PowerEdge R7525', 'serial_number': 'DEMO-SRV-001',
                'architecture': 'x86_64', 'cpu_model': 'AMD EPYC 7313P',
                'cpu_physical_core_count': 16, 'cpu_logical_processor_count': 32,
                'memory_total_gb': 128, 'disk_total_gb': 2048,
            },
            {
                'ip': '198.51.100.22', 'name': '演示-Windows文件服务器',
                'server_type': 'windows', 'os': 'Windows Server 2022',
                'port': 9443, 'username': '', 'password': '',
                'api_url': 'https://windows-server.demo.invalid/api/v1/health',
                'api_token': DEMO_SECRET, 'verify_ssl': True,
                'os_version': '21H2', 'os_build': '20348', 'system_installed_at': '2024-11-18',
                'manufacturer': 'HPE', 'model': 'ProLiant DL380 Gen10', 'serial_number': 'DEMO-SRV-002',
                'architecture': 'x86_64', 'cpu_model': 'Intel Xeon Silver 4310',
                'cpu_physical_core_count': 24, 'cpu_logical_processor_count': 48,
                'memory_total_gb': 64, 'disk_total_gb': 4096,
            },
            {
                'ip': '198.51.100.23', 'name': '演示-数据库服务器',
                'server_type': 'linux', 'os': 'Rocky Linux 9.4',
                'port': 22, 'username': 'demo-inspector', 'password': DEMO_SECRET,
                'api_url': '', 'api_token': '', 'verify_ssl': True,
                'os_version': '9.4', 'os_build': '5.14.0', 'system_installed_at': '2025-02-08',
                'manufacturer': 'Lenovo', 'model': 'ThinkSystem SR650', 'serial_number': 'DEMO-SRV-003',
                'architecture': 'x86_64', 'cpu_model': 'AMD EPYC 7232P',
                'cpu_physical_core_count': 8, 'cpu_logical_processor_count': 16,
                'memory_total_gb': 64, 'disk_total_gb': 8192,
            },
        )
        servers = {
            spec['ip']: _upsert_asset(
                Server,
                {'ip': spec['ip']},
                {key: value for key, value in spec.items() if key != 'ip'},
                f"server:{spec['ip']}",
            )
            for spec in specs
        }
        records = (
            ('198.51.100.21', 1, 165, RecordStatus.SUCCESS, True, '巡检正常', None),
            ('198.51.100.21', 2, 32, RecordStatus.SUCCESS, True, '巡检正常', None),
            ('198.51.100.22', 3, 19, RecordStatus.PARTIAL, True, 'Windows 巡检 API 返回部分指标', '采集不完整'),
            ('198.51.100.23', 4, 9, RecordStatus.FAILED, True, '数据库服务器磁盘空间不足', '磁盘空间不足'),
        )
        for ip, index, minutes_ago, status, reachable, summary, error_type in records:
            event_time = anchor - timedelta(minutes=minutes_ago)
            inspection = _upsert_record(
                Server_Inspection,
                f'server-inspection-{index}',
                {
                    'server': servers[ip],
                    'status': status,
                    'started_at': event_time - timedelta(seconds=15),
                    'finished_at': event_time,
                    'summary': summary,
                    'details': {
                        'cpu': {'usage_percent': 28 + index * 4},
                        'memory': {'usage_percent': 46 + index * 5},
                        'storage_status': [{
                            'mount': 'C:' if servers[ip].server_type == 'windows' else '/',
                            'usage_percent': 94 if error_type == '磁盘空间不足' else 58,
                        }],
                        'service_status': {'total': 18, 'stopped': 1 if error_type else 0},
                    },
                    'is_reachable': reachable,
                    'duration_ms': 1100 + index * 170,
                    'raw_output': {
                        'demo': True,
                        'transport': 'http' if servers[ip].server_type == 'windows' else 'ssh',
                    },
                },
                event_time,
            )
            if error_type:
                _upsert_error(
                    Error_Server,
                    f'server-error-{index}',
                    {
                        'inspection': inspection,
                        'error_message': {error_type: summary},
                    },
                )
        return list(servers.values())

    def _seed_monitors(self, anchor):
        specs = (
            ('203.0.113.31', '演示-大厅摄像机', '摄像机', 'Hikvision', 'DS-2CD3T47EWD-L', 'camera-lobby'),
            ('203.0.113.32', '演示-NVR录像机', 'NVR', 'Dahua', 'NVR5216-4KS2', 'nvr-main'),
            ('203.0.113.33', '演示-仓库摄像机', '摄像机', 'Hikvision', 'DS-2CD2T46WDV3', 'camera-warehouse'),
            ('203.0.113.34', '演示-办公区门禁', '门禁', 'Hikvision', 'DS-K2604', 'access-office'),
            ('203.0.113.35', '演示-园区入口闸机', '闸机', 'Dahua', 'ASGB8XXY', 'gate-entrance'),
        )
        monitors = {}
        for ip, name, device_type, vendor, model, host in specs:
            monitors[ip] = _upsert_asset(
                SecurityDevice,
                {'ip': ip},
                {
                    'device_name': name,
                    'device_type': device_type,
                    'model': model,
                    'vendor': vendor,
                    'api_url': f'https://{host}.demo.invalid/api/v1/health',
                    'api_username': 'demo-api-reader',
                    'api_password': DEMO_SECRET,
                    'api_token': DEMO_SECRET,
                    'verify_ssl': True,
                    'cpu_model': 'Ambarella CV25',
                    'memory_total_gb': 4 if device_type == '摄像机' else 16 if device_type == 'NVR' else 2,
                    'disk_total_gb': 64 if device_type == '摄像机' else 4096 if device_type == 'NVR' else 16,
                },
                f'monitor:{ip}',
            )
        records = (
            ('203.0.113.31', 1, 155, RecordStatus.SUCCESS, True, '巡检正常', None),
            ('203.0.113.31', 2, 25, RecordStatus.SUCCESS, True, '巡检正常', None),
            ('203.0.113.32', 3, 16, RecordStatus.PARTIAL, True, '录像保留天数低于策略要求', '存储策略异常'),
            ('203.0.113.33', 4, 7, RecordStatus.FAILED, False, '摄像机离线（演示）', '设备离线'),
        )
        for ip, index, minutes_ago, status, reachable, summary, error_type in records:
            event_time = anchor - timedelta(minutes=minutes_ago)
            inspection = _upsert_record(
                Monitor_Inspection,
                f'monitor-inspection-{index}',
                {
                    'monitor': monitors[ip],
                    'status': status,
                    'started_at': event_time - timedelta(seconds=11),
                    'finished_at': event_time,
                    'summary': summary,
                    'details': {
                        **({'config_info': {
                            'status': 'success', 'vendor': 'dahua', 'scope': 'Network',
                            'format': 'json', 'complete': True, 'full_backup': False,
                            'content': {'table.Network.Hostname': 'DEMO-NVR', 'table.Network.eth0.DhcpEnable': True},
                        }} if index == 3 else {'config_info': {
                            'status': 'unsupported', 'message': '演示：不支持该厂商的配置导出。',
                        }} if index in (1, 2) else {}),
                        'device_info': {'型号': monitors[ip].model, '厂商': monitors[ip].vendor},
                        'channel_status': [{'channel': 1, 'online': reachable}],
                        'storage_status': [{
                            'status': 'warning' if error_type else 'normal',
                            'retention_days': 12 if error_type else 30,
                        }],
                    },
                    'is_reachable': reachable,
                    'duration_ms': 930 + index * 120,
                    'raw_output': {'demo': True, 'provider_api': monitors[ip].vendor},
                },
                event_time,
            )
            if error_type:
                _upsert_error(
                    Error_Monitor,
                    f'monitor-error-{index}',
                    {
                        'inspection': inspection,
                        'error_message': {error_type: summary},
                    },
                )
        return list(monitors.values())

    def _seed_domain(self, anchor):
        config = Domain_Controller_Config.objects.filter(pk=1).first()
        if config is None:
            Domain_Controller_Config.objects.create(
                pk=1,
                name='演示域控（仅测试）',
                host='dc.demo.invalid',
                port=636,
                use_ssl=True,
                base_dn='DC=demo,DC=invalid',
                bind_username='CN=demo-reader,OU=Service Accounts,DC=demo,DC=invalid',
                bind_password=DEMO_SECRET,
            )
        elif config.name == '演示域控（仅测试）' and config.host == 'dc.demo.invalid':
            config.port = 636
            config.use_ssl = True
            config.base_dn = 'DC=demo,DC=invalid'
            config.bind_username = 'CN=demo-reader,OU=Service Accounts,DC=demo,DC=invalid'
            config.bind_password = DEMO_SECRET
            config.save()

        account_specs = (
            ('demo.zhang@demo.invalid', '演示-张瑾', True, 'OU=Operations,DC=demo,DC=invalid', 'DEMO-DOMAIN-PC-01'),
            ('demo.li@demo.invalid', '演示-李然', True, 'OU=Network,DC=demo,DC=invalid', 'DEMO-DOMAIN-PC-02'),
            ('demo.disabled@demo.invalid', '演示-停用账户', False, 'OU=Disabled,DC=demo,DC=invalid', ''),
        )
        accounts = []
        for index, (login, name, active, ou, workstations) in enumerate(account_specs):
            accounts.append(_upsert_asset(
                Domain_Account,
                {'login_name': login},
                {
                    'account_name': name,
                    'is_active': active,
                    'ou': ou,
                    'object_guid': _demo_uuid(f'domain-account-guid:{login}'),
                    'distinguished_name': f'CN={name},{ou}',
                    'allowed_workstations': workstations,
                    'last_login_date': (anchor - timedelta(days=index + 1)).date(),
                },
                f'domain-account:{login}',
            ))

        computer_specs = (
            ('DEMO-DOMAIN-PC-01', True, 'OU=Operations,DC=demo,DC=invalid', 'Windows 11 Enterprise', 1),
            ('DEMO-DOMAIN-PC-02', True, 'OU=Network,DC=demo,DC=invalid', 'Windows 10 Enterprise', 3),
            ('DEMO-DOMAIN-PC-OLD', False, 'OU=Disabled,DC=demo,DC=invalid', 'Windows 10 Enterprise', 120),
        )
        domain_computers = []
        for name, active, ou, operating_system, days_ago in computer_specs:
            domain_computers.append(_upsert_asset(
                Domain_Computer,
                {'computer_name': name},
                {
                    'is_active': active,
                    'ou': ou,
                    'object_guid': _demo_uuid(f'domain-computer-guid:{name}'),
                    'distinguished_name': f'CN={name},{ou}',
                    'os': operating_system,
                    'last_login_date': (anchor - timedelta(days=days_ago)).date(),
                },
                f'domain-computer:{name}',
            ))

        group_specs = (
            ('DEMO-IT-ADMINS', '演示-IT 管理员', 'security', 'global', 4,
             'OU=Security Groups,DC=demo,DC=invalid'),
            ('DEMO-NOTICES', '演示-通知邮件组', 'distribution', 'universal', 18,
             'OU=Distribution Groups,DC=demo,DC=invalid'),
            ('DEMO-PC-OPERATORS', '演示-PC 运维组', 'security', 'domain_local', 7,
             'OU=Security Groups,DC=demo,DC=invalid'),
        )
        domain_groups = []
        for login_name, group_name, category, scope, member_count, ou in group_specs:
            domain_groups.append(_upsert_asset(
                Domain_Group,
                {'login_name': login_name},
                {
                    'group_name': group_name,
                    'object_guid': _demo_uuid(f'domain-group-guid:{login_name}'),
                    'distinguished_name': f'CN={group_name},{ou}',
                    'description': '域控同步演示分组',
                    'ou': ou,
                    'group_scope': scope,
                    'group_category': category,
                    'member_count': member_count,
                },
                f'domain-group:{login_name}',
            ))
        return accounts, domain_computers, domain_groups

    def _seed_domain_operations(self, anchor, accounts, domain_computers):
        """Create offline audit examples without credentials or LDAP activity."""
        operator, _ = get_user_model().objects.get_or_create(
            username='demo-domain-operator',
            defaults={'is_active': False},
        )
        specs = (
            {
                'identity': 'move-ou',
                'object_type': DomainOperation.ObjectType.COMPUTER,
                'action': DomainOperation.Action.MOVE_OU,
                'target': domain_computers[0],
                'target_type': TaskTargetRun.TargetType.DOMAIN_COMPUTER,
                'name': domain_computers[0].computer_name,
                'parameters': {
                    'destination_dn': 'OU=Archive,DC=demo,DC=invalid',
                },
            },
            {
                'identity': 'enable',
                'object_type': DomainOperation.ObjectType.ACCOUNT,
                'action': DomainOperation.Action.ENABLE,
                'target': accounts[2],
                'target_type': TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
                'name': accounts[2].login_name,
                'parameters': {},
            },
        )
        for index, spec in enumerate(specs, 1):
            task_identity = f'demo-domain-task-{spec["identity"]}'
            target_identity = f'demo-domain-target-{spec["identity"]}'
            operation_identity = f'demo-domain-operation-{spec["identity"]}'
            finished_at = anchor - timedelta(minutes=80 + index)
            scope = {
                'targets': [{
                    'target_type': spec['target_type'],
                    'target_id': str(spec['target'].pk),
                }],
            }
            task, _ = TaskRun.objects.update_or_create(
                pk=_demo_uuid(task_identity),
                defaults={
                    'task_type': TaskRun.TaskType.DOMAIN_OPERATION,
                    'source': TaskRun.Source.MANUAL,
                    'status': TaskRun.Status.SUCCESS,
                    'progress': 100,
                    'available_at': finished_at - timedelta(minutes=1),
                    'started_at': finished_at - timedelta(seconds=10),
                    'finished_at': finished_at,
                    'profile_snapshot': {
                        'object_type': spec['object_type'],
                        'action': spec['action'],
                    },
                    'parameters_snapshot': {**spec['parameters'], 'demo_only': True},
                    'selected_items_snapshot': [],
                    'target_scope_snapshot': scope,
                    'scope_key': TaskRun.build_scope_key(
                        task_type=TaskRun.TaskType.DOMAIN_OPERATION,
                        profile_id=f'{spec["object_type"]}:{spec["action"]}',
                        target_scope_snapshot=scope,
                    ),
                    'active_scope_key': None,
                    'total_targets': 1,
                    'completed_targets': 1,
                    'successful_targets': 1,
                    'failed_targets': 0,
                },
            )
            TaskTargetRun.objects.update_or_create(
                pk=_demo_uuid(target_identity),
                defaults={
                    'task': task,
                    'target_type': spec['target_type'],
                    'target_id': str(spec['target'].pk),
                    'target_snapshot': {
                        'id': str(spec['target'].pk),
                        'name': spec['name'],
                        'distinguished_name': spec['target'].distinguished_name,
                    },
                    'status': TaskRun.Status.SUCCESS,
                    'started_at': task.started_at,
                    'finished_at': task.finished_at,
                    'result_snapshot': {'message': '离线演示：未连接 LDAP。'},
                },
            )
            DomainOperation.objects.update_or_create(
                pk=_demo_uuid(operation_identity),
                defaults={
                    'action': spec['action'],
                    'object_type': spec['object_type'],
                    'requested_by': operator,
                    'status': DomainOperation.Status.SUCCESS,
                    'target_count': 1,
                    'parameter_summary': {**spec['parameters'], 'demo_only': True},
                    'task': task,
                    'started_at': task.started_at,
                    'finished_at': task.finished_at,
                },
            )

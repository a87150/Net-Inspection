import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models


def validate_string_list(value):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError('必须是字符串列表。')


def validate_vlan_list(value):
    if (not isinstance(value, list) or any(
            isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 4094
            for item in value)):
        raise ValidationError('VLAN 必须是 1 至 4094 的整数列表。')


def validate_json_object(value):
    if not isinstance(value, dict):
        raise ValidationError('必须是 JSON 对象。')


def _validate_time_order(instance, field_names):
    values = [getattr(instance, name) for name in field_names]
    present = [value for value in values if value is not None]
    if present != sorted(present):
        raise ValidationError('采集时间顺序无效。')


class TopologyDiscoveryBatch(models.Model):
    class Protocol(models.TextChoices):
        SNMP_LLDP = 'snmp_lldp', 'SNMP LLDP'
        SSH_LLDP = 'ssh_lldp', 'SSH LLDP'
        SSH_CDP = 'ssh_cdp', 'SSH CDP'
        MIXED = 'mixed', '混合证据'

    class Status(models.TextChoices):
        RUNNING = 'running', '运行中'
        SUCCESS = 'success', '成功'
        PARTIAL = 'partial', '部分成功'
        FAILED = 'failed', '失败'
        CANCELLED = 'cancelled', '已取消'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_task = models.ForeignKey('net.TaskRun', on_delete=models.PROTECT, related_name='topology_batches')
    source_target = models.OneToOneField('net.TaskTargetRun', on_delete=models.PROTECT, related_name='topology_batch')
    device = models.ForeignKey('net.Network_Device', on_delete=models.PROTECT, related_name='topology_batches')
    protocol = models.CharField(max_length=16, choices=Protocol.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    schema_version = models.PositiveSmallIntegerField(default=1)
    started_at = models.DateTimeField()
    collected_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    message = models.CharField(max_length=1000, blank=True)
    interface_count = models.PositiveIntegerField(default=0)
    observation_count = models.PositiveIntegerField(default=0)
    resolved_count = models.PositiveIntegerField(default=0)
    unresolved_count = models.PositiveIntegerField(default=0)
    conflict_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=('device', '-collected_at'), name='net_topobatch_device_time_idx'),
            models.Index(fields=('status', '-started_at'), name='net_topobatch_status_time_idx'),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(schema_version__gte=1), name='net_topobatch_schema_positive_ck'),
        ]

    def clean(self):
        super().clean()
        _validate_time_order(self, ('started_at', 'collected_at', 'finished_at'))
        if self.source_target_id and self.source_task_id and self.source_target.task_id != self.source_task_id:
            raise ValidationError({'source_target': '来源目标不属于来源任务。'})


class NetworkTopologyInterface(models.Model):
    STATUS_CHOICES = [
        ('up', 'Up'), ('down', 'Down'), ('testing', 'Testing'),
        ('unknown', 'Unknown'), ('dormant', 'Dormant'),
        ('not_present', 'Not present'), ('lower_layer_down', 'Lower layer down'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey('net.Network_Device', on_delete=models.PROTECT, related_name='topology_interfaces')
    stable_key = models.CharField(max_length=255)
    if_index = models.PositiveBigIntegerField(null=True, blank=True)
    name = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=1000, blank=True)
    mac_address = models.CharField(max_length=64, blank=True)
    admin_status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='unknown')
    oper_status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='unknown')
    speed_bps = models.PositiveBigIntegerField(null=True, blank=True)
    vlan_ids = models.JSONField(default=list, blank=True, validators=[validate_vlan_list])
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    last_batch = models.ForeignKey(TopologyDiscoveryBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name='interfaces')
    is_stale = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('device', 'stable_key'), name='net_topointf_device_key_uniq'),
        ]
        indexes = [
            models.Index(fields=('device', 'last_seen_at'), name='net_topointf_device_seen_idx'),
            models.Index(fields=('mac_address',), name='net_topointf_mac_idx'),
        ]

    def clean(self):
        super().clean()
        if self.first_seen_at and self.last_seen_at and self.last_seen_at < self.first_seen_at:
            raise ValidationError({'last_seen_at': '最后发现时间不能早于首次发现时间。'})


class NetworkTopologyLink(models.Model):
    EVIDENCE_DIRECTION_CHOICES = [('unilateral', '单侧'), ('bidirectional', '双向')]
    RESOLUTION_STATUS_CHOICES = [('resolved', '已解析'), ('unresolved', '未解析'), ('conflict', '冲突')]
    STATUS_CHOICES = [('current', '当前'), ('stale', '陈旧')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stable_link_key = models.CharField(max_length=64, unique=True, validators=[RegexValidator(r'^[0-9a-f]{64}$', '链路键必须是 SHA-256。')])
    local_interface = models.ForeignKey(NetworkTopologyInterface, on_delete=models.PROTECT, related_name='local_links')
    remote_device = models.ForeignKey('net.Network_Device', null=True, blank=True, on_delete=models.SET_NULL, related_name='remote_topology_links')
    remote_interface = models.ForeignKey(NetworkTopologyInterface, null=True, blank=True, on_delete=models.SET_NULL, related_name='remote_links')
    remote_chassis_id = models.CharField(max_length=255, blank=True)
    remote_port_id = models.CharField(max_length=255, blank=True)
    remote_port_description = models.CharField(max_length=1000, blank=True)
    remote_system_name = models.CharField(max_length=255, blank=True)
    remote_management_addresses = models.JSONField(default=list, blank=True, validators=[validate_string_list])
    protocols = models.JSONField(default=list, blank=True, validators=[validate_string_list])
    evidence_direction = models.CharField(max_length=16, choices=EVIDENCE_DIRECTION_CHOICES, default='unilateral')
    resolution_status = models.CharField(max_length=16, choices=RESOLUTION_STATUS_CHOICES, default='unresolved')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='current')
    speed_bps = models.PositiveBigIntegerField(null=True, blank=True)
    vlan_ids = models.JSONField(default=list, blank=True, validators=[validate_vlan_list])
    confidence = models.DecimalField(max_digits=3, decimal_places=2, default='0.60', validators=[MinValueValidator(0), MaxValueValidator(1)])
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    last_batch = models.ForeignKey(TopologyDiscoveryBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name='links')
    missing_complete_batches = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=('status', 'last_seen_at'), name='net_topolink_status_seen_idx'),
            models.Index(fields=('remote_device', 'status'), name='net_topolink_remote_status_idx'),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(confidence__gte=0) & models.Q(confidence__lte=1), name='net_topolink_confidence_range_ck'),
        ]

    def clean(self):
        super().clean()
        if self.first_seen_at and self.last_seen_at and self.last_seen_at < self.first_seen_at:
            raise ValidationError({'last_seen_at': '最后发现时间不能早于首次发现时间。'})


class NetworkTopologyObservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(TopologyDiscoveryBatch, on_delete=models.CASCADE, related_name='observations')
    source_target = models.ForeignKey('net.TaskTargetRun', on_delete=models.PROTECT, related_name='topology_observations')
    local_device = models.ForeignKey('net.Network_Device', on_delete=models.PROTECT, related_name='topology_observations')
    local_interface = models.ForeignKey(NetworkTopologyInterface, null=True, blank=True, on_delete=models.SET_NULL, related_name='observations')
    link = models.ForeignKey(NetworkTopologyLink, null=True, blank=True, on_delete=models.SET_NULL, related_name='observations')
    protocol = models.CharField(max_length=16, choices=TopologyDiscoveryBatch.Protocol.choices)
    neighbor = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    evidence = models.TextField(max_length=65536)
    evidence_sha256 = models.CharField(max_length=64, validators=[RegexValidator(r'^[0-9a-f]{64}$', '证据摘要必须是 SHA-256。')])
    collected_at = models.DateTimeField()

    class Meta:
        permissions = [('view_topology_evidence', 'Can view topology evidence')]
        constraints = [
            models.UniqueConstraint(fields=('batch', 'evidence_sha256'), name='net_topoobs_batch_digest_uniq'),
        ]
        indexes = [
            models.Index(fields=('batch', 'collected_at'), name='net_topoobs_batch_time_idx'),
            models.Index(fields=('collected_at',), name='net_topoobs_time_idx'),
        ]

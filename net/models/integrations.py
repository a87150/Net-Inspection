"""Persistent, secret-safe configuration for personnel directory sources."""

import re
import uuid

from django.core.exceptions import ValidationError
from django.db import models


_SOURCE_KEY_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,190}$')
_CREDENTIAL_FIELDS = {
    'feishu': frozenset(('app_id', 'app_secret')),
    'dingtalk': frozenset(('app_key', 'app_secret')),
}


def _validate_credentials(source_type, credentials):
    if not isinstance(credentials, dict):
        raise ValidationError('人员目录凭据无效。')
    expected = _CREDENTIAL_FIELDS.get(source_type)
    if expected is None or set(credentials) != expected:
        raise ValidationError('人员目录凭据无效。')
    if any(not isinstance(value, str) or not value.strip() for value in credentials.values()):
        raise ValidationError('人员目录凭据无效。')


def _validate_root_department_ids(root_department_ids):
    if not isinstance(root_department_ids, list):
        raise ValidationError('根部门必须是文本标识列表。')
    if any(not isinstance(value, str) or not value.strip() for value in root_department_ids):
        raise ValidationError('根部门必须是文本标识列表。')
    normalized = [value.strip() for value in root_department_ids]
    if len(normalized) != len(set(normalized)):
        raise ValidationError('根部门不能重复。')


class PeopleSyncSource(models.Model):
    """A durable provider configuration; access tokens never belong here.

    Public source contexts must contain public_data(), never the ORM instance
    or a raw QuerySet.values() result. Adapters retain internal credential
    access. Future secret forms must declare explicit write-only inputs and
    assign validated credentials without echoing them into initial/bound data.
    """

    class SourceType(models.TextChoices):
        FEISHU = 'feishu', '飞书'
        DINGTALK = 'dingtalk', '钉钉'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    name = models.CharField(max_length=255)
    source_key = models.CharField(max_length=191, unique=True)
    # Exclude secrets from Django serializers, model_to_dict and auto ModelForms.
    credentials = models.JSONField(default=dict, serialize=False, editable=False)
    root_department_ids = models.JSONField(default=list, blank=True)
    is_enabled = models.BooleanField(default=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(source_type__in=('feishu', 'dingtalk')),
            name='net_people_sync_source_type_ck',
        )]

    def clean(self):
        super().clean()
        errors = {}
        if not isinstance(self.source_key, str) or not _SOURCE_KEY_RE.fullmatch(self.source_key):
            errors['source_key'] = '来源标识必须是稳定的非机密标识符。'
        try:
            _validate_credentials(self.source_type, self.credentials)
        except ValidationError:
            errors['credentials'] = '人员目录凭据无效。'
        try:
            _validate_root_department_ids(self.root_department_ids)
        except ValidationError as exc:
            errors['root_department_ids'] = exc.messages
        if errors:
            raise ValidationError(errors)

    @property
    def type(self):
        """Compatibility spelling for callers that describe the provider as type."""
        return self.source_type

    def public_data(self):
        """The supported source context for public JSON responses and templates.

        Pass this dictionary instead of the model so template attribute lookup
        cannot reach credentials, while internal ORM consumers still can.
        """
        return {
            'id': str(self.pk),
            'source_type': self.source_type,
            'name': self.name,
            'source_key': self.source_key,
            'root_department_ids': list(self.root_department_ids),
            'is_enabled': self.is_enabled,
            'has_credentials': bool(self.credentials),
            'last_tested_at': self.last_tested_at.isoformat() if self.last_tested_at else None,
            'last_synced_at': self.last_synced_at.isoformat() if self.last_synced_at else None,
            'connection_test_current': bool(
                self.last_tested_at and self.updated_at
                and self.last_tested_at >= self.updated_at
            ),
        }

    def __str__(self):
        return f'{self.get_source_type_display()}：{self.name}'

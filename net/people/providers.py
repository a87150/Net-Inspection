"""Canonical Feishu and DingTalk personnel provider settings."""

from dataclasses import dataclass

from django.db import transaction

from net.models import PeopleSyncSource


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    key: str
    label: str
    source_key: str
    credential_fields: tuple[str, ...]


PROVIDERS = {
    'feishu': ProviderDefinition(
        'feishu', '飞书', 'people-provider-feishu', ('app_id', 'app_secret'),
    ),
    'dingtalk': ProviderDefinition(
        'dingtalk', '钉钉', 'people-provider-dingtalk', ('app_key', 'app_secret'),
    ),
}


def get_provider_definition(provider):
    try:
        return PROVIDERS[provider]
    except KeyError as exc:
        raise ValueError('不支持的人员 API 平台。') from exc


def get_provider_source(provider):
    definition = get_provider_definition(provider)
    return PeopleSyncSource.objects.filter(source_key=definition.source_key).first()


@transaction.atomic
def save_provider_source(provider, cleaned_data):
    definition = get_provider_definition(provider)
    source = (
        PeopleSyncSource.objects.select_for_update()
        .filter(source_key=definition.source_key)
        .first()
    )
    if source is None:
        source = PeopleSyncSource(source_key=definition.source_key)

    previous_credentials = dict(source.credentials or {})
    credentials = {
        field: cleaned_data.get(field) or previous_credentials.get(field, '')
        for field in definition.credential_fields
    }
    roots = list(cleaned_data['root_department_ids'])
    enabled = cleaned_data['is_enabled']
    changed = (
        source.pk is None
        or source.source_type != definition.key
        or source.name != definition.label
        or source.root_department_ids != roots
        or source.is_enabled != enabled
        or source.credentials != credentials
    )

    source.source_type = definition.key
    source.name = definition.label
    source.root_department_ids = roots
    source.is_enabled = enabled
    source.credentials = credentials
    if changed:
        source.last_tested_at = None
        source.full_clean()
        source.save()
    return source

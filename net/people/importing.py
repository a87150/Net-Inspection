from dataclasses import dataclass


PERSONNEL_PROVIDERS = {
    'feishu': '飞书',
    'dingtalk': '钉钉',
}


class PersonnelProviderNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class PersonnelImportResult:
    created: int = 0
    updated: int = 0
    deactivated: int = 0


def get_personnel_provider_label(provider):
    try:
        return PERSONNEL_PROVIDERS[provider]
    except KeyError as exc:
        raise ValueError('不支持的人员接口') from exc


def import_people_from_provider(provider):
    """Synchronous provider boundary; Phase 4 supplies configured clients."""
    label = get_personnel_provider_label(provider)
    raise PersonnelProviderNotConfigured(f'{label}人员接口尚未配置')

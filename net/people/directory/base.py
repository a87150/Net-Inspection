"""Pure contract shared by future personnel-directory adapters."""

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import json
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from django.utils.crypto import salted_hmac


class DirectoryAdapterError(RuntimeError):
    """Base error with a safe public message regardless of provider details."""

    public_message = '人员目录接口调用失败。'

    def __init__(self, *_private_details):
        super().__init__(self.public_message)


class DirectoryAuthenticationError(DirectoryAdapterError):
    public_message = '人员目录认证失败。'


class DirectoryRateLimitError(DirectoryAdapterError):
    public_message = '人员目录接口请求过于频繁。'


class DirectoryPayloadError(DirectoryAdapterError):
    public_message = '人员目录接口返回的数据无效。'


class DirectoryReferenceError(DirectoryAdapterError):
    public_message = '无法获取部门或上级姓名，请检查通讯录可见范围及部门详情、用户详情读取权限；本次未导入人员。'


def _optional_text(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise DirectoryPayloadError()
    return value.strip()


def _optional_date(value):
    if value is None or value == '':
        return ''
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise DirectoryPayloadError()
    text = value.strip()
    if not text:
        return ''
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        raise DirectoryPayloadError() from None


@dataclass(frozen=True)
class DirectoryPerson:
    """Provider-neutral directory record with one required employee identity."""

    employee_id: str
    name: str = ''
    email: str = ''
    department: str = ''
    leader: str = ''
    external_user_id: str = ''
    hire_date: str = ''
    departure_date: str = ''
    phone: str = ''

    def __post_init__(self):
        if not isinstance(self.employee_id, str) or not self.employee_id.strip():
            raise DirectoryPayloadError()
        object.__setattr__(self, 'employee_id', self.employee_id.strip())
        for field_name in ('name', 'email', 'department', 'leader', 'external_user_id', 'phone'):
            object.__setattr__(self, field_name, _optional_text(getattr(self, field_name)))
        for field_name in ('hire_date', 'departure_date'):
            object.__setattr__(self, field_name, _optional_date(getattr(self, field_name)))


@dataclass(frozen=True, repr=False)
class DirectorySourceSnapshot:
    """Immutable provider inputs captured before an adapter performs any I/O."""

    source_key: object
    source_type: object
    credentials: object
    root_department_ids: object
    is_enabled: object


def freeze_directory_source(source):
    """Deep-copy the only source fields directory reads are allowed to consume."""
    credentials = deepcopy(getattr(source, 'credentials', None))
    if isinstance(credentials, Mapping):
        credentials = MappingProxyType(dict(credentials))
    roots = deepcopy(getattr(source, 'root_department_ids', None))
    if isinstance(roots, (list, tuple)):
        roots = tuple(roots)
    return DirectorySourceSnapshot(
        source_key=deepcopy(getattr(source, 'source_key', None)),
        source_type=deepcopy(getattr(source, 'source_type', None)),
        credentials=credentials,
        root_department_ids=roots,
        is_enabled=deepcopy(getattr(source, 'is_enabled', None)),
    )


def directory_source_configuration_identity(source):
    """Return the nonsecret identity of the provider inputs captured by an adapter.

    The digest is keyed with Django's secret key and may be signed into a public
    preview.  It binds roots and credentials without disclosing either raw
    credential values or a reversible representation of them.
    """
    source_key = getattr(source, 'source_key', None)
    credentials = getattr(source, 'credentials', None)
    if isinstance(credentials, Mapping):
        credentials = dict(credentials)
    roots = getattr(source, 'root_department_ids', None)
    if isinstance(roots, (list, tuple)):
        roots = list(roots)
    payload = {
        'source_key': source_key,
        'source_type': getattr(source, 'source_type', None),
        'root_department_ids': roots,
        'is_enabled': getattr(source, 'is_enabled', None),
        'credentials': credentials,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return {
        'source_key': source_key,
        'digest': salted_hmac('net.people.directory.fetch-configuration', encoded).hexdigest(),
    }


@runtime_checkable
class DirectoryAdapter(Protocol):
    """Read-only boundary; concrete adapters normalize but never write models."""

    @property
    def source_key(self) -> str: ...

    @property
    def fetch_configuration_identity(self) -> dict[str, str]: ...

    def test_connection(self) -> None: ...

    def iter_people(self) -> Iterator[DirectoryPerson]: ...

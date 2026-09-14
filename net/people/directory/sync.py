"""Preview-first, source-scoped personnel synchronization.

The service deliberately has no HTTP or UI dependency. Callers give it the
fully-buffered adapter snapshot once, then use the short-lived signed preview
to confirm an atomic local write later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import re
from typing import Any, Mapping

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from net.people.directory.base import (
    DirectoryAdapterError,
    DirectoryPerson,
    directory_source_configuration_identity,
)
from net.models import People, PeopleSyncSource


_PREVIEW_SALT = 'net.people.sync.preview.v1'
_PREVIEW_MAX_AGE_SECONDS = 300
_PREVIEW_VERSION = 3
_SAFE_SKIP_REASON_RE = re.compile(r'^[a-z0-9_:-]{1,64}$')
_RECORD_FIELDS = ('employee_id', 'name', 'email', 'department', 'leader', 'external_user_id', 'phone')
_DATE_RECORD_FIELDS = ('hire_date', 'departure_date')
_PERSON_STATE_FIELDS = (
    'employee_id', 'name', 'email', 'phone', 'department', 'leader', 'is_active',
    'source', 'sync_source_id', 'platform_user_id', 'last_synced_at', 'hire_date', 'departure_date',
)


class PeopleSyncError(RuntimeError):
    """A safe error boundary: details stay out of UI and logs by default."""

    public_message = '人员目录同步失败。'

    def __init__(self):
        super().__init__(self.public_message)


class PeopleSyncPreviewError(PeopleSyncError):
    public_message = '人员目录同步预览失败。'


class PeopleSyncApplyError(PeopleSyncError):
    public_message = '人员目录同步预览已失效或无法应用。'


@dataclass(frozen=True)
class SyncPreview:
    """A public, serializable diff; its token authorizes exactly this payload."""

    source_key: str = ''
    creates: tuple[dict[str, str], ...] = ()
    updates: tuple[dict[str, str], ...] = ()
    unchanged: tuple[dict[str, str], ...] = ()
    deactivations: tuple[dict[str, str], ...] = ()
    skipped: tuple[dict[str, str], ...] = ()
    validation_messages: tuple[str, ...] = ()
    token: str = ''

    @property
    def is_valid(self) -> bool:
        return not self.validation_messages

    def to_dict(self) -> dict[str, Any]:
        """Return only public diff data; credentials never enter this boundary."""
        return {
            'source_key': self.source_key,
            'creates': [dict(record) for record in self.creates],
            'updates': [dict(record) for record in self.updates],
            'unchanged': [dict(record) for record in self.unchanged],
            'deactivations': [dict(record) for record in self.deactivations],
            'skipped': [dict(record) for record in self.skipped],
            'validation_messages': list(self.validation_messages),
            'token': self.token,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'SyncPreview':
        """Rehydrate public data; apply still requires a matching signed token."""
        if not isinstance(value, Mapping):
            raise PeopleSyncApplyError()
        try:
            return cls(
                source_key=value.get('source_key', ''),
                creates=tuple(dict(record) for record in value.get('creates', ())),
                updates=tuple(dict(record) for record in value.get('updates', ())),
                unchanged=tuple(dict(record) for record in value.get('unchanged', ())),
                deactivations=tuple(dict(record) for record in value.get('deactivations', ())),
                skipped=tuple(dict(record) for record in value.get('skipped', ())),
                validation_messages=tuple(value.get('validation_messages', ())),
                token=value.get('token', ''),
            )
        except (TypeError, ValueError):
            raise PeopleSyncApplyError() from None


@dataclass(frozen=True)
class SyncResult:
    """Serializable counts from one all-or-nothing apply."""

    created: int = 0
    updated: int = 0
    deactivated: int = 0
    unchanged: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            'created': self.created,
            'updated': self.updated,
            'deactivated': self.deactivated,
            'unchanged': self.unchanged,
        }


def preview_people_sync(source: PeopleSyncSource, adapter) -> SyncPreview:
    """Fully consume a directory snapshot and calculate a no-write local diff."""
    source_key = _source_key(source)
    if not _saved_source(source):
        return _invalid_preview(source_key, '人员目录来源无效。')
    try:
        # Preview must bind the persisted configuration version, not a caller's
        # stale in-memory model after an earlier apply updated sync metadata.
        source = PeopleSyncSource.objects.get(pk=source.pk)
        source_key = _source_key(source)
    except PeopleSyncSource.DoesNotExist:
        return _invalid_preview(source_key, '人员目录来源无效。')
    if not isinstance(getattr(adapter, 'source_key', None), str) or adapter.source_key != source_key:
        return _invalid_preview(source_key, '人员目录来源不匹配。')
    fetch_identity = directory_source_configuration_identity(source)
    if _adapter_fetch_identity(adapter) != fetch_identity:
        return _invalid_preview(source_key, '人员目录来源配置不匹配。')
    try:
        remote_people = tuple(adapter.iter_people())
    except DirectoryAdapterError:
        raise
    except Exception:
        raise PeopleSyncPreviewError() from None
    if getattr(adapter, 'last_snapshot_complete', False) is not True:
        return _invalid_preview(source_key, '人员目录快照不完整。')
    try:
        source = PeopleSyncSource.objects.get(pk=source.pk)
    except PeopleSyncSource.DoesNotExist:
        return _invalid_preview(source_key, '人员目录来源无效。')
    if _adapter_fetch_identity(adapter) != directory_source_configuration_identity(source):
        return _invalid_preview(source_key, '人员目录来源配置已变更。')

    skipped, skip_messages = _safe_skipped_records(getattr(adapter, 'skipped_records', ()))
    records, record_messages = _normalize_remote_people(source, remote_people)
    messages = tuple(skip_messages + record_messages)
    if messages:
        return _invalid_preview(source_key, *messages, skipped=skipped)

    local_people = list(People.objects.order_by('pk'))
    local_by_employee = {person.employee_id: person for person in local_people if person.employee_id}
    creates, updates, unchanged, conflicts = [], [], [], []
    for employee_id in sorted(records):
        record = records[employee_id]
        existing = local_by_employee.get(employee_id)
        if existing is None:
            creates.append(record)
        elif _owned_by_source(existing, source) or _claimable_by_source(existing):
            if _person_matches_record(existing, record):
                unchanged.append({'employee_id': employee_id})
            else:
                updates.append(record)
        else:
            conflicts.append('员工编号与受保护人员记录冲突。')
    if conflicts:
        return _invalid_preview(source_key, *conflicts, skipped=skipped)

    remote_employee_ids = set(records)
    skipped_external_ids = {record['external_user_id'] for record in skipped}
    deactivations = [
        {'employee_id': person.employee_id}
        for person in local_people
        if _owned_by_source(person, source)
        and person.is_active
        and person.employee_id not in remote_employee_ids
        and person.platform_user_id not in skipped_external_ids
    ]
    deactivations.sort(key=lambda item: item['employee_id'])
    unsigned = SyncPreview(
        source_key=source_key,
        creates=tuple(creates), updates=tuple(updates), unchanged=tuple(unchanged),
        deactivations=tuple(deactivations), skipped=skipped,
    )
    return SyncPreview(**{**unsigned.__dict__, 'token': _sign_preview(source, local_people, unsigned)})


def apply_people_sync(source: PeopleSyncSource, preview: SyncPreview | Mapping[str, Any]) -> SyncResult:
    """Apply exactly one valid preview after source and local-state revalidation."""
    if isinstance(preview, Mapping):
        preview = SyncPreview.from_dict(preview)
    if not isinstance(preview, SyncPreview) or not preview.is_valid or not preview.token:
        raise PeopleSyncApplyError()
    signed = _load_signed_preview(source, preview)
    try:
        with transaction.atomic():
            locked_source = PeopleSyncSource.objects.select_for_update().get(pk=source.pk)
            if _source_config(locked_source) != signed['source_config']:
                raise PeopleSyncApplyError()
            locked_people = list(People.objects.select_for_update().order_by('pk'))
            if _state_fingerprint(locked_people) != signed['local_state_fingerprint']:
                raise PeopleSyncApplyError()
            _validate_source(locked_source)
            return _apply_locked_preview(locked_source, locked_people, preview)
    except PeopleSyncApplyError:
        raise
    except (PeopleSyncSource.DoesNotExist, IntegrityError, ValidationError):
        raise PeopleSyncApplyError() from None


def _apply_locked_preview(source, locked_people, preview) -> SyncResult:
    local_by_employee = {person.employee_id: person for person in locked_people if person.employee_id}
    now = timezone.now()
    creates, updates, deactivations = [], [], []
    for record in preview.creates:
        _validate_record_shape(record)
        if record['employee_id'] in local_by_employee:
            raise PeopleSyncApplyError()
        person = _person_from_record(source, record, now)
        _validate_person(person)
        creates.append(person)
        local_by_employee[person.employee_id] = person
    for record in preview.updates:
        _validate_record_shape(record)
        person = local_by_employee.get(record['employee_id'])
        if person is None or not (
            _owned_by_source(person, source) or _claimable_by_source(person)
        ):
            raise PeopleSyncApplyError()
        _set_person_record(person, source, record, now)
        _validate_person(person)
        updates.append(person)
    for record in preview.deactivations:
        if set(record) != {'employee_id'} or not isinstance(record['employee_id'], str):
            raise PeopleSyncApplyError()
        person = local_by_employee.get(record['employee_id'])
        if person is None or not _owned_by_source(person, source):
            raise PeopleSyncApplyError()
        person.is_active = False
        person.last_synced_at = now
        _validate_person(person)
        deactivations.append(person)

    # All model validation is complete before the first write. The surrounding
    # transaction still turns a database-level race into an all-or-nothing fail.
    for person in creates:
        person.save(force_insert=True)
    for person in updates:
        person.save()
    for person in deactivations:
        person.save(update_fields=['is_active', 'last_synced_at'])
    source.last_synced_at = now
    # Synchronization is operational metadata, not a provider configuration change.
    # Preview signatures separately bind last_synced_at to prevent replay, including no-op imports.
    source.save(update_fields=['last_synced_at'])
    return SyncResult(len(creates), len(updates), len(deactivations), len(preview.unchanged))


def _normalize_remote_people(source, people):
    records, messages = {}, []
    for person in people:
        try:
            record = _record_from_directory_person(source, person)
        except (AttributeError, TypeError, ValidationError, PeopleSyncApplyError):
            messages.append('人员目录记录字段无效。')
            continue
        existing = records.get(record['employee_id'])
        if existing is None:
            records[record['employee_id']] = record
        elif existing != record:
            messages.append('人员目录中存在重复员工编号。')
    return records, _deduplicated(messages)


def _record_from_directory_person(source, person):
    if not isinstance(person, DirectoryPerson):
        raise TypeError()
    record = {field_name: getattr(person, field_name) for field_name in _RECORD_FIELDS}
    for field_name in _DATE_RECORD_FIELDS:
        value = getattr(person, field_name)
        if value:
            record[field_name] = value
    _validate_record_shape(record)
    _validate_person(_person_from_record(source, record, None))
    return record


def _safe_skipped_records(records):
    if not isinstance(records, (tuple, list)):
        return (), ('人员目录跳过记录无效。',)
    safe, messages = [], []
    for record in records:
        if not isinstance(record, Mapping):
            messages.append('人员目录跳过记录无效。')
            continue
        external_user_id = record.get('external_user_id')
        reason = record.get('reason')
        if (set(record) != {'external_user_id', 'reason'}
                or not isinstance(external_user_id, str) or not external_user_id.strip()
                or len(external_user_id.strip()) > People._meta.get_field('platform_user_id').max_length
                or not isinstance(reason, str) or not _SAFE_SKIP_REASON_RE.fullmatch(reason)):
            messages.append('人员目录跳过记录无效。')
            continue
        normalized = {'external_user_id': external_user_id.strip(), 'reason': reason}
        if normalized not in safe:
            safe.append(normalized)
    safe.sort(key=lambda item: (item['external_user_id'], item['reason']))
    return tuple(safe), _deduplicated(messages)


def _validate_record_shape(record):
    if not isinstance(record, Mapping) or not set(record).issubset(set(_RECORD_FIELDS + _DATE_RECORD_FIELDS)):
        raise PeopleSyncApplyError()
    if set(_RECORD_FIELDS) - set(record) or any(not isinstance(record[field_name], str) for field_name in record):
        raise PeopleSyncApplyError()
    for field_name in _DATE_RECORD_FIELDS:
        if field_name in record:
            try:
                date.fromisoformat(record[field_name])
            except ValueError:
                raise PeopleSyncApplyError() from None


def _person_from_record(source, record, synced_at):
    return People(
        employee_id=record['employee_id'], name=record['name'], email=record['email'],
        phone=record['phone'],
        department=record['department'], leader=record['leader'],
        platform_user_id=record['external_user_id'], source=source.source_type,
        sync_source=source, is_active=True, last_synced_at=synced_at,
        hire_date=_record_date(record, 'hire_date'),
        departure_date=_record_date(record, 'departure_date'),
    )


def _set_person_record(person, source, record, synced_at):
    person.name = record['name']
    person.email = record['email']
    person.phone = record['phone']
    person.department = record['department']
    person.leader = record['leader']
    person.platform_user_id = record['external_user_id']
    person.source = source.source_type
    person.sync_source = source
    person.is_active = True
    person.last_synced_at = synced_at
    for field_name in _DATE_RECORD_FIELDS:
        if field_name in record:
            setattr(person, field_name, _record_date(record, field_name))


def _validate_person(person):
    try:
        person.full_clean(validate_unique=False)
    except ValidationError:
        raise PeopleSyncApplyError() from None


def _validate_source(source):
    try:
        source.full_clean()
    except ValidationError:
        raise PeopleSyncApplyError() from None


def _owned_by_source(person, source):
    return person.sync_source_id == source.pk and person.source == source.source_type


def _claimable_by_source(person):
    return (
        person.sync_source_id is None
        and person.source in {People.Source.MANUAL, People.Source.CSV}
    )


def _person_matches_record(person, record):
    return (
        person.name or '', person.email or '', person.department or '', person.leader or '',
        person.platform_user_id or '', person.is_active, person.phone,
    ) == (
        record['name'], record['email'], record['department'], record['leader'],
        record['external_user_id'], True, record['phone'],
    ) and all(
        field_name not in record or getattr(person, field_name) == _record_date(record, field_name)
        for field_name in _DATE_RECORD_FIELDS
    )


def _record_date(record, field_name):
    value = record.get(field_name)
    return date.fromisoformat(value) if value else None


def _saved_source(source):
    return isinstance(source, PeopleSyncSource) and source.pk is not None


def _source_key(source):
    value = getattr(source, 'source_key', '')
    return value if isinstance(value, str) else ''


def _invalid_preview(source_key, *messages, skipped=()):
    return SyncPreview(source_key=source_key, skipped=tuple(skipped),
                       validation_messages=_deduplicated(messages))


def _sign_preview(source, local_people, preview):
    return signing.dumps({
        'version': _PREVIEW_VERSION,
        'source_id': str(source.pk),
        'source_config': _source_config(source),
        'local_state_fingerprint': _state_fingerprint(local_people),
        'preview_hash': _hash_data(_preview_data(preview)),
    }, salt=_PREVIEW_SALT, compress=True)


def _load_signed_preview(source, preview):
    try:
        signed = signing.loads(preview.token, salt=_PREVIEW_SALT, max_age=_PREVIEW_MAX_AGE_SECONDS)
    except signing.BadSignature:
        raise PeopleSyncApplyError() from None
    if (not isinstance(signed, Mapping) or signed.get('version') != _PREVIEW_VERSION
            or signed.get('source_id') != str(getattr(source, 'pk', None))
            or not isinstance(signed.get('source_config'), Mapping)
            or not isinstance(signed.get('local_state_fingerprint'), str)
            or signed.get('preview_hash') != _hash_data(_preview_data(preview))):
        raise PeopleSyncApplyError()
    return signed


def _preview_data(preview):
    return {
        'source_key': preview.source_key, 'creates': preview.creates, 'updates': preview.updates,
        'unchanged': preview.unchanged, 'deactivations': preview.deactivations,
        'skipped': preview.skipped, 'validation_messages': preview.validation_messages,
    }


def _source_config(source):
    return {
        'id': str(source.pk), 'source_key': source.source_key, 'source_type': source.source_type,
        'root_department_ids': list(source.root_department_ids), 'is_enabled': source.is_enabled,
        'updated_at': source.updated_at.isoformat() if source.updated_at else None,
        'last_synced_at': source.last_synced_at.isoformat() if source.last_synced_at else None,
        'fetch_configuration_identity': directory_source_configuration_identity(source),
    }


def _adapter_fetch_identity(adapter):
    """Require the explicit adapter contract; never silently trust a double."""
    value = getattr(adapter, 'fetch_configuration_identity', None)
    if not isinstance(value, Mapping) or set(value) != {'source_key', 'digest'}:
        return None
    if not isinstance(value['source_key'], str) or not isinstance(value['digest'], str):
        return None
    return {'source_key': value['source_key'], 'digest': value['digest']}


def _state_fingerprint(people):
    state = []
    for person in people:
        value = {'id': str(person.pk)}
        for field_name in _PERSON_STATE_FIELDS:
            field_value = getattr(person, field_name)
            value[field_name] = field_value.isoformat() if hasattr(field_value, 'isoformat') else field_value
        state.append(value)
    return _hash_data(state)


def _hash_data(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _deduplicated(messages):
    return tuple(dict.fromkeys(messages))

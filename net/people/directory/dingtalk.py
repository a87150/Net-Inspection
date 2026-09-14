"""Read-only DingTalk personnel-directory adapter.

Tokens are method-local only and never written to the source model.
"""

from collections.abc import Mapping

import requests

from .base import (
    DirectoryAdapterError,
    DirectoryAuthenticationError,
    DirectoryPayloadError,
    DirectoryPerson,
    DirectoryRateLimitError,
    DirectoryReferenceError,
    directory_source_configuration_identity,
    freeze_directory_source,
)


class DingTalkDirectoryAdapter:
    """Read one complete DingTalk snapshot through an injected HTTP session."""

    TOKEN_URL = 'https://oapi.dingtalk.com/gettoken'
    USERS_URL = 'https://oapi.dingtalk.com/topapi/v2/user/list'
    DEPARTMENTS_URL = 'https://oapi.dingtalk.com/topapi/v2/department/listsub'

    def __init__(self, source, *, session=None, timeout=(3.05, 15), max_retries=2):
        self.source = freeze_directory_source(source)
        self._fetch_configuration_identity = directory_source_configuration_identity(self.source)
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.last_snapshot_complete = False
        self._skipped_records = []
        self._department_names = {}
        self._user_names = {}

    @property
    def source_key(self):
        return self.source.source_key

    @property
    def fetch_configuration_identity(self):
        """The immutable provider inputs this adapter will use for its fetch."""
        return dict(self._fetch_configuration_identity)

    @property
    def skipped_records(self):
        """Safe records for later sync handling; they contain no token or secret."""
        return tuple(self._skipped_records)

    def test_connection(self):
        self._validate_source()
        self._application_access_token()

    def iter_people(self):
        self.last_snapshot_complete = False
        self._skipped_records = []
        self._department_names = {}
        self._user_names = {}
        try:
            self._validate_source()
            access_token = self._application_access_token()
            records = {}
            pending = [self._department_id(value) for value in self.source.root_department_ids]
            visited = set()
            while pending:
                department_id = pending.pop(0)
                if department_id in visited:
                    continue
                visited.add(department_id)
                self._read_department_people(access_token, department_id, records)
                pending.extend(child for child in self._read_child_departments(access_token, department_id)
                               if child not in visited)
            people = [
                DirectoryPerson(
                    employee_id=record['employee_id'], name=record['name'], email=record['email'],
                    phone=record['phone'],
                    department=','.join(self._reference_name(access_token, 'department', value)
                                        for value in sorted(record['departments'])),
                    leader=self._reference_name(access_token, 'user', record['leader']),
                    external_user_id=external_user_id, hire_date=record['hire_date'],
                    departure_date=record['departure_date'],
                )
                for external_user_id, record in records.items()
            ]
        except DirectoryAdapterError:
            self.last_snapshot_complete = False
            raise
        self.last_snapshot_complete = True
        return iter(people)

    def _reference_name(self, access_token, kind, identifier):
        try:
            return self._load_reference_name(access_token, kind, identifier)
        except DirectoryAdapterError:
            raise DirectoryReferenceError() from None

    def _load_reference_name(self, access_token, kind, identifier):
        if not identifier:
            return ''
        cache = self._department_names if kind == 'department' else self._user_names
        if identifier not in cache:
            payload = self._request_json(
                'post', f'https://oapi.dingtalk.com/topapi/v2/{kind}/get',
                params={'access_token': access_token},
                json={'dept_id': int(identifier)} if kind == 'department' else {'userid': identifier},
            )
            self._require_success(payload)
            detail = payload.get('result')
            if not isinstance(detail, Mapping):
                raise DirectoryPayloadError()
            cache[identifier] = self._required_text(detail.get('name'))
        return cache[identifier]

    def _validate_source(self):
        credentials = getattr(self.source, 'credentials', None)
        roots = getattr(self.source, 'root_department_ids', None)
        if getattr(self.source, 'source_type', None) != 'dingtalk':
            raise DirectoryPayloadError()
        if not getattr(self.source, 'is_enabled', False):
            raise DirectoryAdapterError()
        if not isinstance(getattr(self.source, 'source_key', None), str) or not self.source.source_key.strip():
            raise DirectoryPayloadError()
        if not isinstance(credentials, Mapping) or set(credentials) != {'app_key', 'app_secret'}:
            raise DirectoryPayloadError()
        if any(not isinstance(value, str) or not value.strip() for value in credentials.values()):
            raise DirectoryPayloadError()
        if not isinstance(roots, (list, tuple)) or not roots or any(not isinstance(item, str) or not item.strip()
                                                                    for item in roots):
            raise DirectoryPayloadError()
        for department_id in roots:
            self._department_id(department_id)

    def _application_access_token(self):
        credentials = self.source.credentials
        payload = self._request_json('get', self.TOKEN_URL, params={
            'appkey': credentials['app_key'], 'appsecret': credentials['app_secret'],
        })
        if self._success_code(payload) != 0:
            raise DirectoryAuthenticationError()
        token = payload.get('access_token')
        if not isinstance(token, str) or not token.strip():
            raise DirectoryPayloadError()
        return token

    def _read_department_people(self, access_token, department_id, records):
        cursor, seen_cursors = 0, set()
        while True:
            payload = self._request_json(
                'post', self.USERS_URL, params={'access_token': access_token},
                json={'dept_id': int(department_id), 'cursor': cursor, 'size': 100},
            )
            self._require_success(payload)
            result = payload.get('result')
            if not isinstance(result, Mapping) or not isinstance(result.get('list'), list):
                raise DirectoryPayloadError()
            for item in result['list']:
                self._merge_person_record(item, department_id, records)
            if not isinstance(result.get('has_more'), bool):
                raise DirectoryPayloadError()
            if not result['has_more']:
                return
            next_cursor = result.get('next_cursor')
            if (isinstance(next_cursor, bool) or not isinstance(next_cursor, int)
                    or next_cursor in seen_cursors):
                raise DirectoryPayloadError()
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def _read_child_departments(self, access_token, department_id):
        payload = self._request_json(
            'post', self.DEPARTMENTS_URL, params={'access_token': access_token},
            json={'dept_id': int(department_id)},
        )
        self._require_success(payload)
        result = payload.get('result')
        if not isinstance(result, list):
            raise DirectoryPayloadError()
        children = []
        for item in result:
            if not isinstance(item, Mapping):
                raise DirectoryPayloadError()
            child_id = self._department_id(item.get('dept_id'))
            children.append(child_id)
            name = self._optional_text(item.get('name'))
            if name:
                self._department_names[child_id] = name
        return children

    def _merge_person_record(self, item, current_department, records):
        if not isinstance(item, Mapping):
            raise DirectoryPayloadError()
        external_user_id = self._required_text(item.get('userid'))
        name = self._optional_text(item.get('name'))
        if name:
            self._user_names[external_user_id] = name
        employee_id = self._optional_text(item.get('job_number'))
        if not employee_id:
            self._append_skip(external_user_id)
            return
        department_ids = item.get('dept_id_list', [current_department])
        if not isinstance(department_ids, list):
            raise DirectoryPayloadError()
        departments = {self._department_id(value) for value in department_ids} or {current_department}
        details = {
            'employee_id': employee_id, 'name': self._optional_text(item.get('name')),
            'email': self._optional_text(item.get('email')),
            'phone': self._optional_text(item.get('mobile')),
            'leader': self._optional_text(item.get('manager_userid')), 'departments': departments,
            'hire_date': self._optional_text(item.get('hire_date', item.get('hired_date'))),
            'departure_date': self._optional_text(item.get('departure_date', item.get('leave_date'))),
        }
        existing = records.get(external_user_id)
        if existing is None:
            records[external_user_id] = details
            return
        if existing['employee_id'] != employee_id:
            raise DirectoryPayloadError()
        existing['departments'].update(departments)
        for field in ('name', 'email', 'phone', 'leader', 'hire_date', 'departure_date'):
            if not existing[field] and details[field]:
                existing[field] = details[field]

    def _append_skip(self, external_user_id):
        record = {'external_user_id': external_user_id, 'reason': 'missing_employee_id'}
        if record not in self._skipped_records:
            self._skipped_records.append(record)

    def _request_json(self, method, url, **kwargs):
        for attempt in range(self.max_retries + 1):
            try:
                response = getattr(self.session, method)(url, timeout=self.timeout, **kwargs)
            except requests.RequestException:
                if attempt == self.max_retries:
                    raise DirectoryAdapterError() from None
                continue
            if getattr(response, 'status_code', None) == 429:
                if attempt == self.max_retries:
                    raise DirectoryRateLimitError()
                continue
            if not isinstance(getattr(response, 'status_code', None), int):
                raise DirectoryPayloadError()
            if response.status_code in (401, 403):
                raise DirectoryAuthenticationError()
            if not 200 <= response.status_code < 300:
                raise DirectoryAdapterError()
            try:
                payload = response.json()
            except (TypeError, ValueError):
                raise DirectoryPayloadError() from None
            if not isinstance(payload, Mapping):
                raise DirectoryPayloadError()
            return payload
        raise DirectoryAdapterError()

    @staticmethod
    def _success_code(payload):
        code = payload.get('errcode')
        if isinstance(code, bool) or not isinstance(code, int):
            raise DirectoryPayloadError()
        return code

    def _require_success(self, payload):
        if self._success_code(payload) != 0:
            raise DirectoryAdapterError()

    @staticmethod
    def _department_id(value):
        """Canonical positive decimal ID; provider numbers are not text identities.

        Source roots remain a string list, while DingTalk returns numeric
        dept_id/dept_id_list values. Normalize both before queueing or merging.
        """
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise DirectoryPayloadError()
        if isinstance(value, str):
            value = value.strip()
            if not value.isascii() or not value.isdecimal():
                raise DirectoryPayloadError()
        try:
            number = int(value)
            if number <= 0:
                raise DirectoryPayloadError()
            return str(number)
        except ValueError:
            raise DirectoryPayloadError() from None

    @staticmethod
    def _optional_text(value):
        if value is None:
            return ''
        if not isinstance(value, str):
            raise DirectoryPayloadError()
        return value.strip()

    def _required_text(self, value):
        text = self._optional_text(value)
        if not text:
            raise DirectoryPayloadError()
        return text

"""Version-gated platform adapter contract."""

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import requests
from django.core.exceptions import ValidationError
from django.utils import timezone


@dataclass(frozen=True)
class AccessEvent:
    event_id: str
    occurred_at: object
    employee_number: str = ''
    person_name: str = ''
    door_name: str = ''
    direction: str = 'unknown'
    result: str = 'unknown'
    card_number: str = ''


class AccessPlatformAdapter:
    def __init__(self, source):
        self.source = source

    def fetch_events(self, *, start_at, end_at, cancelled):
        raise NotImplementedError

    def test_connection(self):
        raise NotImplementedError


_ADAPTERS = {}
V6600_V6000_COMPAT_VERSION = 'v6000-api-2.11-compat'
MAX_PAGES = 100
PAGE_SIZE = 1000


def register_adapter(platform, api_version, adapter_class):
    if not isinstance(platform, str) or not isinstance(api_version, str):
        raise TypeError('platform and api_version must be strings')
    _ADAPTERS[(platform, api_version)] = adapter_class


def build_adapter(source):
    adapter = _ADAPTERS.get((source.platform, source.api_version))
    if adapter is None:
        raise ValidationError('该平台 API 版本尚未配置受支持的门禁记录适配器。')
    return adapter(source)


class ZKTecoV6600V6000CompatibilityAdapter(AccessPlatformAdapter):
    """Explicit V6000 2.11 compatibility mode; endpoint remains instance-configured."""

    def _url(self):
        if not self.source.event_path:
            raise ValidationError('请按该 V6600 实例的北向 API 文档填写门禁事件路径。')
        base = urlsplit(self.source.base_url)
        url = urlsplit(urljoin(self.source.base_url.rstrip('/') + '/', self.source.event_path.lstrip('/')))
        if (url.scheme, url.netloc) != (base.scheme, base.netloc):
            raise ValidationError('门禁事件路径必须指向平台基础地址的同一来源。')
        return url.geturl()

    @staticmethod
    def _time(value):
        if not isinstance(value, str):
            raise ValidationError('平台事件时间格式无效。')
        try:
            parsed = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                raise ValidationError('平台事件时间格式无效。') from None
        return parsed if parsed.tzinfo else timezone.make_aware(parsed, timezone.get_current_timezone())

    @classmethod
    def _event(cls, payload):
        if not isinstance(payload, dict):
            raise ValidationError('平台事件记录格式无效。')
        event_id = str(payload.get('id') or '').strip()
        if not event_id:
            raise ValidationError('平台事件记录缺少稳定事件 ID。')
        event_name = str(payload.get('eventName') or '')
        normalized = event_name.casefold()
        return AccessEvent(
            event_id=event_id, occurred_at=cls._time(payload.get('eventTime')),
            employee_number=str(payload.get('pin') or ''), person_name=str(payload.get('name') or ''),
            door_name=str(payload.get('eventPointName') or payload.get('devName') or ''),
            direction='unknown', result='unknown',
            card_number=str(payload.get('cardNo') or ''),
        )

    def test_connection(self):
        credentials = self.source.get_credentials()
        token = credentials.get('access_token') or credentials.get('token')
        if not token:
            raise ValidationError('请保存 V6600 北向 API 的 access_token。')
        headers = {'Accept': 'application/json'}
        params = {'pageNo': 1, 'pageSize': 1}
        if self.source.authentication_mode == self.source.AuthenticationMode.QUERY_TOKEN:
            params[self.source.authentication_name] = token
        else:
            headers[self.source.authentication_name] = token
        response = requests.get(self._url(), params=params, headers=headers, timeout=(5, 30), verify=self.source.verify_ssl, allow_redirects=False)
        response.raise_for_status()
        try:
            envelope = response.json()
        except ValueError:
            raise ValidationError('平台返回的门禁事件不是 JSON。') from None
        if not isinstance(envelope, dict) or not isinstance(envelope.get('code'), int) or envelope['code'] <= 0:
            raise ValidationError('平台拒绝门禁事件查询，请检查 API 权限或配置。')
        page = envelope.get('data')
        if not isinstance(page, list):
            raise ValidationError('平台返回的门禁事件数据格式无效。')
        if page:
            self._event(page[0])

    def fetch_events(self, *, start_at, end_at, cancelled):
        credentials = self.source.get_credentials()
        token = credentials.get('access_token') or credentials.get('token')
        if not token:
            raise ValidationError('请保存 V6600 北向 API 的 access_token。')
        params = {'startDate': timezone.localtime(start_at).strftime('%Y-%m-%d %H:%M:%S'), 'endDate': timezone.localtime(end_at).strftime('%Y-%m-%d %H:%M:%S')}
        headers = {'Accept': 'application/json'}
        if self.source.authentication_mode == self.source.AuthenticationMode.QUERY_TOKEN:
            params[self.source.authentication_name] = token
        else:
            headers[self.source.authentication_name] = token
        events = []
        for page_no in range(1, MAX_PAGES + 1):
            if cancelled is not None and cancelled.is_set():
                raise ValidationError('门禁采集已取消。')
            response = requests.get(self._url(), params={**params, 'pageNo': page_no, 'pageSize': PAGE_SIZE}, headers=headers, timeout=(5, 30), verify=self.source.verify_ssl, allow_redirects=False)
            response.raise_for_status()
            try:
                envelope = response.json()
            except ValueError:
                raise ValidationError('平台返回的门禁事件不是 JSON。') from None
            if not isinstance(envelope, dict) or not isinstance(envelope.get('code'), int):
                raise ValidationError('平台返回的门禁事件响应格式无效。')
            if envelope['code'] <= 0:
                raise ValidationError('平台拒绝门禁事件查询，请检查 API 权限或时间范围。')
            page = envelope.get('data')
            if not isinstance(page, list):
                raise ValidationError('平台返回的门禁事件数据格式无效。')
            if len(page) > PAGE_SIZE or len(events) + len(page) > 10000:
                raise ValidationError('Access event limit exceeded; narrow the time range.')
            events.extend(self._event(item) for item in page)
            if len(page) < PAGE_SIZE:
                return events
        raise ValidationError('门禁事件分页超过安全上限，请缩小采集时间范围。')


register_adapter('zkteco_v6600', V6600_V6000_COMPAT_VERSION, ZKTecoV6600V6000CompatibilityAdapter)

from importlib import import_module
from types import SimpleNamespace

from django.test import SimpleTestCase

from net.people.directory import (
    DirectoryAdapterError,
    DirectoryAuthenticationError,
    DirectoryPayloadError,
    DirectoryRateLimitError,
)


def _adapter(module_name, class_name):
    try:
        return getattr(import_module(module_name), class_name)
    except (AttributeError, ModuleNotFoundError):
        return None


class DirectoryAdapterAvailabilityTests(SimpleTestCase):
    def test_feishu_and_dingtalk_adapters_are_read_only_directory_adapters(self):
        FeishuDirectoryAdapter = _adapter(
            'net.people.directory.feishu', 'FeishuDirectoryAdapter'
        )
        DingTalkDirectoryAdapter = _adapter(
            'net.people.directory.dingtalk', 'DingTalkDirectoryAdapter'
        )

        self.assertIsNotNone(FeishuDirectoryAdapter)
        self.assertIsNotNone(DingTalkDirectoryAdapter)
        self.assertTrue(hasattr(FeishuDirectoryAdapter, 'test_connection'))
        self.assertTrue(hasattr(FeishuDirectoryAdapter, 'iter_people'))
        self.assertTrue(hasattr(DingTalkDirectoryAdapter, 'test_connection'))
        self.assertTrue(hasattr(DingTalkDirectoryAdapter, 'iter_people'))

    def test_adapters_expose_a_captured_nonsecret_fetch_configuration_identity(self):
        FeishuDirectoryAdapter = _adapter(
            'net.people.directory.feishu', 'FeishuDirectoryAdapter'
        )
        DingTalkDirectoryAdapter = _adapter(
            'net.people.directory.dingtalk', 'DingTalkDirectoryAdapter'
        )
        for provider, adapter_class in (('feishu', FeishuDirectoryAdapter),
                                        ('dingtalk', DingTalkDirectoryAdapter)):
            with self.subTest(provider=provider):
                adapter = adapter_class(_source(provider), session=RoutingSession(None))
                identity = getattr(adapter, 'fetch_configuration_identity', None)
                self.assertIsInstance(identity, dict)
                self.assertEqual(identity.get('source_key'), f'{provider}-fixture')
                self.assertIn('digest', identity)
                self.assertNotIn('fixture-secret', repr(identity))

    def test_adapters_fetch_only_construction_time_inputs_after_caller_source_mutation(self):
        for provider in ('feishu', 'dingtalk'):
            with self.subTest(provider=provider):
                source = _source(provider)
                seen = []

                def handler(method, url, kwargs):
                    if provider == 'feishu':
                        if url.endswith('/tenant_access_token/internal'):
                            seen.append(kwargs['json'])
                            return FixtureResponse({'code': 0, 'tenant_access_token': 'tenant-token'})
                        if url.endswith('/users/find_by_department'):
                            seen.append(kwargs['params']['department_id'])
                            return FixtureResponse({'code': 0, 'data': {
                                'items': [{'employee_no': 'EMP-FROZEN', 'open_id': 'ou-frozen'}],
                                'has_more': False, 'page_token': '',
                            }})
                        if '/children' in url:
                            return FixtureResponse({'code': 0, 'data': {
                                'items': [], 'has_more': False, 'page_token': '',
                            }})
                    else:
                        if url.endswith('/gettoken'):
                            seen.append(kwargs['params'])
                            return FixtureResponse({'errcode': 0, 'access_token': 'ding-token'})
                        if url.endswith('/topapi/v2/user/list'):
                            seen.append(kwargs['json']['dept_id'])
                            return FixtureResponse({'errcode': 0, 'result': {
                                'list': [{'job_number': 'EMP-FROZEN', 'userid': 'user-frozen'}],
                                'has_more': False,
                            }})
                        if url.endswith('/topapi/v2/department/listsub'):
                            return FixtureResponse({'errcode': 0, 'result': []})
                    self.fail(f'unexpected fixture request: {method} {url}')

                adapter_class = _adapter(
                    f'net.people.directory.{provider}',
                    'FeishuDirectoryAdapter' if provider == 'feishu' else 'DingTalkDirectoryAdapter',
                )
                adapter = adapter_class(source, session=RoutingSession(handler))
                source.source_key = f'{provider}-mutated'
                source.root_department_ids[:] = ['changed-root' if provider == 'feishu' else '2']
                source.credentials['app_secret'] = 'mutated-secret'
                source.is_enabled = False

                people = list(adapter.iter_people())

                self.assertEqual([person.employee_id for person in people], ['EMP-FROZEN'])
                self.assertEqual(adapter.source_key, f'{provider}-fixture')
                self.assertNotIn('mutated-secret', repr(adapter))
                if provider == 'feishu':
                    self.assertEqual(seen, [
                        {'app_id': 'cli-fixture', 'app_secret': 'fixture-secret'}, 'dept-root',
                    ])
                else:
                    self.assertEqual(seen, [
                        {'appkey': 'ding-fixture', 'appsecret': 'fixture-secret'}, 1,
                    ])


class FixtureResponse:
    def __init__(self, payload=None, status_code=200, json_error=False):
        self.payload = payload
        self.status_code = status_code
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise ValueError('not json')
        return self.payload


class RoutingSession:
    """A tiny, local HTTP fixture boundary; it never reaches a tenant."""

    def __init__(self, handler, *, reference_fixtures=True):
        self.handler = handler
        self.calls = []
        self.reference_fixtures = reference_fixtures

    def reference_response(self, url, kwargs):
        if not self.reference_fixtures:
            return None
        names = {'dept-root': '总部', 'dept-child': '研发部', 'od-child': '研发部',
                 '0': '总部', '1': '总部', '2': '研发部', '3': '运维部'}
        prefix = 'https://open.feishu.cn/open-apis/contact/v3/departments/'
        if url.startswith(prefix) and '/' not in url[len(prefix):]:
            return FixtureResponse({'code': 0, 'data': {'department': {'name': names[url[len(prefix):]]}}})
        if url == 'https://oapi.dingtalk.com/topapi/v2/department/get':
            return FixtureResponse({'errcode': 0, 'result': {'name': names[str(kwargs['json']['dept_id'])]}})
        if url.startswith('https://open.feishu.cn/open-apis/contact/v3/users/') and not url.endswith('/find_by_department'):
            return FixtureResponse({'code': 0, 'data': {'user': {'name': '负责人'}}})
        if url == 'https://oapi.dingtalk.com/topapi/v2/user/get':
            return FixtureResponse({'errcode': 0, 'result': {'name': '负责人'}})
        return None

    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs))
        return self.reference_response(url, kwargs) or self.handler('GET', url, kwargs)

    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs))
        return self.reference_response(url, kwargs) or self.handler('POST', url, kwargs)


def _source(provider, **overrides):
    credentials = {
        'feishu': {'app_id': 'cli-fixture', 'app_secret': 'fixture-secret'},
        'dingtalk': {'app_key': 'ding-fixture', 'app_secret': 'fixture-secret'},
    }[provider]
    values = {
        'source_type': provider,
        'source_key': f'{provider}-fixture',
        'credentials': credentials,
        'root_department_ids': ['1'] if provider == 'dingtalk' else ['dept-root'],
        'is_enabled': True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FeishuDirectoryAdapterTests(SimpleTestCase):
    def test_feishu_connection_checks_root_people_and_department_endpoints(self):
        from net.people.directory.feishu import FeishuDirectoryAdapter

        calls = []

        def handler(method, url, kwargs):
            if url.endswith('/tenant_access_token/internal'):
                calls.append('token')
                return FixtureResponse({'code': 0, 'tenant_access_token': 'tenant-token'})
            if url.endswith('/users/find_by_department'):
                calls.append('people')
                return FixtureResponse({'code': 0, 'data': {'has_more': False}})
            if url.endswith('/departments/0/children'):
                calls.append('departments')
                return FixtureResponse({'code': 0, 'data': {'has_more': False}})
            self.fail(f'unexpected fixture request: {method} {url}')

        adapter = FeishuDirectoryAdapter(
            _source('feishu', root_department_ids=[]),
            session=RoutingSession(handler),
        )

        adapter.test_connection()

        self.assertEqual(calls, ['token', 'people', 'departments'])

    def test_feishu_prefers_open_department_id_for_child_traversal(self):
        from net.people.directory.feishu import FeishuDirectoryAdapter

        visited = []

        def handler(method, url, kwargs):
            if url.endswith('/tenant_access_token/internal'):
                return FixtureResponse({'code': 0, 'tenant_access_token': 'tenant-token'})
            if url.endswith('/users/find_by_department'):
                department_id = kwargs['params']['department_id']
                visited.append(department_id)
                items = ([{'employee_no': 'EMP-OPEN', 'open_id': 'ou-open'}]
                         if department_id == 'od-child' else [])
                return FixtureResponse({'code': 0, 'data': {
                    'items': items, 'has_more': False,
                }})
            if url.endswith('/departments/0/children'):
                return FixtureResponse({'code': 0, 'data': {
                    'items': [{
                        'department_id': 'custom-child',
                        'open_department_id': 'od-child',
                    }],
                    'has_more': False,
                }})
            if url.endswith('/departments/od-child/children'):
                return FixtureResponse({'code': 0, 'data': {'has_more': False}})
            self.fail(f'unexpected fixture request: {method} {url}')

        people = list(FeishuDirectoryAdapter(
            _source('feishu', root_department_ids=[]),
            session=RoutingSession(handler),
        ).iter_people())

        self.assertEqual(visited, ['0', 'od-child'])
        self.assertEqual([person.employee_id for person in people], ['EMP-OPEN'])

    def test_feishu_treats_omitted_items_as_empty_on_successful_pages(self):
        from net.people.directory.feishu import FeishuDirectoryAdapter

        def handler(method, url, kwargs):
            if url.endswith('/tenant_access_token/internal'):
                return FixtureResponse({'code': 0, 'tenant_access_token': 'tenant-token'})
            if url.endswith('/users/find_by_department'):
                department_id = kwargs['params']['department_id']
                if department_id == '0':
                    return FixtureResponse({'code': 0, 'data': {'has_more': False}})
                return FixtureResponse({'code': 0, 'data': {
                    'items': [{'employee_no': 'EMP-CHILD', 'name': '子部门人员',
                               'open_id': 'ou-child'}],
                    'has_more': False,
                }})
            if url.endswith('/departments/0/children'):
                return FixtureResponse({'code': 0, 'data': {
                    'items': [{'department_id': 'dept-child'}],
                    'has_more': False,
                }})
            if url.endswith('/departments/dept-child/children'):
                return FixtureResponse({'code': 0, 'data': {'has_more': False}})
            self.fail(f'unexpected fixture request: {method} {url}')

        people = list(FeishuDirectoryAdapter(
            _source('feishu', root_department_ids=[]),
            session=RoutingSession(handler),
        ).iter_people())

        self.assertEqual([person.employee_id for person in people], ['EMP-CHILD'])

    def test_feishu_uses_tenant_root_when_no_root_department_is_configured(self):
        from net.people.directory.feishu import FeishuDirectoryAdapter

        visited_departments = []

        def handler(method, url, kwargs):
            if url.endswith('/tenant_access_token/internal'):
                return FixtureResponse({'code': 0, 'tenant_access_token': 'tenant-token'})
            if url.endswith('/users/find_by_department'):
                visited_departments.append(kwargs['params']['department_id'])
                return FixtureResponse({'code': 0, 'data': {
                    'items': [{'employee_no': 'EMP-ROOT', 'name': '根部门人员',
                               'open_id': 'ou-root'}],
                    'has_more': False, 'page_token': '',
                }})
            if url.endswith('/departments/0/children'):
                return FixtureResponse({'code': 0, 'data': {
                    'items': [], 'has_more': False, 'page_token': '',
                }})
            self.fail(f'unexpected fixture request: {method} {url}')

        people = list(FeishuDirectoryAdapter(
            _source('feishu', root_department_ids=[]),
            session=RoutingSession(handler),
        ).iter_people())

        self.assertEqual(visited_departments, ['0'])
        self.assertEqual([person.employee_id for person in people], ['EMP-ROOT'])

    def test_feishu_traverses_departments_pages_and_merges_one_user_context(self):
        from net.people.directory.feishu import FeishuDirectoryAdapter

        def handler(method, url, kwargs):
            if url.endswith('/tenant_access_token/internal'):
                return FixtureResponse({'code': 0, 'tenant_access_token': 'tenant-token'})
            if url.endswith('/users/find_by_department'):
                params = kwargs['params']
                department_id = params['department_id']
                page_token = params.get('page_token')
                if department_id == 'dept-root' and not page_token:
                    return FixtureResponse({'code': 0, 'data': {
                        'items': [{'employee_no': 'EMP-1', 'name': ' 王工 ', 'email': 'wang@example.test',
                                   'open_id': 'ou-user', 'department_ids': ['dept-root']}],
                        'has_more': True, 'page_token': 'root-next',
                    }})
                if department_id == 'dept-root' and page_token == 'root-next':
                    return FixtureResponse({'code': 0, 'data': {
                        'items': [], 'has_more': False, 'page_token': '',
                    }})
                if department_id == 'dept-child':
                    return FixtureResponse({'code': 0, 'data': {
                        'items': [
                            {'employee_no': 'EMP-1', 'name': '王工', 'open_id': 'ou-user',
                             'department_ids': ['dept-child']},
                            {'name': '未编号', 'open_id': 'ou-missing', 'department_ids': ['dept-child']},
                        ],
                        'has_more': False, 'page_token': '',
                    }})
            if url.endswith('/departments/dept-root/children'):
                return FixtureResponse({'code': 0, 'data': {
                    'items': [{'department_id': 'dept-child'}], 'has_more': False, 'page_token': '',
                }})
            if url.endswith('/departments/dept-child/children'):
                return FixtureResponse({'code': 0, 'data': {
                    'items': [], 'has_more': False, 'page_token': '',
                }})
            self.fail(f'unexpected fixture request: {method} {url} {kwargs!r}')

        adapter = FeishuDirectoryAdapter(_source('feishu'), session=RoutingSession(handler))
        people = list(adapter.iter_people())

        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].employee_id, 'EMP-1')
        self.assertEqual(people[0].name, '王工')
        self.assertEqual(people[0].department, '研发部,总部')
        self.assertEqual(people[0].external_user_id, 'ou-user')
        self.assertEqual(adapter.skipped_records, ({'external_user_id': 'ou-missing',
                                                    'reason': 'missing_employee_id'},))
        self.assertTrue(adapter.last_snapshot_complete)

    def test_feishu_keeps_different_platform_users_with_one_employee_number_visible(self):
        from net.people.directory.feishu import FeishuDirectoryAdapter

        def handler(method, url, kwargs):
            if url.endswith('/tenant_access_token/internal'):
                return FixtureResponse({'code': 0, 'tenant_access_token': 'tenant-token'})
            if url.endswith('/users/find_by_department'):
                return FixtureResponse({'code': 0, 'data': {'items': [
                    {'employee_no': 'EMP-CONFLICT', 'name': '甲', 'open_id': 'ou-a'},
                    {'employee_no': 'EMP-CONFLICT', 'name': '乙', 'open_id': 'ou-b'},
                ], 'has_more': False, 'page_token': ''}})
            if '/children' in url:
                return FixtureResponse({'code': 0, 'data': {
                    'items': [], 'has_more': False, 'page_token': '',
                }})
            self.fail(f'unexpected fixture request: {method} {url}')

        people = list(FeishuDirectoryAdapter(_source('feishu'), session=RoutingSession(handler)).iter_people())

        self.assertEqual(
            [(person.employee_id, person.external_user_id) for person in people],
            [('EMP-CONFLICT', 'ou-a'), ('EMP-CONFLICT', 'ou-b')],
        )

    def test_feishu_rejects_partial_or_cyclic_pagination_without_a_snapshot(self):
        from net.people.directory.feishu import FeishuDirectoryAdapter

        calls = 0

        def handler(method, url, kwargs):
            nonlocal calls
            if url.endswith('/tenant_access_token/internal'):
                return FixtureResponse({'code': 0, 'tenant_access_token': 'tenant-token'})
            if url.endswith('/users/find_by_department'):
                calls += 1
                return FixtureResponse({'code': 0, 'data': {
                    'items': [{'employee_no': 'EMP-1', 'open_id': 'ou-1'}],
                    'has_more': True, 'page_token': 'cycle',
                }})
            self.fail(f'unexpected fixture request: {method} {url}')

        adapter = FeishuDirectoryAdapter(_source('feishu'), session=RoutingSession(handler))
        with self.assertRaises(DirectoryPayloadError):
            list(adapter.iter_people())

        self.assertEqual(calls, 2)
        self.assertFalse(adapter.last_snapshot_complete)


class DingTalkDirectoryAdapterTests(SimpleTestCase):
    def test_dingtalk_traverses_departments_cursor_pages_and_merges_context(self):
        from net.people.directory.dingtalk import DingTalkDirectoryAdapter

        def handler(method, url, kwargs):
            if url.endswith('/gettoken'):
                return FixtureResponse({'errcode': 0, 'access_token': 'ding-token'})
            if url.endswith('/topapi/v2/user/list'):
                body = kwargs['json']
                department_id, cursor = body['dept_id'], body['cursor']
                self.assertIs(type(department_id), int)
                if department_id == 1 and cursor == 0:
                    return FixtureResponse({'errcode': 0, 'result': {
                        'list': [{'job_number': 'D-1', 'name': '丁工', 'userid': 'user-1',
                                  'email': 'ding@example.test', 'dept_id_list': [1, 2]}],
                        'has_more': True, 'next_cursor': 5,
                    }})
                if department_id == 1 and cursor == 5:
                    return FixtureResponse({'errcode': 0, 'result': {
                        'list': [], 'has_more': False, 'next_cursor': 0,
                    }})
                if department_id == 2 and cursor == 0:
                    return FixtureResponse({'errcode': 0, 'result': {
                        'list': [
                            {'job_number': 'D-1', 'name': '丁工', 'userid': 'user-1',
                             'dept_id_list': [2]},
                            {'name': '未编号', 'userid': 'user-missing'},
                        ],
                        'has_more': False, 'next_cursor': 0,
                    }})
                if department_id == 3 and cursor == 0:
                    return FixtureResponse({'errcode': 0, 'result': {
                        'list': [{'job_number': 'D-1', 'userid': 'user-1', 'dept_id_list': [3]}],
                        'has_more': False, 'next_cursor': 0,
                    }})
            if url.endswith('/topapi/v2/department/listsub'):
                department_id = kwargs['json']['dept_id']
                self.assertIs(type(department_id), int)
                items = {1: [{'dept_id': 2}], 2: [{'dept_id': 3}], 3: []}[department_id]
                return FixtureResponse({'errcode': 0, 'result': items})
            self.fail(f'unexpected fixture request: {method} {url} {kwargs!r}')

        adapter = DingTalkDirectoryAdapter(_source('dingtalk'), session=RoutingSession(handler))
        people = list(adapter.iter_people())

        self.assertEqual([(person.employee_id, person.department, person.external_user_id) for person in people],
                         [('D-1', '总部,研发部,运维部', 'user-1')])
        self.assertEqual(adapter.skipped_records, ({'external_user_id': 'user-missing',
                                                    'reason': 'missing_employee_id'},))
        self.assertTrue(adapter.last_snapshot_complete)

    def test_dingtalk_canonicalizes_numeric_strings_before_merging_and_traversing(self):
        from net.people.directory.dingtalk import DingTalkDirectoryAdapter

        visits = []

        def handler(method, url, kwargs):
            if url.endswith('/gettoken'):
                return FixtureResponse({'errcode': 0, 'access_token': 'ding-token'})
            department_id = kwargs['json']['dept_id']
            self.assertIs(type(department_id), int)
            if url.endswith('/topapi/v2/user/list'):
                visits.append(department_id)
                return FixtureResponse({'errcode': 0, 'result': {
                    'list': [{'job_number': '001', 'userid': '0007',
                              'dept_id_list': [1, '01', ' 002 ', 2]}],
                    'has_more': False,
                }})
            return FixtureResponse({'errcode': 0, 'result':
                                    [{'dept_id': 2}, {'dept_id': '002'}] if department_id == 1 else []})

        adapter = DingTalkDirectoryAdapter(
            _source('dingtalk', root_department_ids=[' 001 ', '01']), session=RoutingSession(handler)
        )
        people = list(adapter.iter_people())
        self.assertEqual(visits, [1, 2])
        self.assertEqual([(p.employee_id, p.external_user_id, p.department) for p in people],
                         [('001', '0007', '总部,研发部')])
        self.assertTrue(adapter.last_snapshot_complete)

    def test_dingtalk_rejects_malformed_department_ids_without_returning_partial_people(self):
        from net.people.directory.dingtalk import DingTalkDirectoryAdapter

        invalid_ids = (None, True, False, 1.0, 1.5, float('inf'), [], {}, '', ' ',
                       'dept-2', '1.0', '+2', '-2', '1e2', '２', 0, -2, '0')
        for location in ('root', 'membership', 'child'):
            for invalid_id in invalid_ids:
                with self.subTest(location=location, invalid_id=invalid_id):
                    def handler(method, url, kwargs):
                        if url.endswith('/gettoken'):
                            return FixtureResponse({'errcode': 0, 'access_token': 'ding-token'})
                        if url.endswith('/topapi/v2/user/list'):
                            users = [{'job_number': 'D-1', 'userid': 'user-1'}]
                            if location == 'membership':
                                users.append({'job_number': 'D-2', 'userid': 'user-2',
                                              'dept_id_list': [invalid_id]})
                            return FixtureResponse({'errcode': 0, 'result': {
                                'list': users, 'has_more': False,
                            }})
                        return FixtureResponse({'errcode': 0, 'result':
                                                [{'dept_id': invalid_id}] if location == 'child' else []})

                    source = _source('dingtalk', root_department_ids=[invalid_id] if location == 'root' else ['1'])
                    adapter = DingTalkDirectoryAdapter(source, session=RoutingSession(handler))
                    with self.assertRaises(DirectoryPayloadError):
                        adapter.iter_people()
                    self.assertFalse(adapter.last_snapshot_complete)

    def test_dingtalk_department_normalization_does_not_coerce_employee_or_user_ids(self):
        from net.people.directory.dingtalk import DingTalkDirectoryAdapter

        for field in ('job_number', 'userid'):
            for value in (7, True, 1.5, ['7']):
                with self.subTest(field=field, value=value):
                    def handler(method, url, kwargs):
                        if url.endswith('/gettoken'):
                            return FixtureResponse({'errcode': 0, 'access_token': 'ding-token'})
                        if url.endswith('/topapi/v2/user/list'):
                            user = {'job_number': 'D-1', 'userid': 'user-1', 'dept_id_list': [1]}
                            user[field] = value
                            return FixtureResponse({'errcode': 0, 'result': {
                                'list': [user], 'has_more': False,
                            }})
                        return FixtureResponse({'errcode': 0, 'result': []})

                    adapter = DingTalkDirectoryAdapter(_source('dingtalk'), session=RoutingSession(handler))
                    with self.assertRaises(DirectoryPayloadError):
                        adapter.iter_people()
                    self.assertFalse(adapter.last_snapshot_complete)


class DirectoryAdapterFailureTests(SimpleTestCase):
    def test_disabled_source_never_returns_an_apparently_complete_empty_snapshot(self):
        from net.people.directory.feishu import FeishuDirectoryAdapter

        adapter = FeishuDirectoryAdapter(_source('feishu', is_enabled=False), session=RoutingSession(None))
        with self.assertRaises(DirectoryAdapterError):
            list(adapter.iter_people())

        self.assertFalse(adapter.last_snapshot_complete)

    def test_authentication_rate_limit_timeout_and_malformed_payload_are_safe_failures(self):
        from requests import Timeout
        from net.people.directory.dingtalk import DingTalkDirectoryAdapter
        from net.people.directory.feishu import FeishuDirectoryAdapter

        def feishu_auth_failure(method, url, kwargs):
            return FixtureResponse({'code': 99991663, 'msg': 'fixture-secret must stay private'})

        auth_adapter = FeishuDirectoryAdapter(
            _source('feishu'), session=RoutingSession(feishu_auth_failure)
        )
        with self.assertRaises(DirectoryAuthenticationError) as auth_error:
            auth_adapter.test_connection()
        self.assertNotIn('fixture-secret', repr(auth_error.exception))

        rate_calls = 0

        def rate_limited(method, url, kwargs):
            nonlocal rate_calls
            rate_calls += 1
            return FixtureResponse(status_code=429)

        rate_adapter = DingTalkDirectoryAdapter(
            _source('dingtalk'), session=RoutingSession(rate_limited), max_retries=1
        )
        with self.assertRaises(DirectoryRateLimitError):
            rate_adapter.test_connection()
        self.assertEqual(rate_calls, 2)

        def timeout(method, url, kwargs):
            raise Timeout('fixture-secret tenant token')

        timeout_adapter = FeishuDirectoryAdapter(_source('feishu'), session=RoutingSession(timeout))
        with self.assertRaises(DirectoryAdapterError) as timeout_error:
            list(timeout_adapter.iter_people())
        self.assertNotIn('fixture-secret', repr(timeout_error.exception))
        self.assertFalse(timeout_adapter.last_snapshot_complete)

        def malformed(method, url, kwargs):
            return FixtureResponse({'code': 0, 'data': {}})

        malformed_adapter = FeishuDirectoryAdapter(_source('feishu'), session=RoutingSession(malformed))
        with self.assertRaises(DirectoryPayloadError):
            list(malformed_adapter.iter_people())
        self.assertFalse(malformed_adapter.last_snapshot_complete)

    def test_missing_employee_number_without_a_safe_platform_identity_fails_complete_read(self):
        from net.people.directory.feishu import FeishuDirectoryAdapter

        def handler(method, url, kwargs):
            if url.endswith('/tenant_access_token/internal'):
                return FixtureResponse({'code': 0, 'tenant_access_token': 'tenant-token'})
            if url.endswith('/users/find_by_department'):
                return FixtureResponse({'code': 0, 'data': {'items': [
                    {'name': 'identity missing'},
                ], 'has_more': False, 'page_token': ''}})
            if '/children' in url:
                return FixtureResponse({'code': 0, 'data': {
                    'items': [], 'has_more': False, 'page_token': '',
                }})
            self.fail(f'unexpected fixture request: {method} {url}')

        adapter = FeishuDirectoryAdapter(_source('feishu'), session=RoutingSession(handler))
        with self.assertRaises(DirectoryPayloadError):
            list(adapter.iter_people())
        self.assertFalse(adapter.last_snapshot_complete)

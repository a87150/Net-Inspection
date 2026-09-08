from django.test import SimpleTestCase

from net.people.directory import DirectoryAdapterError
from net.people.directory.feishu import FeishuDirectoryAdapter
from net.people.directory.dingtalk import DingTalkDirectoryAdapter
from tests.people.test_directory_adapters import _source, RoutingSession, FixtureResponse


class ReferenceNameTests(SimpleTestCase):
    def adapter(self, provider, missing=False):
        def handler(method, url, kwargs):
            if provider == 'feishu':
                if url.endswith('/internal'):
                    payload = {'code': 0, 'tenant_access_token': 'fixture'}
                elif url.endswith('/find_by_department'):
                    payload = {'code': 0, 'data': {'items': [
                        {'open_id': 'u1', 'employee_no': 'E1', 'name': '员工', 'leader_user_id': 'boss'},
                        {'open_id': 'u2', 'employee_no': 'E2', 'name': '员工二', 'leader_user_id': 'boss'},
                    ], 'has_more': False}}
                elif url.endswith('/children'):
                    payload = {'code': 0, 'data': {'items': [], 'has_more': False}}
                elif '/departments/' in url:
                    self.assertEqual(kwargs['params']['department_id_type'], 'open_department_id')
                    payload = {'code': 0, 'data': {'department': {'name': '' if missing else '研发部'}}}
                else:
                    self.assertTrue(url.endswith('/users/boss'))
                    self.assertEqual(kwargs['params']['user_id_type'], 'open_id')
                    payload = {'code': 0, 'data': {'user': {'name': '负责人'}}}
            else:
                if url.endswith('/gettoken'):
                    payload = {'errcode': 0, 'access_token': 'fixture'}
                elif url.endswith('/user/list'):
                    payload = {'errcode': 0, 'result': {'list': [
                        {'userid': 'u1', 'job_number': 'E1', 'name': '员工', 'manager_userid': 'boss'},
                        {'userid': 'u2', 'job_number': 'E2', 'name': '员工二', 'manager_userid': 'boss'},
                    ], 'has_more': False}}
                elif url.endswith('/listsub'):
                    payload = {'errcode': 0, 'result': []}
                elif url.endswith('/department/get'):
                    self.assertEqual(kwargs['json']['dept_id'], 1)
                    payload = {'errcode': 0, 'result': {'name': '' if missing else '研发部'}}
                else:
                    self.assertTrue(url.endswith('/user/get'))
                    self.assertEqual(kwargs['json']['userid'], 'boss')
                    payload = {'errcode': 0, 'result': {'name': '负责人'}}
            return FixtureResponse(payload)
        session = RoutingSession(handler, reference_fixtures=False)
        cls = FeishuDirectoryAdapter if provider == 'feishu' else DingTalkDirectoryAdapter
        return cls(_source(provider), session=session), session

    def test_ids_resolve_to_names_and_repeated_references_are_cached(self):
        for provider in ('feishu', 'dingtalk'):
            with self.subTest(provider=provider):
                adapter, session = self.adapter(provider)
                people = list(adapter.iter_people())
                self.assertEqual([(p.department, p.leader) for p in people],
                                 [('研发部', '负责人'), ('研发部', '负责人')])
                self.assertEqual(len(session.calls), 5)
                self.assertTrue(adapter.last_snapshot_complete)

    def test_missing_name_fails_the_snapshot_instead_of_returning_ids(self):
        for provider in ('feishu', 'dingtalk'):
            with self.subTest(provider=provider):
                adapter, _ = self.adapter(provider, missing=True)
                with self.assertRaises(DirectoryAdapterError):
                    list(adapter.iter_people())
                self.assertFalse(adapter.last_snapshot_complete)

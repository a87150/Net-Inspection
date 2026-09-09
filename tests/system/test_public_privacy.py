"""Guest disclosure contracts; all fixtures live in Django's isolated test DB."""
from importlib import import_module

from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from net.models import (Computer, ComputerLogFile, Network_Device, People,
                        SecurityDevice, Server, Server_Inspection, TaskRun)


class PublicPrivacyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        People.objects.create(name='Alice Public', department='Engineering',
                              employee_id='SECRET-EMP', email='SECRET-EMAIL',
                              leader='SECRET-LEADER', phone='SECRET-PHONE')
        People.objects.create(name='Bob Public', department='Operations', is_active=False)
        computer = Computer.objects.create(computer_name='Public PC', ip_addresses='192.0.2.1',
                                os='SECRET-OS', mac_addresses='SECRET-MAC',
                                cpu_model='SECRET-HARDWARE', login_account='SECRET-LOGIN')
        Network_Device.objects.create(device_name='Public Switch', ip='192.0.2.2',
                                      device_type='Switch', password='SECRET-PASSWORD',
                                      snmp_community='SECRET-COMMUNITY')
        server = Server.objects.create(name='Public Server', ip='192.0.2.3',
                              api_token='SECRET-TOKEN', os='SECRET-SERVER-OS')
        SecurityDevice.objects.create(device_name='Public Camera', ip='192.0.2.4',
                                      api_password='SECRET-CAMERA')
        ComputerLogFile.objects.create(computer=computer, source_path='SECRET-PATH',
                                       modified_at=timezone.now(), collected_date=timezone.localdate(),
                                       content_hash='guest-fixture',
                                       import_status='imported', payload={'raw': 'SECRET-RAWLOG'})
        Server_Inspection.objects.create(server=server, summary='SECRET-RECORD',
                                         raw_output={'raw': 'SECRET-RAWOUTPUT'})
        TaskRun.objects.create(task_type='domain_sync', scope_key='guest-fixture',
                               error_summary='SECRET-TASK',
                               parameters_snapshot={'secret': 'SECRET-PARAMETER'})

    def response(self, view='asset_list', kwargs=None, params=None):
        module = import_module('index.common.public_views')
        request = RequestFactory().get('/', params or {})
        request.user = AnonymousUser()
        response = module.public_response(request, view, kwargs or {})
        response.render()
        return response

    def test_lists_only_pass_allowlisted_dictionary_rows(self):
        expected = {
            'people': {'name', 'department', 'is_active'},
            'computers': {'name', 'ip', 'type', 'manufacturer'},
            'networks': {'name', 'ip', 'type', 'manufacturer'},
            'servers': {'name', 'ip', 'type', 'manufacturer'},
            'monitors': {'name', 'ip', 'type', 'manufacturer'},
        }
        for kind, fields in expected.items():
            with self.subTest(kind=kind):
                response = self.response(kwargs={'kind': kind})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b'SECRET-', response.content)
                for row in response.context_data['page_obj']:
                    self.assertIs(type(row), dict)
                    self.assertEqual(set(row), fields)

    def test_private_parameters_do_not_change_rows_counts_options_or_links(self):
        attack = {'filter_employee_id': 'SECRET-EMP', 'filter_email': 'SECRET-EMAIL',
                  'filter_phone': 'SECRET-PHONE', 'filter_password': 'SECRET-PASSWORD',
                  'filter_os': 'SECRET-OS', 'filter_mac_addresses': 'SECRET-MAC',
                  'sort': 'SECRET-SORT', 'order': 'SECRET-ORDER',
                  'columns': 'SECRET-COLUMNS', 'q_private': 'SECRET-QUERY',
                  'page': 'SECRET-PAGE', 'page_size': 'SECRET-SIZE'}
        for kind in ('people', 'computers', 'networks', 'servers', 'monitors'):
            with self.subTest(kind=kind):
                baseline = self.response(kwargs={'kind': kind})
                attacked = self.response(kwargs={'kind': kind}, params=attack)
                self.assertEqual(baseline.content, attacked.content)
                self.assertEqual(list(baseline.context_data['page_obj']),
                                 list(attacked.context_data['page_obj']))

    def test_public_name_search_filter_sort_and_pagination(self):
        response = self.response(kwargs={'kind': 'people'}, params={'filter_name': 'Alice'})
        self.assertEqual(response.context_data['page_obj'].paginator.count, 1)
        self.assertContains(response, 'Alice Public')
        self.assertNotContains(response, 'Bob Public')
        response = self.response(kwargs={'kind': 'people'}, params={'sort': 'name', 'order': 'desc'})
        self.assertEqual(response.context_data['page_obj'][0]['name'], 'Bob Public')
        response = self.response(kwargs={'kind': 'people'}, params={'q': 'SECRET-EMP'})
        self.assertEqual(response.context_data['page_obj'].paginator.count, 0)
        People.objects.bulk_create([People(name=f'Person {n:03}') for n in range(55)])
        for size in (20, 50, 100, 200, 500):
            response = self.response(kwargs={'kind': 'people'}, params={'page_size': size})
            self.assertEqual(response.context_data['page_obj'].paginator.per_page, size)
        response = self.response(kwargs={'kind': 'people'}, params={'page_size': 20, 'page': 2})
        self.assertEqual(response.context_data['page_obj'].number, 2)
        self.assertEqual(len(response.context_data['page_obj']), 20)

    def test_summaries_have_only_aggregate_public_data(self):
        for view in ('index', 'people_statistics'):
            response = self.response(view)
            self.assertNotIn(b'SECRET-', response.content)
            self.assertNotContains(response, 'Alice Public')
            self.assertEqual(response.context_data['people_summary'],
                             {'total': 2, 'active': 1, 'inactive': 1})

    def test_unknown_routes_and_private_legacy_items_are_404(self):
        for view, kwargs in [('asset_list', {'kind': 'unknown'}),
                             ('item_list', {'item': 'accounts'}),
                             ('item_list', {'item': 'tasks'}), ('record_list', {})]:
            with self.subTest(view=view, kwargs=kwargs), self.assertRaises(Http404):
                self.response(view, kwargs)
        response = self.response('item_list', {'item': 'people'})
        self.assertContains(response, 'Alice Public')

    def test_http_guest_dispatch(self):
        for view, kwargs in [('index', {}), ('people_statistics', {})] + [
            (view, {key: kind}) for view, key in [('asset_list', 'kind'), ('item_list', 'item')]
            for kind in ('people', 'computers', 'networks', 'servers', 'monitors')
        ]:
            with self.subTest(view=view, kwargs=kwargs):
                response = self.client.get(reverse(view, kwargs=kwargs),
                                           {'filter_email': 'SECRET-EMAIL', 'sort': 'SECRET-SORT'})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b'SECRET-', response.content)
                self.assertTemplateUsed(response, 'public/summary.html' if view in
                                        ('index', 'people_statistics') else 'public/list.html')

    def test_private_filters_never_survive_real_pagination_links(self):
        People.objects.bulk_create([People(name=f'Person {n:03}') for n in range(45)])
        from urllib.parse import parse_qs, urlsplit
        response = self.client.get('/assets/people/', {'page_size': 20, 'filter_name': 'Person',
                                  'filter_employee_id': 'SECRET-EMP', 'filter_phone': 'SECRET-PHONE'})
        self.assertNotIn(b'SECRET-', response.content)
        link = response.context_data['next_url']
        self.assertEqual(parse_qs(urlsplit(link).query),
                         {'sort': ['name'], 'order': ['asc'], 'page_size': ['20'],
                          'filter_name': ['Person'], 'page': ['2']})
        second = self.client.get('/assets/people/' + link)
        self.assertEqual(second.context_data['page_obj'].number, 2)
        self.assertEqual(second.context_data['page_obj'].paginator.count, 45)

    def test_http_private_details_and_legacy_routes_are_not_public(self):
        for path in ('/assets/unknown/', '/item/accounts/', '/item/taskrecordcontent/'):
            self.assertEqual(self.client.get(path).status_code, 404)
        for path in ('/computers/logs/', '/tasks/', '/records/servers/', '/tables/people/export/'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertNotIn(b'SECRET-', response.content)

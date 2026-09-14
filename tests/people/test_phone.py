import csv

from tests import response_body
from io import BytesIO, StringIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase

from index.common.table_registry import get_table_definition
from net.data_exchange.inventory_csv import export_csv, export_xlsx_template, import_file
from net.data_exchange.table_csv import export_filtered_csv
from net.data_exchange.xlsx import build_xlsx, read_xlsx_rows
from net.models import People, PeopleSyncSource
from net.people.directory import DirectoryPerson
from net.people.directory.sync import apply_people_sync, preview_people_sync, PeopleSyncApplyError
from tests.people.test_directory_sync import SnapshotAdapter
from tests.people.test_directory_adapters import FixtureResponse, RoutingSession, _source


class PhoneWorkflowTests(TestCase):
    def test_mobile_header_and_label(self):
        self.upload([['工号', '手机号'], ['MOBILE-1', '00123']])
        self.assertEqual(People.objects.get(employee_id='MOBILE-1').phone, '00123')
        field = next(field for field in get_table_definition('people').fields if field.key == 'phone')
        self.assertEqual(field.label, '手机号')

    def upload(self, rows, extension='csv'):
        if extension == 'xlsx':
            content = build_xlsx(rows)
        else:
            stream = StringIO(newline='')
            csv.writer(stream).writerows(rows)
            content = stream.getvalue().encode('utf-8')
        return import_file('people', SimpleUploadedFile('people.' + extension, content))

    def test_csv_and_xlsx_preserve_phone_text_and_absent_vs_blank(self):
        for extension in ('csv', 'xlsx'):
            for number in ('0013800123456', '+86 138-0012-3456', ''):
                with self.subTest(extension=extension, number=number):
                    self.upload([['工号', '姓名', '电话'], ['P1', 'Alice', number]], extension)
                    person = People.objects.get(employee_id='P1')
                    self.assertEqual(getattr(person, 'phone', None), number)
                    self.upload([['工号', '姓名'], ['P1', 'Renamed']], extension)
                    person.refresh_from_db()
                    self.assertEqual(person.phone, number)
                    exported = list(csv.DictReader(StringIO(export_csv('people').lstrip('\ufeff'))))
                    self.assertEqual(exported[0]['手机号'], number)
        self.upload([['employee_id', 'phone'], ['P2', '00123']])
        self.assertEqual(People.objects.get(employee_id='P2').phone, '00123')

    def test_templates_have_matching_width_and_importable_text_phone(self):
        csv_rows = list(csv.reader(StringIO(export_csv('people', template_only=True).lstrip('\ufeff'))))
        xlsx_rows = read_xlsx_rows(BytesIO(export_xlsx_template('people')), max_rows=10,
                                  max_columns=64, max_cell_chars=1000)
        for rows in (csv_rows, xlsx_rows):
            self.assertIn('手机号', rows[0])
            self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
            self.upload(rows)
            self.assertEqual(People.objects.get(employee_id='H10001').phone, '13800138000')

    def test_overlong_phone_rejects_entire_import(self):
        with self.assertRaisesRegex(ValueError, '手机号'):
            self.upload([['工号', '电话'], ['P1', '00123'], ['P2', '1' * 65]])
        self.assertEqual(People.objects.count(), 0)

    def test_old_file_creates_person_with_empty_phone(self):
        self.upload([['工号'], ['P1']])
        self.assertEqual(getattr(People.objects.get(employee_id='P1'), 'phone', None), '')

    def test_phone_filter_search_and_safe_table_export(self):
        self.upload([['工号', '电话'], ['P1', '+86 00123'], ['P2', '999']])
        reader = get_user_model().objects.create_user(username='phone-reader')
        self.client.force_login(reader)
        definition = get_table_definition('people')
        for params in ({'filter_phone': '00123'}, {'q': '00123'}):
            request = RequestFactory().get('/', params)
            request.user = reader
            response = export_filtered_csv(request, definition, People.objects.all(), 'people.csv')
            rows = list(csv.DictReader(StringIO(response_body(response).decode('utf-8-sig'))))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['手机号'], "'+86 00123")

    def source(self):
        return PeopleSyncSource.objects.create(source_type='feishu', name='Fixture',
            source_key='phone-fixture', credentials={'app_id': 'fixture', 'app_secret': 'fixture'},
            root_department_ids=['root'])

    def remote(self, number):
        return DirectoryPerson(employee_id='P1', name='Alice',
                               external_user_id='remote-1', phone=number)

    def test_sync_preview_apply_and_phone_only_update(self):
        source = self.source()
        for number in ('00123', '+86 123', ''):
            preview = preview_people_sync(source, SnapshotAdapter(source, [self.remote(number)]))
            self.assertTrue(preview.is_valid)
            records = preview.creates or preview.updates
            self.assertEqual(records[0].get('phone'), number)
            self.assertFalse(People.objects.filter(phone=number).exists())
            apply_people_sync(source, preview.to_dict())
            self.assertEqual(People.objects.get(employee_id='P1').phone, number)

    def test_phone_preview_tampering_and_local_edit_are_rejected(self):
        source = self.source()
        preview = preview_people_sync(source, SnapshotAdapter(source, [self.remote('00123')]))
        self.assertEqual(preview.creates[0].get('phone'), '00123')
        payload = preview.to_dict()
        payload['creates'][0]['phone'] = '999'
        with self.assertRaises(PeopleSyncApplyError):
            apply_people_sync(source, payload)
        apply_people_sync(source, preview)
        preview = preview_people_sync(source, SnapshotAdapter(source, [self.remote('777')]))
        People.objects.filter(employee_id='P1').update(phone='888')
        with self.assertRaises(PeopleSyncApplyError):
            apply_people_sync(source, preview)
        self.assertEqual(People.objects.get(employee_id='P1').phone, '888')

    def test_overlong_directory_phone_is_invalid_without_writes(self):
        source = self.source()
        preview = preview_people_sync(source, SnapshotAdapter(source, [self.remote('1' * 65)]))
        self.assertFalse(preview.is_valid)
        self.assertEqual(People.objects.count(), 0)

    def test_provider_mobile_mapping_and_unavailable_values(self):
        from net.people.directory.feishu import FeishuDirectoryAdapter
        from net.people.directory.dingtalk import DingTalkDirectoryAdapter
        for provider, adapter_class in (('feishu', FeishuDirectoryAdapter), ('dingtalk', DingTalkDirectoryAdapter)):
            for mobile in ({'mobile': '+86 00123'}, {'mobile': ''}, {'mobile': None}, {}):
                with self.subTest(provider=provider, mobile=mobile):
                    def handler(method, url, kwargs):
                        if url.endswith('/tenant_access_token/internal'):
                            return FixtureResponse({'code': 0, 'tenant_access_token': 'fixture'})
                        if url.endswith('/gettoken'):
                            return FixtureResponse({'errcode': 0, 'access_token': 'fixture'})
                        if url.endswith('/users/find_by_department'):
                            return FixtureResponse({'code': 0, 'data': {'items': [dict(
                                employee_no='P1', open_id='remote-1', **mobile)], 'has_more': False}})
                        if url.endswith('/children'):
                            return FixtureResponse({'code': 0, 'data': {'items': [], 'has_more': False}})
                        if url.endswith('/topapi/v2/user/list'):
                            return FixtureResponse({'errcode': 0, 'result': {'list': [dict(
                                job_number='P1', userid='remote-1', **mobile)], 'has_more': False}})
                        if url.endswith('/topapi/v2/department/listsub'):
                            return FixtureResponse({'errcode': 0, 'result': []})
                        self.fail('Unexpected fixture request: ' + url)
                    people = list(adapter_class(_source(provider), session=RoutingSession(handler)).iter_people())
                    self.assertEqual(getattr(people[0], 'phone', None), mobile.get('mobile') or '')

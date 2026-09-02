import json
from importlib import import_module

from django.core import serializers
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.forms import model_to_dict, modelform_factory
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase

from net.models import People


def _contract(name):
    try:
        return getattr(import_module('net.models'), name)
    except AttributeError:
        return None


def _people_contract(name):
    try:
        return getattr(import_module('net.people.directory'), name)
    except (AttributeError, ModuleNotFoundError):
        return None


class PeopleSyncSourceContractTests(TestCase):
    SECRET = 'directory-app-secret-that-must-never-leak'

    def _source(self, **overrides):
        PeopleSyncSource = _contract('PeopleSyncSource')
        self.assertIsNotNone(PeopleSyncSource, 'PeopleSyncSource 必须从 net.models 导出。')
        values = {
            'source_type': 'feishu',
            'name': '总部人员目录',
            'source_key': 'feishu-hq-directory',
            'credentials': {
                'app_id': 'cli_hq_directory',
                'app_secret': self.SECRET,
            },
            'root_department_ids': ['root-hq', 'ops'],
        }
        values.update(overrides)
        return PeopleSyncSource(**values)

    def test_source_validates_typed_configuration_and_exposes_only_safe_display_data(self):
        source = self._source()
        source.full_clean()
        source.save()

        public_data = source.public_data()
        rendered = '\n'.join((str(source), repr(source), str(public_data)))
        self.assertEqual(public_data['source_type'], 'feishu')
        self.assertEqual(public_data['source_key'], 'feishu-hq-directory')
        self.assertEqual(public_data['root_department_ids'], ['root-hq', 'ops'])
        self.assertTrue(public_data['has_credentials'])
        self.assertNotIn('credentials', public_data)
        self.assertNotIn(self.SECRET, rendered)
        self.assertNotIn('app_secret', rendered)

        invalid_credentials = self._source(credentials={
            'app_id': 'cli_hq_directory',
            'app_secret': True,
        })
        with self.assertRaises(ValidationError) as credentials_error:
            invalid_credentials.full_clean()
        self.assertIn('credentials', credentials_error.exception.message_dict)
        self.assertNotIn('app_secret', str(credentials_error.exception))

        invalid_roots = self._source(root_department_ids=['root-hq', False])
        with self.assertRaises(ValidationError) as roots_error:
            invalid_roots.full_clean()
        self.assertIn('root_department_ids', roots_error.exception.message_dict)

    def test_source_key_is_the_unique_unambiguous_source_identity(self):
        first = self._source()
        first.full_clean()
        first.save()
        duplicate = self._source(name='同一目录的另一个显示名')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                duplicate.save(force_insert=True)

    def test_standard_serializers_omit_credentials_even_when_explicitly_selected(self):
        for provider, credential_name in (('feishu', 'app_id'), ('dingtalk', 'app_key')):
            with self.subTest(provider=provider):
                credentials = {credential_name: 'private-client-identity', 'app_secret': self.SECRET}
                source = self._source(
                    source_type=provider, source_key=f'{provider}-serialize',
                    credentials=credentials,
                )
                source.full_clean()
                source.save()
                loaded = type(source).objects.get(pk=source.pk)
                # Serialization restrictions must not remove adapter inputs from storage.
                self.assertEqual(loaded.credentials, credentials)
                loaded.full_clean()
                for format_name in ('python', 'json', 'xml'):
                    for options in ({}, {'fields': ('source_key', 'credentials')}):
                        with self.subTest(format=format_name, options=options):
                            result = serializers.serialize(format_name, [loaded], **options)
                            rendered = str(result)
                            self.assertIn(f'{provider}-serialize', rendered)
                            for forbidden in ('credentials', credential_name, 'app_secret',
                                              'private-client-identity', self.SECRET):
                                self.assertNotIn(forbidden, rendered)

    def test_form_dict_and_public_template_context_exclude_credentials(self):
        for provider, credential_name in (('feishu', 'app_id'), ('dingtalk', 'app_key')):
            with self.subTest(provider=provider):
                source = self._source(
                    source_type=provider, source_key=f'{provider}-display',
                    credentials={credential_name: 'private-client-identity', 'app_secret': self.SECRET},
                )
                source.save()
                source.refresh_from_db()
                representations = (
                    model_to_dict(source),
                    model_to_dict(source, fields=('source_key', 'credentials')),
                    source.public_data(),
                )
                for data in representations:
                    self.assertNotIn('credentials', data)
                    encoded = json.dumps(data, cls=DjangoJSONEncoder)
                    rendered = Template(
                        '{{ source }}{{ source.credentials }}'
                        '{{ source|json_script:"directory-source" }}'
                    ).render(Context({'source': data}))
                    self.assertIn(f'{provider}-display', encoded)
                    self.assertIn(f'{provider}-display', rendered)
                    for forbidden in (credential_name, 'app_secret',
                                      'private-client-identity', self.SECRET):
                        self.assertNotIn(forbidden, encoded)
                        self.assertNotIn(forbidden, rendered)
                form = modelform_factory(type(source), fields='__all__')(instance=source)
                self.assertNotIn('credentials', form.fields)
                self.assertNotIn(self.SECRET, form.as_p())

    def test_people_keep_provider_label_and_bind_to_one_source_instance(self):
        source = self._source()
        source.full_clean()
        source.save()
        person = People.objects.create(
            name='王工',
            employee_id='EMP-1001',
            source='feishu',
            sync_source=source,
            platform_user_id='ou_directory_user',
        )
        self.assertEqual(person.source, 'feishu')
        self.assertEqual(person.sync_source_id, source.pk)
        self.assertIn(person, source.people.all())
        self.assertTrue(People._meta.get_field('sync_source').null)


class DirectoryPersonContractTests(SimpleTestCase):
    def test_directory_person_normalizes_text_but_requires_a_real_employee_id(self):
        DirectoryPerson = _people_contract('DirectoryPerson')
        self.assertIsNotNone(DirectoryPerson, 'DirectoryPerson 必须从 net.people.directory 导出。')
        person = DirectoryPerson(
            employee_id=' EMP-42 ',
            name=' 王工 ',
            email=None,
            department=' 运维 ',
            leader=' 李主管 ',
            external_user_id=' ou-42 ',
        )
        self.assertEqual(
            person,
            DirectoryPerson(
                employee_id='EMP-42',
                name='王工',
                email='',
                department='运维',
                leader='李主管',
                external_user_id='ou-42',
            ),
        )

        DirectoryPayloadError = _people_contract('DirectoryPayloadError')
        for invalid_employee_id in ('', '   ', 42, True, ['EMP-42']):
            with self.subTest(invalid_employee_id=invalid_employee_id):
                with self.assertRaises(DirectoryPayloadError):
                    DirectoryPerson(employee_id=invalid_employee_id)

    def test_adapter_errors_never_echo_supplied_secret_details(self):
        DirectoryAuthenticationError = _people_contract('DirectoryAuthenticationError')
        error = DirectoryAuthenticationError('token=directory-app-secret-that-must-never-leak')
        self.assertNotIn('directory-app-secret-that-must-never-leak', str(error))
        self.assertNotIn('directory-app-secret-that-must-never-leak', repr(error))


class DirectoryAdapterContractTests(SimpleTestCase):
    def test_protocol_accepts_a_pure_local_adapter(self):
        DirectoryAdapter = _people_contract('DirectoryAdapter')
        DirectoryPerson = _people_contract('DirectoryPerson')
        self.assertIsNotNone(DirectoryAdapter, 'DirectoryAdapter 必须从 net.people.directory 导出。')

        class LocalAdapter:
            source_key = 'feishu-hq-directory'
            fetch_configuration_identity = {
                'source_key': 'feishu-hq-directory',
                'digest': 'local-fixture-identity',
            }

            def test_connection(self):
                return None

            def iter_people(self):
                yield DirectoryPerson(employee_id='EMP-1', name='本地测试人员')

        adapter = LocalAdapter()
        self.assertIsInstance(adapter, DirectoryAdapter)
        self.assertEqual(list(adapter.iter_people())[0].employee_id, 'EMP-1')

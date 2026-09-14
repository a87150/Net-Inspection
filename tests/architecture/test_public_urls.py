"""Public URLs must never disclose connection credentials or change collection."""
import csv

from tests import response_body
from io import StringIO

from django.test import TestCase
from django.urls import reverse
from tests.auth import login_reader

from index.common.table_options import build_field_options
from index.common.table_registry import get_table_definition
from net.models import InspectionProfile, SecurityDevice, Server, TaskRun
from net.inspections.queue import enqueue_task


class PublicURLTests(TestCase):
    def setUp(self):
        login_reader(self.client)
        self.raw = 'https://private-user:private-pass@device.demo.invalid:9443/inspection?token=query-secret&x=opaque-secret#fragment-secret'
        self.safe = 'https://device.demo.invalid:9443/inspection'
        self.assets = [('servers', Server.objects.create(ip='192.0.2.70', name='Fixture', api_url=self.raw)),
                       ('monitors', SecurityDevice.objects.create(ip='192.0.2.71', device_name='Fixture', api_url=self.raw))]

    def assert_private_absent(self, value):
        for secret in ('private-user', 'private-pass', 'query-secret', 'opaque-secret', 'fragment-secret'):
            self.assertFalse(secret in str(value), f'Public presentation leaked {secret}')

    def test_lists_and_html_attributes_use_safe_representation(self):
        for kind, asset in self.assets:
            with self.subTest(kind=kind):
                response = self.client.get(reverse('asset_list', args=[kind]))
                self.assert_private_absent(response.content.decode())
                self.assertContains(response, self.safe)

    def test_detail_fields_use_safe_representation(self):
        for kind, asset in self.assets:
            with self.subTest(kind=kind):
                response = self.client.get(reverse('asset_detail', args=[kind, asset.pk]))
                self.assert_private_absent(response.content.decode())
                self.assert_private_absent(response.context['detail_fields'])
                self.assertContains(response, self.safe)

    def test_filter_option_payloads_never_contain_raw_urls(self):
        for kind, asset in self.assets:
            with self.subTest(kind=kind):
                options = build_field_options(type(asset).objects.all(), get_table_definition(kind))
                self.assert_private_absent(options)
                self.assertEqual(options['api_url'], ())

    def test_filtered_csv_uses_safe_url_and_stored_url_is_unchanged(self):
        for kind, asset in self.assets:
            with self.subTest(kind=kind):
                response = self.client.get(reverse('table_export', args=[kind]), {'filter_ip': asset.ip})
                self.assert_private_absent(response_body(response).decode('utf-8-sig'))
                rows = list(csv.DictReader(StringIO(response_body(response).decode('utf-8-sig'))))
                self.assertEqual(rows[0]['巡检 API 地址'], self.safe)
                asset.refresh_from_db()
                self.assertEqual(asset.api_url, self.raw)

    def test_task_keeps_private_connection_snapshot_but_does_not_render_it(self):
        for kind, asset in self.assets:
            with self.subTest(kind=kind):
                profile = InspectionProfile.objects.create(name=kind, device_type='server' if kind == 'servers' else 'monitor', selected_items=['device_info'])
                task = enqueue_task(profile, [asset.pk], TaskRun.Source.MANUAL)
                self.assertEqual(task.target_runs.get().target_snapshot['api_url'], self.raw)
                self.assert_private_absent(self.client.get(reverse('task_detail', args=[task.pk])).content.decode())

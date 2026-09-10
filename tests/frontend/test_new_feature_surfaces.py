from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from net.models import DeviceConfigurationBackup, Network_Device


class NewFeatureSurfaceDesignTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='new-surface-admin',
            email='new-surface@example.test',
            password='unused',
        )
        self.client.force_login(self.user)

    def test_notification_template_modal_uses_sectioned_glass_workflow(self):
        response = self.client.get(reverse('alert_template_settings'))

        self.assertContains(response, 'alert-template-modal')
        self.assertContains(response, 'modal-dialog-scrollable modal-shell')
        self.assertContains(response, 'modal-content modal-surface')
        self.assertContains(response, 'data-template-section="content"')
        self.assertContains(response, 'data-template-section="variables"')
        self.assertContains(response, 'data-template-section="preview"')
        self.assertContains(response, 'modal-footer modal-footer--sticky')

    def test_domain_inactivity_modal_shows_setting_and_impact_in_glass_cards(self):
        response = self.client.get(reverse('domain_controller_settings'))

        self.assertContains(response, 'domain-statistics-modal')
        self.assertContains(response, 'modal-dialog-scrollable modal-shell')
        self.assertContains(response, 'modal-content modal-surface')
        self.assertContains(response, 'data-domain-statistics-section="setting"')
        self.assertContains(response, 'data-domain-statistics-section="impact"')
        self.assertContains(response, 'modal-footer modal-footer--sticky')

    def test_analysis_problem_modal_uses_scrollable_glass_detail_surface(self):
        response = self.client.get(reverse('computer_analysis_list'))

        self.assertContains(response, 'analysis-problems-modal')
        self.assertContains(response, 'modal-xl modal-dialog-scrollable modal-shell')
        self.assertContains(response, 'modal-content modal-surface')
        self.assertContains(response, 'modal-body modal-body--scroll')
        self.assertContains(response, 'analysis-problems-modal__body')
        self.assertContains(response, 'modal-footer modal-footer--sticky')

        css = Path(settings.BASE_DIR, 'static/app/css/modal-workflows.css').read_text(encoding='utf-8')
        self.assertIn('.analysis-problems-table .table-responsive', css)
        self.assertIn('max-height: none', css)

    def test_configuration_backup_history_uses_shared_readable_table_workspace(self):
        asset = Network_Device.objects.create(
            ip='192.0.2.81',
            device_name='backup-surface-test',
        )
        DeviceConfigurationBackup.objects.create(
            device_type='network_device',
            device_id=asset.pk,
            backup_date=date(2026, 9, 9),
            captured_at=timezone.now(),
            filename='running-config.cfg',
            media_type='text/plain',
            scope='running-config',
            vendor='cisco',
            sha256='a' * 64,
            byte_size=2048,
            ciphertext=b'encrypted-placeholder',
        )

        response = self.client.get(
            reverse('configuration_backup_list', args=['networks', asset.pk]),
        )

        self.assertContains(response, 'configuration-backup-workspace')
        self.assertContains(response, 'configuration-backup-notes')
        self.assertContains(response, 'table-scroll-shell')
        self.assertContains(response, 'table-scroll-hint')
        self.assertContains(response, 'table data-table')

    def test_access_record_sync_uses_compact_single_source_picker(self):
        response = self.client.get(reverse('access_record_list'))
        html = response.content.decode(response.charset)

        self.assertContains(response, 'access-sync-card')
        self.assertContains(response, 'access-sync-card__controls')
        self.assertContains(response, 'id="access-source-id"')
        self.assertContains(response, 'name="source_ids"')
        self.assertContains(response, 'aria-label="选择采集平台"')
        self.assertContains(response, '请选择已启用平台')
        self.assertNotContains(response, '<label for="access-source-id"')
        self.assertNotContains(response, '选择门禁管理平台')
        select_start = html.index('<select', html.index('id="access-source-id"') - 100)
        select_end = html.index('>', select_start)
        self.assertNotIn('multiple', html[select_start:select_end])

        css = Path(settings.BASE_DIR, 'static/app/css/operations.css').read_text(encoding='utf-8')
        access_card_rule = css.split('.access-sync-card {', 1)[1].split('}', 1)[0]
        self.assertIn('width: 100%', access_card_rule)
        self.assertNotIn('max-width:', access_card_rule)

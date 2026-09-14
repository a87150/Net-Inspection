from pathlib import Path

from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class FrontendConsistencyTests(SimpleTestCase):
    def test_design_tokens_cover_shared_glass_and_status_semantics(self):
        css = Path(finders.find('app/css/tokens.css')).read_text(encoding='utf-8')

        for token in (
            '--color-surface-glass',
            '--color-border-glass',
            '--color-focus-ring',
            '--color-status-info',
            '--color-status-neutral',
            '--control-height',
            '--modal-backdrop',
        ):
            with self.subTest(token=token):
                self.assertIn(token, css)

    def test_status_badge_covers_async_and_delivery_states(self):
        cases = {
            'queued': 'text-bg-info',
            'running': 'text-bg-info',
            'sent': 'text-bg-success',
            'delivered': 'text-bg-success',
            'retry': 'text-bg-warning',
            'enabled': 'text-bg-success',
            'disabled': 'text-bg-secondary',
        }
        for status, css_class in cases.items():
            with self.subTest(status=status):
                html = render_to_string('common/status_badge.html', {
                    'status': status,
                    'label': status,
                })
                self.assertIn(css_class, html)
                self.assertIn(f'data-status="{status}"', html)

    def test_alert_templates_use_shared_status_and_severity_components(self):
        root = Path(__file__).resolve().parents[2] / 'index' / 'templates' / 'alerts'
        source = '\n'.join(
            (root / name).read_text(encoding='utf-8')
            for name in (
                'list.html',
                'detail.html',
                'event_row.html',
                'event_detail.html',
                'delivery_list.html',
            )
        )

        self.assertIn("common/status_badge.html", source)
        self.assertIn("inspections/severity_badge.html", source)
        self.assertNotIn('{{ delivery.get_status_display }}', source)

    def test_operational_tables_do_not_duplicate_bootstrap_status_badges(self):
        root = Path(__file__).resolve().parents[2] / 'index' / 'templates'
        paths = (
            'devices/item_list.html',
            'inspections/results_table.html',
            'inspections/errors.html',
            'devices/pc/error_list.html',
        )
        for relative_path in paths:
            with self.subTest(template=relative_path):
                source = (root / relative_path).read_text(encoding='utf-8')
                self.assertNotIn('badge text-bg-success', source)
                self.assertNotIn('badge text-bg-danger', source)
                self.assertNotIn('badge text-bg-warning', source)

    def test_shared_shell_has_persistent_live_region_and_confirmation_dialog(self):
        root = Path(__file__).resolve().parents[2] / 'index' / 'templates' / 'common'
        source = (root / 'base.html').read_text(encoding='utf-8')

        self.assertIn('id="app-live-region"', source)
        self.assertIn('aria-live="polite"', source)
        self.assertIn('id="appConfirmModal"', source)
        self.assertIn('app/js/common/confirm_dialog.js', source)

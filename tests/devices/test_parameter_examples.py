import csv
from io import StringIO
from django import forms
from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from tests.auth import login_admin, login_reader
from index.devices.forms import device_form, device_form_sections
from index.devices.pc.simple_source_form import SimplePCLogSourceForm
from net.data_exchange.inventory_csv import export_csv, export_xlsx_template, import_file
from net.models import Network_Device, Server


class DeviceParameterExamplesTests(TestCase):
    def test_optional_versions_save_and_old_edit_submissions_preserve_them(self):
        for kind, model, version in [('networks', Network_Device, 'VRP V200R019C10'), ('servers', Server, '24.04')]:
            with self.subTest(kind=kind):
                form = device_form(kind, {'ip': '192.0.2.82', 'os_version': version})
                self.assertIn('os_version', form.fields)
                self.assertFalse(form.fields['os_version'].required)
                self.assertIn('os_version', [f.name for s in device_form_sections(form) for f in s['fields']])
                self.assertTrue(form.is_valid(), form.errors)
                obj = form.save()
                self.assertEqual(obj.os_version, version)
                edit = device_form(kind, {'ip': obj.ip}, instance=obj)
                self.assertTrue(edit.is_valid(), edit.errors)
                edit.save()
                obj.refresh_from_db()
                self.assertEqual(obj.os_version, version)
                clear = device_form(kind, {'ip': obj.ip, 'os_version': ''}, instance=obj)
                self.assertTrue(clear.is_valid(), clear.errors)
                self.assertFalse(clear.save().os_version)

    def test_device_text_inputs_have_examples_but_choice_controls_do_not(self):
        for kind in ('networks', 'servers', 'monitors'):
            form = device_form(kind)
            for name, field in form.fields.items():
                with self.subTest(kind=kind, field=name):
                    if isinstance(field.widget, (forms.Select, forms.CheckboxInput)):
                        self.assertNotIn('示例', field.help_text)
                        self.assertNotIn('placeholder', field.widget.attrs)
                    else:
                        self.assertIn('示例', field.help_text)
            self.assertFalse(form['ip'].value())
        self.assertIn('Ubuntu', device_form('servers').fields['os_version'].help_text)
        self.assertNotIn('huawei', device_form('monitors').fields['vendor'].help_text)

    def test_import_dialog_includes_category_guidance_for_all_importable_devices(self):
        login_admin(self.client)
        for kind in ('networks', 'servers', 'monitors'):
            response = self.client.get(reverse('asset_list', args=[kind]))
            self.assertContains(response, '字段填写示例')
            self.assertContains(response, 'CHANGE-ME')
            self.assertTrue(response.context['inventory_import_guide'])
        login_reader(self.client)
        self.assertNotContains(self.client.get(reverse('asset_list', args=['servers'])), '字段填写示例')

    def test_versions_roundtrip_csv_and_excel(self):
        for kind, model in [('networks', Network_Device), ('servers', Server)]:
            rows = list(csv.reader(StringIO(export_csv(kind, template_only=True).lstrip('\ufeff'))))
            self.assertIn('系统版本', rows[0])
            for row in rows[1:]:
                self.assertEqual(len(row), len(rows[0]))
            for suffix, content in [('csv', export_csv(kind, template_only=True).encode()), ('xlsx', export_xlsx_template(kind))]:
                import_file(kind, SimpleUploadedFile('example.' + suffix, content))
                for row in rows[1:]:
                    values = dict(zip(rows[0], row))
                    self.assertEqual(model.objects.get(ip=values['IP地址']).os_version or '', values['系统版本'])

    def test_pc_source_fields_have_examples(self):
        form = SimplePCLogSourceForm()
        for name in ('host', 'username', 'password', 'domain', 'recent_days', 'local_staging_directory', 'shared_path', 'ftp_directory'):
            with self.subTest(field=name):
                self.assertIn('示例', form.fields[name].help_text)

    def test_reader_cannot_change_optional_version(self):
        obj = Server.objects.create(ip='192.0.2.83', os_version='original')
        login_reader(self.client)
        response = self.client.post(reverse('asset_edit', args=['servers', obj.pk]), {'ip': obj.ip, 'os_version': 'changed'})
        self.assertEqual(response.status_code, 403)
        obj.refresh_from_db()
        self.assertEqual(obj.os_version, 'original')

    def test_collection_examples_render_for_every_category_without_changing_defaults(self):
        from index.devices.collection_profiles import CollectionSettingsForm
        import re
        login_admin(self.client)
        for kind in ('networks', 'servers', 'monitors'):
            response = self.client.get(reverse('collection_templates', args=[kind]))
            self.assertEqual(response.status_code, 200)
            self.assertIn('示例：', response.content.decode(), kind)
            if kind != 'monitors':
                self.assertTrue('示例：80' in response.content.decode(), kind)
            if kind == 'servers':
                self.assertNotContains(response, 'display cpu-usage')
            if kind == 'monitors':
                self.assertContains(response, 'snmp-reader')
        form = CollectionSettingsForm(kind='networks')
        pattern = form.fields['template_cpu'].widget.attrs['placeholder']
        sample = form.fields['sample_cpu'].widget.attrs['placeholder']
        self.assertEqual(re.search(pattern, sample).groupdict(), {'usage_percent': '35'})
        self.assertFalse(form['commands_cpu'].value())
        self.assertFalse(form['template_cpu'].value())
        self.assertFalse(form['threshold_cpu'].value())

    def test_admin_can_submit_and_reopen_optional_versions_in_business_modal(self):
        login_admin(self.client)
        for kind, model in [('networks', Network_Device), ('servers', Server)]:
            response = self.client.post(reverse('asset_create', args=[kind]), {'ip': '192.0.2.84', 'os_version': 'Version 1'})
            self.assertEqual(response.status_code, 302)
            obj = model.objects.get(ip='192.0.2.84')
            response = self.client.post(reverse('asset_edit', args=[kind, obj.pk]), {'ip': obj.ip, 'os_version': 'Version 2'})
            self.assertEqual(response.status_code, 302)
            obj.refresh_from_db()
            self.assertEqual(obj.os_version, 'Version 2')
            page = self.client.get(reverse('asset_edit', args=[kind, obj.pk]))
            self.assertEqual(page.context['device_form']['os_version'].value(), 'Version 2')

    def test_pc_analysis_parameters_show_examples_and_keep_values(self):
        from index.inspections.forms import ComputerAnalysisProfileConfigForm
        form = ComputerAnalysisProfileConfigForm()
        for name in ('cpu_max_percent', 'disk_max_percent', 'site_ip_prefixes', 'minimum_windows_release', 'daily_time'):
            self.assertIn('示例', form.fields[name].help_text)
        self.assertIsNone(form.fields['site_ip_prefixes'].initial)
        self.assertEqual(form['site_ip_prefixes'].value(), 'null')


    def test_choice_examples_are_skipped_without_removing_existing_help(self):
        from index.common.form_examples import apply_field_examples
        choice_widgets = (forms.Select, forms.SelectMultiple, forms.RadioSelect,
                          forms.CheckboxSelectMultiple, forms.CheckboxInput,
                          forms.FileInput, forms.HiddenInput)
        for widget in choice_widgets:
            with self.subTest(widget=widget.__name__):
                form = forms.Form()
                form.fields['option'] = forms.CharField(widget=widget(), help_text='原有功能说明')
                bound = form['option']
                apply_field_examples(form, {'option': ('example', '不应显示')})
                self.assertEqual(bound.help_text, '原有功能说明')
                self.assertEqual(form.fields['option'].help_text, '原有功能说明')
                self.assertNotIn('placeholder', form.fields['option'].widget.attrs)

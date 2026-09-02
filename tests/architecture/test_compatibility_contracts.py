from importlib import import_module
from importlib.util import find_spec

from django.apps import apps
from django.contrib.staticfiles import finders
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.test import SimpleTestCase
from django.urls import reverse


class CompatibilityContractTests(SimpleTestCase):
    def test_django_application_labels_remain_stable(self):
        self.assertEqual(apps.get_app_config("net").label, "net")
        self.assertEqual(apps.get_app_config("index").label, "index")

    def test_startup_and_legacy_modules_remain_importable(self):
        modules = (
            "net.settings",
            "net.urls",
            "net.wsgi",
            "net.asgi",
            "net.models",
            "net.task_models",
            "net.alert_models",
            "net.domain_models",
            "net.tasks",
            "index.urls",
            "index.views",
        )
        for module_name in modules:
            with self.subTest(module=module_name):
                self.assertIsNotNone(import_module(module_name))

    def test_migration_validators_remain_exported(self):
        task_models = import_module("net.task_models")
        alert_models = import_module("net.alert_models")
        domain_models = import_module("net.domain_models")

        self.assertTrue(callable(task_models.validate_string_list))
        self.assertTrue(callable(task_models.validate_json_object))
        self.assertTrue(callable(alert_models.validate_finite_json))
        self.assertTrue(callable(domain_models.validate_parameter_summary))

    def test_public_url_contract(self):
        self.assertEqual(reverse("index"), "/")
        self.assertEqual(reverse("people_statistics"), "/people/statistics/")
        self.assertEqual(reverse("domain_account_list"), "/domain/accounts/")
        self.assertEqual(reverse("domain_computer_list"), "/domain/computers/")
        self.assertEqual(reverse("computer_analysis_list"), "/computers/analyses/")
        self.assertEqual(reverse("task_list"), "/tasks/")
        self.assertEqual(reverse("alert_list"), "/alerts/")

    def test_inspection_orchestration_has_canonical_and_legacy_imports(self):
        module_names = (
            "net.inspections.queue",
            "net.inspections.schedules",
            "net.inspections.worker",
        )
        self.assertEqual([name for name in module_names if find_spec(name) is None], [])

        canonical_queue = import_module("net.inspections.queue")
        canonical_schedules = import_module("net.inspections.schedules")
        canonical_worker = import_module("net.inspections.worker")
        legacy_queue = import_module("net.tasks.queue")
        legacy_schedules = import_module("net.tasks.schedules")
        legacy_worker = import_module("net.tasks.worker")

        for name in ("enqueue_task", "claim_next_task", "finish_task"):
            with self.subTest(module="queue", name=name):
                self.assertIs(getattr(legacy_queue, name), getattr(canonical_queue, name))
        self.assertIs(legacy_schedules.enqueue_due_schedules, canonical_schedules.enqueue_due_schedules)
        self.assertIs(legacy_worker.TaskWorker, canonical_worker.TaskWorker)

    def test_feature_template_locations_resolve(self):
        missing = []
        for template_name in ("dashboard/index.html", "common/table_workspace.html"):
            try:
                get_template(template_name)
            except TemplateDoesNotExist:
                missing.append(template_name)
        self.assertEqual(missing, [])

    def test_feature_static_locations_resolve(self):
        missing = [
            path for path in ("app/css/style.css", "app/js/common/table_workspace.js")
            if finders.find(path) is None
        ]
        self.assertEqual(missing, [])

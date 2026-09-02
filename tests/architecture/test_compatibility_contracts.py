from importlib import import_module

from django.apps import apps
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

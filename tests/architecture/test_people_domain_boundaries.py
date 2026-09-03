from importlib import import_module

from django.test import SimpleTestCase


class PeopleDomainBoundaryTests(SimpleTestCase):
    ENTRY_POINTS = (
        ("net.people.directory.base", "DirectoryPerson"),
        ("net.people.directory.feishu", "FeishuDirectoryAdapter"),
        ("net.people.directory.dingtalk", "DingTalkDirectoryAdapter"),
        ("net.people.directory.sync", "apply_people_sync"),
        ("net.people.tasks", "enqueue_people_task"),
        ("net.people.executor", "execute_people_target"),
        ("net.domain.sync", "sync_domain"),
        ("net.domain.tasks", "enqueue_domain_operation"),
        ("net.domain.executor", "execute_domain_target"),
        ("index.domain.views", "domain_object_list"),
        ("index.domain.operations", "domain_operation_create"),
        ("index.domain.forms", "DomainOperationForm"),
    )

    def test_people_and_domain_entry_points_are_available(self):
        for module_name, attribute in self.ENTRY_POINTS:
            with self.subTest(module=module_name, attribute=attribute):
                try:
                    module = import_module(module_name)
                except ModuleNotFoundError:
                    self.fail(f"feature module is missing: {module_name}")
                self.assertTrue(callable(getattr(module, attribute)))

    def test_legacy_multi_source_entry_points_are_absent(self):
        integrations = import_module('index.people.integrations')
        forms = import_module('index.people.forms')
        self.assertFalse(hasattr(integrations, 'people_source_save'))
        self.assertFalse(hasattr(forms, 'PeopleSourceForm'))

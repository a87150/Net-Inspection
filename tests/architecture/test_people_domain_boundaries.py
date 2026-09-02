from importlib import import_module

from django.test import SimpleTestCase


class PeopleDomainBoundaryTests(SimpleTestCase):
    BOUNDARIES = (
        (
            "net.people.importing",
            "net.services.personnel_import",
            "import_people_from_provider",
        ),
        (
            "net.people.directory.base",
            "net.integrations.people.base",
            "DirectoryPerson",
        ),
        (
            "net.people.directory.feishu",
            "net.integrations.people.feishu",
            "FeishuDirectoryAdapter",
        ),
        (
            "net.people.directory.dingtalk",
            "net.integrations.people.dingtalk",
            "DingTalkDirectoryAdapter",
        ),
        (
            "net.people.directory.sync",
            "net.integrations.people.sync",
            "apply_people_sync",
        ),
        ("net.people.tasks", "net.tasks.people", "enqueue_people_task"),
        (
            "net.people.executor",
            "net.tasks.executors.people",
            "execute_people_target",
        ),
        ("net.domain.sync", "net.services.ad_sync", "sync_domain"),
        ("net.domain.tasks", "net.tasks.domain", "enqueue_domain_operation"),
        (
            "net.domain.executor",
            "net.tasks.executors.domain",
            "execute_domain_target",
        ),
        (
            "index.people.integrations",
            "index.views.integrations",
            "people_source_save",
        ),
        ("index.domain.views", "index.views.domain", "domain_object_list"),
        (
            "index.domain.operations",
            "index.views.domain_operations",
            "domain_operation_create",
        ),
        ("index.people.forms", "index.forms.integrations", "PeopleSourceForm"),
        ("index.domain.forms", "index.forms.domain", "DomainOperationForm"),
    )

    def test_canonical_and_legacy_paths_export_the_same_objects(self):
        for canonical_name, legacy_name, attribute in self.BOUNDARIES:
            with self.subTest(module=canonical_name, attribute=attribute):
                try:
                    canonical_module = import_module(canonical_name)
                except ModuleNotFoundError:
                    self.fail(f"canonical feature module is missing: {canonical_name}")
                legacy_module = import_module(legacy_name)
                self.assertIs(
                    getattr(canonical_module, attribute),
                    getattr(legacy_module, attribute),
                )

from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from ldap3 import LEVEL

from net.domain.bitlocker import read_bitlocker_keys
from net.domain.validation import validate_domain_action
from net.models import Domain_Computer, Domain_Controller_Config, TaskRun

GUID = UUID("12345678-1234-1234-1234-123456789abc")
PASSWORD = "111111-222222-333333-444444-555555-666666-111111-222222"


class BitLockerClientTests(SimpleTestCase):
    def setUp(self):
        self.config = SimpleNamespace(use_ssl=True, base_dn="DC=example,DC=test")
        self.computer = SimpleNamespace(distinguished_name="CN=PC,DC=example,DC=test", object_guid=GUID)
        self.connection = Mock(result={"result": 0})
        self.connection.search.return_value = True
        self.connection.response = [{"type": "searchResEntry", "attributes": {
            "msFVE-RecoveryPassword": PASSWORD, "msFVE-RecoveryGuid": GUID.bytes_le,
            "whenCreated": "20260909000000Z",
        }}]

    def read(self):
        with patch("net.domain.bitlocker.DomainClient.connect", return_value=self.connection):
            return read_bitlocker_keys(self.config, self.computer)

    def test_single_computer_scope_and_guid_decoding(self):
        result = self.read()
        self.assertEqual(result[0]["password"], PASSWORD)
        self.assertEqual(result[0]["key_id"], str(GUID))
        self.assertEqual(self.connection.search.call_args.args[0], self.computer.distinguished_name)
        self.assertEqual(self.connection.search.call_args.kwargs["search_scope"], LEVEL)
        self.connection.modify.assert_not_called()
        self.connection.unbind.assert_called_once()

    def test_empty_result_is_not_failure(self):
        self.connection.response = []
        self.assertEqual(self.read(), [])

    def test_hidden_password_is_reported_without_losing_key_id(self):
        del self.connection.response[0]["attributes"]["msFVE-RecoveryPassword"]
        self.assertEqual(self.read()[0]["password"], "")

    def test_ldap_failure_not_reported_as_empty(self):
        self.connection.result = {"result": 50, "message": PASSWORD}
        self.connection.search.return_value = False
        with self.assertRaises(ValidationError) as error:
            self.read()
        self.assertNotIn(PASSWORD, str(error.exception))
        self.connection.unbind.assert_called_once()

    def test_insecure_transport_and_outside_base_never_connect(self):
        for ssl, dn in [(False, self.computer.distinguished_name), (True, "CN=PC,DC=other,DC=test")]:
            self.config.use_ssl = ssl
            self.computer.distinguished_name = dn
            with patch("net.domain.bitlocker.DomainClient.connect") as connect:
                with self.assertRaises(ValidationError):
                    read_bitlocker_keys(self.config, self.computer)
                connect.assert_not_called()

    def test_computer_unlock_rejected_account_unlock_retained(self):
        with self.assertRaises(ValidationError):
            validate_domain_action("computer", "unlock", {})
        self.assertEqual(validate_domain_action("account", "unlock", {}), {})


class BitLockerViewTests(TestCase):
    def setUp(self):
        self.computer = Domain_Computer.objects.create(computer_name="PC", distinguished_name="CN=PC,DC=example,DC=test")
        Domain_Controller_Config.objects.create(host="dc.example.test", use_ssl=True, base_dn="DC=example,DC=test")
        self.url = reverse("domain_computer_bitlocker", args=[self.computer.pk])
        self.admin = get_user_model().objects.create_user(username="admin", is_staff=True)
        self.reader = get_user_model().objects.create_user(username="reader")

    @patch("index.domain.bitlocker.read_bitlocker_keys", return_value=[{"key_id": str(GUID), "password": PASSWORD, "created_at": ""}])
    def test_admin_post_only_and_no_task_persistence(self, read):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["records"][0]["password"], PASSWORD)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(read.call_args.args[1].pk, self.computer.pk)
        self.assertFalse(TaskRun.objects.exists())

    @patch("index.domain.bitlocker.read_bitlocker_keys")
    def test_guest_and_reader_cannot_read(self, read):
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        read.assert_not_called()

    def test_computer_list_replaces_unlock_with_single_device_button(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("domain_computer_list"))
        self.assertNotContains(response, 'value="unlock"')
        self.assertContains(response, self.url)
        self.assertContains(response, "BitLocker")

    @patch("index.domain.bitlocker.read_bitlocker_keys", side_effect=ValidationError("LDAP 权限不足。"))
    def test_lookup_error_is_uncached_and_retryable(self, read):
        self.client.force_login(self.admin)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 400)
        self.assertIn("权限不足", response.json()["message"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_csrf_and_old_unlock_endpoint_are_enforced(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(self.url).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse("domain_operation_create"), {
            "object_type": "computer", "action": "unlock", "target_ids": [str(self.computer.pk)],
        })
        self.assertFalse(TaskRun.objects.exists())

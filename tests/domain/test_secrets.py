import logging
from datetime import timedelta
from io import StringIO

from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from net.domain.secrets import consume_operation_secret, store_operation_secret
from net.models import DomainOperation


class DomainOperationSecretTests(TestCase):
    plaintext_password = 'Only-In-Memory-Password-9384!'

    def setUp(self):
        self.user = get_user_model().objects.create_user('domain-operator')
        self.operation = DomainOperation.objects.create(
            action=DomainOperation.Action.RESET_PASSWORD,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            requested_by=self.user,
            target_count=1,
            parameter_summary={},
        )
        self.fernet_key = Fernet.generate_key().decode('ascii')

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='')
    def test_password_secret_requires_configured_fernet_key(self):
        """Accepting an empty key would store recoverable or unusable password data."""
        with self.assertRaises(ValidationError) as error:
            store_operation_secret(self.operation, {'password': self.plaintext_password})

        self.assertNotIn(self.plaintext_password, str(error.exception))

    def test_secret_is_consumed_once_and_deleted_before_returning_plaintext(self):
        """Removing the delete would make a submitted password replayable by a later worker."""
        with self.settings(DOMAIN_OPERATION_ENCRYPTION_KEY=self.fernet_key):
            store_operation_secret(self.operation, {'password': self.plaintext_password})

            payload = consume_operation_secret(self.operation.pk)
            self.assertEqual(payload, {'password': self.plaintext_password})
            self.assertFalse(DomainOperation.objects.filter(pk=self.operation.pk, secret__isnull=False).exists())

            with self.assertRaises(ValidationError) as error:
                consume_operation_secret(self.operation.pk)

        self.assertNotIn(self.plaintext_password, str(error.exception))

    def test_expired_secret_is_deleted_and_error_contains_no_secret_material(self):
        """Keeping expired ciphertext would leave a password recoverable after its allowed lifetime."""
        with self.settings(DOMAIN_OPERATION_ENCRYPTION_KEY=self.fernet_key):
            store_operation_secret(
                self.operation,
                {'password': self.plaintext_password},
                expires_at=timezone.now() - timedelta(seconds=1),
            )
            encrypted = self.operation.secret.encrypted_payload.decode('ascii')

            with self.assertRaises(ValidationError) as error:
                consume_operation_secret(self.operation.pk)

        self.assertFalse(DomainOperation.objects.filter(pk=self.operation.pk, secret__isnull=False).exists())
        self.assertNotIn(self.plaintext_password, str(error.exception))
        self.assertNotIn(encrypted, str(error.exception))

    def test_secret_path_never_logs_plaintext_or_ciphertext(self):
        """Logging either representation would expose an operator password outside memory."""
        logger = logging.getLogger('net.domain')
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        try:
            with self.settings(DOMAIN_OPERATION_ENCRYPTION_KEY=self.fernet_key):
                store_operation_secret(self.operation, {'password': self.plaintext_password})
                encrypted = self.operation.secret.encrypted_payload.decode('ascii')
                consume_operation_secret(self.operation.pk)
        finally:
            logger.removeHandler(handler)

        logs = stream.getvalue()
        self.assertNotIn(self.plaintext_password, logs)
        self.assertNotIn(encrypted, logs)

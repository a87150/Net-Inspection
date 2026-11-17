"""Tests for line-level configuration comparison."""
import hashlib
from types import SimpleNamespace

from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from net.devices.configuration_diff import diff_backups, text_of


def _backup(key, text):
    raw = text.encode("utf-8")
    return SimpleNamespace(
        ciphertext=Fernet(key).encrypt(raw),
        byte_size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


class ConfigurationDiffTests(SimpleTestCase):
    key = Fernet.generate_key().decode()

    def _pair(self, old, new):
        with override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=self.key):
            return diff_backups(_backup(self.key, old), _backup(self.key, new))

    def test_identical_versions_report_no_change(self):
        result = self._pair("hostname sw1\n!\n", "hostname sw1\n!\n")
        self.assertFalse(result["changed"])
        self.assertEqual((result["added"], result["removed"]), (0, 0))
        self.assertTrue(all(row["kind"] == "same" for row in result["rows"]))

    def test_added_and_removed_lines_carry_line_numbers(self):
        result = self._pair("a\nb\nc\n", "a\nc\nd\n")
        self.assertEqual((result["added"], result["removed"]), (1, 1))
        kinds = [(row["kind"], row["text"]) for row in result["rows"]]
        self.assertIn(("remove", "b"), kinds)
        self.assertIn(("add", "d"), kinds)
        removed = next(row for row in result["rows"] if row["kind"] == "remove")
        self.assertEqual((removed["left"], removed["right"]), (2, None))

    def test_crlf_is_normalized_before_comparison(self):
        with override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=self.key):
            self.assertEqual(text_of(_backup(self.key, "a\r\nb\r\n")), "a\nb\n")

    def test_repeated_short_lines_stay_aligned(self):
        old = "!\n" * 50 + "ntp server 10.0.0.1\n" + "!\n" * 50
        new = "!\n" * 50 + "ntp server 10.0.0.2\n" + "!\n" * 50
        result = self._pair(old, new)
        self.assertEqual((result["added"], result["removed"]), (1, 1))

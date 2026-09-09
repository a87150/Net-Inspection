from importlib.metadata import version
from pathlib import Path
import sys
from unittest import TestCase


PROJECT_ROOT = Path(__file__).resolve().parents[2]

LATEST_STABLE_DIRECT_DEPENDENCIES = {
    "Django": "6.1.1",
    "djangorestframework": "3.18.0",
    "ldap3": "2.9.1",
    "cryptography": "50.0.1",
    "mysqlclient": "2.2.8",
    "requests": "2.34.2",
    "aiohttp": "3.14.3",
    "dnspython": "2.8.0",
    "paramiko": "4.0.0",
    "netmiko": "4.7.0",
    "redis": "8.1.0",
    "python-dotenv": "1.2.3",
    "keyring": "25.7.0",
    "pycryptodome": "3.23.0",
    "pysnmp": "7.1.29",
    "smbprotocol": "1.17.0",
    "waitress": "3.0.2",
    "whitenoise": "6.12.0",
}


def read_exact_requirements(path):
    requirements = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pin, _, marker = line.partition(';')
        if marker:
            if marker.strip().replace("'", '"') != 'sys_platform == "win32"':
                raise AssertionError(f'Unsupported dependency marker: {marker}')
            if sys.platform != 'win32':
                continue
        name, separator, required_version = pin.strip().partition("==")
        if separator != "==":
            raise AssertionError(f"Dependency must use an exact pin: {line}")
        requirements[name] = required_version
    return requirements


class DependencyContractTests(TestCase):
    def test_direct_dependencies_are_exactly_pinned_and_installed(self):
        requirements = read_exact_requirements(PROJECT_ROOT / "requirements.txt")

        self.assertEqual(requirements, LATEST_STABLE_DIRECT_DEPENDENCIES)
        self.assertEqual(
            {name: version(name) for name in requirements},
            LATEST_STABLE_DIRECT_DEPENDENCIES,
        )

    def test_lock_file_covers_the_installed_dependency_closure(self):
        lock_path = PROJECT_ROOT / "requirements.lock.txt"
        self.assertTrue(lock_path.exists(), "requirements.lock.txt is missing")
        locked = read_exact_requirements(lock_path)

        self.assertGreater(len(locked), len(LATEST_STABLE_DIRECT_DEPENDENCIES))
        self.assertEqual(
            {name: locked[name] for name in LATEST_STABLE_DIRECT_DEPENDENCIES},
            LATEST_STABLE_DIRECT_DEPENDENCIES,
        )
        self.assertEqual(
            {name: version(name) for name in locked},
            locked,
        )

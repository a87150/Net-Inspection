from importlib.metadata import version
from pathlib import Path
import sys
from unittest import TestCase


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
        # psycopg[binary] 的发行名仍是 psycopg，去掉 extras 才能查到已安装版本。
        requirements[name.partition("[")[0]] = required_version
    return requirements


class DependencyContractTests(TestCase):
    """requirements / lock / 已安装三方一致，但不钉死具体版本号。

    早期版本把 19 个依赖的最新版本写成常量再双向断言，任何升级都会被当成契约
    破坏拦下，安全公告只能越积越多。这里只保证三份清单互相对齐，升级由审计结果
    驱动，测试跟着走。
    """

    def test_direct_dependencies_are_exactly_pinned_and_installed(self):
        requirements = read_exact_requirements(PROJECT_ROOT / "requirements.txt")

        self.assertTrue(requirements, "requirements.txt 里没有任何依赖")
        self.assertEqual(
            {name: version(name) for name in requirements},
            requirements,
        )

    def test_lock_file_covers_the_installed_dependency_closure(self):
        lock_path = PROJECT_ROOT / "requirements.lock.txt"
        self.assertTrue(lock_path.exists(), "requirements.lock.txt is missing")
        requirements = read_exact_requirements(PROJECT_ROOT / "requirements.txt")
        locked = read_exact_requirements(lock_path)

        self.assertGreaterEqual(len(locked), len(requirements))
        for name, pinned in requirements.items():
            self.assertIn(name, locked, f"{name} 在 lock 文件中缺失")
            self.assertEqual(
                locked[name], pinned,
                f"{name} 在 requirements.txt 与 lock 文件中的版本不一致",
            )
        self.assertEqual(
            {name: version(name) for name in locked},
            locked,
        )

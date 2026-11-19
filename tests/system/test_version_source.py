"""版本号只有一个事实源：仓库根目录的 VERSION。"""

import re
from pathlib import Path

import net
from django.test import SimpleTestCase


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class VersionSourceTests(SimpleTestCase):
    def test_runtime_version_reads_the_version_file(self):
        expected = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()

        self.assertEqual(net.__version__, expected)

    def test_readme_header_matches_the_version_file(self):
        version = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        header = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]

        self.assertIn(version, header, "README 首行的版本号要与 VERSION 一致")

    def test_no_module_hardcodes_a_version_string(self):
        """曾经 net/__init__.py 写死 1.4.0，README 写 1.50，VERSION 又是第三个数。"""
        offenders = []
        for path in (PROJECT_ROOT / "net").rglob("*.py"):
            if "migrations" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r'__version__\s*=\s*"[0-9]', text):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))

        self.assertEqual(offenders, [], "版本号只写在 VERSION 文件里，不要在模块里硬编码")

"""`.env.example` 必须覆盖 settings 会读的每一个变量。"""

import re
from pathlib import Path

from django.test import SimpleTestCase


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATTERN = re.compile(r"os\.getenv\(\s*['\"]([A-Z0-9_]+)['\"]")


def read_env_names():
    names = set()
    for path in (PROJECT_ROOT / "net").rglob("*.py"):
        if "migrations" in path.parts:
            continue
        try:
            names.update(ENV_PATTERN.findall(path.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, OSError):
            continue
    return names


class EnvExampleCoverageTests(SimpleTestCase):
    def test_every_env_variable_settings_reads_is_documented(self):
        example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        declared = set(re.findall(r"^#?\s*([A-Z0-9_]+)=", example, re.M))

        missing = sorted(read_env_names() - declared)
        self.assertEqual(missing, [], "".join([
            "以下变量被代码读取但 .env.example 没有列出：",
            ", ".join(missing),
            "。部署时漏配会静默走默认值。",
        ]))

    def test_example_does_not_teach_the_test_database_collision(self):
        """.env.example 曾经建议 DB_TEST_NAME=net-test，那正是删演示库的写法。"""
        example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertNotIn("DB_TEST_NAME=net-test", example)
        self.assertIn("DB_DEMO_NAME=net-test", example)

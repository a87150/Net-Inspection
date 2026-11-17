"""Every Vue page declared in a template must be registered in boot.mjs."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

TEMPLATE_ROOT = Path(settings.BASE_DIR) / "index" / "templates"
BOOT = Path(settings.BASE_DIR) / "static" / "app" / "vue" / "boot.mjs"


class VuePageRegistryTests(SimpleTestCase):
    def _declared_pages(self):
        pages = set()
        for template in TEMPLATE_ROOT.rglob("*.html"):
            text = template.read_text(encoding="utf-8")
            pages.update(re.findall(r'data-vue-page="([^"]+)"', text))
        return pages

    def test_declared_pages_are_registered(self):
        boot = BOOT.read_text(encoding="utf-8")
        registered = set(re.findall(r"'([a-z0-9-]+)': \(\) => import\(", boot))
        missing = self._declared_pages() - registered
        self.assertEqual(missing, set(), f"boot.mjs 未登记：{sorted(missing)}")

    def test_registered_pages_exist_on_disk(self):
        boot = BOOT.read_text(encoding="utf-8")
        for target in re.findall(r"import\('\./pages/([^']+)'\)", boot):
            path = BOOT.parent / "pages" / target
            self.assertTrue(path.exists(), f"缺少组件文件：{path}")

    def test_every_registered_page_is_used_by_a_template(self):
        boot = BOOT.read_text(encoding="utf-8")
        registered = set(re.findall(r"'([a-z0-9-]+)': \(\) => import\(", boot))
        self.assertLessEqual(registered, self._declared_pages())

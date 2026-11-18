"""Every Vue page declared in a template must be registered in boot.mjs."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from tests.frontend.template_root import TemplateRoot

BOOT = Path(settings.BASE_DIR) / "static" / "app" / "vue" / "boot.mjs"


class VuePageRegistryTests(SimpleTestCase):
    def _declared_pages(self):
        pages = set()
        for template in TemplateRoot.default().rglob("*.html"):
            text = Path(template).read_text(encoding="utf-8")
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

    def test_import_paths_resolve(self):
        """静态模块的相对 import 写错时页面会整块白屏，浏览器只报 404。"""
        for module in sorted(BOOT.parent.rglob("*.mjs")):
            text = module.read_text(encoding="utf-8")
            for target in re.findall(r"from '(\.{1,2}/[^']+)'", text):
                resolved = (module.parent / target).resolve()
                self.assertTrue(
                    resolved.exists(), f"{module.name} 引用了不存在的模块：{target}",
                )

    def test_every_registered_page_is_used_by_a_template(self):
        boot = BOOT.read_text(encoding="utf-8")
        registered = set(re.findall(r"'([a-z0-9-]+)': \(\) => import\(", boot))
        self.assertLessEqual(registered, self._declared_pages())

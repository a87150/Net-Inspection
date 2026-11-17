"""Template lookup for contract tests.

Each app owns a templates/ directory now, so tests search across app roots
instead of hardcoding index/templates.
"""
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
APPS = (
    "index", "people", "domain", "alerts", "topology", "ipam",
    "pc", "server", "weakcurrent", "inspections", "assets", "network",
)


class TemplateRoot:
    """A search path: '/' narrows it, and a single hit resolves to a real Path."""

    def __init__(self, *roots):
        self.roots = tuple(roots)

    @classmethod
    def default(cls):
        return cls(*(BASE / app / "templates" for app in APPS))

    def __truediv__(self, name):
        matches = tuple(root / name for root in self.roots if (root / name).exists())
        if not matches:
            raise FileNotFoundError(
                f"找不到模板：{name}（搜索 {[str(root) for root in self.roots]}）",
            )
        if len(matches) == 1:
            return matches[0]
        return TemplateRoot(*matches)

    def rglob(self, pattern):
        for root in self.roots:
            yield from root.rglob(pattern)

from dataclasses import dataclass, field
from time import monotonic


@dataclass
class CollectionResult:
    reachable: bool
    status: str
    message: str = ''
    data: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    duration_ms: int = 0


class Timer:
    def __enter__(self):
        self.started = monotonic()
        return self

    def __exit__(self, *_):
        self.duration_ms = max(0, round((monotonic() - self.started) * 1000))

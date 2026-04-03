from dataclasses import dataclass

from k_pilot.core.domain.common.enums import Priority


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    priority: Priority = Priority.NORMAL
    icon: str = ""
    timeout_ms: int = 5000

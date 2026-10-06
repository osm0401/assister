from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Todo:
    id: int
    text: str
    done: bool
    created_at: datetime


@dataclass(frozen=True)
class Alarm:
    id: int
    message: str
    due_at: datetime
    fired: bool
    repeat: str = ""  # "daily"면 울린 뒤 다음 날 같은 시각으로 옮겨 간다

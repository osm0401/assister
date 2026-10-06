"""입력창에 친 말에서 할 일·알람 명령을 알아낸다.

AI가 연결되기 전에도 알람과 할 일을 쓸 수 있게 하는 간단한 규칙이다.
여기서 알아듣지 못한 말은 NotACommand로 돌려주고, 그 말은 AI 대화로 넘어간다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

DEFAULT_ALARM_MESSAGE = "알람"
UNIT_SECONDS = {"초": 1, "분": 60, "시간": 3600}

RELATIVE = re.compile(r"^(\d+)\s*(초|분|시간)\s*(?:뒤|후|있다가)(?:에)?\s*(.*)$")
ABSOLUTE = re.compile(r"^(오전|오후)\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분|\s*(반))?(?:에)?\s*(.*)$")
ADD_TODO = re.compile(r"^할\s?일\s*(?:추가|:)\s*(.+)$")
LIST_TODOS = re.compile(r"^할\s?일(?:\s*목록)?$")
COMPLETE = re.compile(r"^(?:(\d+)\s*번?\s*완료|완료\s*(\d+)\s*번?)$")


@dataclass(frozen=True)
class AddAlarm:
    due_at: datetime
    message: str


@dataclass(frozen=True)
class AddTodo:
    text: str


@dataclass(frozen=True)
class ListTodos:
    pass


@dataclass(frozen=True)
class CompleteTodo:
    number: int


@dataclass(frozen=True)
class NotACommand:
    text: str


Command = AddAlarm | AddTodo | ListTodos | CompleteTodo | NotACommand


def _absolute_time(match: re.Match[str], now: datetime) -> datetime | None:
    hour, minute = int(match.group(2)), int(match.group(3) or 0)
    if match.group(4):
        minute = 30
    if not (1 <= hour <= 12 and 0 <= minute <= 59):
        return None
    hour = hour % 12 + (12 if match.group(1) == "오후" else 0)
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # 이미 지난 시각이면 내일로 잡는다.
    return due if due > now else due + timedelta(days=1)


SPOKEN_CLOCK = re.compile(r"(오전|오후|아침|저녁|새벽|밤|낮)\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분|\s*(반))?")
MORNING_WORDS = ("오전", "아침", "새벽")
DAY_WORDS = {"오늘": 0, "내일": 1, "모레": 2, "글피": 3}


def spoken_clock(text: str) -> str | None:
    """'오후 6시 반'처럼 오전·오후가 분명한 시각을 24시간 'HH:MM'으로 읽는다.

    그런 시각이 없거나 둘 이상이면 None. AI가 시각을 암산하다 틀리는 것을 바로잡는 데 쓴다.
    """
    found = SPOKEN_CLOCK.findall(text)
    if len(found) != 1:
        return None
    period, hour_text, minute_text, half = found[0]
    hour, minute = int(hour_text), 30 if half else int(minute_text or 0)
    if not (1 <= hour <= 12 and 0 <= minute <= 59):
        return None
    if period in MORNING_WORDS:
        hour %= 12
    elif period == "밤":
        # '밤 12시'는 자정, '밤 2시'는 새벽 2시, '밤 11시'는 23시.
        hour = hour % 12 if hour == 12 or hour <= 4 else hour + 12
    else:  # 오후, 저녁, 낮
        hour = hour % 12 + 12
    return f"{hour:02d}:{minute:02d}"


def spoken_day_offset(text: str) -> int | None:
    """'내일', '모레'처럼 날을 가리키는 말이 있으면 오늘부터 며칠 뒤인지.

    그런 말이 없거나, 서로 다른 날을 둘 이상 말했으면 None.
    """
    plain = text.replace("내일모레", "모레")
    offsets = {offset for word, offset in DAY_WORDS.items() if word in plain}
    return offsets.pop() if len(offsets) == 1 else None


def parse_command(text: str, now: datetime) -> Command:
    cleaned = text.strip()

    if match := RELATIVE.match(cleaned):
        seconds = int(match.group(1)) * UNIT_SECONDS[match.group(2)]
        return AddAlarm(now + timedelta(seconds=seconds), match.group(3).strip() or DEFAULT_ALARM_MESSAGE)

    if match := ABSOLUTE.match(cleaned):
        due = _absolute_time(match, now)
        if due is not None:
            return AddAlarm(due, match.group(5).strip() or DEFAULT_ALARM_MESSAGE)

    if match := ADD_TODO.match(cleaned):
        return AddTodo(match.group(1).strip())

    if LIST_TODOS.match(cleaned):
        return ListTodos()

    if match := COMPLETE.match(cleaned):
        return CompleteTodo(int(match.group(1) or match.group(2)))

    return NotACommand(cleaned)

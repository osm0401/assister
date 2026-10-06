"""AI가 고를 수 있는 작업(도구). 알람·할 일·기억을 실제로 바꾸는 유일한 통로다.

작업 결과는 Outcome으로 돌려준다. 알람·할 일의 확인 문장은 여기서 실제 값으로 만들어
사용자에게 그대로 보여 준다. AI에게 다시 말하게 하면 시각을 틀리게 옮기는 일이 있어서다.
AI가 잘못된 값을 주는 일도 흔하므로, 그럴 때는 예외를 던지지 않고 ok=False인 결과를
돌려줘서 AI가 사용자에게 사실대로 말하거나 되물을 수 있게 한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from assister.memory.store import MemoryStore
from assister.tasks.models import Alarm
from assister.tasks.repository import TaskRepository
from assister.tasks.service import format_time

MAX_MINUTES = 60 * 24 * 365
MAX_DAY_OFFSET = 365
REPEATS = ("", "daily")
MAX_REVIEW_DAYS = 7  # 화면 기록을 보관하는 기본 기간
REVIEW_SLOT_MINUTES = 30


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


SCHEMAS = [
    _schema(
        "add_alarm",
        "정해진 때가 되면 사용자에게 알려 주는 알람을 등록한다. "
        "사용자가 '몇 분 뒤', '몇 시간 뒤'라고 하면 minutes_from_now만 쓴다. "
        "'6시 반', '내일 9시'처럼 시각을 말하면 time만 쓰고 minutes_from_now는 쓰지 않는다. 직접 계산하지 않는다.",
        {
            "message": {"type": "string", "description": "알람 때 알려 줄 내용을 짧게. 예: 빨래 꺼내기"},
            "minutes_from_now": {"type": "integer", "description": "지금부터 몇 분 뒤인지. 2시간 뒤면 120"},
            "time": {"type": "string", "description": "24시간 형식 시각 HH:MM. 오후 6시 반은 18:30, 아침 9시는 09:00"},
            "day_offset": {"type": "integer", "description": "time과 함께 쓴다. 오늘은 0, 내일은 1, 모레는 2"},
            "date": {"type": "string", "description": "날짜나 요일을 말했을 때 [달력]에서 찾은 날짜 YYYY-MM-DD. time과 함께 쓴다"},
            "repeat": {"type": "string", "description": "'매일'이라고 했으면 daily, 아니면 빈 글"},
        },
        ["message"],
    ),
    _schema("list_alarms", "아직 울리지 않은 알람을 보여 준다.", {}, []),
    _schema(
        "cancel_alarm",
        "알람 하나를 취소한다. 번호는 [지금 울릴 알람]에 적힌 번호다.",
        {"number": {"type": "integer", "description": "취소할 알람의 번호"}},
        ["number"],
    ),
    _schema("add_todo", "할 일 목록에 한 가지를 적는다.", {"text": {"type": "string", "description": "할 일 내용"}}, ["text"]),
    _schema("list_todos", "남은 할 일을 번호와 함께 보여 준다.", {}, []),
    _schema(
        "complete_todo",
        "할 일을 끝낸 것으로 표시한다. 번호는 [지금 남은 할 일]에 적힌 번호다.",
        {"number": {"type": "integer", "description": "끝낸 할 일의 번호"}},
        ["number"],
    ),
    _schema(
        "review_day",
        "사용자가 그날 PC로 무엇을 했는지 화면 기록을 찾아본다. '오늘 나 뭐 했어?', '어제 뭐 했지?' 같은 물음에 쓴다.",
        {"day_offset": {"type": "integer", "description": "오늘은 0, 어제는 -1, 그저께는 -2"}},
        ["day_offset"],
    ),
    _schema(
        "control_pc",
        "화면을 보면서 마우스와 키보드로 PC를 대신 조작한다. 프로그램 열기, 글 쓰기, 버튼 누르기, 웹 페이지 열기 같은 부탁에 쓴다.",
        {"goal": {"type": "string", "description": "사용자가 PC에서 해 달라고 한 일을 그대로 한 문장으로"}},
        ["goal"],
    ),
    _schema(
        "remember",
        "사용자에 대해 오래 기억해 둘 사실 한 가지를 적어 둔다. 이름, 취향, 생활 습관 같은 것.",
        {"fact": {"type": "string", "description": "무엇에 대한 사실인지 드러나는 짧은 문장. 예: 이름은 상민, 고양이 두 마리를 키움"}},
        ["fact"],
    ),
]


def _action_format(schemas: list[dict]) -> dict:
    """작업 목록을 받을 틀. 작업 하나는 도구 이름과, 그 도구에 필요한 칸들로 이루어진다."""
    fields: dict = {"tool": {"type": "string", "enum": [schema["function"]["name"] for schema in schemas]}}
    for schema in schemas:
        for name, info in schema["function"]["parameters"]["properties"].items():
            fields.setdefault(name, {"type": info["type"]})
    action = {"type": "object", "properties": fields, "required": ["tool"]}
    # 여기에 칸을 더하면 작업 선택이 흐트러진다. '작업 말고 대답할 나머지 말'을 함께 받아 봤더니
    # 그냥 하는 말에도 목록 조회를 붙이기 시작해서(실제 모델로 18마디 중 2개 틀림) 뺐다.
    return {
        "type": "object",
        "properties": {"actions": {"type": "array", "items": action}},
        "required": ["actions"],
    }


ACTION_FORMAT = _action_format(SCHEMAS)


@dataclass(frozen=True)
class Outcome:
    text: str
    shown: bool = True  # True면 사용자에게 그대로 보여 주는 확인 문장, False면 AI만 아는 결과
    ok: bool = True


class ToolError(Exception):
    """AI가 준 값으로는 도구를 실행할 수 없을 때."""


def _when(alarm: Alarm, now: datetime) -> str:
    """알람이 울릴 때를 읽기 쉽게. 매일 울리는 알람은 날짜 대신 '매일'이라고 한다."""
    if alarm.repeat == "daily":
        return f"매일 {format_time(alarm.due_at, alarm.due_at)}"
    return format_time(alarm.due_at, now)


def _integer(arguments: dict, key: str) -> int | None:
    value = arguments.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ToolError(f"{key}는 숫자여야 합니다")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ToolError(f"{key}는 숫자여야 합니다: {value!r}") from error


def _text(arguments: dict, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{key}가 비어 있습니다")
    return value.strip()


class ToolBox:
    schemas = SCHEMAS

    def __init__(
        self, tasks: TaskRepository, memory: MemoryStore, *, start_control: Callable[[str], bool] | None = None
    ) -> None:
        """start_control은 PC 조작을 시작시키는 함수다. 시작했으면 True, 이미 다른 조작 중이면 False를 돌려준다."""
        self._tasks = tasks
        self._memory = memory
        self._start_control = start_control
        self._handlers = {
            "review_day": self._review_day,
            "control_pc": self._control_pc,
            "add_alarm": self._add_alarm,
            "list_alarms": self._list_alarms,
            "cancel_alarm": self._cancel_alarm,
            "add_todo": self._add_todo,
            "list_todos": self._list_todos,
            "complete_todo": self._complete_todo,
            "remember": self._remember,
        }

    def execute(self, name: str, arguments: dict, now: datetime) -> Outcome:
        handler = self._handlers.get(name)
        if handler is None:
            return Outcome(f"모르는 작업입니다: {name}", shown=False, ok=False)
        try:
            return handler(arguments if isinstance(arguments, dict) else {}, now)
        except ToolError as error:
            return Outcome(str(error), shown=False, ok=False)

    def _alarm_time(self, arguments: dict, now: datetime) -> datetime:
        # 안 쓰는 칸이 0이나 빈 글로 채워져 오기도 해서, 그런 값은 없는 것으로 본다.
        minutes = _integer(arguments, "minutes_from_now")
        if minutes:
            if not 0 < minutes <= MAX_MINUTES:
                raise ToolError("minutes_from_now는 1 이상이어야 합니다")
            return now + timedelta(minutes=minutes)

        clock = str(arguments.get("time") or "").strip()
        if not clock:
            raise ToolError("minutes_from_now나 time 중 하나가 필요합니다")
        try:
            parsed = datetime.strptime(clock, "%H:%M")
        except ValueError as error:
            raise ToolError(f"time은 HH:MM 형식이어야 합니다: {clock!r}") from error
        # 매일 울리는 알람에는 날짜가 뜻이 없다. 다음에 오는 그 시각부터 시작한다.
        repeats = bool(str(arguments.get("repeat") or "").strip())
        day = "" if repeats else str(arguments.get("date") or "").strip()
        if day:
            try:
                on_day = datetime.strptime(day, "%Y-%m-%d").replace(hour=parsed.hour, minute=parsed.minute)
            except ValueError as error:
                raise ToolError(f"date는 YYYY-MM-DD 형식이어야 합니다: {day!r}") from error
            if on_day <= now:
                raise ToolError(f"{day} {clock}은(는) 이미 지난 때입니다")
            return on_day

        day_offset = 0 if repeats else _integer(arguments, "day_offset") or 0
        if not 0 <= day_offset <= MAX_DAY_OFFSET:
            raise ToolError("day_offset은 0 이상이어야 합니다")
        due = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0) + timedelta(days=day_offset)
        # 오늘의 이미 지난 시각을 말했다면 내일로 잡는다.
        return due if due > now else due + timedelta(days=1)

    def _add_alarm(self, arguments: dict, now: datetime) -> Outcome:
        repeat = str(arguments.get("repeat") or "").strip().lower()
        if repeat not in REPEATS:
            raise ToolError(f"repeat은 daily이거나 비어 있어야 합니다: {repeat!r}")
        due = self._alarm_time(arguments, now)
        message = arguments.get("message")
        alarm = self._tasks.add_alarm(message if isinstance(message, str) else "", due, repeat=repeat)
        return Outcome(f"{_when(alarm, now)}에 '{alarm.message}' 알려 줄게요.")

    def state_summary(self, now: datetime) -> str:
        """지금 남은 할 일과 알람을 번호와 함께 적은 글. AI가 '1번', '그 알람'이 무엇인지 알 수 있게 보여 준다."""
        todos = [f"{number}. {todo.text}" for number, todo in enumerate(self._tasks.list_todos(), start=1)]
        alarms = self._numbered_alarms(now)
        return (
            "[지금 남은 할 일]\n" + ("\n".join(todos) or "없음") + "\n\n[지금 울릴 알람]\n" + ("\n".join(alarms) or "없음")
        )

    def _numbered_alarms(self, now: datetime) -> list[str]:
        alarms = self._tasks.list_pending_alarms()
        return [f"{number}. {_when(alarm, now)} {alarm.message}" for number, alarm in enumerate(alarms, 1)]

    def _list_alarms(self, arguments: dict, now: datetime) -> Outcome:
        alarms = self._numbered_alarms(now)
        if not alarms:
            return Outcome("울릴 알람이 없어요.")
        return Outcome(f"울릴 알람이에요. {', '.join(alarms)}")

    def _cancel_alarm(self, arguments: dict, now: datetime) -> Outcome:
        number = _integer(arguments, "number")
        alarms = self._tasks.list_pending_alarms()
        if number is None or not 1 <= number <= len(alarms):
            raise ToolError(f"{number}번 알람이 없습니다. 지금 울릴 알람은 {len(alarms)}개입니다")
        alarm = alarms[number - 1]
        self._tasks.delete_alarm(alarm.id)
        return Outcome(f"{_when(alarm, now)} '{alarm.message}' 알람을 취소했어요.")

    def _add_todo(self, arguments: dict, now: datetime) -> Outcome:
        todo = self._tasks.add_todo(_text(arguments, "text"), now)
        return Outcome(f"할 일에 '{todo.text}' 적어 뒀어요.")

    def _list_todos(self, arguments: dict, now: datetime) -> Outcome:
        todos = self._tasks.list_todos()
        if not todos:
            return Outcome("남은 할 일이 없어요.")
        listed = ", ".join(f"{number}. {todo.text}" for number, todo in enumerate(todos, start=1))
        return Outcome(f"남은 할 일이에요. {listed}")

    def _complete_todo(self, arguments: dict, now: datetime) -> Outcome:
        number = _integer(arguments, "number")
        todos = self._tasks.list_todos()
        if number is None or not 1 <= number <= len(todos):
            raise ToolError(f"{number}번 할 일이 없습니다. 지금 남은 할 일은 {len(todos)}개입니다")
        done = self._tasks.complete_todo(todos[number - 1].id)
        return Outcome(f"'{done.text}' 끝낸 걸로 표시했어요.")

    def _review_day(self, arguments: dict, now: datetime) -> Outcome:
        offset = _integer(arguments, "day_offset") or 0
        if not -MAX_REVIEW_DAYS <= offset <= 0:
            raise ToolError(f"화면 기록은 오늘부터 {MAX_REVIEW_DAYS}일 전까지만 볼 수 있습니다")
        day = now + timedelta(days=offset)
        # 하루치 기록은 수천 줄이 될 수 있어, 30분에 하나씩만 추려서 AI에게 보여 준다.
        seen: set[tuple[int, int]] = set()
        lines = []
        for note in self._memory.screen_notes_on(day):
            slot = (note.created_at.hour, note.created_at.minute // REVIEW_SLOT_MINUTES)
            if slot not in seen:
                seen.add(slot)
                lines.append(f"- {format_time(note.created_at, note.created_at)} {note.summary}")
        if not lines:
            return Outcome(f"{day.month}월 {day.day}일에는 화면 기록이 없음", shown=False)
        return Outcome(f"{day.month}월 {day.day}일에 사용자의 화면에서 본 것\n" + "\n".join(lines), shown=False)

    def _control_pc(self, arguments: dict, now: datetime) -> Outcome:
        goal = _text(arguments, "goal")
        if self._start_control is None:
            raise ToolError("PC 조작을 쓸 수 없습니다")
        if not self._start_control(goal):
            return Outcome("이미 다른 일을 하고 있어요. 끝나면 다시 말해 주세요.")
        return Outcome("해 볼게요. 멈추려면 Esc를 누르세요.")

    def _remember(self, arguments: dict, now: datetime) -> Outcome:
        text = _text(arguments, "fact")
        known = any(fact.text == text for fact in self._memory.list_facts())
        self._memory.add_fact(text, now)
        # 기억했다는 확인은 따로 띄우지 않는다. AI가 대화 속에서 자연스럽게 받는다.
        label = "이미 기억하고 있음" if known else "사용자가 방금 알려 줘서 새로 기억해 둔 사실"
        return Outcome(f"{label}: {text}", shown=False)

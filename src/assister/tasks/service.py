"""할 일·알람 요청을 처리하고 캐릭터가 할 말을 만든다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from assister.tasks.commands import AddAlarm, AddTodo, CompleteTodo, ListTodos, NotACommand, parse_command
from assister.tasks.repository import TaskRepository


@dataclass(frozen=True)
class Reply:
    text: str
    expression: str = "talking"


def format_time(moment: datetime, now: datetime) -> str:
    """'오후 3:10'처럼 읽기 쉬운 시각. 날짜가 다르면 앞에 붙인다."""
    period = "오전" if moment.hour < 12 else "오후"
    clock = f"{period} {moment.hour % 12 or 12}:{moment.minute:02d}"
    days = (moment.date() - now.date()).days
    if days == 0:
        return clock
    if days == 1:
        return f"내일 {clock}"
    return f"{moment.month}월 {moment.day}일 {clock}"


class TaskService:
    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

    def handle(self, text: str, now: datetime) -> Reply | None:
        """할 일·알람 요청이면 처리하고 답을 돌려준다. 아니면 None."""
        command = parse_command(text, now)

        if isinstance(command, NotACommand):
            return None

        if isinstance(command, AddAlarm):
            alarm = self.repository.add_alarm(command.message, command.due_at)
            return Reply(f"{format_time(alarm.due_at, now)}에 '{alarm.message}' 알려 줄게요.", "smile")

        if isinstance(command, AddTodo):
            todo = self.repository.add_todo(command.text, now)
            return Reply(f"할 일에 '{todo.text}' 적어 뒀어요.", "smile")

        if isinstance(command, ListTodos):
            todos = self.repository.list_todos()
            if not todos:
                return Reply("남은 할 일이 없어요.", "smile")
            lines = [f"{number}. {todo.text}" for number, todo in enumerate(todos, start=1)]
            return Reply("남은 할 일이에요.\n" + "\n".join(lines))

        if isinstance(command, CompleteTodo):
            todos = self.repository.list_todos()
            if not 1 <= command.number <= len(todos):
                return Reply(f"{command.number}번 할 일을 찾지 못했어요.", "thinking")
            done = self.repository.complete_todo(todos[command.number - 1].id)
            return Reply(f"'{done.text}' 완료! 수고했어요.", "excited")

        raise TypeError(f"처리할 수 없는 명령입니다: {command!r}")

    def ring_due_alarms(self, now: datetime) -> list[Reply]:
        """시간이 된 알람을 울리고, 다시 울리지 않게 표시한다."""
        replies = []
        for alarm in self.repository.due_alarms(now):
            if alarm.repeat == "daily":
                # 며칠 밀렸어도 한 번만 울리고, 다음에 올 같은 시각으로 옮긴다.
                days_behind = (now - alarm.due_at).days + 1
                self.repository.reschedule_alarm(alarm.id, alarm.due_at + timedelta(days=days_behind))
            else:
                self.repository.mark_alarm_fired(alarm.id)
            replies.append(Reply(f"{alarm.message} 시간이에요!", "surprised"))
        return replies

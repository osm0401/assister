from datetime import datetime, timedelta

import pytest

from assister.tasks.commands import (
    AddAlarm,
    AddTodo,
    CompleteTodo,
    ListTodos,
    NotACommand,
    parse_command,
    spoken_clock,
    spoken_day_offset,
)
from assister.tasks.repository import TaskRepository

NOW = datetime(2026, 10, 6, 15, 0, 0)


@pytest.fixture
def repo(tmp_path):
    repository = TaskRepository(tmp_path / "tasks.db")
    yield repository
    repository.close()


def test_added_todos_are_listed_in_order(repo):
    repo.add_todo("우유 사기", NOW)
    repo.add_todo("메일 답장", NOW + timedelta(minutes=1))

    assert [todo.text for todo in repo.list_todos()] == ["우유 사기", "메일 답장"]


def test_completed_todo_leaves_the_open_list(repo):
    first = repo.add_todo("우유 사기", NOW)
    repo.add_todo("메일 답장", NOW)

    done = repo.complete_todo(first.id)

    assert done is not None and done.done is True
    assert [todo.text for todo in repo.list_todos()] == ["메일 답장"]
    assert len(repo.list_todos(include_done=True)) == 2


def test_completing_unknown_todo_returns_none(repo):
    assert repo.complete_todo(999) is None


def test_blank_todo_is_rejected(repo):
    with pytest.raises(ValueError):
        repo.add_todo("   ", NOW)


def test_alarm_is_due_only_after_its_time(repo):
    alarm = repo.add_alarm("빨래 꺼내기", NOW + timedelta(minutes=10))

    assert repo.due_alarms(NOW + timedelta(minutes=9)) == []
    assert repo.due_alarms(NOW + timedelta(minutes=10)) == [alarm]


def test_fired_alarm_does_not_ring_again(repo):
    alarm = repo.add_alarm("빨래 꺼내기", NOW)

    repo.mark_alarm_fired(alarm.id)

    assert repo.due_alarms(NOW + timedelta(hours=1)) == []
    assert repo.list_pending_alarms() == []


def test_daily_alarm_moves_to_the_next_day_instead_of_ending(repo):
    alarm = repo.add_alarm("약 먹기", NOW, repeat="daily")

    repo.reschedule_alarm(alarm.id, NOW + timedelta(days=1))

    pending = repo.list_pending_alarms()
    assert [(a.message, a.due_at, a.repeat) for a in pending] == [("약 먹기", NOW + timedelta(days=1), "daily")]


def test_database_made_before_repeating_alarms_still_opens(tmp_path):
    import sqlite3

    path = tmp_path / "tasks.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE alarms (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT NOT NULL, due_at TEXT NOT NULL,"
        " fired INTEGER NOT NULL DEFAULT 0);"
        "INSERT INTO alarms (message, due_at) VALUES ('예전 알람', '2026-10-06T16:00:00');"
    )
    old.commit()
    old.close()

    repository = TaskRepository(path)
    try:
        assert [(a.message, a.repeat) for a in repository.list_pending_alarms()] == [("예전 알람", "")]
        repository.add_alarm("새 알람", NOW, repeat="daily")
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("오후 6시 반에 저녁 약속 있어", "18:30"),
        ("오전 10시 반에 병원", "10:30"),
        ("저녁 8시에 운동 가라고 알려줘", "20:00"),
        ("아침 9시에 회의", "09:00"),
        ("새벽 2시에 깨워 줘", "02:00"),
        ("밤 11시에 자라고 해 줘", "23:00"),
        ("밤 12시에 알려 줘", "00:00"),
        ("밤 2시에 알려 줘", "02:00"),
        ("낮 12시에 점심", "12:00"),
        ("오후 12시 30분에 알려 줘", "12:30"),
        ("오전 12시에 알려 줘", "00:00"),
        ("오후 3시 20분에 전화", "15:20"),
        ("7시에 깨워 줘", None),  # 오전인지 오후인지 말하지 않았다
        ("10분 뒤에 알려 줘", None),
        ("오후 3시랑 오후 5시에 알려 줘", None),  # 시각이 둘이면 어느 것인지 모른다
        ("오후 25시에", None),
    ],
)
def test_clock_time_is_read_from_what_was_said(text, expected):
    assert spoken_clock(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("내일 아침 9시", 1), ("모레 오전 10시", 2), ("내일모레 저녁", 2), ("글피 오후 2시", 3), ("오늘 저녁 7시", 0), ("저녁 7시", None)],
)
def test_day_word_is_read_from_what_was_said(text, expected):
    assert spoken_day_offset(text) == expected


def test_data_survives_reopening(tmp_path):
    path = tmp_path / "tasks.db"
    first = TaskRepository(path)
    first.add_todo("우유 사기", NOW)
    first.add_alarm("회의", NOW + timedelta(hours=1))
    first.close()

    second = TaskRepository(path)
    try:
        assert [todo.text for todo in second.list_todos()] == ["우유 사기"]
        assert [alarm.message for alarm in second.list_pending_alarms()] == ["회의"]
    finally:
        second.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10분 뒤에 빨래 꺼내기", AddAlarm(NOW + timedelta(minutes=10), "빨래 꺼내기")),
        ("30초 후 테스트", AddAlarm(NOW + timedelta(seconds=30), "테스트")),
        ("2시간 뒤 약 먹기", AddAlarm(NOW + timedelta(hours=2), "약 먹기")),
        ("5분 뒤", AddAlarm(NOW + timedelta(minutes=5), "알람")),
        ("오후 6시에 저녁 약속", AddAlarm(datetime(2026, 10, 6, 18, 0), "저녁 약속")),
        ("오후 6시 30분에 저녁 약속", AddAlarm(datetime(2026, 10, 6, 18, 30), "저녁 약속")),
        ("오전 9시에 회의", AddAlarm(datetime(2026, 10, 7, 9, 0), "회의")),
        ("할 일 추가 우유 사기", AddTodo("우유 사기")),
        ("할일: 메일 답장", AddTodo("메일 답장")),
        ("할 일 목록", ListTodos()),
        ("할 일", ListTodos()),
        ("2번 완료", CompleteTodo(2)),
        ("완료 1", CompleteTodo(1)),
        ("안녕, 오늘 기분 어때?", NotACommand("안녕, 오늘 기분 어때?")),
    ],
)
def test_parse_command(text, expected):
    assert parse_command(text, NOW) == expected

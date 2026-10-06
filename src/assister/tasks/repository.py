"""할 일과 알람을 내 PC의 SQLite 파일에 저장한다."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from assister.tasks.models import Alarm, Todo

SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alarms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    due_at TEXT NOT NULL,
    fired INTEGER NOT NULL DEFAULT 0,
    repeat TEXT NOT NULL DEFAULT ''
);
"""
# 예전에 만든 DB에는 없는 칸. 열 때 없으면 더한다.
ADD_REPEAT = "ALTER TABLE alarms ADD COLUMN repeat TEXT NOT NULL DEFAULT ''"


def _todo(row: sqlite3.Row) -> Todo:
    return Todo(id=row["id"], text=row["text"], done=bool(row["done"]), created_at=datetime.fromisoformat(row["created_at"]))


def _alarm(row: sqlite3.Row) -> Alarm:
    return Alarm(
        id=row["id"],
        message=row["message"],
        due_at=datetime.fromisoformat(row["due_at"]),
        fired=bool(row["fired"]),
        repeat=row["repeat"],
    )


class TaskRepository:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # 화면 스레드(알람 확인)와 대화 작업 스레드(AI 도구)가 함께 쓰므로 잠금으로 차례를 지킨다.
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        if "repeat" not in {row["name"] for row in self._db.execute("PRAGMA table_info(alarms)")}:
            self._db.execute(ADD_REPEAT)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add_todo(self, text: str, now: datetime) -> Todo:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("할 일 내용이 비어 있습니다")
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO todos (text, created_at) VALUES (?, ?)", (cleaned, now.isoformat())
            )
        return Todo(id=cursor.lastrowid, text=cleaned, done=False, created_at=now)

    def list_todos(self, *, include_done: bool = False) -> list[Todo]:
        query = "SELECT * FROM todos" + ("" if include_done else " WHERE done = 0") + " ORDER BY id"
        with self._lock:
            return [_todo(row) for row in self._db.execute(query).fetchall()]

    def complete_todo(self, todo_id: int) -> Todo | None:
        with self._lock, self._db:
            self._db.execute("UPDATE todos SET done = 1 WHERE id = ?", (todo_id,))
            row = self._db.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        return _todo(row) if row else None

    def add_alarm(self, message: str, due_at: datetime, *, repeat: str = "") -> Alarm:
        cleaned = message.strip() or "알람"
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO alarms (message, due_at, repeat) VALUES (?, ?, ?)", (cleaned, due_at.isoformat(), repeat)
            )
        return Alarm(id=cursor.lastrowid, message=cleaned, due_at=due_at, fired=False, repeat=repeat)

    def reschedule_alarm(self, alarm_id: int, due_at: datetime) -> None:
        """되풀이되는 알람을 다음에 울릴 때로 옮긴다."""
        with self._lock, self._db:
            self._db.execute("UPDATE alarms SET due_at = ? WHERE id = ?", (due_at.isoformat(), alarm_id))

    def list_pending_alarms(self) -> list[Alarm]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM alarms WHERE fired = 0 ORDER BY due_at").fetchall()
        return [_alarm(row) for row in rows]

    def due_alarms(self, now: datetime) -> list[Alarm]:
        return [alarm for alarm in self.list_pending_alarms() if alarm.due_at <= now]

    def mark_alarm_fired(self, alarm_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE alarms SET fired = 1 WHERE id = ?", (alarm_id,))

    def delete_alarm(self, alarm_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))

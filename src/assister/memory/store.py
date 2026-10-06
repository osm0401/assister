"""대화와 사용자에 대한 사실을 내 PC의 SQLite 파일에 전부 저장한다.

대화는 지우지 않고 계속 쌓는다. AI에게는 최근 대화와, 지금 하는 말과 관련 있는
예전 대화만 골라서 보여 준다.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS summaries (
    day TEXT PRIMARY KEY,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS screen_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    app TEXT NOT NULL,
    summary TEXT NOT NULL
);
"""

MIN_STEM_LENGTH = 2
MAX_ENDING_LENGTH = 2
SEARCH_CANDIDATES = 200
WORD = re.compile(r"\w+")
# 뜻 없이 자주 나와서 검색을 흐리는 말.
STOPWORDS = frozenset({"그리고", "그래서", "그런데", "근데", "그거", "이거", "저거", "뭐야", "어떻게", "있어", "없어"})


@dataclass(frozen=True)
class Message:
    id: int
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class Fact:
    id: int
    text: str
    created_at: datetime


@dataclass(frozen=True)
class ScreenNote:
    """화면을 보고 적은 글. 화면 그림 자체는 어디에도 저장하지 않는다."""

    created_at: datetime
    app: str
    summary: str


def keywords(query: str) -> list[tuple[str, ...]]:
    """검색할 낱말마다, 조사·어미를 한두 글자 떼어 본 후보들을 긴 것부터 돌려준다.

    '우유는' 으로 물어도 '우유를' 이 들어간 예전 말을 찾기 위해서다.
    """
    result = []
    for word in WORD.findall(query):
        if len(word) < MIN_STEM_LENGTH or word in STOPWORDS:
            continue
        stems = [word[: len(word) - cut] for cut in range(MAX_ENDING_LENGTH + 1) if len(word) - cut >= MIN_STEM_LENGTH]
        result.append(tuple(stems))
    return result


def _message(row: sqlite3.Row) -> Message:
    return Message(row["id"], row["role"], row["content"], datetime.fromisoformat(row["created_at"]))


class MemoryStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # 대화 작업 스레드와 화면 스레드가 함께 쓰므로 잠금으로 한 번에 하나씩만 접근하게 한다.
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add_message(self, role: str, content: str, now: datetime) -> Message:
        cleaned = content.strip()
        if not cleaned:
            raise ValueError("빈 말은 기억하지 않습니다")
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
                (role, cleaned, now.isoformat()),
            )
        return Message(cursor.lastrowid, role, cleaned, now)

    def count_messages(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def recent_messages(self, limit: int) -> list[Message]:
        """가장 최근 대화를 오래된 것부터."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_message(row) for row in reversed(rows)]

    def search(self, query: str, *, limit: int = 4, exclude_recent: int = 0) -> list[Message]:
        """query와 낱말이 많이 겹치는 예전 대화를 오래된 것부터 돌려준다.

        최근 exclude_recent개는 어차피 AI에게 그대로 보여 주므로 뺀다.
        """
        groups = keywords(query)
        if not groups:
            return []

        stems = sorted({stem for group in groups for stem in group})
        conditions = " OR ".join("instr(content, ?) > 0" for _ in stems)
        with self._lock:
            newest = self._db.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0]
            skipped = self._db.execute(
                "SELECT COALESCE(MIN(id), ?) FROM (SELECT id FROM messages ORDER BY id DESC LIMIT ?)",
                (newest + 1, exclude_recent),
            ).fetchone()[0]
            rows = self._db.execute(
                f"SELECT * FROM messages WHERE id < ? AND ({conditions}) ORDER BY id DESC LIMIT ?",
                (skipped if exclude_recent else newest + 1, *stems, SEARCH_CANDIDATES),
            ).fetchall()

        def score(row: sqlite3.Row) -> int:
            # 낱말마다 가장 길게 맞은 후보의 글자 수를 더한다.
            return sum(max((len(stem) for stem in group if stem in row["content"]), default=0) for group in groups)

        best = sorted(rows, key=lambda row: (-score(row), -row["id"]))[:limit]
        return [_message(row) for row in sorted(best, key=lambda row: row["id"])]

    def add_fact(self, text: str, now: datetime) -> Fact:
        """사용자에 대해 오래 기억할 사실. 같은 내용은 한 번만 저장한다."""
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("빈 사실은 기억하지 않습니다")
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO facts (text, created_at) VALUES (?, ?)", (cleaned, now.isoformat())
            )
            row = self._db.execute("SELECT * FROM facts WHERE text = ?", (cleaned,)).fetchone()
        return Fact(row["id"], row["text"], datetime.fromisoformat(row["created_at"]))

    def messages_on(self, day: date) -> list[Message]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM messages WHERE created_at LIKE ? ORDER BY id", (f"{day.isoformat()}%",)
            ).fetchall()
        return [_message(row) for row in rows]

    def days_to_summarise(self, now: datetime) -> list[date]:
        """대화는 있는데 아직 요약하지 않은 지난 날들. 오늘은 아직 끝나지 않았으니 뺀다."""
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT substr(created_at, 1, 10) AS day FROM messages"
                " WHERE substr(created_at, 1, 10) < ? AND substr(created_at, 1, 10) NOT IN (SELECT day FROM summaries)"
                " ORDER BY day",
                (now.date().isoformat(),),
            ).fetchall()
        return [date.fromisoformat(row["day"]) for row in rows]

    def add_summary(self, day: date, text: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO summaries (day, text) VALUES (?, ?)", (day.isoformat(), text))

    def recent_summaries(self, limit: int) -> list[tuple[date, str]]:
        """가장 최근 날들의 대화 요약을 오래된 것부터."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM summaries ORDER BY day DESC LIMIT ?", (limit,)).fetchall()
        return [(date.fromisoformat(row["day"]), row["text"]) for row in reversed(rows)]

    def count_user_messages_on(self, day: datetime) -> int:
        """그날 사용자가 한 말의 수."""
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM messages WHERE role = 'user' AND created_at LIKE ?", (f"{day.date().isoformat()}%",)
            ).fetchone()[0]

    def get_state(self, key: str, default: int) -> int:
        """친밀도 점수처럼 계속 이어지는 숫자 하나."""
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: int) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, value))

    def add_screen_note(self, summary: str, app: str, now: datetime) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO screen_notes (created_at, app, summary) VALUES (?, ?, ?)", (now.isoformat(), app, summary)
            )

    def recent_screen_notes(self, limit: int) -> list[ScreenNote]:
        """가장 최근 화면 기록을 오래된 것부터."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM screen_notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [ScreenNote(datetime.fromisoformat(row["created_at"]), row["app"], row["summary"]) for row in reversed(rows)]

    def screen_notes_on(self, day: datetime) -> list[ScreenNote]:
        """그날의 화면 기록을 시간순으로."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM screen_notes WHERE created_at LIKE ? ORDER BY id", (f"{day.date().isoformat()}%",)
            ).fetchall()
        return [ScreenNote(datetime.fromisoformat(row["created_at"]), row["app"], row["summary"]) for row in rows]

    def purge_screen_notes(self, before: datetime) -> int:
        """before보다 오래된 화면 기록을 지우고 지운 개수를 돌려준다. 대화는 지우지 않는다."""
        with self._lock, self._db:
            return self._db.execute("DELETE FROM screen_notes WHERE created_at < ?", (before.isoformat(),)).rowcount

    def list_facts(self) -> list[Fact]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM facts ORDER BY id").fetchall()
        return [Fact(row["id"], row["text"], datetime.fromisoformat(row["created_at"])) for row in rows]

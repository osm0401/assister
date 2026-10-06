"""파일 위치. 기억·설정은 전부 내 PC의 data 폴더에만 둔다."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR_ENV = "ASSISTER_DATA_DIR"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    return Path(override) if override else project_root() / "data"


def characters_dir() -> Path:
    return project_root() / "characters"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def tasks_db_path() -> Path:
    return data_dir() / "tasks.db"


def memory_db_path() -> Path:
    return data_dir() / "memory.db"

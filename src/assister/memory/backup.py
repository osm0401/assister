"""기억을 파일 하나로 내보내고 가져온다. PC를 바꿀 때 쓴다.

기억은 내 PC에만 있으므로, 옮기려면 사용자가 이 파일을 직접 들고 가야 한다.
"""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from pathlib import Path

DATABASES = ("memory.db", "tasks.db")
PLAIN_FILES = ("settings.json",)
KNOWN = (*DATABASES, *PLAIN_FILES)


class BackupError(Exception):
    """내보내거나 가져올 수 없을 때."""


def export_data(data_dir: Path, archive: Path) -> None:
    """data 폴더의 기억·할 일·설정을 zip 하나로 묶는다. 앱이 켜져 있어도 된다."""
    try:
        with tempfile.TemporaryDirectory() as scratch, zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name in DATABASES:
                source = data_dir / name
                if not source.exists():
                    continue
                # 쓰는 중인 DB 파일을 그대로 복사하면 깨질 수 있어 SQLite의 백업 기능으로 뜬다.
                copy = Path(scratch) / name
                with sqlite3.connect(source) as live, sqlite3.connect(copy) as snapshot:
                    live.backup(snapshot)
                snapshot.close()
                live.close()
                bundle.write(copy, name)
            for name in PLAIN_FILES:
                if (data_dir / name).exists():
                    bundle.write(data_dir / name, name)
    except (OSError, sqlite3.Error) as error:
        raise BackupError(f"기억을 내보내지 못했습니다: {error}") from error


def contents(archive: Path) -> list[str]:
    """zip 안에서 가져올 수 있는 파일 이름. 하나도 없거나 zip이 아니면 BackupError."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            # 아는 이름의 파일만, 폴더 없이 꺼낸다. 압축 파일이 다른 곳에 파일을 쓰지 못하게 하기 위해서다.
            names = [name for name in bundle.namelist() if name in KNOWN]
    except (OSError, zipfile.BadZipFile) as error:
        raise BackupError(f"기억 파일을 읽지 못했습니다: {error}") from error
    if not names:
        raise BackupError("이 파일에는 가져올 기억이 없습니다.")
    return names


def import_data(archive: Path, data_dir: Path) -> None:
    """내보낸 zip을 data 폴더에 푼다. 같은 이름의 파일은 덮어쓴다. 앱을 끈 상태에서 불러야 한다."""
    names = contents(archive)
    try:
        with zipfile.ZipFile(archive) as bundle:
            data_dir.mkdir(parents=True, exist_ok=True)
            for name in names:
                (data_dir / name).write_bytes(bundle.read(name))
    except (OSError, zipfile.BadZipFile) as error:
        raise BackupError(f"기억을 가져오지 못했습니다: {error}") from error

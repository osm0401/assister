"""Windows를 켤 때 앱도 같이 켜지게 한다.

시작프로그램 폴더에 실행 명령 한 줄짜리 파일을 두는 방식이다. 레지스트리는 건드리지 않고,
끄면 그 파일만 지운다. 사용자가 설정 창에서 켜고 끌 때만 불린다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from assister import paths

SCRIPT_NAME = "assister.cmd"


def startup_folder() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def windowless_python() -> str:
    """검은 명령 창이 뜨지 않는 파이썬. 없으면 지금 쓰는 파이썬."""
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate if candidate.exists() else sys.executable)


def is_enabled(folder: Path | None = None) -> bool:
    return ((folder or startup_folder()) / SCRIPT_NAME).exists()


def set_enabled(enabled: bool, folder: Path | None = None, *, python: str | None = None, workdir: str | None = None) -> None:
    """켜면 실행 파일을 쓰고, 끄면 지운다. 폴더에 쓸 수 없으면 OSError가 올라간다."""
    script = (folder or startup_folder()) / SCRIPT_NAME
    if not enabled:
        script.unlink(missing_ok=True)
        return
    script.parent.mkdir(parents=True, exist_ok=True)
    command = f'start "" /D "{workdir or paths.project_root()}" "{python or windowless_python()}" -m assister'
    script.write_text(f"@echo off\n{command}\n", encoding="utf-8")

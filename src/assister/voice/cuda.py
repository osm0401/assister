"""그래픽카드용 라이브러리(DLL)를 찾을 수 있게 위치를 알려 준다.

pip로 설치한 nvidia-cublas, nvidia-cudnn의 DLL은 Windows가 기본으로 찾는 곳에 없어서,
음성 인식을 그래픽카드로 돌리려면 먼저 그 폴더를 등록해야 한다.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

_registered: list[str] | None = None


def register_nvidia_dlls() -> list[str]:
    """등록한 폴더 목록을 돌려준다. 여러 번 불러도 한 번만 등록한다."""
    global _registered
    if _registered is not None:
        return _registered

    spec = importlib.util.find_spec("nvidia")
    roots = list(spec.submodule_search_locations) if spec and spec.submodule_search_locations else []
    folders = [str(folder) for root in roots for folder in sorted(Path(root).glob("*/bin")) if folder.is_dir()]
    for folder in folders:
        os.add_dll_directory(folder)
    if folders:
        # 일부 DLL은 나중에 이름만으로 불려 와서 PATH에도 있어야 한다.
        os.environ["PATH"] = os.pathsep.join([*folders, os.environ.get("PATH", "")])
    _registered = folders
    return folders

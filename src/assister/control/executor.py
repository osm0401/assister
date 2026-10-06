"""마우스와 키보드를 실제로 움직인다 (Windows).

여기 있는 함수는 시키는 대로 할 뿐이다. 해도 되는 일인지는 agent.py와 actions.py가 먼저 가려낸다.
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

from assister.control.actions import Action, ActionError
from assister.screen.capture import monitor_rects
from assister.system import uia

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSE_MOVE, MOUSE_LEFT_DOWN, MOUSE_LEFT_UP, MOUSE_WHEEL = 0x0001, 0x0002, 0x0004, 0x0800
MOUSE_ABSOLUTE, MOUSE_VIRTUAL_DESK = 0x8000, 0x4000
KEY_UP, KEY_UNICODE = 0x0002, 0x0004
WHEEL_NOTCH = 120
SM_VIRTUAL_SCREEN = (76, 77, 78, 79)  # 왼쪽, 위, 너비, 높이
MAX_WAIT_SEC = 5
TYPE_GAP_SEC = 0.005
DOUBLE_CLICK_GAP_SEC = 0.06

VIRTUAL_KEYS = {
    "enter": 0x0D, "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    **{f"f{number}": 0x70 + number - 1 for number in range(1, 13)},
}  # fmt: skip


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mouse", _MouseInput), ("keyboard", _KeyboardInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", _InputUnion)]


def virtual_key(name: str) -> int:
    if name in VIRTUAL_KEYS:
        return VIRTUAL_KEYS[name]
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    raise ActionError(f"모르는 키입니다: {name!r}")


def normalized(point: tuple[int, int], virtual_screen: tuple[int, int, int, int]) -> tuple[int, int]:
    """화면 좌표를 SendInput이 쓰는 0~65535 좌표(모든 모니터 기준)로 바꾼다."""
    left, top, width, height = virtual_screen
    return (
        round((point[0] - left) * 65535 / max(width - 1, 1)),
        round((point[1] - top) * 65535 / max(height - 1, 1)),
    )


def _send(*inputs: _Input) -> None:
    array = (_Input * len(inputs))(*inputs)
    if ctypes.windll.user32.SendInput(len(inputs), array, ctypes.sizeof(_Input)) != len(inputs):
        # 관리자 권한으로 뜬 창이 앞에 있으면 Windows가 막는다.
        raise OSError("Windows가 입력을 받아 주지 않았습니다")


def _mouse(flags: int, point: tuple[int, int] = (0, 0), data: int = 0) -> _Input:
    return _Input(type=INPUT_MOUSE, mouse=_MouseInput(point[0], point[1], data & 0xFFFFFFFF, flags, 0, 0))


def _key(code: int, flags: int = 0, *, scan: int = 0) -> _Input:
    return _Input(type=INPUT_KEYBOARD, keyboard=_KeyboardInput(code, scan, flags, 0, 0))


class Win32Executor:
    def monitors(self) -> list[tuple[int, int, int, int]]:
        return monitor_rects()

    def element_name(self, x: int, y: int) -> str:
        """그 자리에 있는 버튼이나 메뉴의 이름을 Windows에 물어본다. 알 수 없으면 빈 글."""
        return uia.element_name(x, y)

    def perform(self, action: Action, point: tuple[int, int] | None) -> None:
        """point는 누르거나 굴릴 자리의 화면 좌표다."""
        if action.kind in ("click", "double_click", "scroll") and point is None:
            raise ActionError(f"{action.kind}에는 누를 자리가 필요합니다")
        if action.kind in ("click", "double_click"):
            self._move(point)
            for _ in range(2 if action.kind == "double_click" else 1):
                _send(_mouse(MOUSE_LEFT_DOWN), _mouse(MOUSE_LEFT_UP))
                time.sleep(DOUBLE_CLICK_GAP_SEC)
        elif action.kind == "scroll":
            self._move(point)
            _send(_mouse(MOUSE_WHEEL, data=action.amount * WHEEL_NOTCH))
        elif action.kind == "type":
            self._type(action.text)
        elif action.kind == "key":
            codes = [virtual_key(name) for name in action.keys.split("+")]
            _send(*(_key(code) for code in codes), *(_key(code, KEY_UP) for code in reversed(codes)))
        elif action.kind == "open":
            os.startfile(action.text)  # noqa: S606 (무엇을 열어도 되는지는 risk_reason이 먼저 가려낸다)
        elif action.kind == "wait":
            time.sleep(min(max(action.amount, 1), MAX_WAIT_SEC))
        else:
            raise ActionError(f"실행할 수 없는 동작입니다: {action.kind}")

    def _move(self, point: tuple[int, int]) -> None:
        metrics = ctypes.windll.user32.GetSystemMetrics
        virtual_screen = tuple(metrics(index) for index in SM_VIRTUAL_SCREEN)
        _send(_mouse(MOUSE_MOVE | MOUSE_ABSOLUTE | MOUSE_VIRTUAL_DESK, normalized(point, virtual_screen)))

    def _type(self, text: str) -> None:
        """글자를 유니코드로 직접 넣는다. 한/영 상태와 상관없이 그대로 들어간다."""
        for char in text:
            if char == "\n":
                _send(_key(VIRTUAL_KEYS["enter"]), _key(VIRTUAL_KEYS["enter"], KEY_UP))
            else:
                units = char.encode("utf-16-le")
                for index in range(0, len(units), 2):
                    scan = int.from_bytes(units[index : index + 2], "little")
                    _send(_key(0, KEY_UNICODE, scan=scan), _key(0, KEY_UNICODE | KEY_UP, scan=scan))
            time.sleep(TYPE_GAP_SEC)

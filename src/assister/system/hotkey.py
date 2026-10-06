"""다른 프로그램을 쓰는 중에도 듣는 전역 단축키 (Windows)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

MODIFIERS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
NAMED_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "pause": 0x13,
    "`": 0xC0,
}


class HotkeyError(Exception):
    """단축키를 해석하거나 등록할 수 없을 때."""


def parse_hotkey(text: str, *, allow_bare: bool = False) -> tuple[int, int]:
    """'Ctrl+Shift+Space' 같은 글자를 (보조키 묶음, 키 코드)로 바꾼다.

    보조키 없는 단축키는 그 키를 다른 프로그램이 못 쓰게 되므로 allow_bare일 때만 받는다.
    """
    parts = [part.strip().lower() for part in text.split("+")]
    if not all(parts):
        raise HotkeyError(f"단축키 형식이 잘못됐습니다: {text!r}")

    modifiers = 0
    keys: list[int] = []
    for part in parts:
        if part in MODIFIERS:
            modifiers |= MODIFIERS[part]
        elif part in NAMED_KEYS:
            keys.append(NAMED_KEYS[part])
        elif len(part) == 1 and part.isalnum():
            keys.append(ord(part.upper()))
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            keys.append(0x70 + int(part[1:]) - 1)
        else:
            raise HotkeyError(f"모르는 키입니다: {part!r} ({text!r})")

    if not modifiers and not allow_bare:
        raise HotkeyError(f"Ctrl, Alt, Shift, Win 중 하나는 함께 눌러야 합니다: {text!r}")
    if len(keys) != 1:
        raise HotkeyError(f"보조키 말고 키 하나가 필요합니다: {text!r}")
    return modifiers, keys[0]


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, hotkey_id: int, on_pressed) -> None:
        super().__init__()
        self._hotkey_id = hotkey_id
        self._on_pressed = on_pressed

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt 이름)
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self._hotkey_id:
                self._on_pressed()
                return True, 0
        return False, 0


class GlobalHotkey(QObject):
    """등록한 단축키가 눌리면 pressed 신호를 보낸다."""

    pressed = Signal()
    _next_id = 0xA551

    def __init__(self, text: str, parent: QObject | None = None, *, allow_bare: bool = False) -> None:
        super().__init__(parent)
        modifiers, key = parse_hotkey(text, allow_bare=allow_bare)
        self._id = GlobalHotkey._next_id
        GlobalHotkey._next_id += 1

        if not ctypes.windll.user32.RegisterHotKey(None, self._id, modifiers | MOD_NOREPEAT, key):
            raise HotkeyError(f"단축키 {text}을(를) 등록하지 못했습니다. 다른 프로그램이 이미 쓰고 있을 수 있습니다.")
        self._registered = True
        self._filter = _HotkeyFilter(self._id, self.pressed.emit)
        QCoreApplication.instance().installNativeEventFilter(self._filter)

    def release(self) -> None:
        if self._registered:
            ctypes.windll.user32.UnregisterHotKey(None, self._id)
            QCoreApplication.instance().removeNativeEventFilter(self._filter)
            self._registered = False

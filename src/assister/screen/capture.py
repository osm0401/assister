"""화면을 찍어 가릴 곳을 가린 뒤 AI에게 보낼 그림으로 만든다 (Windows).

찍은 그림은 메모리에만 두고 디스크에 저장하지 않는다.
"""

from __future__ import annotations

import base64
import ctypes
import io
import os
import sys
from collections.abc import Sequence
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageGrab

from assister.screen.privacy import Window, is_hidden, mask
from assister.system.uia import password_focused  # noqa: F401 (take_snapshot이 이 모듈을 바탕화면으로 쓴다)

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
MONITORINFOF_PRIMARY = 1
JPEG_QUALITY = 85
LOOK_SIZE = (32, 18)
DESKTOP_PROCESS = "explorer.exe"  # 바탕화면도 모니터를 꽉 채우는 창이다
FOCUS_MIN_WINDOW = 120  # 이보다 작은 창은 따로 잘라 보여 주지 않는다
FOCUS_MIN_SIDE = 768  # 잘라 낸 그림의 긴 변이 이보다 짧으면 키운다


@dataclass(frozen=True)
class Snapshot:
    images: list[str]  # 모니터마다 한 장, base64 JPEG. 주 모니터가 맨 앞.
    foreground: Window | None
    hidden: list[str]  # 가린 창의 제목
    skipped: bool  # 가릴 창이 맨 앞에 있어 아예 찍지 않았으면 True
    look: bytes = b""  # 화면을 아주 작게 줄인 흑백 그림. 화면이 바뀌었는지 견주는 데만 쓴다.
    focus: str = ""  # 맨 앞 창만 잘라 낸 그림(base64 JPEG). PC를 조작할 때 버튼을 정확히 짚는 데 쓴다.
    focus_rect: tuple[int, int, int, int] | None = None  # 그 그림이 화면의 어디인지 (왼쪽, 위, 오른쪽, 아래)
    fullscreen: bool = False  # 맨 앞 창이 모니터 하나를 꽉 채우고 있는지 (게임, 전체 화면 영상)


class Desktop(Protocol):
    def list_windows(self) -> list[Window]: ...
    def foreground_window(self) -> Window | None: ...
    def password_focused(self) -> bool: ...
    def monitor_rects(self) -> list[tuple[int, int, int, int]]: ...
    def grab(self) -> tuple[Image.Image, tuple[int, int]]: ...


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _declare_handle_types() -> None:
    """핸들을 주고받는 함수의 타입을 밝혀 둔다.

    밝히지 않으면 ctypes가 핸들을 32비트 정수로 다뤄, 값이 잘린 채 CloseHandle에 넘어가
    엉뚱한 핸들(예: 열려 있는 네트워크 소켓)을 닫을 수 있다.
    """
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]


_declare_handle_types()


def _process_name(hwnd: int) -> str:
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return "?"
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return "?"
        return os.path.basename(buffer.value)
    finally:
        kernel32.CloseHandle(handle)


def _window(hwnd: int) -> Window:
    user32, dwmapi = ctypes.windll.user32, ctypes.windll.dwmapi
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title, len(title))
    rect = wintypes.RECT()
    # 그림자를 뺀 실제 보이는 테두리. 실패하면 일반 창 크기를 쓴다.
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return Window(title.value, _process_name(hwnd), (rect.left, rect.top, rect.right, rect.bottom))


def list_windows() -> list[Window]:
    """화면에 보이는 창을 앞에 있는 것부터."""
    user32, dwmapi = ctypes.windll.user32, ctypes.windll.dwmapi
    found: list[Window] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        cloaked = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd) and not cloaked.value:
            window = _window(hwnd)
            if window.rect[2] > window.rect[0] and window.rect[3] > window.rect[1]:
                found.append(window)
        return True

    user32.EnumWindows(visit, 0)
    return found


def foreground_window() -> Window | None:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return _window(hwnd) if hwnd else None


def monitor_rects() -> list[tuple[int, int, int, int]]:
    """모니터마다 화면 좌표 (왼쪽, 위, 오른쪽, 아래). 주 모니터가 맨 앞."""
    user32 = ctypes.windll.user32
    found: list[tuple[bool, tuple[int, int, int, int]]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def visit(monitor, _dc, _rect, _lparam):
        info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            area = info.rcMonitor
            primary = bool(info.dwFlags & MONITORINFOF_PRIMARY)
            found.append((primary, (area.left, area.top, area.right, area.bottom)))
        return True

    user32.EnumDisplayMonitors(None, None, visit, 0)
    return [rect for _primary, rect in sorted(found, key=lambda item: not item[0])]


def grab() -> tuple[Image.Image, tuple[int, int]]:
    """모든 모니터를 한 장으로 찍는다. 그림의 왼쪽 위가 화면 좌표로 어디인지도 함께 돌려준다."""
    user32 = ctypes.windll.user32
    origin = (user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    return ImageGrab.grab(all_screens=True).convert("RGB"), origin


def _encode(image: Image.Image, width: int) -> str:
    if image.width > width:
        image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _fills_a_monitor(window: Window | None, monitors: Sequence[tuple[int, int, int, int]]) -> bool:
    """창이 모니터 하나와 꼭 같은 자리를 차지하는지. 최대화한 창은 작업 표시줄만큼 모자라서 해당하지 않는다."""
    if window is None or window.process.lower() == DESKTOP_PROCESS:
        return False
    return tuple(window.rect) in {tuple(monitor) for monitor in monitors}


def _close_up(
    image: Image.Image,
    origin: tuple[int, int],
    window: Window | None,
    monitors: Sequence[tuple[int, int, int, int]],
    width: int,
) -> tuple[str, tuple[int, int, int, int] | None]:
    """맨 앞 창만 잘라 낸 그림과 그 자리. 창이 없거나 너무 작으면 ("", None)."""
    if window is None:
        return "", None

    def visible_part(monitor: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return (
            max(window.rect[0], monitor[0]),
            max(window.rect[1], monitor[1]),
            min(window.rect[2], monitor[2]),
            min(window.rect[3], monitor[3]),
        )

    # 창이 가장 많이 걸쳐 있는 모니터 안쪽만 쓴다. 화면 밖으로 삐져나간 부분은 누를 수 없다.
    parts = [visible_part(monitor) for monitor in monitors]
    left, top, right, bottom = max(parts, key=lambda p: max(p[2] - p[0], 0) * max(p[3] - p[1], 0), default=(0, 0, 0, 0))
    if right - left < FOCUS_MIN_WINDOW or bottom - top < FOCUS_MIN_WINDOW:
        return "", None
    crop = image.crop((left - origin[0], top - origin[1], right - origin[0], bottom - origin[1]))
    if max(crop.size) < FOCUS_MIN_SIDE:
        # 작은 창은 키워서 보낸다. 글자와 버튼이 커야 AI가 정확히 짚는다.
        scale = FOCUS_MIN_SIDE / max(crop.size)
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS)
    return _encode(crop, width), (left, top, right, bottom)


def take_snapshot(keywords: Sequence[str], width: int, desktop: Desktop | None = None) -> Snapshot:
    """지금 화면을 AI에게 보낼 수 있는 모양으로 만든다. 그림은 모니터마다 가로 width로 줄인다."""
    desktop = desktop or sys.modules[__name__]
    foreground = desktop.foreground_window()
    if foreground is not None and is_hidden(foreground, keywords):
        return Snapshot(images=[], foreground=foreground, hidden=[foreground.title], skipped=True)
    if desktop.password_focused():
        # 창 제목으로는 알 수 없는 로그인 화면도, 비밀번호를 치는 중이면 찍지 않는다.
        return Snapshot(images=[], foreground=foreground, hidden=["비밀번호 입력란"], skipped=True)

    image, origin = desktop.grab()
    masked, hidden = mask(image, desktop.list_windows(), keywords, origin=origin)
    monitors = desktop.monitor_rects()
    screens = [
        masked.crop((left - origin[0], top - origin[1], right - origin[0], bottom - origin[1]))
        for left, top, right, bottom in monitors
    ]
    focus, focus_rect = _close_up(masked, origin, foreground, monitors, width)
    return Snapshot(
        focus=focus,
        focus_rect=focus_rect,
        fullscreen=_fills_a_monitor(foreground, monitors),
        images=[_encode(screen, width) for screen in screens],
        foreground=foreground,
        hidden=[window.title for window in hidden],
        skipped=False,
        look=b"".join(screen.convert("L").resize(LOOK_SIZE, Image.BILINEAR).tobytes() for screen in screens),
    )

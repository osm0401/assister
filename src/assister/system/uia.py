"""Windows UI Automation으로 화면 요소에 대해 묻는다 (이름, 비밀번호 입력란인지).

알 수 없을 때는 예외를 던지지 않고 빈 값이나 False를 돌려준다. 여기서 얻는 답은
안전장치를 보태는 용도라서, 못 얻었다고 앱이 멈추면 안 된다.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

# 앱을 켤 때(주 스레드에서) 한 번 불러 둔다. 뒤쪽 스레드에서 처음 부르면 그 스레드가 혼자 쓰는 방식으로 열려 버린다.
import comtypes
import comtypes.client

log = logging.getLogger(__name__)

# UI Automation의 속성 번호
RECT, TYPE, NAME, ENABLED, HELP, PASSWORD, OFFSCREEN = 30001, 30003, 30005, 30010, 30013, 30019, 30022
DESCENDANTS = 4  # 창 안의 모든 것
DOCUMENT = 50030  # 웹 페이지나 문서의 안쪽
PAGE_WINDOW = "Chrome_RenderWidgetHostHWND"  # 크롬·엣지에서 페이지를 그리는 안쪽 창
WM_GETOBJECT = 0x003D
WAKE_WAIT_SEC = 1.0  # 깨운 뒤 페이지 안쪽이 채워지기를 기다리는 시간
READ_TRIES, READ_RETRY_SEC = 3, 0.7

_local = threading.local()


def _automation():
    """스레드마다 하나씩 만들어 둔다. COM 객체는 만든 스레드에서만 쓸 수 있다."""
    if getattr(_local, "automation", None) is None:
        # 뒤쪽 스레드는 COM을 여럿이 쓰는 방식(MTA)으로 연다. 혼자 쓰는 방식(STA)으로 열면,
        # 내용이 계속 바뀌는 웹 페이지에서 목록 읽기가 실패했다(실제로 다나와에서 그랬다).
        # 화면 스레드처럼 이미 다른 방식으로 열려 있으면 오류가 나는데, 그때는 열려 있는 대로 쓴다.
        with contextlib.suppress(OSError):
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient

        _local.automation = comtypes.client.CreateObject(
            UIAutomationClient.CUIAutomation, interface=UIAutomationClient.IUIAutomation
        )
    return _local.automation


def _errors() -> tuple[type[BaseException], ...]:
    return (comtypes.COMError, OSError, ValueError)


def element_name(x: int, y: int) -> str:
    """그 자리에 있는 버튼이나 메뉴의 이름. 알 수 없으면 빈 글."""
    try:
        return _automation().ElementFromPoint(wintypes.POINT(x, y)).CurrentName or ""
    except _errors() as error:
        log.info("(%s, %s)에 있는 것의 이름을 알 수 없습니다: %s", x, y, error)
        return ""


@dataclass(frozen=True)
class Seen:
    """창 안에서 읽은 것 하나. type은 UI Automation이 붙인 종류 번호다."""

    type: int
    name: str
    help: str
    rect: tuple[int, int, int, int]  # 화면 좌표 (왼쪽, 위, 오른쪽, 아래)
    enabled: bool
    password: bool


def _page_windows(hwnd: int) -> list[int]:
    """브라우저 창 안에서 페이지를 그리는 안쪽 창들. 탭이나 옆 패널마다 하나씩 있을 수 있다."""
    user32 = ctypes.windll.user32
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    found: list[int] = []
    while child := user32.FindWindowExW(hwnd, found[-1] if found else None, PAGE_WINDOW, None):
        found.append(child)
    return found


def _wake(page: int) -> None:
    """브라우저에 '화면을 읽는 프로그램이 있다'고 알린다. 그래야 페이지 안쪽의 버튼과 링크를 내준다.

    알리지 않으면 도구 모음만 준다(실제로 재 보니 다나와에서 51개 → 알린 뒤 230개).
    """
    send = ctypes.windll.user32.SendMessageTimeoutW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
    send(page, WM_GETOBJECT, 0, 1, 0x0002, 500, None)  # 0x0002: 멈춘 프로그램이면 기다리지 않는다


def harvest(hwnd: int) -> tuple[list[Seen], tuple[int, int, int, int] | None]:
    """창 안에서 지금 화면에 나와 있는 것을 전부 읽는다. 입력 칸에 적힌 내용은 읽지 않는다.

    브라우저면 페이지가 차지하는 자리도 함께 돌려준다(아니면 None).
    읽지 못하면 빈 목록을 돌려준다. 관리자 권한으로 뜬 창이나 화면을 직접 그리는 프로그램이 그렇다.
    """
    pages = _page_windows(hwnd)
    if not pages:
        return _read(hwnd), None
    for page in pages:
        _wake(page)
    seen = _read(hwnd)
    if not any(item.type == DOCUMENT for item in seen):
        time.sleep(WAKE_WAIT_SEC)  # 처음 깨운 직후에는 아직 비어 있다
        seen = _read(hwnd)
    box = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(box))
    return seen, page_area(seen, (box.left, box.top, box.right, box.bottom))


def page_area(seen: list[Seen], window: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """읽어 온 것 가운데 페이지가 차지하는 자리. 페이지가 없으면 None.

    가장 큰 문서의 자리를 쓴다. 안쪽 창의 자리로 잡았더니, 탭이 여러 개인 창에서 엉뚱한 탭의 자리가
    잡혀 목록이 텅 비었다(실제로 그랬다). 문서는 화면 밖까지 이어질 수 있어 창 안쪽으로 잘라 낸다.
    """
    documents = [item.rect for item in seen if item.type == DOCUMENT]
    if not documents:
        return None
    left, top, right, bottom = max(documents, key=lambda rect: (rect[2] - rect[0]) * (rect[3] - rect[1]))
    area = (max(left, window[0]), max(top, window[1]), min(right, window[2]), min(bottom, window[3]))
    return area if area[0] < area[2] and area[1] < area[3] else None


def _read(hwnd: int) -> list[Seen]:
    """페이지가 뜨는 중에는 읽다가 실패할 때가 있어(실제로 봄) 몇 번 다시 해 본다."""
    for attempt in range(READ_TRIES):
        seen = _read_once(hwnd)
        if seen is not None:
            return seen
        if attempt < READ_TRIES - 1:
            time.sleep(READ_RETRY_SEC)
    return []


def _read_once(hwnd: int) -> list[Seen] | None:
    try:
        automation = _automation()
        request = automation.CreateCacheRequest()
        for property_id in (NAME, TYPE, RECT, ENABLED, PASSWORD, HELP):
            request.AddProperty(property_id)
        on_screen = automation.CreatePropertyCondition(OFFSCREEN, False)
        found = automation.ElementFromHandle(hwnd).FindAllBuildCache(DESCENDANTS, on_screen, request)
        seen = []
        for index in range(found.Length):
            element = found.GetElement(index)
            box = element.CachedBoundingRectangle
            seen.append(
                Seen(
                    element.CachedControlType,
                    element.CachedName or "",
                    element.CachedHelpText or "",
                    (box.left, box.top, box.right, box.bottom),
                    bool(element.CachedIsEnabled),
                    bool(element.CachedIsPassword),
                )
            )
        return seen
    except _errors() as error:
        log.info("창 안의 것들을 읽을 수 없습니다: %s", error)
        return None


def password_focused() -> bool:
    """지금 커서가 비밀번호 입력란에 있는지. 알 수 없으면 False."""
    try:
        return bool(_automation().GetFocusedElement().CurrentIsPassword)
    except _errors() as error:
        log.info("커서가 있는 곳을 알 수 없습니다: %s", error)
        return False

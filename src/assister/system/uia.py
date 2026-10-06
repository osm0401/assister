"""Windows UI Automation으로 화면 요소에 대해 묻는다 (이름, 비밀번호 입력란인지).

알 수 없을 때는 예외를 던지지 않고 빈 값이나 False를 돌려준다. 여기서 얻는 답은
안전장치를 보태는 용도라서, 못 얻었다고 앱이 멈추면 안 된다.
"""

from __future__ import annotations

import logging
import threading
from ctypes import wintypes

log = logging.getLogger(__name__)

_local = threading.local()


def _automation():
    """스레드마다 하나씩 만들어 둔다. COM 객체는 만든 스레드에서만 쓸 수 있다."""
    if getattr(_local, "automation", None) is None:
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient

        _local.automation = comtypes.client.CreateObject(
            UIAutomationClient.CUIAutomation, interface=UIAutomationClient.IUIAutomation
        )
    return _local.automation


def _errors() -> tuple[type[BaseException], ...]:
    import comtypes

    return (comtypes.COMError, OSError, ValueError)


def element_name(x: int, y: int) -> str:
    """그 자리에 있는 버튼이나 메뉴의 이름. 알 수 없으면 빈 글."""
    try:
        return _automation().ElementFromPoint(wintypes.POINT(x, y)).CurrentName or ""
    except _errors() as error:
        log.info("(%s, %s)에 있는 것의 이름을 알 수 없습니다: %s", x, y, error)
        return ""


def password_focused() -> bool:
    """지금 커서가 비밀번호 입력란에 있는지. 알 수 없으면 False."""
    try:
        return bool(_automation().GetFocusedElement().CurrentIsPassword)
    except _errors() as error:
        log.info("커서가 있는 곳을 알 수 없습니다: %s", error)
        return False

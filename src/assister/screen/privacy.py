"""화면에서 무엇을 가릴지 정하고 가린다.

창 제목이나 프로그램 이름에 아래 낱말이 들어 있으면 그 창을 검게 칠한다.
기준에 맞는 창이 맨 앞에 있으면 화면을 아예 찍지 않는다(capture.take_snapshot).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw

# 여기는 창 제목·프로그램 이름만 본다. 제목에 드러나지 않는 로그인 화면은
# capture.take_snapshot이 '커서가 비밀번호 입력란에 있는지'를 따로 확인해 거른다.
DEFAULT_SENSITIVE = (
    # 돈. '페이', '카드', '토스'처럼 짧은 낱말은 '페이지', '그래픽카드', '토스트'에도 걸려서 이름을 풀어 적는다.
    "은행", "bank", "뱅킹", "증권", "결제", "paypal", "toss", "토스뱅크", "토스증권", "토스페이",
    "카카오페이", "네이버페이", "삼성페이", "페이코",
    "신한카드", "삼성카드", "현대카드", "국민카드", "롯데카드", "우리카드", "하나카드", "비씨카드", "bc카드", "농협카드",
    # 로그인과 비밀번호
    "비밀번호", "password", "로그인", "login", "sign in", "인증", "otp",
    "1password", "bitwarden", "keepass", "lastpass",
    # 메신저와 메일
    "kakaotalk", "카카오톡", "telegram", "discord", "slack", "whatsapp", "signal",
    # 흔적을 남기지 않으려고 연 창
    "inprivate", "incognito", "시크릿", "private browsing",
)  # fmt: skip


@dataclass(frozen=True)
class Window:
    title: str
    process: str  # 실행 파일 이름. 예: chrome.exe
    rect: tuple[int, int, int, int]  # 화면 좌표 (왼쪽, 위, 오른쪽, 아래)


def is_hidden(window: Window, keywords: Sequence[str]) -> bool:
    text = f"{window.title}\n{window.process}".lower()
    return any(keyword.lower() in text for keyword in keywords if keyword.strip())


def mask(
    image: Image.Image, windows: Sequence[Window], keywords: Sequence[str], *, origin: tuple[int, int]
) -> tuple[Image.Image, list[Window]]:
    """가릴 창을 검게 칠한 새 그림과, 가린 창 목록을 돌려준다.

    origin은 그림의 왼쪽 위 모서리가 화면 좌표로 어디인지다. 다른 창에 덮여 있어도
    창 자리 전체를 칠한다. 덜 가리는 것보다 더 가리는 쪽이 안전하다.
    """
    masked = image.copy()
    draw = ImageDraw.Draw(masked)
    hidden = [window for window in windows if is_hidden(window, keywords)]
    for window in hidden:
        left, top, right, bottom = window.rect
        draw.rectangle((left - origin[0], top - origin[1], right - origin[0], bottom - origin[1]), fill="black")
    return masked, hidden

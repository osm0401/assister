"""캐릭터, 말풍선, 입력창을 화면 어디에 둘지 계산한다. 화면 좌표만 다루는 순수 계산이다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

SCREEN_MARGIN = 48
GAP = 4
MIN_VISIBLE = 40


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


def default_position(size: tuple[int, int], screens: Sequence[Rect]) -> tuple[int, int]:
    """첫 번째 화면의 오른쪽 아래."""
    screen = screens[0]
    return screen.right - size[0] - SCREEN_MARGIN, screen.bottom - size[1] - SCREEN_MARGIN


def _visible_enough(position: tuple[int, int], size: tuple[int, int], screen: Rect) -> bool:
    overlap_x = min(position[0] + size[0], screen.right) - max(position[0], screen.x)
    overlap_y = min(position[1] + size[1], screen.bottom) - max(position[1], screen.y)
    return overlap_x >= MIN_VISIBLE and overlap_y >= MIN_VISIBLE


def place_on_screen(
    position: tuple[int, int] | None, size: tuple[int, int], screens: Sequence[Rect]
) -> tuple[int, int]:
    """저장해 둔 위치가 지금 연결된 화면에 보이면 그대로, 아니면 기본 위치."""
    if position is not None and any(_visible_enough(position, size, screen) for screen in screens):
        return position
    return default_position(size, screens)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def bubble_position(character: Rect, size: tuple[int, int], screen: Rect) -> tuple[int, int]:
    """말풍선은 캐릭터 머리 위 가운데. 화면 밖으로 나가지 않게 한다."""
    x = character.x + (character.width - size[0]) // 2
    y = character.y - size[1] - GAP
    return _clamp(x, screen.x, screen.right - size[0]), _clamp(y, screen.y, screen.bottom - size[1])


def input_position(character: Rect, size: tuple[int, int], screen: Rect) -> tuple[int, int]:
    """입력창은 캐릭터 발 아래 가운데. 아래에 자리가 없으면 화면 안쪽으로 올린다."""
    x = character.x + (character.width - size[0]) // 2
    y = character.bottom + GAP
    return _clamp(x, screen.x, screen.right - size[0]), _clamp(y, screen.y, screen.bottom - size[1])

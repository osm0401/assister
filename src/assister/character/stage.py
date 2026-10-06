"""캐릭터 창, 말풍선, 입력창을 한 덩어리로 움직인다."""

from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QRect, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from assister.character.bubble import SpeechBubble
from assister.character.input_bar import InputBar
from assister.character.pack import CharacterPack
from assister.character.placement import Rect, bubble_position, input_position, place_on_screen
from assister.character.window import CharacterWindow

IDLE_EXPRESSION = "neutral"
THINKING_TEXT = "…"


def _rect(qrect: QRect) -> Rect:
    return Rect(qrect.x(), qrect.y(), qrect.width(), qrect.height())


def screen_rects() -> list[Rect]:
    """연결된 모니터의 쓸 수 있는 영역. 주 모니터가 맨 앞이다."""
    primary = QGuiApplication.primaryScreen()
    others = [screen for screen in QGuiApplication.screens() if screen is not primary]
    return [_rect(screen.availableGeometry()) for screen in [primary, *others]]


class CharacterStage(QObject):
    submitted = Signal(str)
    clicked = Signal()
    mic_clicked = Signal()
    menu_requested = Signal(QPoint)
    position_changed = Signal(QPoint)

    def __init__(self, pack: CharacterPack, outfit: str, height: int, *, always_show_input: bool = True) -> None:
        super().__init__()
        self.pack = pack
        self.character = CharacterWindow(pack, outfit, height)
        self.bubble = SpeechBubble()
        self.input_bar = InputBar(pack.name)
        self._always_show_input = always_show_input

        self.idle_expression = IDLE_EXPRESSION
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(lambda: self.character.set_expression(self.idle_expression))

        self.character.dragged.connect(self._reposition)
        self.character.drag_finished.connect(self.position_changed)
        self.character.clicked.connect(self.clicked)
        self.character.menu_requested.connect(self.menu_requested)
        self.bubble.resized.connect(self._reposition)
        self.bubble.hidden.connect(lambda: self._idle_timer.start(0))
        self.input_bar.submitted.connect(self.submitted)
        self.input_bar.mic_clicked.connect(self.mic_clicked)

    def show(self, position: tuple[int, int] | None) -> None:
        size = (self.character.width(), self.character.height())
        self.character.move(*place_on_screen(position, size, screen_rects()))
        self.character.show()
        if self._always_show_input:
            self.input_bar.show()
        self._reposition()

    def close(self) -> None:
        for window in (self.bubble, self.input_bar, self.character):
            window.close()

    def say(self, text: str, expression: str = "talking") -> None:
        """말풍선으로 말하고, 말풍선이 사라지면 평소 표정으로 돌아간다."""
        self._idle_timer.stop()
        self.character.set_expression(expression)
        self.bubble.show_text(text)
        self._reposition()

    def think(self) -> None:
        """답을 만드는 동안의 모습."""
        self.hold(THINKING_TEXT, "thinking")

    def stream(self, text_so_far: str) -> None:
        """답이 흘러나오는 동안 말풍선을 고쳐 쓴다."""
        self.hold(text_so_far, "talking")

    def hold(self, text: str, expression: str) -> None:
        """release()를 부를 때까지 말풍선을 띄워 둔다."""
        self._idle_timer.stop()
        self.character.set_expression(expression)
        self.bubble.hold_text(text)
        self._reposition()

    def release(self) -> None:
        """붙잡아 둔 말풍선을 놓아준다. 조금 뒤 사라지고 평소 표정으로 돌아간다."""
        self.bubble.release()

    def set_idle_expression(self, expression: str) -> None:
        """말하지 않을 때의 표정(기분)을 바꾼다. 지금 쉬고 있으면 바로 그 표정이 된다."""
        resting = not self.bubble.isVisible() and self.character.expression == self.idle_expression
        self.idle_expression = expression
        if resting:
            self.character.set_expression(expression)

    def set_expression(self, expression: str, *, hold_ms: int | None = None) -> None:
        self.character.set_expression(expression)
        if hold_ms is not None:
            self._idle_timer.start(hold_ms)

    def summon(self) -> None:
        self.character.raise_()
        self.input_bar.focus_input()
        self._reposition()

    def _reposition(self) -> None:
        character = _rect(self.character.geometry())
        screen = self.character.screen() or QGuiApplication.primaryScreen()
        area = _rect(screen.availableGeometry())
        self.bubble.move(*bubble_position(character, (self.bubble.width(), self.bubble.height()), area))
        self.input_bar.move(*input_position(character, (self.input_bar.width(), self.input_bar.height()), area))

"""캐릭터가 말하고 표정 짓는 일을 맡는다. 보이는 것은 전부 왼쪽 패널 안에 있다."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QObject, QPoint, QTimer, Signal

from assister.character.pack import CharacterPack
from assister.character.panel import SidePanel

IDLE_EXPRESSION = "neutral"
THINKING_TEXT = "…"
MIN_SHOW_MS = 4000
MAX_SHOW_MS = 20000
MS_PER_CHARACTER = 90


def show_duration_ms(text: str) -> int:
    """말한 뒤 그 표정을 얼마나 짓고 있을지. 말이 길수록 오래다."""
    return max(MIN_SHOW_MS, min(MAX_SHOW_MS, len(text) * MS_PER_CHARACTER))


class CharacterStage(QObject):
    submitted = Signal(str)
    clicked = Signal()
    mic_clicked = Signal()
    menu_requested = Signal(QPoint)
    moved = Signal(int, int)  # 패널을 옮겨 놓은 자리
    resized = Signal(int, int)  # 패널의 바꿔 놓은 크기
    docked = Signal()  # 패널을 왼쪽 1/4 자리로 되돌렸다

    def __init__(self, pack: CharacterPack, outfit: str) -> None:
        super().__init__()
        self.pack = pack
        self.panel = SidePanel(pack, outfit)
        self.character = self.panel.character
        self.input_bar = self.panel.input_bar
        self.speech = ""  # 지금 하고 있거나 마지막으로 한 말
        self.idle_expression = IDLE_EXPRESSION
        self._entry = None  # 아직 고쳐 쓰는 중인 말. 대화 기록의 한 덩어리다
        self._speaking = False
        # 말을 마치면 조금 뒤 평소 표정으로 돌아간다.
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self.settle)

        self.character.clicked.connect(self.clicked)
        self.character.menu_requested.connect(self.menu_requested)
        self.input_bar.submitted.connect(self.submitted)
        self.input_bar.mic_clicked.connect(self.mic_clicked)
        self.panel.hide_clicked.connect(self.panel.hide)
        self.panel.moved.connect(self.moved)
        self.panel.resized.connect(self.resized)
        self.panel.docked.connect(self.docked)

    @property
    def settling(self) -> bool:
        """말을 다 했고, 평소 표정으로 돌아가기를 기다리는 중인지."""
        return self._settle_timer.isActive()

    def show(self, position: tuple[int, int] | None = None, size: tuple[int, int] | None = None) -> None:
        """position과 size는 사용자가 패널을 옮기고 늘려 둔 자리와 크기다. 없으면 화면 왼쪽 1/4에 붙는다."""
        self.panel.place(position, size)
        self.panel.show()

    def close(self) -> None:
        self.panel.close()

    def load_history(self, messages: Iterable[tuple[str, str]]) -> None:
        """예전에 나눈 말을 대화 기록에 채운다. (누가, 무슨 말) 순서쌍을 오래된 것부터 받는다."""
        for role, text in messages:
            self.panel.log.add(role, text)

    def show_user(self, text: str) -> None:
        self.panel.log.add("user", text)

    def note(self, text: str) -> None:
        """캐릭터가 한 일(실행한 명령, 검색어)을 대화 기록에 작게 남긴다. 그 일로 하는 답보다 위에 온다."""
        self.panel.log.add("note", text, before=self._entry)

    def say(self, text: str, expression: str = "talking") -> None:
        """한 마디 하고, 조금 뒤 평소 표정으로 돌아간다."""
        self.character.set_expression(expression)
        self._write(text)
        self._entry = None
        self._settle_timer.start(show_duration_ms(text))

    def think(self) -> None:
        """답을 만드는 동안의 모습."""
        self.hold(THINKING_TEXT, "thinking")

    def stream(self, text_so_far: str) -> None:
        """답이 흘러나오는 동안 같은 덩어리를 고쳐 쓴다."""
        self.hold(text_so_far, "talking")

    def hold(self, text: str, expression: str) -> None:
        """release()를 부를 때까지 이 말을 하는 중으로 둔다."""
        self._settle_timer.stop()
        self.character.set_expression(expression)
        self._write(text)

    def release(self) -> None:
        """하던 말을 마친다. 조금 뒤 평소 표정으로 돌아간다."""
        self._entry = None
        if self._speaking:
            self._settle_timer.start(show_duration_ms(self.speech))

    def settle(self) -> None:
        self._settle_timer.stop()
        self._speaking = False
        self.character.set_expression(self.idle_expression)

    def set_idle_expression(self, expression: str) -> None:
        """말하지 않을 때의 표정(기분)을 바꾼다. 지금 쉬고 있으면 바로 그 표정이 된다."""
        resting = not self._speaking and self.character.expression == self.idle_expression
        self.idle_expression = expression
        if resting:
            self.character.set_expression(expression)

    def set_expression(self, expression: str, *, hold_ms: int | None = None) -> None:
        self.character.set_expression(expression)
        if hold_ms is not None:
            self._settle_timer.start(hold_ms)

    def summon(self) -> None:
        """패널을 꺼내고 바로 글을 칠 수 있게 한다."""
        self.reveal()
        self.input_bar.focus_input()

    def reveal(self) -> None:
        """패널을 꺼내기만 한다. 사용자가 다른 창에 글을 치고 있을 수 있어 글쇠는 가져오지 않는다."""
        self.panel.show()
        self.panel.raise_()

    def hide(self) -> None:
        self.panel.hide()

    def toggle(self) -> None:
        """보이면 숨기고, 숨어 있으면 꺼낸다 (단축키)."""
        if self.panel.isVisible():
            self.hide()
        else:
            self.summon()

    def _write(self, text: str) -> None:
        self.speech = text
        self._speaking = True
        if self._entry is None:
            self._entry = self.panel.log.add("assistant", text)
        else:
            self._entry.set_text(text)

"""패널 맨 아래의 입력창과 마이크·화면 보기 버튼."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

HEARD_SHOW_MS = 4000
EDIT_STYLE = """
QLineEdit {
    background: #fffaf3;
    color: #2b2622;
    border: 1.5px solid #d9cfc4;
    border-radius: 16px;
    padding: 6px 14px;
    font-size: 13px;
}
QLineEdit:focus { border-color: #f28c38; }
"""
MIC_STYLE = """
QPushButton {{
    background: {background};
    color: {color};
    border: 1.5px solid {border};
    border-radius: 16px;
    font-size: 14px;
}}
"""
# 마이크 상태마다 (배경, 글자, 테두리, 풍선 도움말). 듣고 있는지 언제든 눈으로 알 수 있어야 한다.
MIC_LOOKS = {
    "off": ("#fffaf3", "#b5aca2", "#d9cfc4", "마이크 꺼짐 · 누르면 한 마디 듣기"),
    "waiting": ("#fffaf3", "#f28c38", "#f28c38", "이름을 부르면 듣습니다 · 누르면 바로 듣기"),
    "armed": ("#f28c38", "#ffffff", "#f28c38", "말씀하세요"),
    "hearing": ("#e5484d", "#ffffff", "#e5484d", "듣는 중"),
    "thinking": ("#f5c542", "#2b2622", "#f5c542", "받아쓰는 중"),
}
# 화면을 보고 있는지도 같은 방식으로 늘 보여 준다.
EYE_LOOKS = {
    "off": ("#fffaf3", "#b5aca2", "#d9cfc4", "화면을 보지 않음 · 누르면 보기 시작"),
    "watching": ("#fffaf3", "#f28c38", "#f28c38", "화면을 보는 중 · 누르면 끄기"),
    "hidden": ("#eeeae4", "#8a8178", "#8a8178", "가릴 창이 앞에 있어 지금은 보지 않음 · 누르면 끄기"),
    "resting": ("#eeeae4", "#8a8178", "#8a8178", "전체 화면이라 쉬는 중 · 누르면 끄기"),
}


class _LineEdit(QLineEdit):
    escaped = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt 이름)
        if event.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
        else:
            super().keyPressEvent(event)


class InputBar(QWidget):
    submitted = Signal(str)
    mic_clicked = Signal()
    eye_clicked = Signal()

    def __init__(self, character_name: str) -> None:
        super().__init__()
        self._placeholder = f"{character_name}에게 말하기…"
        self.mic_state = "off"
        self.eye_state = "off"

        self._edit = _LineEdit()
        self._edit.setPlaceholderText(self._placeholder)
        self._edit.setStyleSheet(EDIT_STYLE)
        self._edit.returnPressed.connect(self._submit)
        self._edit.escaped.connect(self._edit.clear)

        self._mic = self._round_button("🎤", self.mic_clicked)
        self._eye = self._round_button("👁", self.eye_clicked)

        self._heard_timer = QTimer(self)
        self._heard_timer.setSingleShot(True)
        self._heard_timer.timeout.connect(lambda: self._edit.setPlaceholderText(self._placeholder))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._edit)
        layout.addWidget(self._mic)
        layout.addWidget(self._eye)
        self.set_mic_state("off")
        self.set_eye_state("off")

    @staticmethod
    def _round_button(text: str, clicked: Signal) -> QPushButton:
        button = QPushButton(text)
        button.setFixedSize(34, 32)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(clicked)
        return button

    def set_eye_state(self, state: str) -> None:
        background, color, border, tip = EYE_LOOKS.get(state, EYE_LOOKS["off"])
        self.eye_state = state
        self._eye.setStyleSheet(MIC_STYLE.format(background=background, color=color, border=border))
        self._eye.setToolTip(tip)

    def focus_input(self) -> None:
        """단축키나 캐릭터 클릭으로 불렀을 때 바로 글을 칠 수 있게 한다."""
        self.activateWindow()
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def set_mic_state(self, state: str) -> None:
        background, color, border, tip = MIC_LOOKS.get(state, MIC_LOOKS["off"])
        self.mic_state = state
        self._mic.setStyleSheet(MIC_STYLE.format(background=background, color=color, border=border))
        self._mic.setToolTip(tip)

    def show_heard(self, text: str) -> None:
        """마이크로 알아들은 말을 잠깐 보여 준다. 잘못 알아들었는지 확인할 수 있다."""
        self._edit.setPlaceholderText(f"들은 말: {text}")
        self._heard_timer.start(HEARD_SHOW_MS)

    def _submit(self) -> None:
        text = self._edit.text().strip()
        if text:
            self._edit.clear()
            self.submitted.emit(text)

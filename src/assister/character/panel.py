"""화면 왼쪽 1/4을 위에서 아래까지 채우는 패널.

캐릭터, 대화 기록, 할 일·알람, 오늘 화면 기록, 입력창이 들어 있다. 다른 창 위에 덮여 뜬다.
"""

from __future__ import annotations

import ctypes
import html
import sys
from collections.abc import Sequence
from ctypes import wintypes

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from assister.character.input_bar import InputBar
from assister.character.pack import CharacterPack
from assister.character.window import FLOATING_FLAGS, CharacterWindow

SCREEN_SHARE = 4  # 화면 가로의 1/4
CHARACTER_SHARE = 0.26  # 패널 높이에서 캐릭터 그림이 차지하는 몫
MAX_CHARACTER_HEIGHT = 300
ENTRY_WIDTH_SHARE = 0.8  # 말 한 덩어리가 대화 기록 폭에서 차지할 수 있는 몫
MAX_ENTRIES = 200
WDA_EXCLUDEFROMCAPTURE = 0x11
GRIP = 20  # 기억해 둔 자리가 쓸 만한지 볼 때, 창 왼쪽 위에서 이만큼 안쪽이 화면에 있어야 한다
MOVE_SAVE_MS = 400  # 끄는 동안에는 자리와 크기가 계속 바뀌니, 멈춘 뒤에 한 번만 알린다
MIN_SIZE = (280, 420)  # 이보다 작으면 입력창과 대화 기록이 쓸 수 없게 좁아진다
ROLES = ("user", "assistant", "note")
ALIGN = {"user": Qt.AlignmentFlag.AlignRight, "assistant": Qt.AlignmentFlag.AlignLeft, "note": Qt.AlignmentFlag.AlignLeft}
PADDING = {"user": (12, 7, 12, 7), "assistant": (12, 7, 12, 7), "note": (4, 0, 4, 0)}
STYLE = """
SidePanel { background: #fbf6ee; border-right: 1.5px solid #d9cfc4; }
QLabel { color: #2b2622; font-size: 13px; background: transparent; }
QLabel#name { font-size: 15px; font-weight: 600; }
QLabel#status { color: #8a8178; font-size: 12px; }
QFrame#user { background: #fde6d2; border-radius: 12px; }
QFrame#assistant { background: #ffffff; border: 1px solid #e6ddd2; border-radius: 12px; }
QFrame#note QLabel { color: #8a8178; font-size: 11px; font-family: Consolas, monospace; }
QTabWidget::pane { border: none; border-top: 1px solid #e6ddd2; }
QTabBar::tab {
    background: transparent; color: #8a8178; padding: 7px 12px; font-size: 13px;
    border: none; border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: #2b2622; border-bottom: 2px solid #f28c38; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #d9cfc4; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QPushButton#hide { background: transparent; color: #8a8178; border: none; font-size: 16px; }
QLabel#grip { color: #b5aca2; font-size: 14px; }
QPushButton#hide:hover { color: #2b2622; }
"""


class _Entry(QFrame):
    """대화 기록의 한 덩어리."""

    def __init__(self, role: str, text: str, max_width: int) -> None:
        super().__init__()
        self.role = role
        self.setObjectName(role)  # 꾸밈은 역할 이름으로 고른다
        self._max_width = max_width
        self._label = QLabel()
        self._label.setWordWrap(True)
        # 인터넷에서 온 글이 섞일 수 있다. 꾸밈 기호로 읽지 않고 글자 그대로 보여 준다.
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PADDING[role])
        layout.addWidget(self._label)
        self._label.setText(text)

    @property
    def text(self) -> str:
        return self._label.text()

    def set_text(self, text: str) -> None:
        self._label.setText(text)
        # 줄바꿈되는 글상자는 폭을 좁게 잡는 버릇이 있어, 폭과 높이를 직접 정해 준다.
        self._label.ensurePolished()
        metrics = self._label.fontMetrics()
        natural = max(metrics.horizontalAdvance(line) for line in text.split("\n")) + 6
        self._label.setFixedWidth(max(1, min(self._max_width, natural)))
        self._label.setFixedHeight(self._label.heightForWidth(self._label.width()))

    def fit(self, max_width: int) -> None:
        """패널 폭이 바뀌었을 때 줄바꿈을 다시 맞춘다."""
        self._max_width = max_width
        self.set_text(self.text)


class ChatLog(QScrollArea):
    """말한 것을 위에서 아래로 쌓는다. 사용자의 말은 오른쪽, 캐릭터의 말은 왼쪽, 한 일은 작은 글씨로."""

    def __init__(self) -> None:
        super().__init__()
        self.entry_width = 200  # 패널이 자리를 잡으면 알맞게 바뀐다
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._rows = QVBoxLayout(body)
        self._rows.setContentsMargins(10, 10, 10, 10)
        self._rows.setSpacing(6)
        self._rows.addStretch(1)
        self.setWidget(body)
        self._entries: list[_Entry] = []
        # ponytail: 새 글이 오면 늘 맨 아래로 간다. 옛 글을 읽는 중에도 내려가니, 거슬리면 맨 아래에 있을 때만 따라가게 한다.
        self.verticalScrollBar().rangeChanged.connect(lambda _low, high: self.verticalScrollBar().setValue(high))

    def add(self, role: str, text: str, *, before: _Entry | None = None) -> _Entry:
        """맨 아래에 한 덩어리를 더한다. before를 주면 그 덩어리 바로 위에 끼운다."""
        entry = _Entry(role, text, self.entry_width)
        at = self._entries.index(before) if before in self._entries else len(self._entries)
        self._rows.insertWidget(at, entry, 0, ALIGN[role])
        # 크기는 패널에 넣은 뒤에 잰다. 그 전에는 패널의 글씨 크기가 아직 적용되지 않아 글이 잘린다.
        entry.set_text(text)
        self._entries.insert(at, entry)
        for old in self._entries[:-MAX_ENTRIES]:
            old.deleteLater()
        self._entries = self._entries[-MAX_ENTRIES:]
        return entry

    def set_entry_width(self, width: int) -> None:
        if width != self.entry_width:
            self.entry_width = width
            for entry in self._entries:
                entry.fit(width)

    def entries(self) -> list[tuple[str, str]]:
        """(누가, 무슨 말) 목록. 오래된 것부터."""
        return [(entry.role, entry.text) for entry in self._entries]


class _Page(QScrollArea):
    """글 목록을 보여 주는 쪽."""

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._label.setContentsMargins(12, 10, 12, 10)
        self.setWidget(self._label)

    @property
    def text(self) -> str:
        return self._label.text()

    def show_sections(self, sections: Sequence[tuple[str, Sequence[str], str]]) -> None:
        """(제목, 줄들, 비었을 때 보여 줄 말)을 차례로 보여 준다."""
        parts = []
        for title, lines, empty in sections:
            body = "<br>".join(html.escape(line) for line in lines) if lines else f"<span style='color:#b5aca2'>{empty}</span>"
            parts.append(f"<p style='line-height:140%'><b>{title}</b><br>{body}</p>")
        self._label.setText("".join(parts))


def _keep_out_of_screenshots(widget: QWidget) -> None:
    """화면을 찍을 때 이 창이 찍히지 않게 한다.

    화면 보기가 30초마다 자기 대화창을 읽어 봐야 쓸모가 없고, 화면 공유에도 대화가 드러나지 않는다.
    """
    if sys.platform != "win32":
        return
    affinity = ctypes.windll.user32.SetWindowDisplayAffinity
    affinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    affinity(int(widget.winId()), WDA_EXCLUDEFROMCAPTURE)


class SidePanel(QWidget):
    hide_clicked = Signal()
    moved = Signal(int, int)  # 옮겨 놓은 자리 (왼쪽 위)
    resized = Signal(int, int)  # 바꿔 놓은 크기 (너비, 높이)
    docked = Signal()  # 사용자가 왼쪽 1/4 자리로 되돌렸다

    def __init__(self, pack: CharacterPack, outfit: str) -> None:
        super().__init__(None, FLOATING_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(STYLE)
        self.setMinimumSize(*MIN_SIZE)
        area = QGuiApplication.primaryScreen().availableGeometry()

        self.character = CharacterWindow(pack, outfit, min(MAX_CHARACTER_HEIGHT, round(area.height() * CHARACTER_SHARE)))
        self.input_bar = InputBar(pack.name)
        self.log = ChatLog()
        self.tasks = _Page()
        self.screen = _Page()
        self._status = QLabel()
        self._status.setObjectName("status")
        name = QLabel(pack.name)
        name.setObjectName("name")
        hide = QPushButton("—")
        hide.setObjectName("hide")
        hide.setToolTip("패널 숨기기 · 트레이 아이콘을 누르면 다시 나옵니다")
        hide.setCursor(Qt.CursorShape.PointingHandCursor)
        hide.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hide.clicked.connect(self.hide_clicked)

        grip = QLabel("⋮⋮")
        grip.setObjectName("grip")
        grip.setToolTip("위쪽을 잡고 끌면 옮겨집니다 · 두 번 누르면 왼쪽 자리로 돌아갑니다")
        grip.setCursor(Qt.CursorShape.SizeAllCursor)
        self._moved_timer = QTimer(self)
        self._moved_timer.setSingleShot(True)
        self._moved_timer.timeout.connect(self._report_place)

        top = QHBoxLayout()
        top.addWidget(grip)
        top.addStretch(1)
        top.addWidget(hide)
        header = QVBoxLayout()
        header.setContentsMargins(8, 4, 8, 8)
        header.setSpacing(2)
        header.addLayout(top)
        for widget in (self.character, name, self._status):
            header.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.log, "대화")
        self.tabs.addTab(self.tasks, "할 일·알람")
        self.tabs.addTab(self.screen, "화면 기록")

        corner = QSizeGrip(self)
        corner.setToolTip("끌어서 크기를 바꿉니다")
        bottom = QHBoxLayout()
        bottom.setContentsMargins(10, 8, 2, 2)
        bottom.setSpacing(2)
        bottom.addWidget(self.input_bar, 1)
        bottom.addWidget(corner, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(bottom)

        self.show_tasks([], [])
        self.show_screen_notes([])
        self.dock()

    def dock(self) -> None:
        """주 모니터의 왼쪽 1/4에 위에서 아래까지 붙는다. 작업 표시줄은 가리지 않는다."""
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.setGeometry(area.x(), area.y(), area.width() // SCREEN_SHARE, area.height())
        self._refit()

    def place(self, position: tuple[int, int] | None, size: tuple[int, int] | None = None) -> None:
        """기억해 둔 자리와 크기로 놓는다. 그 자리가 지금 연결된 화면 밖이면 왼쪽 1/4 자리에 둔다."""
        self.dock()
        if size is not None:
            area = QGuiApplication.primaryScreen().availableGeometry()
            self.resize(min(size[0], area.width()), min(size[1], area.height()))  # 화면보다 크게는 두지 않는다
            self._refit()
        if position is not None and QGuiApplication.screenAt(QPoint(position[0] + GRIP, position[1] + GRIP)):
            self.move(*position)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        # 위쪽(캐릭터 둘레)을 잡고 끌면 창이 따라온다. 옮기는 일은 Windows에 맡긴다.
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < self.tabs.y():
            self.windowHandle().startSystemMove()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.position().y() < self.tabs.y():
            self.dock()
            self._moved_timer.stop()  # 되돌린 자리를 '옮겨 둔 자리'로 기억하지 않게 한다
            self.docked.emit()

    def _refit(self) -> None:
        # 창을 띄우기 전에는 크기가 바뀌었다는 알림이 오지 않으므로, 크기를 정할 때마다 직접 맞춘다.
        self.log.set_entry_width(max(80, round(self.width() * ENTRY_WIDTH_SHARE) - 44))

    def _report_place(self) -> None:
        self.moved.emit(self.x(), self.y())
        self.resized.emit(self.width(), self.height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refit()
        if self.isVisible():
            self._moved_timer.start(MOVE_SAVE_MS)

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        if self.isVisible():
            self._moved_timer.start(MOVE_SAVE_MS)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        super().showEvent(event)
        _keep_out_of_screenshots(self)

    def show_status(self, text: str) -> None:
        self._status.setText(text)

    def show_tasks(self, todos: Sequence[str], alarms: Sequence[str]) -> None:
        numbered = [f"{number}. {text}" for number, text in enumerate(todos, start=1)]
        self.tasks.show_sections([("할 일", numbered, "남은 할 일이 없어요"), ("알람", alarms, "울릴 알람이 없어요")])

    def show_screen_notes(self, notes: Sequence[str]) -> None:
        self.screen.show_sections([("오늘 화면에서 본 것", notes, "아직 본 것이 없어요")])

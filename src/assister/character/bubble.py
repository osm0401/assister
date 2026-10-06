"""캐릭터 머리 위에 뜨는 말풍선."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from assister.character.window import FLOATING_FLAGS

MAX_TEXT_WIDTH = 300
TAIL_HEIGHT = 12
MIN_SHOW_MS = 4000
MAX_SHOW_MS = 20000
MS_PER_CHARACTER = 90


def show_duration_ms(text: str) -> int:
    """글이 길수록 오래 보여 준다."""
    return max(MIN_SHOW_MS, min(MAX_SHOW_MS, len(text) * MS_PER_CHARACTER))


class SpeechBubble(QWidget):
    resized = Signal()
    hidden = Signal()

    def __init__(self) -> None:
        super().__init__(None, FLOATING_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(MAX_TEXT_WIDTH)
        self._label.setStyleSheet("color:#2b2622; font-size:14px; background:transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12 + TAIL_HEIGHT)
        layout.addWidget(self._label)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    @property
    def text(self) -> str:
        return self._label.text()

    def show_text(self, text: str, duration_ms: int | None = None) -> None:
        """말을 보여 주고, 읽을 만큼 시간이 지나면 저절로 사라진다."""
        self._set_text(text)
        self._timer.start(duration_ms if duration_ms is not None else show_duration_ms(text))

    def hold_text(self, text: str) -> None:
        """말을 보여 주고 release()를 부를 때까지 그대로 둔다. 글이 흘러나오는 동안 고쳐 쓸 때 쓴다."""
        self._timer.stop()
        self._set_text(text)

    def release(self, duration_ms: int | None = None) -> None:
        """붙잡아 둔 말풍선이 이제부터 시간이 지나면 사라지게 한다."""
        if self.isVisible():
            self._timer.start(duration_ms if duration_ms is not None else show_duration_ms(self.text))

    def _set_text(self, text: str) -> None:
        self._label.setText(text)
        # 줄바꿈되는 글상자는 폭을 좁게 잡는 버릇이 있어, 짧은 말은 한 줄 폭을 직접 정해 준다.
        self._label.ensurePolished()
        metrics = self._label.fontMetrics()
        natural = max(metrics.horizontalAdvance(line) for line in text.split("\n")) + 6
        self._label.setFixedWidth(min(MAX_TEXT_WIDTH, natural))
        self._label.setFixedHeight(self._label.heightForWidth(self._label.width()))
        self.adjustSize()
        self.resized.emit()
        self.show()

    def dismiss(self) -> None:
        if self.isVisible():
            self._timer.stop()
            self.hide()
            self.hidden.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        self.dismiss()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(1, 1, self.width() - 2, self.height() - TAIL_HEIGHT - 2)
        path = QPainterPath()
        path.addRoundedRect(body, 14, 14)
        middle = self.width() / 2
        tail = QPolygonF([
            QPointF(middle - 10, body.bottom() - 1),
            QPointF(middle + 10, body.bottom() - 1),
            QPointF(middle, self.height() - 1),
        ])
        path.addPolygon(tail)
        painter.setPen(QPen(QColor("#d9cfc4"), 1.5))
        painter.setBrush(QColor("#fffaf3"))
        painter.drawPath(path.simplified())

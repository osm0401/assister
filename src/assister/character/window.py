"""바탕화면 위에 항상 떠 있는 캐릭터 창."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from assister.character.pack import CharacterPack

DRAG_THRESHOLD = 5
FLOATING_FLAGS = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool


class CharacterWindow(QWidget):
    clicked = Signal()
    dragged = Signal()  # 끄는 동안 계속
    drag_finished = Signal(QPoint)
    menu_requested = Signal(QPoint)

    def __init__(self, pack: CharacterPack, outfit: str, height: int) -> None:
        super().__init__(None, FLOATING_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self._pack = pack
        self._outfit = outfit
        self._props: tuple[str, ...] = ()
        self._expression = "neutral"
        self._height = height
        self._pixmap = QPixmap()
        self._cache: dict[tuple, QPixmap] = {}
        self._press_global: QPoint | None = None
        self._press_window = QPoint()
        self._dragging = False
        self._refresh()

    @property
    def expression(self) -> str:
        return self._expression

    def set_expression(self, expression: str) -> None:
        if expression != self._expression:
            self._expression = expression
            self._refresh()

    def set_outfit(self, outfit: str) -> None:
        self._outfit = outfit
        self._refresh()

    def set_props(self, props: tuple[str, ...]) -> None:
        self._props = props
        self._refresh()

    def _compose(self) -> QPixmap:
        """표정 그림 위에 소품을 겹치고 화면 배율에 맞춰 줄인다."""
        ratio = self.devicePixelRatioF()
        key = (self._outfit, self._expression, self._props, self._height, ratio)
        if key in self._cache:
            return self._cache[key]

        base = QPixmap(str(self._pack.image_path(self._outfit, self._expression)))
        if base.isNull():
            raise OSError(f"표정 그림을 열 수 없습니다: {self._pack.image_path(self._outfit, self._expression)}")
        painter = QPainter(base)
        for prop in self._pack.props:
            if prop.id in self._props:
                painter.drawPixmap(base.rect(), QPixmap(str(prop.path)))
        painter.end()

        scaled = base.scaledToHeight(round(self._height * ratio), Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self._cache[key] = scaled
        return scaled

    def _refresh(self) -> None:
        self._pixmap = self._compose()
        self.setFixedSize(self._pixmap.deviceIndependentSize().toSize())
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._pixmap)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_window = self.pos()
            self._dragging = False
        elif event.button() == Qt.MouseButton.RightButton:
            self.menu_requested.emit(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._press_global is None:
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if not self._dragging and delta.manhattanLength() >= DRAG_THRESHOLD:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._dragging:
            self.move(self._press_window + delta)
            self.dragged.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._press_global is None:
            return
        self._press_global = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if self._dragging:
            self._dragging = False
            self.drag_finished.emit(self.pos())
        else:
            self.clicked.emit()

"""캐릭터 우클릭과 작업 표시줄 아이콘에서 여는 메뉴."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QObject, QPoint, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from assister.character.pack import CharacterPack


class AppMenu(QObject):
    do_not_disturb_toggled = Signal(bool)
    tts_toggled = Signal(bool)
    wake_toggled = Signal(bool)
    screen_toggled = Signal(bool)
    outfit_chosen = Signal(str)
    prop_toggled = Signal(str, bool)
    todos_requested = Signal()
    settings_requested = Signal()
    export_requested = Signal()
    import_requested = Signal()
    tray_clicked = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        pack: CharacterPack,
        *,
        outfit: str,
        do_not_disturb: bool,
        tts_enabled: bool,
        wake_word_enabled: bool,
        screen_watch_enabled: bool,
        props: Sequence[str] = (),
        level: int = 1,
        points_left: int | None = None,
    ) -> None:
        super().__init__()
        self.menu = QMenu()

        self._dnd = self._switch("방해금지", do_not_disturb, self.do_not_disturb_toggled)
        self._tts = self._switch("목소리로 답하기", tts_enabled, self.tts_toggled)
        self._wake = self._switch(f"이름 부르기 (\"{pack.name}\" · 마이크 대기)", wake_word_enabled, self.wake_toggled)
        self._screen = self._switch("화면 보기", screen_watch_enabled, self.screen_toggled)
        self.menu.addSeparator()

        todos = QAction("할 일 보기", self.menu)
        todos.triggered.connect(self.todos_requested)
        self.menu.addAction(todos)

        # 옷과 소품은 친밀도 레벨이 오르면 하나씩 열린다. 잠긴 것은 몇 레벨에 열리는지 보여 준다.
        self._bond = QAction("", self.menu)
        self._bond.setEnabled(False)
        self.menu.addAction(self._bond)
        self._lockable: list[tuple[QAction, str, int]] = []

        outfits = self.menu.addMenu("옷 갈아입기")
        group = QActionGroup(outfits)
        for item in pack.outfits:
            action = QAction(item.label, outfits, checkable=True)
            action.setChecked(item.id == outfit)
            action.triggered.connect(lambda _checked, outfit_id=item.id: self.outfit_chosen.emit(outfit_id))
            group.addAction(action)
            outfits.addAction(action)
            self._lockable.append((action, item.label, item.unlock_level))

        if pack.props:
            prop_menu = self.menu.addMenu("소품")
            for prop in pack.props:
                action = QAction(prop.label, prop_menu, checkable=True)
                action.setChecked(prop.id in props)
                action.toggled.connect(lambda checked, prop_id=prop.id: self.prop_toggled.emit(prop_id, checked))
                prop_menu.addAction(action)
                self._lockable.append((action, prop.label, prop.unlock_level))
        self.set_level(level, points_left)

        self.menu.addSeparator()
        for label, signal in (
            ("설정…", self.settings_requested),
            ("기억 내보내기…", self.export_requested),
            ("기억 가져오기…", self.import_requested),
        ):
            action = QAction(label, self.menu)
            action.triggered.connect(signal)
            self.menu.addAction(action)
        quit_action = QAction("종료", self.menu)
        quit_action.triggered.connect(self.quit_requested)
        self.menu.addAction(quit_action)

        self.tray = QSystemTrayIcon(QIcon(str(pack.image_path(outfit, "neutral"))))
        self.tray.setToolTip(pack.name)
        self.tray.setContextMenu(self.menu)
        # 아이콘을 누르면 캐릭터를 부른다. 다른 창에 가려졌을 때 찾기 쉽다.
        clicks = (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick)
        self.tray.activated.connect(lambda reason: self.tray_clicked.emit() if reason in clicks else None)

    def _switch(self, label: str, checked: bool, signal) -> QAction:
        action = QAction(label, self.menu, checkable=True)
        action.setChecked(checked)
        action.toggled.connect(signal)
        self.menu.addAction(action)
        return action

    def set_level(self, level: int, points_left: int | None) -> None:
        """친밀도 레벨에 맞춰 잠긴 옷과 소품을 열고, 메뉴 맨 위 글을 고친다."""
        ahead = f"다음 레벨까지 {points_left}마디" if points_left is not None else "가장 높은 레벨"
        self._bond.setText(f"친밀도 레벨 {level} · {ahead}")
        for action, label, unlock_level in self._lockable:
            unlocked = level >= unlock_level
            action.setEnabled(unlocked)
            action.setText(label if unlocked else f"{label} (레벨 {unlock_level}에 열림)")

    def set_wake_checked(self, checked: bool) -> None:
        """마이크가 고장 나 저절로 꺼졌을 때 메뉴 표시를 맞춘다. 신호는 다시 보내지 않는다."""
        self._set_quietly(self._wake, checked)

    def set_screen_checked(self, checked: bool) -> None:
        """입력창의 눈 버튼으로 켜고 껐을 때 메뉴 표시를 맞춘다."""
        self._set_quietly(self._screen, checked)

    @staticmethod
    def _set_quietly(action: QAction, checked: bool) -> None:
        action.blockSignals(True)
        action.setChecked(checked)
        action.blockSignals(False)

    def show_tray(self) -> None:
        self.tray.show()

    def popup(self, position: QPoint) -> None:
        self.menu.popup(position)

    def close(self) -> None:
        self.tray.hide()

"""설정 창. 캐릭터의 이름·말투·목소리와 화면 보기, 단축키를 고친다.

고친 값은 result_values()로 돌려줄 뿐이고, 파일에 쓰고 다시 시작하는 일은 app.py가 한다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
)

from assister.character.pack import CharacterPack, Voice
from assister.config import INT_RANGES, Settings
from assister.system.hotkey import HotkeyError, parse_hotkey

# Edge TTS의 한국어 목소리. 목록에 없는 이름도 직접 적어 넣을 수 있다.
VOICES = ("ko-KR-SunHiNeural", "ko-KR-InJoonNeural", "ko-KR-HyunsuMultilingualNeural")
SIGNED_NUMBER = re.compile(r"[+-]?\d+")


def _number(text: str) -> int:
    """'+10%', '-4Hz' 같은 글에서 숫자만 꺼낸다."""
    match = SIGNED_NUMBER.match(text)
    return int(match.group()) if match else 0


def _spin(name: str, value: int, suffix: str) -> QSpinBox:
    box = QSpinBox()
    box.setRange(*INT_RANGES[name])
    box.setValue(value)
    box.setSuffix(suffix)
    return box


def _offset(value: int, suffix: str) -> QSpinBox:
    box = QSpinBox()
    box.setRange(-50, 50)
    box.setValue(value)
    box.setSuffix(suffix)
    return box


class SettingsDialog(QDialog):
    preview_requested = Signal(object)  # 지금 고른 목소리(Voice)를 들어 보고 싶다

    def __init__(
        self, settings: Settings, pack: CharacterPack, *, autostart: bool = False, characters: Sequence[str] = ()
    ) -> None:
        """characters는 고를 수 있는 캐릭터 폴더 이름들이다."""
        super().__init__()
        self._settings = settings
        self._pack_id = pack.id
        self.character = QComboBox()
        self.character.addItems(sorted({pack.id, *characters}))
        self.character.setCurrentText(pack.id)
        self.character.setToolTip("characters 폴더에 캐릭터 폴더를 넣으면 여기에 나타납니다. 바꾸면 아래 이름·말투·목소리는 저장하지 않습니다.")
        self.setWindowTitle(f"{pack.name} 설정")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)

        self.name = QLineEdit(pack.name)
        self.persona = QPlainTextEdit(pack.persona)
        self.persona.setPlaceholderText("비워 두면 AI 본래의 말투를 따릅니다. 예: 반말로, 장난기 있게")
        self.persona.setFixedHeight(64)
        self.aliases = QLineEdit(", ".join(pack.wake_names[1:]))
        self.aliases.setPlaceholderText("이름 말고도 알아들을 호칭. 쉼표로 나눠 적습니다")
        self.voice = QComboBox()
        self.voice.setEditable(True)
        self.voice.addItems(VOICES)
        self.voice.setCurrentText(pack.voice.name)
        self.rate = _offset(_number(pack.voice.rate), " %")
        self.pitch = _offset(_number(pack.voice.pitch), " Hz")
        self.preview = QPushButton("들어 보기")
        self.preview.clicked.connect(lambda: self.preview_requested.emit(self._voice()))
        self.autostart = QCheckBox("Windows를 켤 때 같이 켜기")
        self.autostart.setChecked(autostart)
        self.rest_when_fullscreen = QCheckBox("게임·전체 화면 중에는 화면 보기 쉬기")
        self.rest_when_fullscreen.setChecked(settings.rest_when_fullscreen)

        self.interval = _spin("screen_interval_sec", settings.screen_interval_sec, " 초")
        self.retention = _spin("screen_retention_days", settings.screen_retention_days, " 일")
        self.proactive = _spin("proactive_per_hour", settings.proactive_per_hour, " 번")
        self.blocked = QPlainTextEdit("\n".join(settings.blocked_apps))
        self.blocked.setPlaceholderText("한 줄에 하나씩. 창 제목이나 프로그램 이름에 이 낱말이 있으면 보지 않습니다")
        self.blocked.setFixedHeight(64)

        self.engine = QComboBox()
        self.engine.addItem("내 PC (Ollama)", "ollama")
        self.engine.addItem("Google Gemini", "gemini")
        self.engine.addItem("Antigravity CLI (agy)", "agy")
        self.engine.setCurrentIndex(max(0, self.engine.findData(settings.engine)))
        self.engine.setToolTip(
            "Gemini를 고르면 대화와 화면 그림이 Google 서버로 나갑니다. "
            "API 키는 환경 변수 GEMINI_API_KEY에 넣어 두어야 합니다. "
            "agy는 키 없이 Antigravity 로그인으로 대화만 맡습니다. 답이 7초쯤 걸리고, "
            "화면 보기와 PC 조작은 Ollama가 계속 맡습니다."
        )

        self.summon_hotkey = QLineEdit(settings.summon_hotkey)
        self.voice_hotkey = QLineEdit(settings.voice_hotkey)

        self.error = QLabel("")
        self.error.setStyleSheet("color: #c0392b;")
        self.error.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장하고 다시 시작")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        for label, widget in (
            ("캐릭터", self.character),
            ("이름", self.name),
            ("말투 메모", self.persona),
            ("다른 호칭", self.aliases),
            ("목소리", self.voice),
            ("말 빠르기", self.rate),
            ("목소리 높이", self.pitch),
            ("", self.preview),
            ("화면 보는 주기", self.interval),
            ("화면 기록 보관", self.retention),
            ("먼저 말 걸기 (시간당)", self.proactive),
            ("가릴 프로그램", self.blocked),
            ("", self.rest_when_fullscreen),
            ("AI 엔진", self.engine),
            ("부르는 단축키", self.summon_hotkey),
            ("음성 입력 단축키", self.voice_hotkey),
            ("", self.autostart),
        ):
            form.addRow(label, widget)
        form.addRow(self.error)
        form.addRow(buttons)

    def character_changed(self) -> bool:
        """다른 캐릭터를 골랐는지. 그랬다면 이름·말투·목소리 칸은 예전 캐릭터의 것이라 저장하면 안 된다."""
        return self.character.currentText() != self._pack_id

    def _voice(self) -> Voice:
        return Voice(self.voice.currentText().strip(), f"{self.rate.value():+d}%", f"{self.pitch.value():+d}Hz")

    def _problem(self) -> str:
        if not self.name.text().strip():
            return "이름을 적어 주세요."
        if not self.voice.currentText().strip():
            return "목소리를 골라 주세요."
        keys = []
        for field in (self.summon_hotkey, self.voice_hotkey):
            try:
                keys.append(parse_hotkey(field.text()))
            except HotkeyError as error:
                return str(error)
        return "두 단축키가 같습니다. 서로 다르게 정해 주세요." if keys[0] == keys[1] else ""

    def accept(self) -> None:
        problem = self._problem()
        self.error.setText(problem)
        if not problem:
            super().accept()

    def result_values(self) -> tuple[Settings, dict]:
        """(새 설정, character.json에 적을 캐릭터 설정)"""
        blocked = tuple(line.strip() for line in self.blocked.toPlainText().splitlines() if line.strip())
        settings = replace(
            self._settings,
            character=self.character.currentText(),
            engine=self.engine.currentData(),
            screen_interval_sec=self.interval.value(),
            screen_retention_days=self.retention.value(),
            proactive_per_hour=self.proactive.value(),
            rest_when_fullscreen=self.rest_when_fullscreen.isChecked(),
            blocked_apps=blocked,
            summon_hotkey=self.summon_hotkey.text().strip(),
            voice_hotkey=self.voice_hotkey.text().strip(),
        )
        profile = {
            "name": self.name.text().strip(),
            "persona": self.persona.toPlainText().strip(),
            "voice": asdict(self._voice()),
            "wake_aliases": [alias.strip() for alias in self.aliases.text().split(",") if alias.strip()],
        }
        return settings, profile

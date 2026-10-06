"""속도 측정용 가짜 화면을 그린다.

실제 화면을 찍지 않고, 버튼 위치를 정확히 아는 가짜 바탕화면을 만들어
좌표 클릭 정확도를 채점할 수 있게 한다. 창은 화면에 띄우지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTextEdit,
    QWidget,
)

MAIL_BODY = (
    "민지 님, 안녕하세요.\n\n"
    "다음 주 수요일 오후 2시 회의 자료를 첨부해 보냅니다.\n"
    "3쪽의 예산 표는 아직 확정 전이라 숫자가 바뀔 수 있습니다.\n"
    "확인하시고 의견 주시면 금요일까지 반영하겠습니다.\n\n"
    "감사합니다.\n상민 드림"
)

FILES = [
    "회의자료_10월.pptx",
    "예산표_초안.xlsx",
    "여행 사진 (2026-09)",
    "영수증_스캔.pdf",
    "메모.txt",
    "캐릭터 표정 PNG",
]

PANEL_STYLE = "background:#ffffff; border:1px solid #c8ccd4;"
TITLE_STYLE = "background:#2f5fa8; color:#ffffff; padding-left:12px; border:none;"
TASKBAR_STYLE = "background:#1f2430; border:none;"


@dataclass(frozen=True)
class Target:
    """채점할 클릭 대상. rect는 (x, y, 너비, 높이)."""

    name: str
    rect: tuple[int, int, int, int]


def _button(parent: QWidget, text: str, rect: tuple[int, int, int, int], style: str = "") -> Target:
    button = QPushButton(text, parent)
    button.setGeometry(*rect)
    if style:
        button.setStyleSheet(style)
    return Target(name=text, rect=rect)


def build_screen(width: int, height: int) -> tuple[QWidget, list[Target]]:
    """메일 쓰기 창, 파일 탐색기, 작업 표시줄이 있는 가짜 화면을 만든다."""
    root = QWidget()
    root.setObjectName("desktop")
    root.setFixedSize(width, height)
    # 이름을 지정해 배경색이 안쪽 위젯으로 번지지 않게 한다.
    root.setStyleSheet("QWidget#desktop { background:#3b6ea5; }")
    root.setFont(QFont("Malgun Gothic", 11))
    targets: list[Target] = []

    # 왼쪽: 메일 쓰기 창
    mail = QFrame(root)
    mail.setGeometry(60, 50, 980, height - 190)
    mail.setStyleSheet(PANEL_STYLE)
    mail_title = QLabel("새 메일 쓰기 - 메일", root)
    mail_title.setGeometry(60, 50, 980, 40)
    mail_title.setStyleSheet(TITLE_STYLE)

    for row, (label, value) in enumerate(
        [("받는 사람", "minji.kim@example.com"), ("제목", "수요일 회의 자료 보내드립니다")]
    ):
        y = 110 + row * 52
        caption = QLabel(label, root)
        caption.setGeometry(84, y, 90, 36)
        caption.setStyleSheet("background:#ffffff; color:#202020; border:none;")
        field = QLineEdit(value, root)
        field.setGeometry(180, y, 836, 36)

    body = QTextEdit(root)
    body.setPlainText(MAIL_BODY)
    body.setGeometry(84, 220, 932, height - 470)

    button_y = height - 230
    targets.append(_button(root, "보내기", (84, button_y, 120, 44), "background:#2f5fa8; color:white;"))
    targets.append(_button(root, "임시 저장", (220, button_y, 120, 44)))
    targets.append(_button(root, "파일 첨부", (356, button_y, 120, 44)))
    targets.append(_button(root, "취소", (896, button_y, 120, 44)))

    # 오른쪽: 파일 탐색기
    explorer = QFrame(root)
    explorer.setGeometry(1100, 120, 760, 620)
    explorer.setStyleSheet(PANEL_STYLE)
    explorer_title = QLabel("문서 - 파일 탐색기", root)
    explorer_title.setGeometry(1100, 120, 760, 40)
    explorer_title.setStyleSheet(TITLE_STYLE)

    targets.append(_button(root, "새 폴더", (1124, 176, 110, 38)))
    targets.append(_button(root, "이름 바꾸기", (1244, 176, 130, 38)))
    targets.append(_button(root, "삭제", (1384, 176, 90, 38)))

    file_list = QListWidget(root)
    file_list.addItems(FILES)
    file_list.setGeometry(1124, 230, 712, 486)
    file_list.setCurrentRow(1)

    # 아래: 작업 표시줄
    taskbar = QFrame(root)
    taskbar.setGeometry(0, height - 56, width, 56)
    taskbar.setStyleSheet(TASKBAR_STYLE)
    taskbar_style = "background:#2c3344; color:white; border:1px solid #454d61;"
    targets.append(_button(root, "시작", (12, height - 48, 80, 40), taskbar_style))
    targets.append(_button(root, "설정", (104, height - 48, 80, 40), taskbar_style))
    targets.append(_button(root, "메모장", (196, height - 48, 90, 40), taskbar_style))
    clock = QLabel("오후 3:42", root)
    clock.setGeometry(width - 130, height - 48, 110, 40)
    clock.setStyleSheet("background:#1f2430; color:white; border:none;")

    return root, targets


def render(path: Path, width: int, height: int) -> list[Target]:
    """가짜 화면을 PNG로 저장하고 채점용 대상 목록을 돌려준다."""
    app = QApplication.instance() or QApplication([])
    # Windows가 어두운 테마여도 흔한 밝은 화면으로 그린다.
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    root, targets = build_screen(width, height)
    pixmap = root.grab()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(path), "PNG"):
        raise OSError(f"가짜 화면을 저장하지 못했습니다: {path}")
    root.deleteLater()
    app.processEvents()
    return targets

"""실행: uv run assister"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtWidgets import QApplication, QMessageBox

from assister import paths
from assister.app import DesktopFriend, single_instance_lock
from assister.character.pack import PackError


def set_up_logging() -> None:
    """명령 창과 data/assister.log 양쪽에 남긴다. 창 없이 뜬 앱은 파일로만 문제를 알 수 있다."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        paths.data_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(paths.data_dir() / "assister.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    except OSError:
        pass  # 파일을 못 만들어도 앱은 떠야 한다. 명령 창에는 그대로 남는다.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


def main() -> int:
    set_up_logging()
    app = QApplication(sys.argv)
    # 캐릭터 창은 작업 표시줄에 없는 도구 창이라, 창이 닫혀도 앱이 꺼지지 않게 한다.
    app.setQuitOnLastWindowClosed(False)
    lock = single_instance_lock(paths.data_dir())  # 앱이 끝날 때까지 쥐고 있어야 한다
    if lock is None:
        QMessageBox.information(None, "바탕화면 AI 친구", "이미 켜져 있어요.")
        return 0
    try:
        friend = DesktopFriend(app)
    except PackError as error:
        logging.getLogger(__name__).error("캐릭터를 불러오지 못했습니다: %s", error)
        return 1
    friend.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
